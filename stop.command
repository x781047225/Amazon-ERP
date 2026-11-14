#!/bin/zsh
set -e
cd "$(dirname "$0")"
./backend/.venv/bin/python scripts/control.py stop
