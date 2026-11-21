# dfhack-mcp

An MCP server that uses the `dfhack_client_python` package to connect to a locally
running Dwarf Fortress game through DFHack's RPC connection.

Setup and troubleshooting: [SETUP-LINUX.md](SETUP-LINUX.md) · `./start.sh [start|stop|status|logs|fg]`

## The problem this repo had

DFHack's `RemoteFortressReader` answers the whole map as raw per-tile arrays. Handing
that to a language model does not work - it is not "verbose", it is three orders of
magnitude too big. Measured on a live save (DF 53.17-r1, `region1`, 192×192 tiles,
191 z-levels, 22,127 discovered blocks; the player model is `global-default`, a
524,288-token context):

| What the model was handed | Payload | ~Tokens |
|---|---|---|
| `get_block_list`, **one** z-level | 588 KB | **147,000** (28% of the whole context) |
| the whole map, all z | 120 MB | **30,000,000** (57× the context) |
| the whole map, all z, in one request | — | socket killed: `reply too large: 853660541` |

So the server *had* a map tool, and no model could use it.

## The preprocessing layer

[`worldmodel.py`](worldmodel.py) sits between the RPC protos and the model. It fetches,
caches, decodes, aggregates, and projects. Rules:

* **Never send tile arrays.** 697 tiletype names → a 29-glyph alphabet + a legend
  read once. Water, magma and hidden are read from their own arrays, not the tiletype.
* **Aggregate before projecting.** Walkable regions by flood fill (named A, B, C…),
  designation tiles clustered into rectangles, items rolled up by type/material,
  buildings and units rolled up with decoded names.
* **Progressive disclosure.** A briefing, then strips, then a viewport, then raw.
* **Hard token budget on every call** (default 2,000, cap 30,000) with truncation
  announced, so a reply cannot silently blow the context.
* **Deltas.** Consecutive calls report what *changed* (new/cleared designations,
  buildings, item counts, water/magma) — 29 tokens, which is what makes a long game
  playable in a small context.
* **Coordinates are game elevations** (`elev = rpc_z - 128`). The offset, the x/y
  dig transposition, and the half-open z bounds are handled inside the layer and never
  reach the model.
* **Mode gating.** Fortress and adventure are different games; map projections check
  `GetWorldInfo` mode and refuse to answer in adventure mode.

### Tools, measured on the live save

| Tool | Gives you | Tokens |
|---|---|---|
| `look` | date, pause, population by role/species/soldier/wounded/hostile, reports, 12-elevation strip, deltas, dig clusters, item and building rollups, named regions, ladder status + next step | **1,232** |
| `fort_progress` | the Quickstart milestone ladder evaluated live, with keys, why, a coordinate box, flood alerts | **430** |
| `world_zone(e0,e1)` | one line per elevation | **286** |
| `world_map(e,x0,x1,y0,y1)` | glyph grid of a window, RLE, region letters | **721** |
| `world_objects(e0,e1)` | everything actionable, nothing else | **674** |
| `world_delta` | only what changed since the last read | **29** |
| `tile_legend` | the stable glyph alphabet | **321** |
| `world_minimap` | 1 char per 16×16 block, 48 z (opt-in: all 191 z) | 3,713 (13,688) |
| `world_raw` | raw blocks **written to a file** (grep it), never into context | **50** |
| `get_block_list` | the old raw tool, kept for power users, with its size announced | 196,361 |

Briefing vs raw: **159× smaller.** Raw z-level vs objects view: **180× smaller**, and
the objects view is the same planning content.

### The ladder (why `fort_progress` exists)

