# What playing `start` taught me (measured, this session)

Fort `start`, world *The Universes of Destiny*, DF 0.53.17 linux64 STEAM / DFHack 53.17-r1.
Every line below is something a tool answer, an alert, or the player proved. Tool-gap numbers
refer to [`tool_missing.md`](../tool_missing.md).

**Where this should live (the player's question: skill or tool?)** — for now it is logged here.
Recommendation, once code changes are allowed: **both, split by nature.**
- Rules a **tool can check at write time** belong in the **tool** (site gates + rung acceptance):
  roof/indoors, soil under a farm plot, door-wall flanking, designation overlap, room
  satisfaction. A gate that refuses with the measured reason beats any prose.
- Rules that are **sequencing and judgement** belong in the **skill** (the playbook): "build
  indoors", "stairs before rooms", "direct shop queues before manager orders", "pause only
  when the crew is genuinely idle".
- Rules about **what an answer means** (`usable_tiles: 0`, "success" that attached nothing, a
  rung satisfied by a raw water count) belong in **tool output contracts** — standing rule:
  a success flag is not a fact.

---

## 1. Build the fort under a roof — dwarves hate being outdoors
`look` elev37 over my whole shop/pile band: `open 149`; elev36 strip: `open 445` — the cavern
floor carrying Stoneworker 7, Craftsdwarf 8, Still 11, Carpenter 12, Kitchen 13, Loom 15,
Farmer's 16, Smelter 17, Furnace 18, Forge 19, piles 1/2/3, dump 10 and the *first* meeting
area (zone 4) has open space above = **outdoors**. Player: *"most of your fort is outside,
including the meeting area, the dwarfs don't like to be outside"* (#77).
- The rung "Meeting area **inside the fort**, 2nd level below ground" was marked `[x]`
  satisfied on a tile with sky above it.
- Fix: zone 4 removed (verified by absence); meeting zone **24 @ `55..59,114..118@35`**,
  bedroom zone **31 @ `60..66,114..120@35`**, office zone **27 @ `59..61,109..111@36`** — all
  under rock.
- **Rule:** read the tile column above before placing anything dwarves live, sleep, eat or
  meet in. `look`'s `open` column is the read.

## 2. Doors need walls — design the opening, not just the room
`place_building Door` on open cavern floor: **`FAIL construct: cannot place at this
position`** — a door needs solid walls on two opposite sides.
- Working shape: a **1-tile corridor with undug rock on both flanks**. Room
  `59..61,109..111@36` + corridor `60,112` → **Door 30, `complete: true`**.
- A 1×1 office has no tile for a door → can never be satisfied. Office needs ≥2 tiles:
  furniture inside + door in the neck (Chair 28 + Table 29 + Door 30).
- Doors are walls cave creatures cannot open — with **19 crundles** and 7 wounded dwarves
  (#72), sealed openings are the cheapest defense I have.
- **Rule:** plan openings (1-wide necks with rock flanks) first, then the room behind them.

## 3. A farm plot needs soil, and soil is a property of the layer
`fort_strata(58,117)`: elev35-37 `soil_walls 0`, `layer_class "stone"`; elev38-40 soil
(`soil_walls 35`, `dug_floors 73` at 40; `potential_soil 102` at 39); its own
`recommendations.farm_elev: 38`. My first plot (id 9, `62..64,116..118@35`) was placed on
**stone** and can never grow (#75).
- A dug floor takes the material of the layer **above** → farmable ground is soil-covered rock.
- Three green lights, one dead farm: `place_building` placed it (gate checks dug floor/water/fog,
  never soil); `configure_farmplot` said `success: true` with a perfect 4-season read-back;
  the ladder rung "Farm plot (3×3 in a 5×5 soil room)" was satisfied.
- Fix: soil rooms elev38 `x53-57 y114-118` (`dug 25`) and elev41 `x48-52 y108-112` (`dug 25`,
  the player's pick) → plots **25@41 plump helmets**, **26@38 pig tail**.
- **Rule:** read the strata column for the target elevation before designating a farm room;
  surface plots are seasonal (plump helmets are all-seasons only underground).

## 4. Stairs before rooms; a designation needs a reachable face
349 floors + 72 stairs transition-verified. A face reading `designated N, dug 0` across many
reads is almost always (1) unreachable, (2) no able miner, (3) deprioritized — check in order.

## 5. A later DEFAULT_DIG box silently flattens stair designations
My 7×7 farm box (`x60-66,y114-120@35`) covered the 4 tiles just designated
`UP_DOWN_STAIR_DIG`, turning a shaft bottom into a plain dig → `dig_stalled` A12 (#69).
- **Rule:** designate stairs first, then box the room **around** them. No designation-type read
  exists to catch this (requested).

## 6. Mining priority: the job type is `Dig`, not `Mine`
Five rounds of frozen faces. `prioritize status` showed hauling/brewing/meals prioritized and
no mining; `prioritize Mine 100` answered `Ignoring unknown job type: "Mine"` *and*
`Ignoring unknown job type: "100"`, then printed the list as if nothing failed; `prioritize -r`
(200 lines) is the only way to learn the name. `prioritize Dig` → "Prioritized 19 jobs" (#76).

## 7. Wounds stop a fort; crew diagnostics count flags, not bodies
`wounded 7` of 7 — every miner plus the Manager — while `picks 3`, `bottlenecks: []`,
`recent_cancellations: []` answered "all clear" as nothing dug (#78). Path that works with my
tools: **CMD appointed → cloth → beds → hospital** (7 beds crafted, 2 placed: 32/33).

## 8. Direct shop queues beat manager orders early
`workshop_job(action="queue")` needs no Manager and no office: 2 tables, 3 doors, 6 bins,
3 chairs, 7 beds, 3 jugs, brew and meals. `work_order` stayed `validated: false, active: false`
for many rounds with a wounded Manager → **charcoal (furnace fuel) and squad kit have no
working path** until that flips (#79).

## 9. The task map lies about a few items
`task="wheelbarrow"` queues **minecarts** ("Make wooden minecart") → the stone pile's
`max_wheelbarrows: 3` rung can never flip (#71). `cloth`, `charcoal`, `training_weapon`,
`weapon_rack`, `armor_stand` are absent entirely (#79).

## 10. Furniture is a building object — place the crafted item
`place_building` with `material="chair"/"table"/"door"/"bed"` attaches the **crafted item** and
returns `complete: true` verified by `findAtTile` (Chair 28, Table 29, Door 30, Bed 32/33).
Beds were refused on 3 of 5 tiles with the reasonless `could not construct the building` (#80).

## 11. Buildings: plan route for multi-tile, and the real DF names
`material=` on an item-needing building crashed on the game's assertion
(`!items.empty() == needsItems(bld)`) with 13 boulders in stock (#70); the plan route
(`nominal=false w=3 h=3`) built the Still, shops, plots, Loom, Farmer's. Spellings that work:
`Farmer's Workshop`, `Wood Furnace`, `Metalsmith's Forge`, `Craftsdwarf's Workshop` (#74).

## 12. Verify by the live list, never by the write's reply
Zone removal said `success: true, verdict: "removed"` while the zone sat unchanged (#61); the
proof is absence from `list_zones`. Bed removal answered `verdict: "still present"` (queued,
not done) — verify later (farm-plot census 3 → 2). `usable_tiles: 0` / `holes: 25` is labelled
UNMEASURED by the tool itself — not a fact about usability.

## 13. Pastures: trust the grazers alert; grass is the gate
`alerts_and_wellbeing` reported **6 unpastured tame grazers risking starvation** while the
species census looked all-wild. `create_zone kind=pen` states exactly why a spot fails
(`{'open': 1}`, `{'hidden': 24,'rock': 15,'open': 20}`); a pass means grass was measured.
Pen 5 (`56..60,116..119@40`) holds all 6 (sheep, goat, cavy, rabbit, 2 yaks).

## 14. Pause discipline
Pause only when the crew is genuinely idle. Wounds heal with time, jobs need time, crops grow
with time — with 7 wounded, 82 queued jobs and growing plots the right call was **unpaused**,
re-running the game whenever it self-paused with work queued.

## 16. A separator needs at least one unexcavated row — my "three rooms" were one room
Player: *"to have a separator you need to keep at least one row of fog"*. My noble rooms at
elev36 were laid back to back with **no rock between them**: Manager `59..61,109..111`,
Bookkeeper `62..64,109..111`, Broker `65..67,109..111` — contiguous dug columns, so the three
"rooms" plus their `y112` necks form **one connected space with three doors**, not three
enclosed rooms. A door only separates what walls enclose.
- Fix applied: `NO_DIG` on `x65-67,y109-112` (12 tiles → that block becomes the separator
  wall) and the Broker's room re-sited to **`x69-71,109..111`** with a 1-tile neck at `70,112`.
  Between Bookkeeper (`x62-64`) and Broker (`x69-71`) the rock stays unexcavated (`x65..68`) —
  a real 4-column separator.
- Honest residue: Manager `x59-61` and Bookkeeper `x62-64` are already contiguous, so those two
  **share** one room; I do not claim them as separate enclosed rooms.
- **Rule:** leave **≥1 unexcavated column/row between rooms** (fog is the material you are not
  removing). Check it before designating, not after.
- **Tool gap (#86):** nothing in my surface reports room enclosure. `create_zone` accepts a
  room-typed zone anywhere dug (#85); `survey`/`look` never say "these two dug areas are
  contiguous"; `place_building Door` answers only a bare refusal (#80/#82). Requested: an
  enclosure/separation verdict per zone footprint (which sides are solid rock, separator
  width, neck width, door count, contiguous neighbours), at `create_zone` time and as a pre-dig gate.

## 17. Keep buildings off the outer wall — ramps there are openings
Player: *"building too close to the outside wall may have ramp openings"*. Confirmed on this
fort: at elev36 the cavern side (`x73+`) is full of `^` ramp-top / `/` ramp tiles, and `look`
reports `open 445` on elev36 with `open 149` at elev37 over that band — so a footprint near the
edge sits on slopes that **open to the outside/cavern**, exactly where my 19 crundles live.
Evidence from this round: `place_building Mechanic` at `x70-72,109-111@36` → site gate
`{'rock': 3}` (it straddled the new Broker room's undug wall); moving it to `77,113` (right by
the cavern opening) → `constructBuilding refused: cannot place at this position` (#82's
reasonless refusal); the clean, interior site `74,125` built fine —
**Mechanic's Workshop 49, `74..76,125..127@36`**.
- **Rule:** place shops/piles/rooms **inward** — check the footprint for ramp/ramp-top tiles
  and for open space above (outdoors), and prefer sites with solid rock on the outside side.
- **Tool gap:** the site gate reports `rock/hidden/open/water` and refuses ramps for zones,
  but nothing reports "this footprint opens to the cavern/outside via ramps", and the construct
  path refuses without reason (#82/#80). Requested: a ramp-opening / exposure verdict per
  footprint (ramp counts, which side opens, distance to open space), same place as the
  enclosure check in #86.
- Note: `Mechanic` is an accepted def name, so the Mechanic's shop is buildable — which is the
  prerequisite for mechanisms → floodgates/pumps, the real answer to the water breach (A51,
  `water_tiles 1088 -> 1130`). Interim dam: **Door 48 at `80,110@35`** in the 1-wide adit.

## 18. A neck that slopes down is a ramp, not a doorway — you cannot door it later
Player's ramp rule, second half. For the Broker's room I made the textbook shape: 3×3 room at
`69..71,109..111@36` with a 1-tile neck at `70,112`, behind a real rock separator (`x65-68`).
The result, measured: **`70,112` and `71,112` came out as ramps (`/`)** — because the tunnel at
`y113` sits a level below, the game slopes the floor — so the entrance is a 2-wide ramp neck
with no solid wall flanks, which is precisely what `place_building Door` refuses.
- Manager office: neck `60,112`, rock flanks `59,112`/`61,112` → **Door 30 ✓**.
- Bookkeeper office: neck `63,112`, rock flanks `62,112`/`64,112` → **Door 43 ✓**.
- Broker office: Chair 50 + Table 51 + zone 52 (owner Aban) ✓, **no door** — the neck is a ramp.
- **Rule:** plan the neck on a row where the tile below is **at the same level** (no drop), so
  it stays flat floor with rock on both sides. A neck facing down-slope becomes an opening you
  can never close, and dug ramps cannot be undone.
- Tool side: `survey` reports the ramp count (`ramp: 2`) — reading the neck row *before*
  digging the room is the check; the tools still never say "this footprint opens via a ramp"
  (#86), and the door refusals stay reasonless (#80/#82).

## 19. The Bookkeeper is what makes stock numbers mean anything
Player: *"with a bookkeeper your stock numbers should be more accurate"*. True in the rules and
visible in my own reads: every stock line I have is labelled **"ON WATCHED PILES, not the
game's fort-wide Food/Drink bars"** — e.g. `food 17, drink 0, wood 63, stone 68` from
`fort_progress`, and the same numbers disagree with `workshop_job stock`, which reported
`FOOD: 0` in the same period. Both are true because they measure different scopes: pile
contents vs an item-type scan.
- What the Bookkeeper changes: the game keeps **stockpile records** (the `UpdateStockpileRecords`
  job), and a Bookkeeper with a satisfied office + maximum precision makes those records
  current. `bookkeeper_settings: 4` is already set; Minkot has office zone 44 (Chair 41 +
  Table 42 + Door 43), so the seat is staffed with the right aptitude (`RECORD_KEEPING 5`).
- Action taken: `prioritize UpdateStockpileRecords` → "Prioritized 0 jobs" (no record job
  pending right now; the prioritization is registered for when they appear).
- **Tool gap (#88):** no tool reads the game's *own* fort-wide stock statistics, nor reports
  whether the bookkeeper is satisfied and at what precision, so my "stock" numbers stay
  pile-scoped and mutually contradictory. Requested: `GET /fort-stock` returning the game's
  food/alcohol/wood/stone/metal counts with the source named (fort bar vs pile census vs item
  scan), plus `bookkeeper: {unit, satisfied, precision}` so I stop quoting numbers whose scope
  I have to explain in prose.

## 15. Automation to keep on (read back live, not assumed)
`autofarm` (plump 30/50), `seedwatch` (5/30 on 5 crops), `ban-cooking seeds+brew` (12+20),
`suspendmanager`, `buildingplan`, `prioritize`, `tailor`, `autonestbox`, `autoslab`,
`preserve-rooms/tombs`, `logistics`, `autochop target 60 30` — note `autochop 30 60` is
`CR_WRONG_USAGE`; it needs the `target` subcommand.
