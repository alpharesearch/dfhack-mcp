---
name: df-quickstart
description: The Dwarf Fortress Quickstart Dependency Graph & Execution Playbook (from FlowchartDF.png and the DF Wiki Quickstart Guide), mapping every resource, workshop, stockpile, zone, noble, and goal node to exact dfhack-mcp tools and prerequisite checks.
whenToUse: When starting a new fortress, deciding what to dig/build/queue next in the Quickstart sequence, diagnosing why a downstream workshop or noble is blocked by a missing upstream input, or progressing from embark to farming, brewing, furniture, offices, healthcare, trading, metalworking, and fortress defense.
metadata:
  source: https://dwarffortresswiki.org/images/e/e6/FlowchartDF.png and https://dwarffortresswiki.org/index.php/Quickstart_guide
  tools: look, fort_progress, alerts_and_wellbeing, set_pause_state, world_map, survey, fort_strata, fort_plan, send_dig_command, find_trees, cut_trees, find_water, gather_and_items, place_building, remove_building, building_status, configure_farmplot, workshop_job, create_stockpile, configure_stockpile, list_stockpiles, create_zone, list_zones, assign_zone_owner, assign_pasture, work_details, manage_nobles, work_order, trade_depot, dfhack_automation
---

# Dwarf Fortress Quickstart Dependency Graph & Execution Playbook

Source flowchart: <https://dwarffortresswiki.org/images/e/e6/FlowchartDF.png> (companion to <https://dwarffortresswiki.org/index.php/Quickstart_guide>).

You are controlling a live Dwarf Fortress fortress via `dfhack-mcp` (32 MCP tools). **Never build or queue a downstream node before its upstream prerequisites in this dependency graph exist.** Before acting, run `look`, `fort_progress`, and `alerts_and_wellbeing`; after acting, verify completion with `survey` (for digging), `building_status(only_incomplete=True)` (for construction—ignores completed zones/stockpiles), `work_order(action="list")` (for production), or `trade_depot(action="status")` (for trade). (`transitions`, `hazards`, `depth_profile`, `unit_labors` are HTTP-only diagnostics; see `docs/tool-notes.md`.)

---

## 0. Global Embark Setup (`Start` & Grey System Nodes)