A player does not learn Dwarf Fortress by staring at a map; they learn a sequence of
milestones, each a short key press with a reason — pause, labors, entry tunnel
(`m`/`u`), stairwell (`m-t`), stockpile (`p`), meeting area (`z`), refuse pile, farm
plot (`b-o-f-p`), wood (`l`), still (`b-o-f-l`), pasture, workshops (`b-o` + `h/t/p/j`),
furniture, wheelbarrows, barrels, garbage dump, trade depot, bedrooms, dining, offices,
smelter + forge, military. `fort_progress` marks each rung `[x]`/`[ ]` from live world
state and returns the **first broken rung with its key sequence, its reason, a
concrete coordinate box inside a real walkable region, and water/magma warnings** —
computed server-side, with `pass_keyboard_event` able to execute the keys. The model
picks up where the ladder is broken instead of inventing a plan.

Rungs are detectable because in DF, zones, stockpiles and pastures *are* buildings, and
labors are readable (`ListUnitsIn(mask=BasicUnitInfoMask(labors=True))`).

### Recommended agent loop

`look` → `fort_progress` → act (`run_command`, `apply_blueprint`, `send_dig_command`,
`pass_keyboard_event`, `set_pause_state`) → `world_delta` → repeat. Zoom with
`world_map` / `world_objects` only where you are working; write bulk detail to
`world_raw` and grep the file.

## Tests

```bash
uv run --no-sync python tests/test_worldmodel.py      # 12 tests, live-game ones skip if no game
uv run --no-sync python tests/test_reconnect.py
uv run --no-sync python tests/test_game_restart.py
```

`tests/test_worldmodel.py` proves: the glyph alphabet classifies all 697 live tiletype
names; the half-open window fix includes the top z-level; tile-index round-trip; RLE;
designation clustering; flood-fill walkability; budget clamp and truncation; the ladder
reporting the first broken rung; and, when the game is up, that raw is >15× the
briefing. `tests/fixtures/tiletypes.json` is the live tiletype vocabulary fixture.

## Reloading, and making DSH re-read the tools

Restarting the server does **not** change the tools your agent sees: `dsh-mcp-client`
registers `mcp__dfhack__<tool>` when its fiber connects, and an open session keeps the
tool list it started with. After any change to the tool surface:

```bash
./scripts/mcp-reload.sh all      # restart + refresh the DSH fiber + probe + port check
./scripts/mcp-reload.sh probe    # what does /mcp expose right now? (expect 27, layer complete)
./scripts/mcp-reload.sh refresh  # headless: POST /mcp-toggle/api off/on, poll to phase=active
./scripts/mcp-reload.sh check-ports
```

Then **open a new session** to pick the new tools up. Full routine, the headless API,
the `enabled=false phase=null` state that silently disables a row, and the
**port rule (this server is 8000; 8001 is the shared mcp-proxy - binding here breaks
`browser-mcp`, `time`, `ddg-search` and every other proxied row)** are documented in
[SETUP-LINUX.md §15](SETUP-LINUX.md).

## Known RPC traps

See the module docstring in [`main.py`](main.py) — every entry there is verified
against a running game (half-open `max_z`, span-1 returning nothing, chunk spilling
double-counting, ~850 MB replies killing the socket, transposed dig, `NONE` building-def
key collisions, species not in RFR, `Unused9999` tiletypes, cursor sentinel −30000,
no Lua eval over RPC, fog reading empty until `reveal`).

## Roadmap

* **Adventure mode** as its own projector set (`AdventureControl`: menu, movement, world
  map) — currently gated off, not implemented.
* **RAG for long-game memory** — deliberately *not* the map solution. Aggregation
  already fits the world in context; retrieval pays off for unbounded *text*: a plan
  journal across hundreds of turns, report history, and the static DF corpus (697
  tiletype captions, building/material/job definitions). Planned as sqlite + sqlite-vec
  over an embedding endpoint; none is serving an embedder today (checked: llama-swap's
  68 profiles and ollama have none).
* **Candidate action ranking** beyond the ladder, so the next step carries evaluated
  options rather than the canonical one.

## Files the MCP owns (deployables)

Blueprints and lua scripts are authored in the repo under `deployables/` and deployed by
copy into the game directory (`./scripts/deploy.sh`). The game tree is never the authoring
place - see `SETUP-LINUX.md` §17.
