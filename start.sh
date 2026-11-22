#!/usr/bin/env bash
# Start/stop the DFHack MCP server by hand.
#
#   ./start.sh            start in background (default)
#   ./start.sh fg         run in this terminal (Ctrl+C stops it)
#   ./start.sh status     is it running, and is Dwarf Fortress reachable?
#   ./start.sh stop       stop it
#   ./start.sh restart    stop + start
#   ./start.sh logs       follow the log
#
# The server connects lazily, so it is fine to start it before Dwarf Fortress;
# tool calls connect on demand and a game restart reconnects by itself.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${DFHACK_MCP_HOST:-127.0.0.1}"
PORT="${DFHACK_MCP_PORT:-8000}"
BASE="http://127.0.0.1:${PORT}"
PIDFILE="${DIR}/.dfhack-mcp.pid"
LOG="${DIR}/dfhack-mcp.log"

port_pids() {   # pids currently holding the server port
  ss -tlnp 2>/dev/null | grep -w "$PORT" | grep -oE 'pid=[0-9]+' | sed 's/pid=//' | sort -u || true
}

server_pids() {  # pidfile + port owner + our command line; a stale pidfile is not enough
  {
    [[ -f "$PIDFILE" ]] && cat "$PIDFILE" 2>/dev/null || true
    port_pids
    pgrep -f "uv run --no-sync python main.py" 2>/dev/null || true
    pgrep -f "python.*main\.py" 2>/dev/null || true
  } | grep -E '^[0-9]+$' | sort -u || true
}

running() {   # any live server process?
  local pid
  for pid in $(server_pids); do
    kill -0 "$pid" 2>/dev/null && return 0
  done
  return 1
}

status() {
  local pids; pids="$(server_pids | tr '\n' ' ')"
  if running; then
    echo "server: running (pids:${pids:+ $pids} ${HOST}:${PORT})"
  else
    echo "server: stopped"
    rm -f "$PIDFILE"
  fi
  if command -v curl >/dev/null && curl -s -m 10 "${BASE}/get-status" 2>/dev/null | head -1; then
    :
  else
    echo "get-status: no answer from ${BASE}"
  fi
}

start() {
  if running; then
    echo "already running (pid $(cat "$PIDFILE")) - ./start.sh stop first, or ./start.sh restart"
    return 0
  fi
  rm -f "$PIDFILE"
  cd "$DIR"
  if [[ -x "${DIR}/.venv/bin/python" ]]; then
    setsid nohup "${DIR}/.venv/bin/python" main.py </dev/null >>"$LOG" 2>&1 &
  else
    command -v uv >/dev/null || { echo "uv not found in PATH (get https://docs.astral.sh/uv/)"; exit 1; }
    setsid nohup uv run --no-sync python main.py </dev/null >>"$LOG" 2>&1 &
  fi
  local pid=$!
  echo "$pid" >"$PIDFILE"
  disown "$pid" 2>/dev/null || true

  # wait for the port to answer; the game is not required for this
  for _ in $(seq 1 40); do
    sleep 0.5
    if curl -s -m 3 -o /dev/null "${BASE}/docs" 2>/dev/null; then
      # record every live pid (uv wrapper + python), one per line
      server_pids >"$PIDFILE"
      echo "started (pids: $(server_pids | tr '\n' ' ')) -> ${BASE}/mcp   log: ${LOG}"
      echo "DFHack RPC endpoint: $(curl -s -m 10 "${BASE}/get-status")"
      return 0
    fi
  done
  echo "did not come up within 20s - check: tail -40 ${LOG}"
  return 1
}

stop() {
  if ! running; then echo "not running"; rm -f "$PIDFILE"; return 0; fi
  local pid
  for pid in $(server_pids); do kill "$pid" 2>/dev/null || true; done
  for _ in $(seq 1 20); do running || break; sleep 0.3; done
  if running; then
    echo "still up after SIGTERM, sending SIGKILL"
    for pid in $(server_pids); do kill -9 "$pid" 2>/dev/null || true; done
    sleep 1
  fi
  rm -f "$PIDFILE"
  if running; then echo "WARNING: still running - check: ss -tlnp | grep -w ${PORT}"; exit 1; fi
  echo "stopped"
}

case "${1:-start}" in
  start)   start ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)  status ;;
  logs)    tail -f "$LOG" ;;
  fg)      cd "$DIR" && exec uv run --no-sync python main.py ;;
  *)       echo "usage: $0 [start|stop|restart|status|logs|fg]"; exit 1 ;;
esac
