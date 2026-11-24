# DFHack MCP Tool Notes & HTTP-Only Diagnostic Reference

This document preserves historical playtest findings, DF 53.17-r1 engine quirks, crash postmortems, and the HTTP-only diagnostic/operator endpoint catalog that were previously embedded in `main.py` MCP tool docstrings. Keeping these notes here rather than in `FastApiMCP` tool schemas keeps the LLM tool surface focused and compact.

---

## 1. Digging & Fog Workflow

- **Elevation-to-Z offset**: All map reads and `send_dig_command` box/shaft helpers convert in-game elevation using `z = elevation + 129` (`WM.GAME_Z_OFFSET`).
- **FOG WORKFLOW (verified live on DF 53.17-r1)**: Designating into unexplored fog (`hidden=True`, `?` glyph) **WORKS** and is the intended workflow for extending tunnels, rooms, and stair shafts into the mountain.
  - Pre-flight with `fort_strata` (aquifers/water/magma/soil) and `survey` (do not designate over existing floors/buildings).
  - Note: `tile_at` does not show `dig_designation` on hidden tiles; verify fog orders via `look` / `world_map` or HTTP `GET /world-objects`, and verify completion by positive tile state (`dug > 0 and to_dig == 0` in `survey`), never by designation count dropping to 0.
- **Middle-first ordering**: `send_dig_command` orders box centers before flanks (`middle_first=True`) so miners carve access into fog without stranding tiles.
- **Stair shafts**: `stair_shaft` automatically designates `DOWN_STAIR_DIG` at the top elevation, `UP_DOWN_STAIR_DIG` on intermediate elevations, and `UP_STAIR_DIG` at the bottom elevation.

---

## 2. Trees & Water

- **Tree felling (`find_trees`, `cut_trees`)**:
  - Measured 2026-11-21: As in-game UI selection targets the tree trunk, `cut_trees` designates `DEFAULT_DIG` only on `tree_trunk` (`TreeTrunk`) tiles.
  - Saplings (`sapling`) are counted separately and are *not* felled (clearing a sapling is the "clear vegetation" order, not tree felling).
  - `wood_after` in `cut_trees` reflects the moment of reading; hauling and chopping take in-game time.
- **Water & Wells (`find_water`)**:
  - DF wiki rule: a well is 1x1 on a floor with a clear vertical line to water `>= 3/7` deep, and does not work through a staircase.
  - `find_water` scans map blocks and clusters water tiles per cell with depth range and elevation.

---

## 3. Buildings, Stockpiles & Zones

- **`place_building`**:
  - Multi-tile stockpiles must use `create_stockpile` (`allocInstance -> setSize -> constructAbstract`) rather than `constructBuilding` (which ignores `w/h` and produces a 1x1 pile).
  - Multi-tile workshops route through `constructBuilding` with `width`/`height` + `room.extents` (`constructAbstract()` rejects workshops with `expected !bld->isActual()`).
  - `constructWithItems` rejects an empty item list, and RemoteFortressReader item IDs do not resolve directly—`material="log"|"boulder"|"block"` with `count=N` or crafted furniture lookup resolves real `df.item` objects.
  - Pre-flight site gate rejects footprints containing rock, fog, open air, or water.
- **`create_stockpile` & `configure_stockpile`**:
  - **Mid-air stockpile postmortem (measured 2026-11-21)**: Placing a stockpile over `open` cavern air, unexcavated rock, or fog is accepted by DF and leaves the pile floating in mid-air. The ground site gate reads the footprint first and refuses if any tile is not a dug, walkable floor.
  - **DF 53.x stockpile category flags**: `ammo`, `animals`, `armor`, `bars_blocks`, `cloth`, `coins`, `corpses`, `finished_goods`, `food`, `furniture`, `gems`, `leather`, `misc`, `refuse`, `sheet`, `stone`, `weapons`, `wood`. (Note: `plants` is not a top-level category flag; plants live under `food`.)
  - **General pile**: `all_categories=True` with `deny=["stone", "wood"]`.
  - **Custom-menu `pile_arrays` crash**: Enabling category flags over empty material arrays can crash when opening Custom settings in-game; `configure_stockpile` initializes the underlying arrays via Lua (`POST /pile-arrays` inspects/repairs them over HTTP).
- **`create_zone` & `remove_zone`**:
  - **Zone allocation rule**: Zones are abstract buildings created via `allocInstance(30, subtype=<kind>) -> setSize -> constructAbstract` (never post-construction `z.type = ...` or `notifyCivzoneModified`, which crashed DF in v1/v2 tests).
  - **`remove_zone` crash postmortem (measured 2026-11-21 22:53:41, `crash_2026-11-21-22-53-41.txt`)**: Zone removal via RPC previously caused a SIGSEGV at address 0 inside `Buildings::notifyCivzoneModified`. The current Lua path performs `setOwner(b, nil)`, cleans `assigned_units` and `general_refs` civzone entries in `units.active`, and calls `deconstruct`, and is gated behind `acknowledge_risk=True` with live zone-list read-back verification. Prefer in-game zone removal (`z -> remove`) when practical on a prized save.

---

## 4. Work Details & Labors

