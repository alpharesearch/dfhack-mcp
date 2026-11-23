---
name: df-placement
description: Decide what goes where in a Dwarf Fortress fortress - the quickstart spine (entrance, stairwell, depot, piles), which rooms must be adjacent and which must be far apart, the wiki's room sizes, the noise/miasma/aquifer/cave-in placement rules, and the layout rules this site measured in play.
whenToUse: When planning a fortress layout: choosing a level for a room, deciding where the stairs, piles, workshops, farm, still, kitchen, bedrooms, dining, meeting area, depot, well, bridges and refuse go, or why a district works on paper and fails in the game.
metadata:
  source: condensed from dwarffortresswiki.org (Quickstart, Design strategies, Zone,
    Noise, Stockpile, Workshop, Trade depot, Well, Reservoir, Aquifer, Cave-in, Bridge,
    Floodgate, Moat, Miasma, Garbage dump, Pasture, Stress, Dining room, Office, Health
    care) plus three embarks played on region1 with the dfhack-mcp layer
  tools: survey, box_state, hazards, depth_profile, tile_at, look, create_stockpile,
    configure_stockpile, create_zone, place_building_plan, send_dig_command, fort_progress
---


# What goes where (DF 0.50.17+ / 53.17, wiki-condensed + measured in play)

Condensed from dwarffortresswiki.org (Quickstart guide, Design strategies, Zone, Noise,
Stockpile, Workshop, Trade depot, Well, Reservoir, Aquifer, Cave-in, Bridge, Floodgate,
Moat, Miasma, Garbage dump, Pasture, Stress, Dining room, Office, Health care) and from
three embarks played on this site. Anything the wiki does not state is marked
**UNCONFIRMED** - do not build a plan on those.

## 1. The spine comes first

One entrance, one stairwell, everything hangs off them.

- **Entry tunnel**: 1 tile wide at the mouth, widening to **3 tiles** from the first room
  outward (the wagon needs 3). Put it next to the wagon - shorter hauling. 10-20+ tiles
  with **rock above** (the tunnel is the fort's only defended opening; a surface cut is
  not a tunnel).
- **Stairwell is the spine**: one 1x1-2x1 stair per floor is plenty (the wiki calls a 3x3
  staircase overkill). Vertical travel costs a tile of horizontal walking, so stack
  industries by level and keep the stair in the middle (hub and spokes).
- **Trade depot**: 5x5 room, >=10 tiles from the map edge, joined by a **3-wide** passage
  with **3-wide turns**, plus a **3x4 parking area** beside it. Blocked by: stairs (use
  ramps), doors (even open, even lever-controlled), traps/plates, boulders, tracks.
  Build **one** depot; keep a pile on the depot's z-level (purchased goods get stuck).
- **General pile**: right off the stairwell, on a dug level. Everything except Corpses,
  Refuse, Stone, Gems, Wood. 11x11 is the wiki's "reasonable".
- **Corridors**: 1 wide is legal, **2-3 wide for hallways** people and haulers actually use.

## 2. Dwarves live in zones, not in squares

In v50 a "room" is a **zone** drawn over a dug, walled, roofed area. The negative thoughts
are specific: sleeping without a proper zone, sleeping on dirt/grass, eating without a
proper dining room, eating at a crowded table, at a table with no chair and orthogonally
adjacent table. So the daily-life cluster wants to be **underground, dry, furnished**:

- **Bedrooms**: 2-4 tiles each (3 = bed + cabinet + coffer). Smooth walls/floor *before*
  furniture.
- **Dining room**: a big communal hall (the wiki's 300-dwarf test: 15x20 dug space, one
  table counted as a dining room). Tables+chairs for ~1/5 of the population, chair
  orthogonally adjacent to its table. Do **not** put tables/chairs in commoner bedrooms -
  they eat there and skip the dining memory.
- **Meeting area**: 5x5-7x7, the first zone you dig, on the level ~2 below the stockpile
  level, in the fort's core - idle dwarves, migrants and pets congregate there, which
  keeps them inside and safe. Leave it roofed if you want cave adaptation (sunlight in a
  meeting area blocks it).
- **Offices**: 1x3-5x5, chair + table, one per office (overlapping zones drop value to 0).
  Manager and Bookkeeper **need** an office to work. Put them near the bedrooms/piles.
- Overlapping zones and furniture in the open are the two ways a "room" silently does
  nothing. Shared walls and doors are fine.

## 3. Adjacencies that matter (measured and wiki-stated)

