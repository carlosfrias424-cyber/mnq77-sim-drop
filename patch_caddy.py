#!/usr/bin/env python3
"""Insert /tv/signal -> 127.0.0.1:8788 in the duckdns Caddy site. Run with sudo."""
from pathlib import Path
import shutil
import sys

P = Path(sys.argv[1] if len(sys.argv) > 1 else "/etc/caddy/Caddyfile")
s = P.read_text()
print("===== BEFORE =====")
print(s)
if "127.0.0.1:8788" in s and "/tv/signal" in s:
    print("already patched")
    raise SystemExit(0)
key = "mnqhyrbid.duckdns.org"
i = s.find(key)
if i < 0:
    # fallback: first site block
    i = s.find("{")
    if i < 0:
        raise SystemExit("no site block")
    i = s.rfind("\n", 0, i)
else:
    i = s.find("{", i)
if i < 0:
    raise SystemExit("no opening brace")
insert = """
	@notbullbot {
		not path /tv/signal /tv/signal/*
	}
	reverse_proxy /tv/signal* 127.0.0.1:8788
"""
# wrap a bare reverse_proxy (no matcher) so it does not steal /tv/signal
# only inside this site: from this { to matching } is too heavy; replace first
# unmatched reverse_proxy after insert point that isn't already matched.
s = s[: i + 1] + insert + s[i + 1 :]
# if there is still `reverse_proxy 127.` without a matcher token on the same line,
# prefix with @notbullbot
out = []
seen_bare = False
for ln in s.splitlines(True):
    stripped = ln.strip()
    if (
        stripped.startswith("reverse_proxy ")
        and "127.0.0.1:8788" not in stripped
        and "@notbullbot" not in stripped
        and not stripped.startswith("reverse_proxy /")
        and not stripped.startswith("reverse_proxy @")
        and not seen_bare
    ):
        indent = ln[: len(ln) - len(ln.lstrip())]
        rest = stripped[len("reverse_proxy ") :]
        ln = f"{indent}reverse_proxy @notbullbot {rest}\n"
        seen_bare = True
    out.append(ln)
s2 = "".join(out)
bak = P.with_suffix(P.suffix + ".bak.bullbot")
shutil.copy2(P, bak)
P.write_text(s2)
print("===== AFTER =====")
print(s2)
print("backup", bak)
