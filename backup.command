#!/bin/zsh
set -e
cd "$(dirname "$0")"
PYTHONDONTWRITEBYTECODE=1 ./backend/.venv/bin/python scripts/backup.py
