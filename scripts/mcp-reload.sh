#!/usr/bin/env bash
# Reload the DFHack MCP server and make DSH re-read its tools.
#
#   scripts/mcp-reload.sh probe     # what tools does the MCP endpoint expose right now?
#   scripts/mcp-reload.sh refresh   # tell DSH to drop the fiber and re-list the tools
#   scripts/mcp-reload.sh restart   # restart the server process, then probe
#   scripts/mcp-reload.sh all       # restart + refresh + probe  (the full routine)
#
# Why each part exists (all verified on a live 53.17-r1 save + a live harness):
#  * `refresh` is the ONLY way DSH re-reads a tool list. The mcp-client fiber registers
#    `mcp__dfhack__<tool>` at connect time; restarting the server process does not
#    re-register anything. Toggling disposes the fiber and starts it again: it re-runs
#    `initialize` -> `tools/list` live, no harness restart (dsh-mcp-toggle).
#  * An ALREADY-OPEN chat session keeps the tool list it started with. After a refresh,
#    open a NEW session to get the new tools.
#  * Port rule: this server belongs on 8000 (the profile row's url). 8001 is the shared
#    mcp-proxy for time/fetch/ddg-search/freecad/blender/browser-mcp. Binding this
#    server to 8001 silently breaks every one of those rows AND leaves the dfhack row
#    with nothing behind it. `check-ports` below detects exactly that.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${DFHACK_MCP_PORT:-8000}"
BASE="http://127.0.0.1:${PORT}"
DSH="${DSH_WEB_URL:-http://127.0.0.1:3080}"
ENTRY="${DSH_MCP_ENTRY:-include:mcp-dfhack}"
DSH_URL="${DSH_WEB_URL:-http://127.0.0.1:3080}"
PY="${PYTHON:-python3}"
export DSH_URL   # row_phase() reads it from the environment

say() { printf '%s\n' "$*"; }

status() {   # all three layers at once, because each one can lie about the others
  say "status: server ${PORT} + DSH row ${ENTRY}"
  local health row
  health=$(server_health)
  say "  1. SERVER: $health"
  row=$(row_phase 2>/dev/null)
  say "  2. DSH ROW: ${row:-no mcp-toggle api}"
  case "$health" in
    "not answering"*)
      if printf '%s' "$row" | grep -q "phase=active"; then
        say "     ^ the row reports active while the server is silent: the client is"
        say "       inside reconnect.maxAttempts, so phase=active does NOT prove the"
        say "       server answers. Start the server, then run: $0 refresh"
      fi ;;
  esac
  check_ports
}

server_health() {   # one line: connected / generation / dfhack version, or "not answering"
  # the reply travels in the environment: `python - <<EOF` takes the SCRIPT from stdin,
  # so piped data and a heredoc script cannot share stdin
  local raw; raw=$(curl -s -m 20 "${BASE}/get-status" 2>/dev/null)
  RAW="$raw" BASE_URL="$BASE" "$PY" - <<'PYEOF'
import json, os
raw = os.environ.get("RAW", "").strip()
if not raw:
    print(f"not answering on {os.environ['BASE_URL']} (down, or bound to another port)")
    raise SystemExit
try:
    d = json.loads(raw)
except Exception:
    print(f"unparseable reply: {raw[:120]}")
    raise SystemExit
print(f"connected={d.get('connected')} generation={d.get('generation')} "
      f"boundMethods={d.get('boundMethods')} dfhack={d.get('dfhackVersion')}")
if d.get("error"):
    print(f"   error: {d['error']}")
PYEOF
}

row_phase() {   # print "enabled=<bool> phase=<phase>" for $ENTRY from /mcp-toggle/api
  ENTRY="$ENTRY" "$PY" - <<'PYEOF'
import json, os, sys, urllib.request
d = json.load(urllib.request.urlopen(f"{os.environ['DSH_URL']}/mcp-toggle/api", timeout=20))
row = [e for e in d["entries"] if e["entryId"] == os.environ["ENTRY"]]
print("row not found" if not row else
      f"enabled={row[0]['enabled']} phase={row[0]['fiberPhase']}")
PYEOF
}


probe() {   # MCP initialize -> tools/list -> a real tool call
  say "probing ${BASE}/mcp"
  curl -s -m 15 -o /dev/null "${BASE}/docs" || { say "  server not answering on ${PORT} - start it: ./start.sh"; return 1; }
  curl -s -m 30 "${BASE}/get-status" | head -c 220; say ""
  curl -s -m 90 -X POST "${BASE}/mcp" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"mcp-reload","version":"1"}}}' \
  | "$PY" -c '