1. **Pause & Survey**: Call `set_pause_state(paused=True)`, then `look`, `fort_progress`, and `alerts_and_wellbeing`.
2. **Staff the work details (`(y) Labor menu`, the green tick marks)**: Call `list_units` for the
   unit ids, then `GET /work-details action=assign detail=Miners unit_id=<id> mode=add` for each
   dwarf who should mine. `GET /work-details action=list` shows every detail with the unit ids it
   contains, and `GET /unit-labors?labor=MINE` is the read-back of the effective flags.
   > `set_unit_labors` is **removed** (the player's decision, 2026-11-22): it wrote
   > `unit.status.labors`, a layer the game **recomputes from the work details** - measured, a census
   > of 5 `MINE` holders fell to the 2 ticked dwarves the moment a tick was added, and the tool's
   > `applied: N` only validated labor *names*. Staffing means the detail; the flags follow it.
   Cover:
   - `MINE` (2–3 dwarves; check `building_status` `diagnostics.crew.picks` so every miner has a pick)
   - `CUTWOOD` (1 dwarf; requires a battle axe)
   - `CARPENTER`, `MASON`, `PLANT` (farming), `HERBALISM` (plant gathering), `BREWER`, `COOK`, `BUTCHER`, `MECHANIC`, and stone/wood crafting.
3. **Enable Safe DFHack Automation & Protect Seeds**: Call `dfhack_automation(action="apply_preset", preset="early_fort")` immediately. This enables `autofarm`, `seedwatch`, `ban-cooking` (seeds & brew, equivalent to `gather_and_items(action="protect_seeds")`), `suspendmanager`, `buildingplan`, `autonestbox`, `autoslab`, `prioritize`, and `tailor`.
4. **Appoint Skilled Core Nobles (`(n)oble menu`)**: Call `manage_nobles(action="candidates")` to inspect citizen skill scores (`ORGANIZATION` for `MANAGER`, `APPRAISAL`/`NEGOTIATION` for `BROKER`, `RECORD_KEEPING` for `BOOKKEEPER`, medical skills for `CHIEF_MEDICAL_DWARF`), then call `manage_nobles(action="auto_assign", reassign_unskilled=True)` so skilled dwarves are appointed instead of round-robin picks.

### Early-Game Manager & Barrel Bootstrap (before `work_order` can run)
Manager work orders cannot validate until a **Manager** has an **Office** with a **Chair**, and placing a Chair requires a **crafted Chair item** in stock:
1. **Get early wood if needed**: break up the Wagon (`remove_building(building_id=0)`) for 3 wood logs (~6s unpaused; immediate reply may say `still present` until dwarves disassemble it—confirm `WOOD >= 3` via `workshop_job(action="stock")`), or fell nearby trees (`find_trees(..., near_x=..., near_y=...)` + `cut_trees`).
2. **Build starter workshops**: find clean dug/flat `3x3` sites via `fort_plan(action="find_site", elev=..., width=3, height=3)` and place `Carpenter`, `Stoneworker`, and `Still` (`place_building`).
3. **Craft Chair & Barrels/Pots directly**: queue at the workshop via `workshop_job(action="queue", task="chair", material="wood", count=1)` and `workshop_job(action="queue", task="barrel", count=2)` (or `MakeRockPot` at Stoneworker's). `workshop_job(action="stock")` counts free furniture AND usable contained `DRINK`/`FOOD`/`SEEDS`.
4. **Place Chair & assign Office**: once `items.CHAIR >= 1`, place the crafted chair (`place_building(building="Chair", ...)`—attaches the crafted item and rejects raw boulders), create an `office` zone (`create_zone(kind="office", ..., acknowledge_risk=True)`), and assign the skilled `MANAGER` (`assign_zone_owner(building_id=..., unit_id=...)`).
5. **Queue orders & scale**: queue `work_order(action="create", job="MakeBarrel", material="wood", amount=2)` (validated on the Manager's next office check tick), and use `fort_plan(action="qf_status")` / `qf_run` once ready for Quickfort blueprints.

---

## 1. Root Resource Extraction (Olive Designation Nodes)

Every production chain in the flowchart stems from two initial designations:

- **`Dig`** (`Labor: Mining` → produces **`Stone`** + underground rooms):
  - **Tool**: `fort_strata` -> `fort_plan` (`bootstrap_camp` first -> `find_site` -> Quickfort `qf_status`/`qf_run` once bootstrapped) or `send_dig_command` (`box`/`boxes` with `middle_first=True`, or `stair_shaft`).
  - **Gate & Verify**: Check `fort_strata` (aquifer/water/magma/soil) and `survey` before digging; verify excavation via `survey` (`dug > 0 and to_dig == 0`).
  - **Unlocks**: Underground soil/mud rooms for **`Farm plot`**, plus raw **`Stone`** boulders for **`Wall`**, **`Stoneworker's workshop`**, **`Mechanic's shop`**, and **`Craftdwarf's workshop`**.
- **`Chop trees`** (`Labor: Wood cutting` → produces **`Wood`**):
  - **Tool**: `find_trees(elev0=..., elev1=..., near_x=..., near_y=...)` (sorts closest clusters first) → `cut_trees(x0=..., y0=..., x1=..., y1=..., elev=..., limit=10)`.
  - **Unlocks**: Raw **`Wood`** logs for **`Carpenter's workshop`** and **`Wood furnace`**.

---

## 2. The Wood Chain (`Wood` → Carpentry & Charcoal)

**Prerequisite**: `Wood` logs on the ground (`cut_trees`).

1. **`Carpenter's workshop`** (`Labor: Carpentry`)
   - **Build**: `place_building(building="Carpenter", x=..., y=..., elev=..., w=3, h=3)`
   - **Downstream Items (queue via `work_order`)**:
     - **`Barrel`** (`work_order(job="MakeBarrel", amount=10)`) → **Required by `Still` to brew `Booze`** and by `Food pile`
     - **`Beds`** (`work_order(job="ConstructBed", amount=8)`) → **Required by `Bedrooms` and `Hospital`**
     - **`Bins`** (`work_order(job="ConstructBin", material="wood", amount=10)`) → **Required by `All stockpiles`**
     - **`Buckets`** (`MakeBucket`) & **`Splint/Crutches`** (`ConstructSplint`, `ConstructCrutch`) → **Required by `Hospital`**
     - **`Training weapons`** (`MakeTrainingWeapon`) → **Required by `Squad`**
2. **`Wood furnace`** (`Labor: Furnace operating`)
   - **Build**: `place_building(building="WoodFurnace", x=..., y=..., elev=..., w=3, h=3)`
   - **Downstream Item**: **`Charcoal`** (`work_order(job="MakeCharcoal", amount=10)`) → **Required fuel for `Smelter` and `Forge`**.

---

## 3. The Farming, Brewing, Food, Pasture & Cloth Chain

**Prerequisite**: Excavated room (`Dig`) on soil or muddy stone.

1. **`Farm plot`** (`Labor: Farming (fields)`)
   - **Build**: `place_building(building="Farm Plot", x=..., y=..., elev=..., w=3, h=3)`
   - **Configure**:
     - Food/Booze plot: `configure_farmplot(building_id=..., crop="MUSHROOM_HELMET_PLUMP", all_seasons=True)` → produces **`Plump helmets`**
     - Cloth/Thread plot: `configure_farmplot(building_id=..., crop="GRASS_TAIL_PIG", all_seasons=True)` → produces **`Pig tail`**
   - **Surface Gathering Supplement**: Call `gather_and_items(action="gather_plants", x0=..., y0=..., x1=..., y1=..., elev=...)` on surface shrubs while waiting for the first harvest.

### Branch 3A: `Plump helmets` + `Animal pasture` → `Booze` & `Meals`
- **Underground Farm Plot (exact 4-step workflow)**:
  1. Call `fort_plan(action="find_site", kind="farm", elev=<farm_elev>, width=3, height=3)` to get ready soil `sites` or `dig_to_soil_sites` (one level below soil, where digging reveals soil).
  2. If needed, dig a connecting tunnel + one `dig_to_soil_sites` box (`send_dig_command`), wait for miners, then `place_building(building="Farm Plot", x=..., y=..., elev=..., w=3, h=3)`.
  3. Set seasonal crops: `configure_farmplot(building_id=..., crop="MUSHROOM_HELMET_PLUMP", all_seasons=True)` (and keep `autofarm` enabled via `dfhack_automation`).
- **`Still`** (`Labor: Brewing` → produces **`Booze`**):
  - **Prerequisites**: `Plump helmets` + empty **`Barrel`** (from `Carpenter's workshop`) or **`Stone pots`** (from `Stoneworker's` / `Craftdwarf's`).
  - **Build & Queue**: `place_building(building="Still", x=..., y=..., elev=..., w=3, h=3)` → `work_order(action="create", job="brew", amount=5)` (`BREW_DRINK_FROM_PLANT`), or `workshop_job(action="queue", task="brew")` before a Manager exists.
- **`Animal pasture` → `Butcher's shop` & `Kitchen` → `Meals`**:
  - **Pasture Grazing Animals**: `create_zone(kind="pen", x=..., y=..., elev=..., w=10, h=10, acknowledge_risk=True)` on **surface grass** (`grass_tiles > 0` — never on bare stone or dug underground floor, which starves grazing animals; `create_zone` rejects 0-grass sites) → `assign_pasture(action="assign", all_grazers=True)`.
  - **`Butcher's shop`** (`Labor: Butchery`) & **`Kitchen`** (`Labor: Cooking`): `place_building(building="Butcher" | "Kitchen", w=3, h=3)` → `work_order(job="PrepareMeal", amount=10)`.
  - **Stockpiles**:
    - **`Food pile`**: `create_stockpile(x=..., y=..., elev=..., w=5, h=5, allow=["food"], max_barrels=15)`
    - **`Refuse pile`**: `create_stockpile(x=..., y=..., elev=..., w=5, h=5, allow=["refuse", "corpses"])` (outdoors or behind doors to prevent miasma).

### Branch 3B: `Pig tail` → `Thread` → `Cloth` → `Clothes` & `Hospital`
1. **`Farmer's workshop`** (`Labor: Plant processing`): `place_building(building="FarmersWorkshop", w=3, h=3)` → `work_order(job="ProcessPlants", amount=10)` → produces **`Thread`** (feeds `Loom` and `Hospital`).
2. **`Loom`** (`Labor: Weaving`): `place_building(building="Loom", w=3, h=3)` → weaves `Thread` into **`Cloth`** (feeds `Clothier's shop` and `Hospital`).
3. **`Clothier's shop`** (`Labor: Clothier`): `place_building(building="ClothiersShop", w=3, h=3)` → makes **`Clothes`** → feeds **`Finished goods`** for **`Trade`**.

---

## 4. The Stone, Furniture, Rooms, Nobles, Hospital & Crafts Chain

**Prerequisite**: `Stone` boulders from `Dig`.

1. **`Stoneworker's workshop`** (`Mason`, `Labor: Masonry`)
   - **Build**: `place_building(building="Mason", x=..., y=..., elev=..., w=3, h=3)`
   - **Produce `Furniture` via `work_order`**: **`Tables`** (`ConstructTable`), **`Thrones`** (`ConstructThrone`), **`Doors`** (`ConstructDoor`), **`Armor stands / Weapon racks`** (`ConstructArmorStand`, `ConstructWeaponRack` → **`Barracks`**), **`Coffins`** (`ConstructCoffin`), **`Cabinets`** (`ConstructCabinet`), **`Chests`** (`ConstructChest`).
   - **Store**: **`Furniture pile`** (`create_stockpile(..., allow=["furniture"])`).
2. **Zones, Nobles & Early Goals (Green, Purple & Red Nodes)**:
   - **`Office`**: Place a `Table` + `Chair` via `place_building`, then `create_zone(kind="office", x=..., y=..., elev=..., w=3, h=3, owner_unit_id=<manager_unit_id>, acknowledge_risk=True)` (or `assign_zone_owner`) for **`Manager`** and **`Bookkeeper`**.
   - **`Dining hall`**: Place `Tables` + `Thrones`, then `create_zone(kind="dining", x=..., y=..., elev=..., w=5, h=5, acknowledge_risk=True)`.
   - **`Bedrooms`**: Place `Beds` (from `Carpenter's workshop`) + `Doors`, then `create_zone(kind="bedroom", x=..., y=..., elev=..., w=3, h=3, acknowledge_risk=True)`.
   - **`Remove embark wagon`** - **a wood decision, not an approval question** (the player's rule,
     2026-11-22): *"no need to ask the player, if you need the wood break up the wagon, if there are
     lots of trees don't bother."* So: check the wood supply and the trees first (`find_trees`,
     `alerts_and_wellbeing` wood count, your `wood` pile). If trees are plentiful, leave the wagon
     standing - it anchors the caravan route and the next wagon needs the 3-wide path. If the fort
     needs logs now (barrels/bins/beds/buckets are queued behind wood and choppings are far away),
     take it apart via `remove_building` (it yields 3 logs) and verify the logs land on the wood pile.
3. **`Hospital` (Orange Location Node)**:
   - **Converging Prerequisites**: `Chief Medical Dwarf` (`manage_nobles`) + `Thread` + `Cloth` + `Buckets` + `Splint/Crutches` + `Beds` + `Tables` + `Chests`.
4. **`Craftdwarf's workshop`** (`Labor: Crafting`)
   - **Build**: `place_building(building="CraftsdwarfsWorkshop", x=..., y=..., elev=..., w=3, h=3)`
   - **Outputs**:
     - **`Stone pots (replacement for barrels)`**: `work_order(job="MakePot", amount=10)` → feeds `Still` and `Food pile`.
     - **`Wooden/Bone Ammo`**: `work_order(job="MakeAmmo", amount=10)` → **`Ammo stockpile`** (`create_stockpile(..., allow=["ammo"])`).
     - **`Craft`** (`work_order(job="MakeCrafts", amount=15)`) → feeds **`Finished goods`** for **`Trade`**.

---

## 5. Trade, Wealth, Metal Industry & Fortress Defense

### A. `Trade` → `Wealth` → `Migrants` → `Specialized workforce` & `Squad`
- **Prerequisites**: **`Finished goods`** (`Craft` + `Clothes`) + **`Trade depot`** (`place_building(building="TradeDepot", w=5, h=5)`, verified with `wagon_path`) + **`Broker`** (`manage_nobles`).
- **Execute**:
  - Haul trade goods to the depot: `trade_depot(action="mark_goods", min_value=15, max_items=25)`.
  - When `alerts_and_wellbeing` reports `caravan_present`, summon the Broker: `trade_depot(action="request_broker", trader_requested=True)`.
  - When the barter screen opens: `trade_depot(action="barter_status" | "barter_select")`.
- **Outcome**: `Trade` builds **`Wealth`** → attracts **`Migrants`** → enables **`Specialized workforce`** and **`Squad`**.

### B. Metal Industry (`Smelter` + `Forge` → `Make metal weapons/armour`)
- **Fuel Path**: `Wood furnace` → **`Charcoal`** → **`Smelter`** (`place_building(building="Smelter", w=3, h=3)`) & **`Forge`** (`place_building(building="MetalsmithsForge", w=3, h=3)`).
- **Magma Path (`Seek magma`)**: Probe depth with `magma_depth` → build **`Magma smelter`** & **`Magma forge`**.
- **Outputs**: **`Make metal weapons/armour`** (`SmeltOre`, `MakeWeapon`, `MakeArmor`) → **`Armor stockpile`** (`allow=["armor"]`), **`Weapon stockpile`** (`allow=["weapons"]`), and **`Upgrade squad`**.

### C. `Prepare fortress defense` → `Dig deep`
- **Mechanical & Structural Defense**: `Stone` → **`Wall`** + **`Mechanic's shop`** (`place_building(building="Mechanic", w=3, h=3)`) → **`Mechanisms`** (`work_order(job="ConstructMechanisms", amount=10)`) → **`Levers`** & **`Traps`** + **`Bridge`**.
- **Military Defense**: `Armor stands / Weapon racks` → **`Barracks`** (`create_zone(kind="barracks", ..., acknowledge_risk=True)`) + `Migrants` + `Training weapons` + `Upgrade squad` → **`Squad`**.
- **Final Goal (`Dig deep`)**: Once defense is ready, sink a deep `stair_shaft` via `send_dig_command`, gating each depth band with `hazards` and `magma_depth`.
