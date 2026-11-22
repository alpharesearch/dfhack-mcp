#!/usr/bin/env bash
# Reset routine for the DF observer after the LOADED GAME changes (save swap, game
# restart, embark). The rule it implements is the player's (2026-11-21): "when a
# different game is detected, even by just reloading a different save, throw
# everything out and refetch everything once again" - because the only truth is the DF game.
#
#   scripts/observer-reset.sh            # all: server caches + MCP fiber + probe
#   scripts/observer-reset.sh server     # GET /world-cache-reset (no server restart needed)
#   scripts/observer-reset.sh probe      # what the game says NOW (save, date, box tallies)
#   scripts/observer-reset.sh status     # server + DSH row + the loaded save's identity
#   scripts/observer-reset.sh command    # print the observer-side command to run
#
# THREE LAYERS, and only the first two are reachable from a shell:
#  1. SERVER derived views (block cache, stack memo, fort band, delta snapshot).
#     A save reload does NOT change the RPC `generation` (measured: it stayed 1 while
#     the fort's date moved from Y104-2-25 back to Y100-0-15), so the server cannot key
#     invalidation on it. It now checks the game's own signature (save name, world, mode,
#     map extent, in-game clock) on every read and clears everything itself - this script
#     is the manual version of that, for use right after loading a save.
#  2. DSH's MCP fiber (the JSON-RPC session id). Dropping the server process invalidates
#     it; `mcp-reload.sh refresh` disposes and restarts it (see scripts/mcp-reload.sh).
#  3. THE OBSERVER'S OWN STATE - the to-do ledger, watched boxes, dig baselines, alerts.
#     That lives in the plugin and on disk (~/.dsh/storages/df_observer), so NOTHING a
#     shell can do clears it. The plugin does it itself when it sees the swap; by hand:
#         /df-reset                       (chat command, any session)
#         df_todo({action:"reset"})       (the agent's tool)
#     ...both archive the dropped to-dos and force a full refetch.

set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${DFHACK_MCP_PORT:-8000}"
BASE="http://127.0.0.1:${PORT}"
PY="${PYTHON:-python3}"

say() { printf '%s\n' "$*"; }

get_json() {   # one GET, one parsed answer
  local path="$1"; shift
  curl -s -m "${CURL_TIMEOUT:-90}" "${BASE}${path}" "$@"
}

server_health() {
  local raw; raw=$(get_json "/get-status")
  RAW="$raw" PY_BIN="$PY" "$PY" - <<'PYEOF'
import json, os
raw = os.environ.get("RAW", "").strip()
if not raw:
    print("  server: NOT ANSWERING on the configured port")
    raise SystemExit
try:
    d = json.loads(raw)
except Exception:
    print(f"  server: unparseable reply: {raw[:120]}")
    raise SystemExit
sig = d.get("worldSignature") or {}
print(f"  server: connected={d.get('connected')} generation={d.get('generation')} "
      f"reloads(seen by server)={d.get('reloads')}")
if sig:
    print(f"  loaded game: save={sig.get('save')} mode={sig.get('mode')} map={sig.get('tiles')} "
          f"Y{sig.get('year')} tick {sig.get('tick')}")
if d.get("reloaded"):
    print(f"  PENDING SAVE SWAP: {d['reloaded'].get('why')}")
PYEOF
}

reset_server() {
  say "server: dropping every derived view of the world (${BASE}/world-cache-reset)"
  local raw; raw=$(get_json "/world-cache-reset")
  RAW="$raw" "$PY" - <<'PYEOF'
import json, os
raw = os.environ.get("RAW", "").strip()
if not raw:
    print("  no answer (is the server running? ./start.sh)")
    raise SystemExit
d = json.loads(raw)
print(f"  scope: {d.get('scope')}")
sig = d.get("worldSignature") or {}
print(f"  loaded game now: save={sig.get('save')} Y{sig.get('year')} tick {sig.get('tick')}")
print(f"  note: {d.get('note')}")
PYEOF
}

probe_boxes() {
  say "probe: what the game says about the monitored boxes"
  local q='{"boxes":[],"facts":true,"rungs":false,"reports":0,"pause":true,"survey":true}'
  local raw; raw=$(curl -s -m "${CURL_TIMEOUT:-90}" -G --data-urlencode "q=${q}" "${BASE}/observe")
  RAW="$raw" "$PY" - <<'PYEOF'
import json, os
raw = os.environ.get("RAW", "").strip()
if not raw:
    print("  no answer from /observe")
    raise SystemExit
d = json.loads(raw)
sig = d.get("worldSignature") or {}
print(f"  game: save={sig.get('save')} Y{sig.get('year')} tick {sig.get('tick')} | paused={d.get('paused')} | gen={d.get('generation')}")
f = d.get("facts") or {}
print(f"  facts: pop {f.get('pop')} dwarves {f.get('dwarves')} miners {f.get('miners')} picks {f.get('picks')} "
      f"pending_dig {f.get('pending_dig')} water {f.get('water_tiles')}")
if d.get("reloaded"):
    print(f"  SAVE SWAP REPORTED: {d['reloaded'].get('why')} (cleared: {d['reloaded'].get('cleared')})")
PYEOF
}

show_command() {
  say "observer side (the plugin's own reset - a shell cannot reach a session's plugin state):"
  say "  in any chat session:   /df-reset"
  say "  for the agent:         df_todo({\"action\":\"reset\"})"
  say "  panel:                 the \"↺ reset\" button in the fort observer panel"
  say "Both archive the dropped to-dos (todos/arch-*.json) and force a full refetch on the next poll."
}

refresh_fiber() {
  if [[ -x "$DIR/scripts/mcp-reload.sh" ]]; then
    "$DIR/scripts/mcp-reload.sh" refresh
  else
    say "scripts/mcp-reload.sh missing: no fiber refresh"
  fi
}

case "${1:-all}" in
  status)
    say "observer reset: status (${BASE})"
    server_health
    probe_boxes
    show_command
    ;;
  server)
    reset_server
    ;;
  probe)
    probe_boxes
    ;;
  command)
    show_command
    ;;
  refresh)
    refresh_fiber
    ;;
  all)
    say "observer reset: all layers"
    reset_server
    refresh_fiber
    probe_boxes
    show_command
    say "done. The plugin detects a swap by itself from here on; run /df-reset if it is already mid-fort and you know the save changed."
    ;;
  *)
    say "usage: $0 [all|status|server|probe|refresh|command]"
    exit 2
    ;;
esac
