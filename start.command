#!/bin/zsh
set -e
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1
./backend/.venv/bin/python scripts/control.py start
open http://127.0.0.1:8787
