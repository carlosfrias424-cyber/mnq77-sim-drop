#!/bin/bash
# Bullbot SIM fire. 7/7 paper-only. Does not touch /tv/poi.
set -euo pipefail
ROOT=/home/administrator/.openclaw/workspace/mnq_hybrid
B=https://raw.githubusercontent.com/carlosfrias424-cyber/mnq77-sim-drop/main
mkdir -p "$ROOT/apps/bullbot" "$ROOT/apps/tradovate" "$HOME/.config/systemd/user"

curl -fsSL "$B/bullbot_hook.py" -o "$ROOT/apps/bullbot/bullbot_hook.py"
curl -fsSL "$B/flatten_mkt.py" -o "$ROOT/apps/tradovate/flatten_mkt.py"
curl -fsSL "$B/mnq-bullbot.service" -o "$HOME/.config/systemd/user/mnq-bullbot.service"

"$ROOT/.venv/bin/python" -m py_compile \
  "$ROOT/apps/bullbot/bullbot_hook.py" \
  "$ROOT/apps/tradovate/flatten_mkt.py" && echo COMPILE_OK

# 7/7: log / score only — no new SIM fires (same account)
if grep -q '^FIRE = True' "$ROOT/apps/watcher7/live_77.py"; then
  sed -i 's/^FIRE = True/FIRE = False  # paper: bullbot owns SIM/' "$ROOT/apps/watcher7/live_77.py"
  echo "seven FIRE=False"
fi

systemctl --user daemon-reload
systemctl --user enable --now mnq-bullbot.service
systemctl --user restart mnq-seven.service || true
sleep 1
systemctl --user is-active mnq-bullbot.service mnq-seven.service mnq-be20.service

# Caddy route if we can see a Caddyfile
CF=""
for f in /etc/caddy/Caddyfile "$HOME/Caddyfile" /opt/caddy/Caddyfile "$HOME/.config/caddy/Caddyfile"; do
  if [ -f "$f" ]; then CF="$f"; break; fi
done
if [ -n "$CF" ] && ! grep -q '/tv/signal' "$CF"; then
  echo "---- add this to $CF (inside the duckdns site block), then: sudo systemctl reload caddy"
  echo "handle /tv/signal* {"
  echo "    reverse_proxy 127.0.0.1:8788"
  echo "}"
else
  echo "Caddyfile: ${CF:-NOT FOUND — add reverse_proxy /tv/signal -> 127.0.0.1:8788}"
fi

echo "local health:"
curl -sS http://127.0.0.1:8788/health || true
echo
echo "public (needs Caddy):"
curl -sS -o /tmp/bb.out -w "http %{http_code}\n" https://mnqhyrbid.duckdns.org/tv/signal || true
head -c 200 /tmp/bb.out; echo
echo "tail hook:"
journalctl --user -u mnq-bullbot.service -n 5 --no-pager || true