import sys,json
raw=sys.stdin.read()
for line in raw.splitlines():
    if line.startswith("data: "): raw=line[6:]; break
d=json.loads(raw)
print("  serverInfo:", d["result"]["serverInfo"]["name"])
' || { say "  MCP initialize failed"; return 1; }
  # initialize twice: the first POST /mcp 400 (no session id) is NORMAL for streamable HTTP
  sid=$(curl -s -m 30 -D - -o /dev/null -X POST "${BASE}/mcp" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"mcp-reload","version":"1"}}}' \
    | tr -d '\r' | sed -n 's/^mcp-session-id: //p' | head -1)
  curl -s -m 90 -X POST "${BASE}/mcp" -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' ${sid:+-H "mcp-session-id: $sid"} \
    -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
  tools=$(curl -s -m 90 -X POST "${BASE}/mcp" -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' ${sid:+-H "mcp-session-id: $sid"} \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}')
  echo "$tools" | "$PY" -c '
import sys,json
raw=sys.stdin.read()
for line in raw.splitlines():
    if line.startswith("data: "): raw=line[6:]; break
d=json.loads(raw)
ts=sorted(t["name"] for t in d["result"]["tools"])
print(f"  {len(ts)} tools: {", ".join(ts)}")
need={"look","fort_progress","world_zone","world_map","world_objects","world_delta","transitions","building_status","configure_farmplot","game_paths"}
missing=need-set(ts)
print("  PREPROCESSED LAYER:", "complete" if not missing else f"MISSING {sorted(missing)} -> restart the server, code is not loaded")
'
}

check_ports() {
  say "port discipline check"
  local proxy
  proxy=$(curl -s -m 10 -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:8001/servers/time/mcp" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"p","version":"1"}}}' 2>/dev/null)
  if [ "$proxy" = "200" ]; then
    say "  8001 mcp-proxy: OK (time/fetch/ddg-search/freecad/blender/browser-mcp reachable)"
  else
    local who; who=$(curl -s -m 10 -X POST "http://127.0.0.1:8001/mcp" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"p","version":"1"}}}' 2>/dev/null | grep -o '"name":"[^"]*"' | head -1)
    say "  8001 mcp-proxy NOT answering (http $proxy)${who:+ - $who is holding the port}"
    say "  FIX: move this server back to 8000 (DFHACK_MCP_PORT=8000 ./start.sh restart) and restart the proxy (swap/start.sh). Binding here breaks every proxied MCP row."
  fi
}

refresh() {   # the DSH-side reread: dispose the fiber, start it, poll the phase
  say "DSH refresh on ${DSH} (entry ${ENTRY})"
  local cur; cur=$(curl -s -m 20 "${DSH}/mcp-toggle/api" 2>/dev/null); export DSH_URL
  if [ -z "$cur" ]; then
    say "  /mcp-toggle/api did not answer: dsh-mcp-toggle is not installed/enabled."
    say "  Fallback (documented in SETUP-LINUX.md §11): Plugin manager toggle, or edit"
    say "  cordis.patch.yml - patchReload:'live' re-creates the plugin instance."
    return 1
  fi
  say "  before: $(row_phase)"
  curl -s -m 30 -X POST "${DSH}/mcp-toggle/api" -H 'Content-Type: application/json' \
    -d "{\"entryId\":\"${ENTRY}\",\"disabled\":true}" | head -c 200; say ""
  sleep 2
  curl -s -m 30 -X POST "${DSH}/mcp-toggle/api" -H 'Content-Type: application/json' \
    -d "{\"entryId\":\"${ENTRY}\",\"disabled\":false}" | head -c 200; say ""
  for i in 1 2 3 4 5 6 7 8; do
    sleep 3
    phase=$(row_phase)
    say "  poll $i: $phase"
    case "$phase" in *phase=active*) say "  tools re-registered in DSH."; return 0;; esac
  done
  say "  phase never reached active - check the server is up on ${PORT} and the row url"
  return 1
}

case "${1:-status}" in   # default is read-only: never restart by accident
  probe)       probe; check_ports ;;
  status)      status ;;
  refresh)     refresh ;;
  restart)     cd "$DIR" && ./start.sh restart && probe ;;
  check-ports) check_ports ;;
  all)         cd "$DIR" && ./start.sh restart; probe; refresh; check_ports ;;
  *) say "usage: $0 [probe|refresh|restart|check-ports|status|all]"; exit 1 ;;
esac
