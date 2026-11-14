"""Route a SOCKS proxy connection through an explicit HTTP CONNECT proxy.

The relay is loopback-only. SOCKS authentication and destination TLS remain
between Requests and the existing SOCKS server / destination respectively.
No credentials are logged and no direct fallback is attempted.
"""

import select
import socket
import socketserver
import threading
import time
from urllib.parse import urlsplit, urlunsplit

_relays = {}
_relay_lock = threading.Lock()


def _read_connect_response(sock):
    header = bytearray()
    while not header.endswith(b"\r\n\r\n"):
        if len(header) >= 16384:
            raise OSError("Upstream proxy response header too large")
        part = sock.recv(1)
        if not part:
            raise OSError("Upstream proxy closed before CONNECT completed")
        header.extend(part)
    status = bytes(header).split(b"\r\n", 1)[0].split()
    if len(status) < 2 or status[1] != b"200":
        raise OSError("Upstream proxy rejected CONNECT")


class _RelayServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # Avoid traceback output containing connection details or credentials.
        pass


class _RelayHandler(socketserver.BaseRequestHandler):
    def handle(self):
        upstream = None
        try:
            upstream = socket.create_connection(self.server.upstream, timeout=15)
            upstream.settimeout(15)
            host, port = self.server.target
            authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
            upstream.sendall(
                (
                    f"CONNECT {authority} HTTP/1.1\r\n"
                    f"Host: {authority}\r\n"
                    "Proxy-Connection: Keep-Alive\r\n\r\n"
                ).encode("ascii")
            )
            _read_connect_response(upstream)
            self.request.settimeout(30)
            upstream.settimeout(30)
            active = [self.request, upstream]
            peers = {self.request: upstream, upstream: self.request}
            last_activity = time.monotonic()
            while active and time.monotonic() - last_activity < 120:
                readable, _, _ = select.select(active, [], [], 1)
                for source in readable:
                    block = source.recv(65536)
                    target = peers[source]
                    if not block:
                        active.remove(source)
                        try:
                            target.shutdown(socket.SHUT_WR)
                        except OSError:
                            pass
                        continue
                    target.sendall(block)
                    last_activity = time.monotonic()
        except OSError:
            # Closing the connection propagates failure to Requests/PySocks.
            pass
        finally:
            if upstream is not None:
                upstream.close()


def build_request_proxies(proxy_url, upstream_http_proxy=None):
    """Return Requests proxies; leave existing behavior unchanged if unset."""
    if not upstream_http_proxy:
        return {"http": proxy_url, "https": proxy_url}
    target = urlsplit(proxy_url)
    upstream = urlsplit(upstream_http_proxy)
    if target.scheme not in ("socks5", "socks5h") or not target.hostname:
        raise ValueError("Chained target must be a SOCKS5 proxy")
    if upstream.scheme != "http" or not upstream.hostname:
        raise ValueError("Upstream must be an HTTP CONNECT proxy")
    if upstream.username is not None or upstream.password is not None:
        raise ValueError("Authenticated upstream HTTP proxies are not supported")
    if any(c in target.hostname for c in "\r\n"):
        raise ValueError("Invalid proxy hostname")
    endpoint = (target.hostname, target.port or 1080)
    upstream_endpoint = (upstream.hostname, upstream.port or 80)
    key = (upstream_endpoint, endpoint)
    with _relay_lock:
        server = _relays.get(key)
        if server is None:
            server = _RelayServer(("127.0.0.1", 0), _RelayHandler)
            server.upstream = upstream_endpoint
            server.target = endpoint
            threading.Thread(
                target=server.serve_forever,
                name="amazon-proxy-chain",
                daemon=True,
            ).start()
            _relays[key] = server
    # Preserve the original encoded SOCKS credentials byte-for-byte.
    auth, separator, _ = target.netloc.rpartition("@")
    netloc = (auth + separator if separator else "") + (
        f"127.0.0.1:{server.server_address[1]}"
    )
    local_url = urlunsplit((target.scheme, netloc, "", "", ""))
    return {"http": local_url, "https": local_url}
