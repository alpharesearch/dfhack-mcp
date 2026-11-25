---
name: df-farm
description: Underground soil farming, dig-to-soil room planning, 3x3 Farm Plot placement and 4-season crop setup, seed protection, Still/Barrel brewing pairing, Kitchen meals, and surface grass pastures.
whenToUse: Use when locating underground soil or dig-to-soil layers, digging a farm room, placing and configuring a 3x3 Farm Plot, setting up Still brewing and Kitchen cooking, enabling autofarm/seedwatch/ban-cooking, or assigning grazing animals to a surface pasture.
metadata:
  source: measured live on DF 53.17-r1 / dfhack-mcp (33-tool MCP surface)
  tools: fort_strata, fort_plan, send_dig_command, survey, place_building, configure_farmplot, workshop_job, dfhack_automation, gather_and_items, create_zone, assign_pasture, building_status, fort_progress
---

# Underground Farming, Brewing & Pastures

## 1. The Soil Rule & Dig-to-Soil Planning

- **Farms require soil (or muddy stone)**: Bare stone floors are unplantable. `place_building("Farm Plot")` enforces a soil/mud gate and refuses stone footprints.
- **Dig-to-soil rule**: Digging 1 elevation below a surface soil layer (`soil` at `e + 1`) creates a **covered underground soil floor** at `e`.
- **Find the site cleanly**:
  1. Run `fort_strata` to inspect `soil_walls`, `potential_soil`, and `farm_elev` (avoiding aquifer layers).
  2. Call `fort_plan(action="find_site", kind="farm", elev=<farm_elev>, w=3, h=3)`:
     - Use `sites` if a clean, already-dug 3x3 soil footprint exists.
     - Otherwise use `dig_to_soil_sites` (solid rock covered by soil above), connect a `5x5` room to the fort shaft (`send_dig_command`), and verify with `survey` (`dug == 25, to_dig == 0`).

## 2. Place & Configure a 3x3 Farm Plot

- **Always pass `w=3, h=3, nominal=False`**:
  `place_building(building="Farm Plot", x=..., y=..., elev=..., w=3, h=3, nominal=False)`
  *(Omitting `w/h` and `nominal=False` creates a 1x1 plot that starves the fort.)*
- **Set all 4 seasons**:
  Call `configure_farmplot(building_id=<id>, crop="MUSHROOM_HELMET_PLUMP", all_seasons=True)` and verify:
  - `plantable: true` and `soil_tiles == 9` (never `UNPLANTABLE`).
  - `spring`, `summer`, `autumn`, and `winter` all show `MUSHROOM_HELMET_PLUMP`.
- Staff at least 1–2 dwarves on `PLANT` (farming) via `work_details` (check `unit_profile(action="skills", skill="PLANT")`).

## 3. Protect Seeds & Automate Crop Cycles

- Run `dfhack_automation(action="apply_preset", preset="early_fort")` (or `gather_and_items(action="protect_seeds")` + `dfhack_automation(action="configure", plugin="autofarm", args=["default", "50"])`).
- **Why this matters**:
  - `ban-cooking seeds` and `ban-cooking brew` prevent cooks from destroying your plump helmets and spawn.
  - **Brewing yields seeds; cooking destroys seeds.** Always brew plump helmets into drink rather than cooking them.
  - `seedwatch` and `autofarm` balance planting automatically once plots are active.

## 4. Pair Still, Barrels & Kitchen

- **Still (`3x3`)**: Place near the farm room and food stockpile (`place_building(building="Still", w=3, h=3)`).
- **Barrels / Rock Pots**: Queue `barrel` (Carpenter's) or `pot` (Craftsdwarf's) via `workshop_job(action="queue")` so brewers have containers.
- **Brewing**: Queue `brew` (`repeat_job=True`) via `workshop_job(action="queue", task="brew", building_id=<still_id>, repeat_job=True)` (or `work_order` once a Manager is active).
  - If `DRINK` falls, prioritize brewing: `dfhack_automation(action="configure", plugin="prioritize", args=["CustomReaction", "--reaction-name", "BREW_DRINK_FROM_PLANT"])`.
- **Kitchen (`3x3`)**: Place nearby for `meal` (`PrepareMeal`) from meat, fish, eggs, and non-seed plants.

## 5. Surface Pastures for Grazing Animals

- Check `alerts_and_wellbeing` for `unassigned_grazers > 0` (yaks, horses, sheep, llamas starve underground on bare stone).
- Create a `Pen/Pasture` on **surface grass**: `create_zone(kind="pen", x=..., y=..., elev=<surface_elev>, w=10, h=10, acknowledge_risk=True)` (gated on `grass_tiles > 0`).
- Assign all grazers with `assign_pasture(building_id=<zone_id>, all_grazers=True)` and verify `unassigned_grazers_remaining == 0`.