| pair | rule |
|---|---|
| kitchen - farm - still | the wiki's food block: kitchen 5x5 in the middle of the food-storage room, on the dining level; **still 3x3 directly connected to the farm**; farm underground on soil, 3x3 plot in a 5x5 room, plump helmets all seasons |
| butcher - kitchen | butcher behind/beside the kitchen, in a **door-closed room** (meat rots, miasma follows) |
| tannery - butcher | hides rot: keep them adjacent |
| carpenter - wood pile | barrels, bins, beds, mechanisms: keep it beside the wood pile |
| stoneworker - stone pile | blocks, doors, floodgates, millstone, quern |
| mechanic - tables + wood/stone + rope | queue 2 tables + 2 chairs first; mechanisms need the mechanic |
| smelter - forge | bars, flux and fuel beside the smelter; forge takes bars + fuel; fuel is fetched **last**, so a feeder pile without fuel silently kills a furnace |
| kiln - clay/gypsum + fuel | NOT plain stone (pearlash needs potash: ashery -> farm) |
| mill/quern - farm | quern (1x1, no power) beside the farm with a small plants-only pile |
| garbage dump (1x1) - stoneworker + mechanic | the wiki treats it as mandatory; dumped items become forbidden; put it in the workshop cluster |
| pasture | on grass, ~10x10 for the wagon's draft animals (mountain-biome grass never regrows; >half bare = overloaded); 1x1 pen at the entrance for non-grazers (early warning) |
| refuse pile | **outdoors**, >=5x5, corpses+refuse only, no bins/barrels/wheelbarrows |
| hospital | a corner of the meeting area: beds in corners, surgery table adjacent to a bed, 1-2 traction benches, 2 chests, well nearby, soap+water+thread+cloth, doors around the beds; in the quiet zone |

Workshops are 3x3 (siege workshop and vermin catcher 5x5; quern/millstone/screw press 1x1).
The wiki's ideal workshop room: **5x5 room, 3x3 shop in the middle, 16 tiles of input
storage around it** - a feeder pile of >=3x3 per workshop.

## 4. Noise - only five jobs are loud

Current wiki: **workshops make no noise** - forge, kiln, smelter, mill, glass, stone shops
are safe beside bedrooms. The five noisy jobs: digging (8), wood-cutting (8), engraving (4),
carving fortifications (4), removing constructions (8). Noise spreads as a **cube across
z-levels** (8-tile noise = 9x9 across 9 floors) and **walls/doors do not insulate**.

Quiet-quarters rule: bedrooms, hospital, jail, barracks **>=8 tiles** from where digging and
wood-cutting happen, and never directly above/below a dig site (the wiki's alternative:
bedrooms 9 z-levels below the surface). Smooth bedroom walls before furniture. Bury your
dead - ghosts howl and that counts as noise.

## 5. Water

- **Well 1x1**: over an open tile with a **clear vertical line to water >=3/7 deep**;
  many z-levels below is fine; **it does not work through a staircase**; a grate/bars/hatch
  between well and water disables it. 10 draws remove 1/7 of the tile below, so a source
  must be fed. Build it **1 z-level above** a freezing outdoor source (ice deconstructs it).
- **Cistern/reservoir**: a dug room sealed by wall/floodgate/bridge; **20x25x1 underground
  lasts years**; deeper than 1 z-level avoids muddy water; a light aquifer above self-fills
  it (~4 water/tile/month).
- **Screw pump 2x1**, needs 4 tiles in a row, lifts 1 z-level per pump (a stack),
  **powered pumps only** (windmill/waterwheel), adjacent pumps pass power.
- Wounded dwarves drink **only water** - the hospital needs a clean source (stagnant and
  salty water must be pumped to be purified).

## 6. Defense placement

- One entrance, all defenses concentrate there: alternating stone-fall + cage traps lining
  the entry - **never on the wagon route**.
- **Raising drawbridge**: the arrow points at the hinge (the side that becomes the wall).
  Raised = a 1-z wall immune to building destroyers; bridges give **no structural
  support**; a link needs 3 mechanisms; put the lever next to the meeting area.
- **Dry moat**: 3-wide trench + 3-wide bridge over it; smooth (unclimbable) walls, a
  2-tile lip overhang, pave the bottom, keep it >1 tile wide (a 1-tile gap is jumpable).
- **Floodgate 1x1** on a flat unflooded floor with adjacent walls to dam against; jams on
  any item; a raising bridge is usually the better tool.
- **Doors 1x1** need a cardinally adjacent wall at designation: use them for internal
  lockdown (dining, butcher, bedrooms) - **never on the trade route**.

