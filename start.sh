#!/usr/bin/env bash
# Start/stop the DFHack MCP server and automatically provision its Miniconda environment.
#
#   ./start.sh            ensure conda env + dependencies, then start in background (default)
#   ./start.sh setup      create/update the 'dfhack-mcp' Miniconda env and install dependencies
#   ./start.sh fg         ensure conda env, then run in this terminal (Ctrl+C stops it)
#   ./start.sh status     is it running, and is Dwarf Fortress reachable?
#   ./start.sh stop       stop it
#   ./start.sh restart    stop + start
#   ./start.sh logs       follow the log
#
# Environment overrides:
#   DFHACK_CONDA_ENV      conda environment name (default: dfhack-mcp)
#   DFHACK_PYTHON_VERSION python version when creating the conda env (default: 3.12)
#   DFHACK_MCP_HOST       bind address (default: 127.0.0.1)
#   DFHACK_MCP_PORT       bind port (default: 8000)
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
CONDA_ENV="${DFHACK_CONDA_ENV:-dfhack-mcp}"
CONDA_PYTHON_VERSION="${DFHACK_PYTHON_VERSION:-3.12}"
PYTHON_BIN=""

find_conda() {
  if command -v conda >/dev/null 2>&1; then
    command -v conda
    return 0
  fi
  local candidate
  for candidate in \
    "${CONDA_EXE:-}" \
    "$HOME/miniconda3/bin/conda" \
    "$HOME/anaconda3/bin/conda" \
    "$HOME/miniforge3/bin/conda" \
    "$HOME/mambaforge/bin/conda" \
    "/opt/miniconda3/bin/conda" \
    "/opt/conda/bin/conda"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

deps_installed() {
  local py="$1"
  [[ -x "$py" ]] || return 1
  "$py" -c "import fastapi, fastapi_mcp, mcp, uvicorn, async_lru, google.protobuf, requests, dfhack_client_python" >/dev/null 2>&1
}

ensure_env() {
  local force="${1:-}"
  local conda_bin=""
  if conda_bin="$(find_conda)"; then
    local conda_base
    conda_base="$(cd "$(dirname "$conda_bin")/.." && pwd)"
    local env_py="${conda_base}/envs/${CONDA_ENV}/bin/python"

    if [[ ! -x "$env_py" ]]; then
      echo "[setup] Creating Miniconda environment '${CONDA_ENV}' (python=${CONDA_PYTHON_VERSION})..."
      "$conda_bin" create -y -n "$CONDA_ENV" "python=${CONDA_PYTHON_VERSION}" pip
    fi

    if [[ "$force" == "--force" ]] || ! deps_installed "$env_py"; then
      echo "[setup] Installing dependencies into '${CONDA_ENV}' from requirements.txt..."
      (cd "$DIR" && "$env_py" -m pip install --upgrade pip && "$env_py" -m pip install -r "${DIR}/requirements.txt")
    fi

    if ! deps_installed "$env_py"; then
      echo "[setup] ERROR: dependency verification failed in ${env_py}" >&2
      exit 1
    fi
    PYTHON_BIN="$env_py"
    return 0
  fi

  # Fallback when conda is not installed on the system (e.g., pre-built .venv or uv)
  if [[ -x "${DIR}/.venv/bin/python" ]] && deps_installed "${DIR}/.venv/bin/python"; then
    if [[ "$force" == "--force" ]]; then
      (cd "$DIR" && "${DIR}/.venv/bin/python" -m pip install -r "${DIR}/requirements.txt")
    fi
    PYTHON_BIN="${DIR}/.venv/bin/python"
    return 0
  fi

  if command -v uv >/dev/null 2>&1; then
    echo "[setup] conda not found; syncing environment with uv..."
    (cd "$DIR" && uv sync)
    PYTHON_BIN="${DIR}/.venv/bin/python"
    return 0
  fi

  echo "ERROR: Miniconda (conda) not found in PATH or standard locations (\$HOME/miniconda3)." >&2
  echo "Install Miniconda from https://docs.anaconda.com/miniconda/ and re-run: ./start.sh setup" >&2
  exit 1
}

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
    echo "already running (pid $(cat "$PIDFILE" 2>/dev/null | tr '\n' ' ')) - ./start.sh stop first, or ./start.sh restart"
    return 0
  fi
  ensure_env
  rm -f "$PIDFILE"
  cd "$DIR"
  setsid nohup "$PYTHON_BIN" main.py </dev/null >>"$LOG" 2>&1 &
  local pid=$!
  echo "$pid" >"$PIDFILE"
  disown "$pid" 2>/dev/null || true

  # wait for the port to answer; the game is not required for this
  for _ in $(seq 1 40); do
    sleep 0.5
    if curl -s -m 3 -o /dev/null "${BASE}/docs" 2>/dev/null; then
      # record every live pid, one per line
      server_pids >"$PIDFILE"
      echo "started (pids: $(server_pids | tr '\n' ' '), env: ${PYTHON_BIN}) -> ${BASE}/mcp   log: ${LOG}"
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

setup() {
  ensure_env --force
  echo "[setup] Ready: ${PYTHON_BIN} ($("$PYTHON_BIN" --version 2>&1))"
}

case "${1:-start}" in
  start)   start ;;
  setup)   setup ;;
  stop)    stop ;;
  restart) stop; start ;;
  status)  status ;;
  logs)    tail -f "$LOG" ;;
  fg)      ensure_env; cd "$DIR" && exec "$PYTHON_BIN" main.py ;;
  *)       echo "usage: $0 [start|setup|stop|restart|status|logs|fg]"; exit 1 ;;
esac
