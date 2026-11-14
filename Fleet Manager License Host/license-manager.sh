#!/usr/bin/env bash
# Fleet Manager - License Manager for Linux (Ubuntu 24.04). Same as "License Manager.bat" on Windows.
#
#   ./license-manager.sh               interactive menu
#   ./license-manager.sh list          any License Manager command (see: ./license-manager.sh help)
#
# First time only:  chmod +x license-manager.sh license-server.sh
# Use a specific Python:  PYTHON=/usr/bin/python3.12 ./license-manager.sh
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  if command -v python3 >/dev/null 2>&1; then PY=python3
  elif command -v python >/dev/null 2>&1; then PY=python
  else
    echo "Python 3 is needed. On Ubuntu: sudo apt install python3" >&2
    exit 1
  fi
elif ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python not found at: $PY  (check the PYTHON setting, or leave it out to use python3)" >&2
  exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
  echo "Python 3.8 or newer is needed (found: $("$PY" --version 2>&1))." >&2
  exit 1
fi

exec "$PY" license_tool.py "$@"