## 7. Ground, water and roof safety

- **Aquifer**: leaks N/S/E/W and below (not above, not diagonally). Dig **2 z-levels below
  the lowest aquifer layer**; only *rough* walls leak, so smooth/wall the same tiles on
  every damp z-level you use. Leave **at least one wall tile between your dig and water**;
  you may dig one z-level under a water body, tile by tile.
- **Magma**: adjacent tiles heat (~10075 degU = "warm"); non-magma-safe buildings and
  mechanisms melt; a 1-wide unpressurized feed covers ~3x20 before evaporating.
- **Cave-ins are connectivity, not thickness** (v50 has no "N levels of rock" rule -
  **UNCONFIRMED** as a number): tiles support along X/Y/Z only; diagonals and bridges
  support nothing; ramps support same-level neighbours, not the tile above; up-stairs and
  supports act as an invisible floor for the level above. Practical: channel top-down one
  level at a time, never channel floors that carry terrain above, one channeller per area,
  no checkerboard floor removal.
- **Miasma** forms on **subterranean** tiles from rotting corpses/food and spreads
  N/S/E/W/up/down (not diagonally). That is why the refuse pile is outdoors. If refuse
  must be indoors: its own room, doors, a roof opening, or a chute several z-levels down.
  Barrels/pots preserve stockpiled food (stockpiled, traded and carried food never rots).

## 8. Measured on this site (played, not read)

- **The pile room needs its own stairs.** A general pile the crew cannot walk to is a
  painted rectangle: put the stair columns *inside* the pile room's footprint (two UP_DOWN
  columns for haul traffic beat one), and never leave the room sealed by the dig plan.
- **Designate stair tops UP_DOWN, not plain DOWN.** A down-stair on the top tile asks the
  miner to stand on a tile that does not exist - the fort ran 20 in-game days with three
  idle miners while the room below the surface never started. The player's helper-stair
  (a second column) is the fix when the top is unreachable.
- **A pile needs dug floor under every tile of its footprint.** `open` is cavern air; the
  game accepts piles there and they float. A ramp top IS ground.
- **Furrowed soil is a dug floor** (a farm plot does not destroy your room count), and
  **soil floors are luck**: a floor is soil when the wall one level below is soil.
- **No stone pile in early game** - on a 7-dwarf embark it buys hauling labour and nothing
  else. Add one when a dig face out-produces the general pile (with Max wheelbarrows=3).
- **"A pile exists" is not "the general pile exists"**: check categories (>=8 accepted,
  none of stone/wood/corpses/refuse/gems).
- Farm the level you can reach from the stairwell; the kitchen and dining want to be on the
  same level as the bedrooms; the workshops in their own district near the pile that feeds
  them; the meeting area in the core; the depot on the surface level with its 3-wide road.

## 9. UNCONFIRMED - do not plan on these

Distilling as "sacks + water"; vanilla toilets/bathrooms/sewers; "kitchen needs water";
"forge is 4x4"; a broker office requirement; "sympathy" as a mechanic (v50 uses zone value
ranks: Meager -> Royal, and noble demands); master-bedroom adjacency preferences; a
structural cover number for corridors; "Comfort" as a wiki mechanic.

## Well, measured on a live fort (Y101, this site)

The well is **not** the hard part; the well's *materials* are. Built here with the tools:
* Site rule worked as written: 1x1 on a floor/ramp-top at 36 with water 7/7 directly below at
  35 (`160,116 @36` over the corridor; the pool sits at `x169-172 y116 @35`).
* The game then answered: **"The dwarves were unable to complete the Well."** and
  **"Metalcrafter cancels Construct building: Needs mechanisms."** A well is a *mechanism*
  build - so the water rung is gated behind the **metal** rung: furnace + smelter + iron ore.
  Do not conclude the site is wrong when the build fails; read the cancellation text.
* Digging a tunnel at the same elevation as a pool makes an **aqueduct**: this fort's shaft at
  35 y128 went from dry to 4/7 then 7/7 water along 90+ tiles (water_tiles 1030 -> 1161),
  while the rooms one level up stayed dry. Expect water to fill any level you open into it,
  and plan hauling (a 7/7 tunnel slows/stops hauling) before you need that tunnel.

### Do not open the pool by walking dwarves into it (measured on this site)

The pool at `x169-172 y116 @35` is **7/7 water on ramps**, and the level above (`@36`) is dry
cavern floor with ramp-tops - so the "free" water access is an existing walkable ramp straight
into drowning depth. Two measurements from trying it:
* A down-stair designation on those ramp tiles came back `dig None` - the game removes it (the
  tile is already open; and the plugin flags it DAMP: an aquifer line).
