#!/usr/bin/env bash
# Fleet Manager - licence server for Linux (Ubuntu 24.04). Same as "License Server.bat" on Windows.
#
#   ./license-server.sh                          listen on 0.0.0.0:8750 (port from server_config.json)
#   ./license-server.sh --host 127.0.0.1         behind Caddy (as in the Kamatera guide)
#
# On a server, run it as a systemd service instead (see "Kamatera Hosting Guide.pdf").
# First time only:  chmod +x license-manager.sh license-server.sh
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

exec "$PY" -u license_server.py "$@"
