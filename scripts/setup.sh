#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_CACHE_DIR="$PWD/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PWD/.runtime/python"
export npm_config_cache="$PWD/.cache/npm"
mkdir -p .cache .runtime logs data backups
command -v node >/dev/null || { echo '请安装 Node.js 22.12+ / 24 LTS'; exit 1; }
command -v uv >/dev/null || { echo '请安装 uv，然后重新运行'; exit 1; }
npm ci
# New npm versions require explicit approval of PostgreSQL initialization scripts.
if npm install-scripts --help >/dev/null 2>&1; then
  node -e "console.log('@embedded-postgres/'+process.platform+'-'+process.arch)" | while read -r pkg; do npm install-scripts approve "$pkg"; npm rebuild "$pkg"; done
fi
npm ci --prefix frontend
uv sync --project backend --python 3.12 --frozen
if [ ! -x .runtime/redis-7.4.2/src/redis-server ]; then
  curl -fLsS https://download.redis.io/releases/redis-7.4.2.tar.gz -o .runtime/redis-7.4.2.tar.gz
  tar -xzf .runtime/redis-7.4.2.tar.gz -C .runtime
  make -C .runtime/redis-7.4.2 -j4 MALLOC=libc
fi
npm run build --prefix frontend
./backend/.venv/bin/python scripts/init_config.py
echo '安装完成。运行 start.command 启动。'
