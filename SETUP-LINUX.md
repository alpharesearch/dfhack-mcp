# Running dfhack-mcp on Linux (Steam, native DF)

Verified working: **Dwarf Fortress v0.53.17 linux64 STEAM + DFHack 53.17-r1**, native Linux
build (no Proton/Wine), MCP server answering live game data.

## 1. Which build does Steam launch?

The Linux client. Proof:

* `dwarfort` is an `ELF 64-bit LSB executable ... for GNU/Linux 3.2.0`
* In-game report: `GetDFVersion()` → **`v0.53.17 linux64 STEAM`**
* Launch chain is `SteamLinuxRuntime_soldier` + `SteamLinuxRuntime` (scout-on-soldier) —
  the native pressure-vessel runtime, not Proton; no `wineserver`/`wine` in the process tree.

So DFHack runs as native Linux `libdfhack.so`, its RPC server listens on a real Linux TCP
socket, and plain Linux Python can talk to it. No Wine/Proton network tricks needed.

## 2. DFHack RPC endpoint

`dfhack-config/remote-server.json`:

```json
{ "allow_remote": false, "port": 5000 }
```

Port **5000** is DFHack's default; `allow_remote: false` binds to `127.0.0.1` only, which is
exactly what `dfhack_client_python.dfhack_remote.connect()` hardcodes
(`asyncio.open_connection('127.0.0.1', 5000)`).

Check it is up while the game runs:

```bash
ss -tlnp | grep dwarfort       # LISTEN 127.0.0.1:5000 users:(("dwarfort",...))
```

Protocol is protobuf over TCP (not HTTP, not gRPC): handshake `DFHack?\n` + int32 `1`
→ reply `DFHack!\n` + int32 `1`. Raw probe:

```python
import socket, struct
s = socket.create_connection(("127.0.0.1", 5000), 5)
s.sendall(b"DFHack?\n" + struct.pack("<i", 1))
print(s.recv(12))              # b'DFHack!\n\x01\x00\x00\x00'
```

## 3. `dfhack-client-python` is vendored (and NOT on PyPI)

The dependency lives **in this repo** at `vendor/dfhack-client-python`, and `pyproject.toml`
declares it as `{ path = "vendor/dfhack-client-python", editable = true }`. `uv.lock`
matches. So a clone of this repo is enough to run the MCP - nothing outside it is needed.

Why vendored: it is not published on PyPI, it is small (604 KB), and the MCP needs two
local fixes in it (see `vendor/PROVENANCE.md`). A submodule was rejected because the
upstream clone here is **shallow** (`git subtree add` refuses shallow roots, and
`--unshallow` needs network), and a submodule cannot carry our commits.

Provenance and refresh procedure: **`vendor/PROVENANCE.md`**. Short version - the sibling
clone `../dfhack-client-python` is the place to `git pull` upstream; `rsync` it into
`vendor/` (excluding `.git`), re-apply the two local fixes, run the tests.

If you prefer the sibling layout again, point `pyproject.toml` back at
`../dfhack-client-python`, run `uv lock`, and `uv sync`.

## 4. Regenerate protobuf for YOUR DFHack version

The checked-in `py_export/` is for **51.13-r1** — a mismatch fails method binding.
Regenerate against the running version (`protoc`, `cmake`, `make` required):

```bash
python3 scripts/fetch_proto_files.py --tag 53.17-r1
python3 scripts/generate_python.py --tag 53.17-r1
ls src/dfhack_client_python/py_export/          # *_pb2.py generated
```

## 5. Pin `mcp<2` (required)

Unpinned `fastapi-mcp` installs `mcp 2.x`, which crashes at import with

```
TypeError: Server.__init__() takes 2 positional arguments but 3 were given
```

`fastapi-mcp 0.4.0` calls `Server(name, description)`; mcp 2.x renamed `FastMCP` →
`MCPServer` and changed signatures. Pinned in `pyproject.toml`:

```toml
"mcp<2",
```

```bash
cd ~/github/dfhack-mcp && uv sync && uv run python main.py
```

## 6. Transports / endpoints