* The plugin's damp alert (A153) named my own stair designations, and the guidance is to clear
  them before a dwarf digs. Cleared with NO_DIG: read-back `dig None`.
So on this kind of site the water plan is: **well above the water** (needs mechanisms -> metal
chain), or **pump/drain the pool** first. Deep water access by hand is how fortresses lose
dwarves, not how they get drink.

## Zone kinds are the game's list, not my guesses

`/create-zone` answers with the accepted kinds when you get it wrong (measured 2026-11-21):
`archery, barracks, bedroom, dining, dormitory, dump, dungeon, fishing, garbage, meeting,
office, pasture, pen, pit, pond, refuse, sand, tomb, training, water, watersource`. The
outdoor waste zone is **`dump`** (a "Garbage Dump" request is refused by name), the farm's
livestock pen is **`pen`**/**`pasture`**, and the refuse pile is a **stockpile category**, not
a zone kind.

## A silent write may already have happened - verify BEFORE you retry

Measured on this fort: a `bedroom` zone write answered `"lua produced no output; the chunk
printed nothing"`, so I retried - and the zone list then showed **two identical Bedroom
zones** (ids 36 and 38 on `60..65,138..143 @36`): the first write had landed and its reply
was silent. Silent replies on writes are a property of the RPC reply stream (see
`tool_missing.md`), not evidence about the game. So: **read the live list first, then decide
whether a retry is even needed.** (The removal path is already fixed this way; zone creation
needs the same gate.)

## Zone writes are the fort-killing class (measured crash)

`crash_2026-11-21-22-53-41.txt`: my zone-removal chunk called
`dfhack.buildings.notifyCivzoneModified()` with **no building argument** and the game died
with `SIGSEGV (SEGV_MAPERR) at address 0` in `Buildings::notifyCivzoneModified`. Rules that
came out of it, in order of preference:
1. **Do zones in-game** (`z -> ... -> draw rect -> Accept`, and remove zones the same way).
   The UI is the path the game was designed for; the agent's zone writes are a last resort.
2. Zone writes stay behind `acknowledge_risk` (create *and* remove), and never retried on the
   same id without reading `/zones` first.
3. `pcall` is not a safety story: a native fault kills the process, not the coroutine.

## Trading, measured on a live caravan (Y100, this site)

* `manage_nobles(action='auto_assign')` appointed **MANAGER / BOOKKEEPER / BROKER** in one call
  (fresh embark: all vacant; read-back: all `vacant: false`). Trading needs the BROKER.
* `trade-depot` actions measured: `status` (goods/queue/caravan state), `mark_goods`
  (**hauling works**: marked 25 -> depot stock 227 -> 252, haul queue drained),
  `unmark_goods`, `request_broker` (read-back `trader_requested: true`), `barter_status`.
* A depot the dwarves cannot walk to is a depot that cannot trade: the tool reports
  **`accessible: false`**, and that is why the barter lists stay empty (`have_talker: false`).
  Build the 3-wide wagon passage BEFORE any trade plan, and treat `accessible` as the gate.
* The Trade **screen** is UI state: `main_interface.trade.open` is a boolean, not a function.
  Feeding `tr.bld` + `open = true` opens it, but the good lists only fill once a fort trader
  and a merchant are actually talking - so "open the screen" is not a route to barter.

## A broker in the menu is not a broker to the depot

Measured 2026-11-22: the depot screen said **"You do not have a broker"** while the nobles
screen showed BROKER appointed. Reading the game's data: the BROKER position has **no aptitude
list** on this build (`apt_count = 0`), histfig flags contain **no `merchanted`** field, and
`df.job_type.NEGOTIATOR` is nil - so agent-side tools cannot verify the dwarf qualifies, and an
appointment the menu accepts can be one the depot refuses. Rules:
1. **`trade_flags.anyone_can_trade = true`** is the fortress's own answer for a fort without a
   legitimate broker (`/trade-depot request_broker anyone_can_trade=true`; read-back `True`).
2. `accessible` is the **game's flag** (`depot.accessible`), not a tool opinion: goods, a broker
   and an open window all mean nothing until a dwarf can walk to the depot. The wagon route
   (surface -> depot, 3 wide) is part of the *trade* prerequisite, not just the depot rung.
3. Appointments by index are a UI-visible lie. If a tool cannot verify eligibility, it must say
   so on the result (the module now stamps `legibility: UNVERIFIED`).
