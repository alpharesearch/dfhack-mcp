---
name: df-crisis
description: Emergency triage and fort recovery - diagnosing wounded crews, cavern wildlife/invader breaches, building and stocking a hospital, drink/seed shortages, aquifer/damp cancellations, and strange moods.
whenToUse: Use when `alerts_and_wellbeing` flags `wildlife_on_work_level`, `hostile_units`, `wounded > 0`, `citizen_sustenance`, or `strange_mood_or_tantrum`, when `building_status` reports wounded miners or stalled digs, or when `send_dig_command` reports immediate cancellations.
metadata:
  source: measured live on DF 53.17-r1 / dfhack-mcp (33-tool MCP surface)
  tools: alerts_and_wellbeing, building_status, unit_profile, look, survey, fort_strata, send_dig_command, place_building, create_zone, workshop_job, gather_and_items, dfhack_automation, manage_nobles, configure_farmplot, find_water, set_pause_state
---

# Crisis Triage & Fort Recovery

## 1. Pause & Diagnose Before Writing

When an emergency hits, pause (`set_pause_state(paused=True)`), gather all three diagnostic reads, then act:
1. **`alerts_and_wellbeing`**: Check `hostiles`, `wildlife_near_citizens` (wild creatures within ±1 elevation of citizens), `citizen_issues` (includes `x`, `y`, `elev` per dwarf), `thirsty`, `hungry`, `wounded`, and `strange_moods`.
2. **`building_status`**: Check `diagnostics.crew` (`miners_available`, `miners_wounded`, `citizens_wounded`) and `diagnostics.bottlenecks`.
   - **Wounded miners rest instead of digging.** If `miners_available == 0`, stop issuing more dig boxes and stabilize healthcare/safety first.
3. **`unit_profile(action="profile", unit_id=...)`**: Inspect a specific dwarf's `health` (`wounds_count`, `stress`, `mood`, `thirst_timer`, `hunger_timer`), `skills`, `inventory`, and `current_job`.

## 2. Cavern / Wildlife Breaches (e.g. Crundles on Work Levels)

- **Seal the opening**: If `wildlife_near_citizens` shows predators on workshop/living elevations, find the open cavern edge via `look` / `survey` and seal 1-wide choke points with a `Door` (requires opposing N+S or E+W solid wall flanks, lockable in-game) or constructed walls.
- **Keep forts indoors**: Never build living/meeting zones or main workshops on unroofed cavern floors (`open_above > 0`).

## 3. Wounded Crew & Hospital Setup

A working hospital requires all four layers:
1. **Chief Medical Dwarf**: Check medical skills (`unit_profile(action="skills", skill="DIAGNOS")`) and appoint via `manage_nobles(action="auto_assign", position="CHIEF_MEDICAL_DWARF")`.
2. **Indoor Hospital Zone**: Dig an enclosed indoor room and call `create_zone(kind="hospital", ..., acknowledge_risk=True)`.
3. **Hospital Furniture**: Queue via `workshop_job(action="queue")` and place inside the zone:
   - `Bed` (2+ for resting/recovery)
   - `Table` (1+ for surgery)
   - `Chest` / `Box` (1–2 to store cloth, thread, splints, crutches)
4. **Medical Supplies & Water**:
   - Queue `process_plants` (Farmer's) → `thread` & `cloth` (Loom) → `splint`, `crutch`, `bucket` (Carpenter's).
   - Ensure clean water via `find_water` + a `Well` or `WaterSource` zone (never breach an aquifer directly into a walkway).

## 4. Drink & Seed Emergency

- **Protect seeds immediately**: Run `gather_and_items(action="protect_seeds")` (or `dfhack_automation(action="apply_preset", preset="early_fort")`) so cooks never destroy plump helmets or seeds.
- **Check inputs**: Call `workshop_job(action="stock")` for `DRINK`, `SEEDS`, `BARREL`, `LARGE_POT`.
- **Brew & prioritize**:
  1. Queue `barrel` (Carpenter's) or `pot` (Craftsdwarf's) if containers are low.
  2. Queue `brew` (`repeat_job=True`) at the Still.
  3. Prioritize brewing: `dfhack_automation(action="configure", plugin="prioritize", args=["CustomReaction", "--reaction-name", "BREW_DRINK_FROM_PLANT"])`.
- **No seeds/plants left**: Gather surface shrubs (`gather_and_items(action="gather_plants", ...)`), or buy plants/drinks from a caravan (`df-trade`).

## 5. Aquifer / Water Breaches & Cancelled Designations

- If `send_dig_command` reports `cancelled_immediately > 0` (or `survey` shows `designated 0` right after writing), the face hit **damp/aquifer rock** or is **unreachable**.
- **Never spam the same box**: Inspect with `fort_strata` and `survey`, keep `>= 1` solid wall tile between excavations and water/aquifers, and seal any flooded 1-wide tunnel with a `Door` or `Floodgate` + `Lever` (`Mechanic's Workshop` + `mechanisms`).

## 6. Strange Moods

- When `strange_mood_or_tantrum` fires, call `unit_profile(action="profile", unit_id=...)` on the moody dwarf to identify their highest craft skill (`MASONRY`, `CARPENTRY`, `METALCRAFT`, `STONECRAFT`, `WEAVING`, `GEM_CUTTING`, etc.), verify that matching workshop is built, and check `workshop_job(action="stock")` for stone, wood, bars, cloth, and rough gems.