- **DF 53.x Labor Menu (`work_details`)**:
  - Measured 2026-11-22: Work details live at `df.global.plotinfo.labor_info.work_details`, each with `name`, `allowed_labors`, `flags.mode` (`1` = `EverybodyDoesThis`, `2` = `NobodyDoesThis`, `3` = `OnlySelectedDoesThis`), and `assigned_units` (a sorted vector of unit IDs).
  - After every mutation (`assign`, `set-mode`, `set-labor`, `create`, `delete`), `work_details` keeps `assigned_units` sorted, switches `flags.mode` to `OnlySelectedDoesThis` (`3`) when adding a unit, and runs `dfhack.units.setAutomaticProfessions(u)` across all citizens so `unit.status.labors` updates immediately even while paused.

---

## 5. HTTP-Only Diagnostic & Operator Endpoints

All 56 endpoints below remain live on the FastAPI HTTP server for `df-observer`, operator scripts, and unit tests, but are excluded from `FastApiMCP` to keep the LLM tool surface focused (~32 tools):

### Vertical / Geology / Reachability Micro-Probes (13)
| Operation ID | Method & Path | Purpose |
|---|---|---|
| `overburden` | `GET /overburden` | Non-air levels above `(x, y, elev)` |
| `column` | `GET /column` | Single `(x, y)` tile column top-to-bottom |
| `cross_section` | `GET /cross-section` | Vertical slice across an x/y span |
| `depth_profile` | `GET /depth-profile` | Depth under local surface for a rectangle |
| `magma_depth` | `GET /magma-depth` | Distance down to magma from a dig point |
| `probe_plan` | `GET /probe-plan` | Rank elevations in one column for a probe dig |
| `tunnel_sites` | `GET /tunnel-sites` | Find solid-rock tunnel lines at depth >= 2 |
| `find_soil` | `GET /find-soil` | Soil-floor cluster census (superseded by `fort_strata`) |
| `hazards` | `GET /hazards` | DAMP/HOT check for a dig rectangle |
| `warnings` | `GET /warnings` | Designation-time DAMP/HOT warnings across current digs |
| `reach_map` | `GET /reach-map` | 3D walk-component summary |
| `dig_reachable` | `GET /dig-reachable` | Connectivity check to a target dig tile |
| `walkable_between` | `GET /walkable-between` | `dfhack.maps.canWalkBetween` check between two tiles |

### Legacy / Raw / Debug / UI-Hack Endpoints (11)
| Operation ID | Method & Path | Purpose |
|---|---|---|
| `apply_blueprint` | `POST /apply-blueprint` | Write & apply quickfort CSV blueprint |
| `copy_screen` | `GET /copy-screen` | Capture current game screen ASCII text |
| `pass_keyboard_event` | `POST /pass-keyboard-event` | Inject raw SDL1.2 keyboard event |
| `reconnect` | `POST /reconnect` | Drop and reopen DFHack RPC connection |
| `world_cache_reset` | `GET /world-cache-reset` | Drop WorldModel block/decoder caches |
| `world_delta` | `GET /world-delta` | Diff since last world read |
| `world_minimap` | `GET /world-minimap` | 1-char-per-16x16-block map overview |
| `world_objects` | `GET /world-objects` | Dig designations, buildings, and item clusters |
| `transitions` | `GET /transitions` | SQLite TransitionStore excavated tile counts |
| `pile_arrays` | `POST /pile-arrays` | Inspect/repair stockpile Custom-menu arrays |
| `wagon_path` | `GET /wagon-path` | BFS check for 3-wide wagon path to map edge |

### Redundant Read / Lookup Endpoints (16)
| Operation ID | Method & Path | Purpose |
|---|---|---|
| `get_status` | `GET /get-status` | RPC connection, versions, and save identity |
| `get_pause_state` | `GET /get-pause-state` | Current pause boolean |
| `get_reports` | `GET /get-reports` | Recent in-game announcements/reports |
| `tile_at` | `GET /tile-at` | Detailed single-tile inspection |
| `tile_legend` | `GET /tile-legend` | 29-glyph tiletype legend |
| `list_buildings` | `GET /list-buildings` | RPC block building census |
| `buildings_live` | `GET /buildings-live` | Full `df.global.world.buildings.all` census |
| `get_building_def_list` | `GET /building-def-list` | Buildable definition names |
| `labor_list` | `GET /labor-list` | Valid labor enum names |
| `unit_counts` | `GET /unit-counts` | Population counts by category |
| `list_units` | `POST /list-units` | Detailed unit records by ID |
| `unit_labors` | `GET /unit-labors` | Effective citizen `status.labors` |
| `unit_professions` | `GET /unit-professions` | Citizen professions & labor ticks |
| `unit_activity` | `GET /unit-activity` | Current position, move target, and jobs per dwarf |
| `job_queue` | `GET /job-queue` | Active fortress job queue |
| `work_order_link` | `GET /work-order-link` | Link a workshop to a manager work order |

### Previously Excluded Core/Observer/Debug Endpoints (16)
`game_lua`, `observe`, `observe_stats`, `game_paths`, `get_block_list`, `world_raw`, `get_versions`, `get_world_info`, `get_map_info`, `get_view_info`, `get_unit_list`, `world_zone`, `building_at`, `make_stockpile`, `resize_building`, `repair_stockpile_settings`.
