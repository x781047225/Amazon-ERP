"""Independent SQS inbox receiver. Statistics projection is a separate stage."""
import hashlib
import json
import signal
import threading
import time
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from .db import Session, engine
from .amazon import redis
from .store_credentials import decode
from .ads_queue_models import AdsMessageQueue, AdsMessageInbox, utcnow
from .ads_queue import make_client, friendly_error, read_attributes

RENEW = "if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('EXPIRE',KEYS[1],180) end return 0"
RELEASE = "if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) end return 0"

def parse_message(body):
    payload = {}
    reason = ''
    try:
        payload = json.loads(body)
        if isinstance(payload, dict) and payload.get('Type') == 'Notification' and isinstance(payload.get('Message'), str):
            payload = json.loads(payload['Message'])
        if not isinstance(payload, dict):
            raise ValueError()
    except (ValueError, TypeError):
        payload = {}
        reason = '消息不是可识别的 JSON 对象'
    dataset = str(payload.get('dataset_id', ''))[:80]
    advertiser = str(payload.get('advertiser_id', ''))[:80]
    market = str(payload.get('marketplace_id', ''))[:80]
    if not reason and (dataset not in ('sp-traffic', 'sp-conversion', 'budget-usage') or not advertiser or not market):
        reason = '暂不支持的数据类型或缺少账户信息；原始消息已保留'
    token = payload.get('idempotency_id')
    if not reason and dataset in ('sp-traffic', 'sp-conversion') and (not isinstance(token, str) or not token):
        reason = '流量或转化消息缺少去重标识'
    if not reason and dataset == 'budget-usage' and not all(payload.get(k) is not None for k in ('usage_updated_timestamp','budget_scope_id','budget_scope_type')):
        reason = '预算消息缺少更新时间或预算对象'
    if reason:
        identity = ['quarantine', body]
    elif dataset == 'budget-usage':
        # Snapshot messages have no idempotency ID. Preserve differing snapshots.
        identity = [dataset, advertiser, market, payload]
    else:
        identity = [dataset, advertiser, market, token]
    key = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',',':'), ensure_ascii=False).encode()).hexdigest()
    return dict(key=key, dataset=dataset, advertiser_id=advertiser, marketplace_id=market,
                body=body, state='quarantined' if reason else 'pending', reason=reason)

def persist_messages(queue_id, revision, messages):
    """All inbox writes commit before any DeleteMessage call. Returns false after pause/edit."""
    with Session.begin() as db:
        row = db.scalar(select(AdsMessageQueue).where(AdsMessageQueue.id == queue_id).with_for_update())
        if not row or not row.enabled or row.revision != revision:
            return False
        datasets = dict(row.datasets)
        for msg in messages:
            event = parse_message(msg['Body'])
            stmt = insert(AdsMessageInbox).values(**event, queue_id=queue_id, received_at=utcnow())
            created = db.scalar(stmt.on_conflict_do_nothing(index_elements=['key']).returning(AdsMessageInbox.key))
            if created:
                row.received_count += 1
                if event['state'] == 'quarantined':
                    row.quarantined_count += 1
                dataset = event['dataset'] or 'unknown'
                datasets[dataset] = int(datasets.get(dataset, 0)) + 1
            else:
                row.duplicate_count += 1
        row.datasets = datasets
        if messages:
            row.received_at = utcnow()
        row.heartbeat_at = utcnow()
    return True

def receive_once(client, config, queue_id, revision):
    result = client.receive_message(QueueUrl=config['queue_url'], MaxNumberOfMessages=10,
                                    WaitTimeSeconds=20, VisibilityTimeout=120)
    messages = result.get('Messages', [])
    if not persist_messages(queue_id, revision, messages):
        return False
    # ACK only durable data. A failed/uncertain ACK causes harmless redelivery.
    if messages:
        result = client.delete_message_batch(QueueUrl=config['queue_url'], Entries=[
            {'Id':str(i), 'ReceiptHandle':msg['ReceiptHandle']} for i, msg in enumerate(messages)])
        if result.get('Failed'):
            raise RuntimeError('Message acknowledgement incomplete')
    return True

def update_status(queue_id, revision, **values):
    with Session.begin() as db:
        row = db.scalar(select(AdsMessageQueue).where(AdsMessageQueue.id == queue_id).with_for_update())
        if not row or not row.enabled or row.revision != revision:
            return False
        for name, value in values.items():
            setattr(row, name, value)
    return True

def run_queue(queue_id, revision, stop):
    key = f'erp:ads-queue:{engine.url.database}:{queue_id}'
    token = str(uuid4())
    client = None
    acquired = False
    try:
        acquired = bool(redis.set(key, token, nx=True, ex=180))
        if not acquired:
            return
        with Session() as db:
            row = db.get(AdsMessageQueue, queue_id)
            if not row or not row.enabled or row.revision != revision:
                return
            config = decode(row)
        last_attributes = 0
        failures = 0
        while not stop.is_set():
            try:
                if not redis.eval(RENEW, 1, key, token):
                    return
                if not update_status(queue_id, revision, heartbeat_at=utcnow()):
                    return
                if client is None:
                    client = make_client(config)
                if time.monotonic() - last_attributes > 60:
                    attrs = read_attributes(client, config['queue_url'])
                    if not update_status(queue_id, revision, attributes=attrs):
                        return
                    last_attributes = time.monotonic()
                if not receive_once(client, config, queue_id, revision):
                    return
                update_status(queue_id, revision, consumer_error=None, heartbeat_at=utcnow())
                failures = 0
            except Exception as exc:
                failures += 1
                try:
                    update_status(queue_id, revision, consumer_error=friendly_error(exc))
                except Exception:
                    pass  # Storage may be unavailable; uncommitted messages remain in SQS.
                if client:
                    client.close()
                    client = None
                # Keep backoff below lease expiry; no tight loop on invalid credentials.
                stop.wait(min(60, 5 * 2 ** min(failures - 1, 4)))
    finally:
        if client:
            client.close()
        if acquired:
            try:
                redis.eval(RELEASE, 1, key, token)
            except Exception:
                pass

def main():
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_:stopping.set())
    signal.signal(signal.SIGINT, lambda *_:stopping.set())
    workers = {}
    while not stopping.is_set():
        try:
            with Session() as db:
                configs = list(db.execute(select(AdsMessageQueue.id, AdsMessageQueue.revision).where(AdsMessageQueue.enabled)))
            for queue_id, revision in configs:
                current = workers.get(queue_id)
                if current and current[0].is_alive():
                    if current[2] != revision:
                        current[1].set()
                    continue
                stop = threading.Event()
                thread = threading.Thread(target=run_queue, args=(queue_id, revision, stop), daemon=True)
                workers[queue_id] = (thread, stop, revision)
                thread.start()
            enabled = {item[0] for item in configs}
            for queue_id, (thread, stop, _) in list(workers.items()):
                if queue_id not in enabled:
                    stop.set()
                    if not thread.is_alive():
                        workers.pop(queue_id)
        except Exception:
            pass  # Local service recovery is retried, without logging credentials or bodies.
        stopping.wait(5)
    for thread, stop, _ in workers.values():
        stop.set()
    deadline = time.monotonic() + 25
    for thread, _, _ in workers.values():
        thread.join(max(0, deadline - time.monotonic()))

if __name__ == '__main__':
    main()