`main.py` binds `127.0.0.1:8000` (override with `DFHACK_MCP_HOST` / `DFHACK_MCP_PORT`) and
uses `mcp.mount_http()` → **Streamable HTTP** on a single route:

* `POST /mcp` → JSON-RPC (`initialize`, `notifications/initialized`, `tools/list`, `tools/call`);
  reply carries an `mcp-session-id` header that later requests must send back.
* REST endpoints also remain: `/get-versions`, `/get-view-info`, `/get-unit-list`,
  `/get-world-info`, `POST /list-units`

Registered MCP tools: `get_versions`, `get_world_info`, `get_view_info`, `list_units`, `get_unit_list`.

`mcp.mount()` (the original code) is deprecated and mounts the legacy pair
`GET /mcp` + `POST /mcp/messages/`, which needs a two-channel SSE client. `mount_http()`
is what DSH speaks, so it is used here.

Verified over Streamable HTTP: `initialize` (200) → `notifications/initialized` (202) →
`tools/list` (5 tools) → `tools/call get_versions` → live versions.

## 7. Connected to DSH (DeepSeek Harness)

The profile patch layer is `~/.dsh/profiles/web/cordis.patch.yml`; its `dsh-mcp-client` rows
all point at the shared `mcp-proxy` on port 8001. DFHack does **not** go through that proxy —
it speaks Streamable HTTP natively, so the row connects directly (like the WordPress row), and
`swap/start.sh` never needs restarting to add it.

```yaml
    - id: mcp-dfhack
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: dfhack
        transport: streamable-http
        url: http://127.0.0.1:8000/mcp
        toolCallTimeoutMs: 120000
        reconnect:
          maxAttempts: 60
```

* `serverName: dfhack` publishes tools as `mcp__dfhack__get_versions`, etc.
  (must match `[A-Za-z0-9_-]{1,32}`, unique across live instances)
* `toolCallTimeoutMs: 120000` — a loaded fort makes `get_unit_list` / `get_material_list`
  move large protobuf payloads; the 60 s default is too tight.
* `reconnect.maxAttempts: 60` — game and server are started by hand, so the retry window must
  cover a manual restart. `failOnStartupError` stays false, so booting the harness without
  Dwarf Fortress running still starts it; the tools are simply absent.
* `patchReload: "live"` (profile `package.json` → `dsh.profile.patchReload`) applies the edit
  **without restarting the harness**. Confirmed: the harness opened a connection to
  `127.0.0.1:8000` and completed `initialize` + `tools/list` immediately after the edit.

Start order no longer matters (see §10): the server connects lazily, so it can be
started before, after, or without Dwarf Fortress running.

```bash
cd ~/github/dfhack-mcp && uv run python main.py
```

## 8. Verified results

| Call | Result |
|---|---|
| `GetVersion` | `53.17-r1` |
| `GetDFVersion` | `v0.53.17 linux64 STEAM` |
| `GetViewInfo` | view `0,0,0` size `40x28`, cursor `-30000` (menu sentinel) |
| `GetUnitList` | `unitCount: 0` (no fort loaded) |
| `GetPauseState`, `GetMaterialList`, `ListEnums` | OK |
| MCP `initialize` + `tools/list` | 5 tools listed |
| MCP `tools/call get_versions` | returns live versions |
| `./dfhack-run ls` | works (same RPC channel) |
| `GetWorldInfo` | `CR_NOT_FOUND` — needs a loaded fort |
| `GetMapInfo` | `CR_FAILURE` — needs a loaded fort |

## 9. Gotchas

* **Game state matters.** With no fortress loaded (main menu / `save/` empty),
  `get_world_info` and `get_map_info` fail. Start a fort and they work.
* **`RunLua` takes `module` + `function`, not a code string** — you cannot eval arbitrary
  Lua over RPC. Use `RunCommand` (`CoreRunCommandRequest{command, arguments}`) for commands.
* **`connect()` swallows a failed handshake** (the upstream version sets
  `_reader/_writer = None` silently), so later calls raise `AttributeError` on `None`
  instead of a clear "game not running" error. This repo's patched transport raises
  `DFHackTransportError` with a readable message and reconnects on its own.
