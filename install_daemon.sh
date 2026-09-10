#!/bin/bash
# Static user daemons for 7/7 + BE20. Does not flatten any open trade.
set -euo pipefail
ROOT=/home/administrator/.openclaw/workspace/mnq_hybrid
B=https://raw.githubusercontent.com/carlosfrias424-cyber/mnq77-sim-drop/main
U="$HOME/.config/systemd/user"
mkdir -p "$U" "$ROOT/apps/watcher7" "$ROOT/apps/tradovate"

curl -fsSL "$B/live_77.py" -o "$ROOT/apps/watcher7/live_77.py"
curl -fsSL "$B/manage_be20.py" -o "$ROOT/apps/tradovate/manage_be20.py"
curl -fsSL "$B/place_struct40.py" -o "$ROOT/apps/tradovate/place_struct40.py"
curl -fsSL "$B/mnq-seven.service" -o "$U/mnq-seven.service"
curl -fsSL "$B/mnq-be20.service" -o "$U/mnq-be20.service"

"$ROOT/.venv/bin/python" -m py_compile \
  "$ROOT/apps/watcher7/live_77.py" \
  "$ROOT/apps/tradovate/manage_be20.py" \
  "$ROOT/apps/tradovate/place_struct40.py"
echo COMPILE_OK

# stop leftover nohup copies; systemd takes over
pkill -f 'watcher7/live_77.py' 2>/dev/null || true
pkill -f 'manage_be20.py' 2>/dev/null || true
sleep 1

loginctl enable-linger "$USER" 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable --now mnq-seven.service mnq-be20.service
sleep 1
systemctl --user is-active mnq-seven.service mnq-be20.service
grep -E 'cut_1400|seven_start|SESSION|after_close|open' /tmp/live_77.out | tail -n 4
