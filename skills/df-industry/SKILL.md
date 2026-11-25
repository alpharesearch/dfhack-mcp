---
name: df-industry
description: Workshops, direct workshop jobs vs manager work orders, fuel/metal and cloth/hospital production chains, skill-based staffing, Bookkeeper accuracy, enclosed noble rooms, and job prioritization.
whenToUse: Use when building workshops or furnaces, queuing items before or after a Manager exists, running fuel/smelting/forging or cloth/splint chains, staffing dwarves by skill, assigning a Bookkeeper or offices/bedrooms, or prioritizing stalled workshop/dig jobs.
metadata:
  source: measured live on DF 53.17-r1 / dfhack-mcp (33-tool MCP surface)
  tools: workshop_job, work_order, place_building, building_status, create_zone, assign_zone_owner, list_zones, manage_nobles, unit_profile, work_details, dfhack_automation, fort_plan, survey, run_command
---

# Workshops, Production Chains & Noble Rooms

## 1. Two Production Paths: `workshop_job` vs `work_order`

- **Before a satisfied Manager exists** (or while the Manager is wounded): `work_order` orders sit at `validated: false, active: false`. Use **`workshop_job(action="queue", ...)`** directly on completed workshops and furnaces.
- **Supported `workshop_job` tasks**:
  - **Furniture & storage**: `chair`, `table`, `bed`, `door`, `cabinet`, `chest`, `coffin`, `barrel`, `bin`, `bucket`, `wheelbarrow`, `pot`, `blocks`, `crafts`.
  - **Food & drink**: `brew` (Still), `meal` (Kitchen).
  - **Fuel, cloth, hospital & military**: `charcoal` (Wood Furnace), `process_plants` (Farmer's Workshop), `thread` (Loom), `cloth` (Loom), `bag` / `rope` (Clothier's), `splint` / `crutch` / `training_weapon` (Carpenter's), `weapon_rack` / `armor_stand` (Carpenter's or Mason's), `mechanisms` (Mechanic's).
- **Verify outputs & queues**:
  - Read a single shop's queue cheaply with `workshop_job(action="list", building_id=<id>)`.
  - Read crafted free items with `workshop_job(action="stock")` (tracks `WHEELBARROW`, `LARGE_POT`, `CLOTH`, `CHARCOAL`, `SPLINT`, `CRUTCH`, `ARMORSTAND`, `WEAPONRACK`, plus contained `DRINK`/`FOOD`/`SEEDS`).
  - Cancel by exact `job_id` via `workshop_job(action="cancel", building_id=..., job_id=...)`.

## 2. Placing Workshops & Furnaces

- **Building names**: Both spaced/apostrophe (`Wood Furnace`, `Farmer's Workshop`, `Metalsmith's Forge`, `Trade Depot`) and compact CamelCase (`WoodFurnace`, `FarmersWorkshop`, `MetalsmithsForge`, `TradeDepot`) are accepted. If a name fails, read `closest matches` in the error.
- **Place as 3x3 plans**: Call `place_building(building="...", x=..., y=..., elev=..., w=3, h=3)` **without** `material=...` so builders fetch materials normally (passing `material=...` when 0 items match fails cleanly).
- **Indoor roof check**: Keep workshops and living areas under solid rock (not on unroofed cavern floors or open ramp necks).

## 3. Core Production Chains

1. **Manager Bootstrap**: `Carpenter's Workshop` → `workshop_job(task="chair")` → `place_building("Chair")` inside an enclosed office → `create_zone(kind="office", acknowledge_risk=True)` + `assign_zone_owner` → `manage_nobles(action="auto_assign", position="MANAGER")`.
2. **Fuel & Metal**: `cut_trees` → `Wood Furnace` (`workshop_job(task="charcoal")`) → `Smelter` (ore + charcoal + flux) → `Metalsmith's Forge` (picks, axes, weapons, armor).
3. **Cloth & Hospital Gear**: `Farmer's Workshop` (`process_plants`) → `Loom` (`thread`, `cloth`) → `Carpenter's Workshop` (`bed`, `splint`, `crutch`, `bucket`, `table`, `chair`, `chest`).

## 4. Skill-Based Staffing & Bookkeeper Stock Scope

- **Inspect skills first**: Call `unit_profile(action="skills")` (or filter with `skill="MINING"|"MASON"|"CARPENTRY"|"BREW"`) to see every citizen's ratings before assigning labors via `work_details(action="assign")`. Never strip the sole holder of a rare craft.
- **Bookkeeper**: Appoint the highest `RECORD_KEEPING` dwarf via `manage_nobles(action="auto_assign", bookkeeper_settings=4)` and give them a satisfied enclosed office with a chair.
- **Always name stock scope**: `fort_progress` reports `pile/band stock` (watched stockpiles), whereas `workshop_job(action="stock")` reports `in_play_item_scan` (free items + contained `DRINK`/`FOOD`/`SEEDS` stack sizes). Do not treat those two scopes as interchangeable.

## 5. Enclosed Noble Rooms & Doorway Geometry

- **Separator walls**: Separate adjacent rooms (`office`, `bedroom`, `dining`, `tomb`) with **`>= 1` solid unexcavated rock tile**. Contiguous dug rooms without a rock separator merge into one shared room.
- **No overlapping owned zones**: Never stack an `office` inside a `bedroom`; `create_zone` rejects overlapping room zones and outdoor rooms (`open_above > 0`) and returns `enclosure` metrics.
- **Doorway flanks**: A `Door` requires opposing solid rock walls (N+S or E+W) in a flat 1-tile neck. A neck sitting on a ramp or level drop cannot hold a door.

## 6. Job Prioritization (`prioritize`)

- Use `dfhack_automation(action="configure", plugin="prioritize", args=["Dig"])` or `args=["CustomReaction", "--reaction-name", "BREW_DRINK_FROM_PLANT"]`.
- Use real DF job names (`Dig`, `CarveUpDownStaircase`, `ConstructBuilding`, `CustomReaction`), **not** `"Mine"`. `prioritize` is on/off (it ignores numeric priority levels).