* **Binding.** `main.py` used to bind `0.0.0.0:8000`, exposing game control on your LAN.
  It now binds `127.0.0.1` by default (matching DFHack's own localhost-only RPC), with
  `DFHACK_MCP_HOST` / `DFHACK_MCP_PORT` as the deliberate override.
* **`get_header()` in the old `master` branch reads only 3 size bytes** (`h[4:7]`) — the
  package branch fixed this to `h[4:8]`.

## 10. No restart needed when the game restarts

The original design connected **once**, in the FastAPI lifespan:

```python
async def lifespan(app): await connect()   # one socket for the life of the process
```

so a Dwarf Fortress restart left the running MCP server holding a dead socket, and
`uv run python main.py` had to be restarted. Three separate problems had to be fixed
(`src/dfhack_client_python/dfhack_remote.py` in the client package):

1. **Lazy connect.** The lifespan no longer connects. `ensure_connection()` opens the
   socket on the first tool call, so the server can start before the game (or without it).
2. **Auto-reconnect with id re-binding.** DFHack assigns RPC method ids *per TCP
   connection* (`BindMethod`, id 0). A reconnect against a new game process must
   re-bind, so every successful connect bumps a generation counter and calls
   `BindMethod.cache_clear()`. A call that hits a dropped socket is retried once on a
   fresh connection (`MAX_CALL_ATTEMPTS = 2`).
3. **The mid-call hang.** The old read loop was
   `while size: more = await _reader.read(size)`. When the game exits mid-response
   `read()` returns `b''`, `size` never decreases, and the loop **spins forever** — a tool
   call hangs until the client timeout. `_read_exact()` now raises
   `DFHackTransportError` on EOF.

Also fixed: concurrent tool calls share one socket, so a full request/response exchange
runs under an `asyncio.Lock` — without it, parallel calls interleave frames and responses
get matched to the wrong request. Endpoint resolution honours DFHack's precedence
(`DFHACK_PORT` > `remote-server.json` > 5000) and is read per connect, so a port change
needs no restart.

### New MCP commands

| Tool | Route | Purpose |
|---|---|---|
| `get_status` | `GET /get-status` | `connected`, `generation`, `host`, `port`, `boundMethods`, versions — answers even when the game is closed, with a readable `error` |
| `reconnect` | `POST /reconnect` | Drops and reopens the RPC connection; use it right after restarting the game to skip the detection round-trip |

### Verified (server process never restarted during these)

| Scenario | Result |
|---|---|
| Game exits mid-response | `DFHackTransportError` in 0.00 s (was an infinite loop) |
| Game not running at all | clean error naming `127.0.0.1:5000` and the plugin to check |
| Game restarted on the same port **with different method ids** | next call reconnected, generation 1→2, ids re-bound, correct data |
| Server started before the game | starts fine, `get_status` reports not-connected, first call connects |
| 8 parallel `GetVersion` calls | 8 correct results on one socket (serialized) |
| Live server `POST /reconnect` | generation 1→2→3, `boundMethods` re-populated, `/get-view-info` still 200 |

`/get-status` reports `connected: true` briefly after a game exit — the TCP socket is
half-open and only the next read detects it. Detection therefore happens *inside* the
call, which is the point: the call self-heals instead of failing.

## 11. Recovering the DSH link (when the *harness* stops calling)

The transport above fixes game restarts. A different case: if the MCP server process is
down for long enough that `dsh-mcp-client` exhausts its `reconnect.maxAttempts`, the plugin
**unregisters the server's tools and stops retrying** - by design, "disposal (including HMR)
is the only way back". The fix is to re-create the plugin instance, not to restart the harness:

* Toggle it in the Plugin manager (`include:mcp-dfhack`, module `@deepseek-ai/dsh-mcp-client`),
  off then on; or
* Edit `cordis.patch.yml` (any change, e.g. a comment) - `patchReload: "live"` reloads the
  patch layer and re-creates the instance.

Check which case you are in:

```bash
ss -tnp | grep -w 8000        # ESTAB = harness is connected; empty = plugin not connecting
curl -s http://127.0.0.1:8000/get-status   # server side: game reachable? which generation?
```

Verified: after the toggle, the harness reconnected and completed
`initialize` → `notifications/initialized` → `tools/list`. The first
`POST /mcp 400 Bad Request` in the log is normal - the Streamable HTTP client probes
without a session id, then re-initializes.

Summary of the three restart levels, so only the smallest one is ever needed:

| What changed | What must restart |
|---|---|
| Dwarf Fortress game | nothing (auto-reconnect / `reconnect` tool) |
| `dfhack_remote.py` / `main.py` code | MCP server process only |
| DSH plugin link exhausted | plugin instance (toggle / patch edit) |

## 12. Re-runnable tests

```bash
cd ~/github/dfhack-mcp
uv run python tests/test_reconnect.py      # mid-call drop, no game, self-heal, concurrency
uv run python tests/test_game_restart.py   # game restart w/ DIFFERENT method ids, server untouched
```

`tests/mock_dfhack.py` is a stub remote-server (handshake + BindMethod + two methods) used
by the suites; the game is never touched, and `test_game_restart.py` starts/stops stubs on
port 15100 so the real DFHack port is left alone.

## 13. start.sh (manual control)

```bash
cd ~/github/dfhack-mcp
./start.sh            # start in background (pid .dfhack-mcp.pid, log dfhack-mcp.log)
./start.sh status     # running? + live get-status from the game
./start.sh stop       # clean stop (verifies the port frees, escalates to SIGKILL)
./start.sh restart    # stop + start
./start.sh logs       # follow the log
./start.sh fg         # run in this terminal, Ctrl+C to stop
```

`DFHACK_MCP_HOST` / `DFHACK_MCP_PORT` override the bind address. The log, pidfile, and
`tests/mock_*.log` are git-ignored.

Verified: `start` reports the live endpoint (`generation:1 … "v0.53.17 linux64 STEAM"`);
`status` shows pid + game data; `stop` frees port 8000 with no orphaned `uv` child;
after `start` + a plugin toggle (§11) the harness completed `initialize` → `tools/list`.

## 14. Daily flow

1. Start Dwarf Fortress (Steam).
2. `cd ~/github/dfhack-mcp && ./start.sh`
3. Play. Restarting the game needs **nothing** — the next tool call reconnects.
   `mcp__dfhack__get_status` tells you what the server sees; `mcp__dfhack__reconnect`
   forces a fresh connection.

## 15. Reload routine: restart the server, then make DSH re-read the tools

**Verified 2026-11-20** against a live 53.17-r1 save and the live harness on
`http://127.0.0.1:3080`. One command does the whole routine:

```bash
cd ~/github/dfhack-mcp
./scripts/mcp-reload.sh all          # restart + refresh + probe + port check
./scripts/mcp-reload.sh probe        # only: what does /mcp expose right now?
./scripts/mcp-reload.sh refresh      # only: make DSH drop the fiber and re-list tools
./scripts/mcp-reload.sh check-ports  # only: is the 8000/8001 port rule respected?
```

### The rule that makes this necessary

Restarting the server process does **not** change what DSH sees. `dsh-mcp-client`
registers `mcp__dfhack__<tool>` when its fiber connects; the tool list is read at
`initialize` → `tools/list` and lives in the harness. After **any** change to the
server's tool surface (new endpoint, renamed operation, removed tool):

1. restart the server process,
2. **refresh the DSH fiber** (below),
3. **open a new session** - an already-open session keeps the tool list it started with
   (the toggle plugin's own FAQ: a tool can still appear because "the page session
   hasn't refreshed its tool list").

### Refresh without clicking anything (headless)

`dsh-mcp-toggle` exposes an HTTP API on the harness web port. This is the whole
mechanism, and it is scriptable:

```bash
DSH=http://127.0.0.1:3080; ENTRY=include:mcp-dfhack
curl -s $DSH/mcp-toggle/api                         # every MCP row: enabled + fiberPhase
curl -s -X POST $DSH/mcp-toggle/api -H 'Content-Type: application/json' \
     -d "{\"entryId\":\"$ENTRY\",\"disabled\":true}"    # dispose: tools unregister
curl -s -X POST $DSH/mcp-toggle/api -H 'Content-Type: application/json' \
     -d "{\"entryId\":\"$ENTRY\",\"disabled\":false}"   # restart: initialize + tools/list
```

Verified output of a refresh: `fiberPhase` went `null` → `loading` → `active` within
~3 s, `persisted: true`, written to `/home/markus/.dsh/cordis.patch.yml` (the **home**
patch layer, which outranks the profile). No harness restart; the alternatives are the
Plugin manager toggle, or any edit to `cordis.patch.yml` (`patchReload: "live"`
re-creates the plugin instance - see §11).

Read the row state before and after: `enabled=true phase=active` means the tools are
registered. `enabled=false phase=null` means the row is **disabled in the home patch**
and the tools are absent from every new session - which is exactly how this repo's
`dfhack` row was found (it was silently off, so the server had no consumer).

### Port discipline (this is the trap that bites)

| Port | Owner | Proof it is right |
|---|---|---|
| **8000** | **this** MCP server - the profile row's url is `http://127.0.0.1:8000/mcp` | `curl 127.0.0.1:8000/get-status` answers |
| **8001** | the shared **mcp-proxy** (`uvx mcp-proxy --named-server-config config_mcp.json --port 8001 --stateless`), serving `time`, `fetch`, `ddg-search`, `freecad`, `blender`, `browser-mcp` at `/servers/<name>/mcp` | `POST 127.0.0.1:8001/servers/time/mcp` answers 200 |

Binding this server to 8001 is self-inflicted damage: it **displaces the proxy**, so
every proxied row breaks (observed live: `browser_*` tool calls returned
`{"detail":"Not Found"}` - a FastAPI answer, not the proxy), **and** the `dfhack` row
is left pointing at an empty 8000. `./scripts/mcp-reload.sh check-ports` detects this
and prints the fix; it reported:

```
8001 mcp-proxy NOT answering (http 404) - "name":"Dwarf Fortress MCP Server" is holding the port
FIX: move this server back to 8000 ... and restart the proxy (swap/start.sh)
```

### Probe expectations

`./scripts/mcp-reload.sh probe` prints the endpoint's live tool surface. With the
preprocessing layer loaded it reports **27 tools** and `PREPROCESSED LAYER: complete`
(`look`, `fort_progress`, `world_zone`, `world_map`, `world_objects`, `world_delta`,
`world_minimap`, `tile_legend`, `world_raw`, `world_cache_reset`). If it prints
`MISSING [...]`, the running process predates the code - restart it. The first
`POST /mcp 400` is normal (a streamable-HTTP probe without a session id).

### Sandbox/automation note

For an agent running this routine non-interactively, three limits were hit and verified:

* `uv run` fails in a file sandbox because uv's cache lives outside the workspace
  (`Failed to initialize cache at /home/markus/.cache/uv ... Read-only file system`).
  `.venv/bin/python main.py` avoids uv and works.
* `./start.sh stop|restart` cannot touch a server the **user** started in a terminal: the
  agent sandbox runs `--unshare-pid`, so `pgrep` and `ss -ltnp` see no host processes and
  report no pid for the port. `probe`, `refresh` and `check-ports` work from the sandbox
  (they are just HTTP); `restart` must be run in the user's shell.
* A server an agent starts as a managed background job dies with that job. So an agent
  can validate the chain on a temporary port, but a **durable** server must come from
  `./start.sh`. If an agent leaves one bound to 8000, the user's own `./start.sh` will
  fail to bind - stop the agent's job first (`port 8000 is held by the agent's job`).

### End-to-end verification in a fresh session

Ask a new session to enumerate `mcp__dfhack__*` tools and call
`mcp__dfhack__look` / `mcp__dfhack__fort_progress`; expect 10+ tools and the reported
`estTokens` (briefing ~1.2k, ladder ~400). A one-shot scheduled task is the tidy way
to run this check without touching the current session.

### What the routine proved the first time it was run (2026-11-20)

1. **Tool registration in a fresh session: PASS.** A scheduled one-shot in a *new*
   session enumerated **27 `mcp__dfhack__*` tools** and completed live round-trips
   (`look` 1,082 tokens, `fort_progress` 407 tokens, `NEXT (labors)`), writing the
   evidence to `dumps/session-tool-check.md`. The refresh was therefore confirmed on
   the consumer side, not just on the server side.
2. **It caught a real bug.** The same check reported that `look(max_tokens: 800)`
   answered **1,082 tokens with `truncated: false`** - `max_tokens` was a report field,
   not a cap, because header and rollup lines were appended without budget checks.
   `worldmodel.finish()` now trims to the cap for every projection and announces it;
   regression test `test_budget_is_a_hard_cap_not_a_report_field`
   (`tests/test_worldmodel.py`, 13 tests total). Live after the fix:
   `max_tokens=400 -> 378 (truncated)`, `800 -> 775 (truncated)`, `1500 -> 1240 (whole)`.
   Lesson worth keeping: an agent-side check found what the developer-side tests did
   not, because it asked the tool for an unusual budget.
3. **A silent disable was found.** `include:mcp-dfhack` was `enabled=false phase=null`
   in the home patch - the row was off, so a working server had no consumer. `refresh`
   prints the row state first for exactly this reason: read `before:` before you debug
   the server.

### Verified stop/start cycle (2026-11-20, agent-managed)

Run as `stop → start → probe → refresh`, all four layers watched:

| Step | Observed |
|---|---|
| server up | `connected=true generation=1 boundMethods=18 dfhack=53.17-r1`; 27 tools; live `look` 886 / `fort_progress` 407 tokens |
| `job_kill` (stop) | port 8000 **FREE**, `get-status` silent, MCP calls refuse |
| while down | **DSH row stayed `enabled=true phase=active`** — the client sits inside `reconnect.maxAttempts: 60`, so a row phase never proves the server answers. `mcp-reload.sh status` prints this as an explicit warning |
| start again | binds 8000, `connected=true` with `boundMethods` starting at 0 and growing per call (lazy `BindMethod`), generation bumped on reconnect |
| `refresh` | `disabled=true` → `fiberPhase=null`; `disabled=false` → `loading` → `active` in ~3 s; 27 tools re-listed, `PREPROCESSED LAYER: complete` |

Two operational notes from doing this:

* **`./start.sh` is for the user's shell.** An agent in the file sandbox cannot stop a
  server the user started (`--unshare-pid` hides host pids from `ss -ltnp`/`pgrep`), and an
  agent's own server is a managed job that dies with that job. So: an agent can start,
  stop, probe and refresh a server it owns; a server the user started must be stopped by
  the user.
* **A server the agent leaves running holds port 8000.** If the user then runs
  `./start.sh`, the bind fails with `address already in use`. Have the agent stop its
  job first, or point the profile row and the server at a port both sides agree on.

## 16. Driving the game the way a player does (drivability map)

Quickfort is one way to build; it is not the only one, and it is not the most
player-shaped. This map is measured on the live 53.17-r1 build, not copied from docs.

| Player action | Drivable? | How, and the measured catch |
|---|---|---|
| Labor menu (`y`) | **YES** | RPC `SetUnitLabors`. Field is `change` (not `changes`). Verified: assigning `MINE` to dwarf 6489 moved the layer's report `miners 2 → 3` |
| Labor *names* | **YES** | `ListEnums.unit_labor` items are `{value, name, bit_size}` — `e.id` does not exist. Mining is **`MINE`**, not `MINING` (matching the wrong string made the ladder cry "miners 0" on a fort that had 2) |
| Manager workorders (`j-m-q`) | **YES** | `workorder` accepts **JSON only** on this build; the docs' positional `workorder CollectRock 10` is fed to a JSON parser and fails. Game validates the name: `MakeBarrel` → "Queuing MakeBarrel x5"; `MakeChair` → "Invalid job type for manager order: MakeChair" |
| Dig designations (`m`/`d`) | **YES** | RPC `SendDigCommand` (x/y transposed; z-dependent acceptance) |
| Instant dig / build | **YES** | `dig-now`, `build-now` (`build-now` finished 9 queued constructions) |
| Blueprint layouts | **YES** | `quickfort` with `-c x,y,z` (no cursor needed); `orders --dry-run` prints quickfort's plan, **not** a game rule; `delete`, `undo` exist |
| Menu hotkeys (`b`, `c`, `T`, `y`, `z`, `escape`) | **YES** | Injected SDL1 key events reach the UI and menus react |
| **Aiming the map cursor** | **NO** | Keyboard cursor can be switched on (`toggle-kbd-cursor`, a *toggle* - confirm via `GetViewInfo` real coords, not the command), but **no arrow/numpad/keypad variant moves it**: tested syms 273/274/275/276 and 258/262/271, `which=0/1`, shift. So rectangle drags and building placement by cursor are not reachable |
| Mouse | **NO** | RemoteFortressReader exposes `PassKeyboardEvent` only; there is no mouse event RPC |
| Zones menu (`z`) | **NO (CLI)** | `zone` is "a plugin but does not implement any commands" - meeting areas, pastures, dumps, depots-as-zones are UI-only |
| Stockpile config (`p`) | **NO** | `stockpile` is not installed in this build; `stockpiles.proto` is a settings schema with no RPCs |
| `buildingplan` | **PARTIAL** | CLI covers settings (`buildingplan set boulders false`, `status`, `reset`, `ignore <burrow>`); placement is an in-game overlay, so it needs the mouse |
| `units`, `noble`, `military` | **NO** | "not a recognized command" in this build |

**Practical shape of a play loop:** read with `look` / `fort_progress`; act with
`set_unit_labors`, `work_order`, `send_dig_command`, `run_command` (`dig-now`,
`build-now`, `quickfort`); verify with `world_delta`. The gaps (zones, stockpiles,
placement drags) are mouse-only, so the honest contract with a human player is:
**the agent designs and states exact coordinates; the human drags the rectangle.**

## 17. Deployables: the repo is the source, the game dir is a copy target

Everything the MCP authors lives under git in `deployables/`, and reaches the game only by
copy. This came out of a real wart: `apply_blueprint` used to write straight into
`<gamedir>/dfhack-config/blueprints/`, which is read-only to the sandbox, so the whole MCP
server had to run with host write access just to save a text file.

```
deployables/blueprints/*.csv      ->  <gamedir>/dfhack-config/blueprints/
deployables/scripts/**/*.lua      ->  <gamedir>/hack/scripts/        (subdirs mirrored)
```

```bash
./scripts/deploy.sh            # check: report same/differing/new, copy nothing (default)
./scripts/deploy.sh deploy     # copy repo -> game; needs write access to the game dir
./scripts/deploy.sh list       # print the mapping
DF_GAMEDIR=/x/df ./scripts/deploy.sh deploy
```

- `apply_blueprint` writes to `REPO_BLUEPRINT_DIR` (default `deployables/blueprints`),
  copies to `DFHACK_BLUEPRINT_DIR` (the game's dir), then runs quickfort - quickfort
  resolves names only against its own tree, so the copy is mandatory before the run.
- Replies now carry **both** paths (`blueprint` + `deployed_to`). A missing target answers
  `success:false` with the `deploy.sh` instruction; a failed quickfort run answers with the
  deployed path, because authoring succeeded.
- 20 pre-existing blueprints were copied out of the game directory into
  `deployables/blueprints/`, so the player's designs are versioned too. `deploy.sh check`
  reported all 19 remaining as `same`.
- Lua scripts: `deployables/scripts/dfhack_mcp.lua` is deployed to `<gamedir>/hack/scripts/dfhack_mcp.lua` via `./scripts/deploy.sh deploy` (or discovered via `GET /game-paths`) and hot-loaded on first use via `reqscript('dfhack_mcp')`.
