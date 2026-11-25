---
name: df-stockpile
description: Set up Dwarf Fortress stockpiles that work - the general pile off the wagon, a wood pile by the entrance, an outdoor refuse/corpse pile - with real sizes, the game's category flags, container and wheelbarrow limits, and hauling as the proof.
whenToUse: When the fort needs storage: items lie scattered, workshops clutter, hauling never finishes, a pile "exists" but nothing goes to it, or you are placing the first stockpiles of a new embark.
metadata:
  source: measured on DF 53.17-r1 (STEAM) with the dfhack-mcp layer, during play on region1
  tools: create_stockpile, configure_stockpile, list_stockpiles, remove_building, list_zones, survey, fort_plan, world_map, workshop_job
---


# Stockpiles that work (measured on DF 53.17-r1)

All elevations are **in-game Elevation panel numbers** (`z = elev + 129`).

## 0. A stockpile is a hauling contract, not a painted rectangle
The wiki's definition: dwarves with a **hauling** labour look for items not already on a
pile that accepts them, and carry them to a suitable pile **if one exists**. So a pile
either changes job behaviour or it is decoration. Proof is measured on the tiles, not by
the building existing: `scripts/watch_pile.py` counts items inside a pile's extent.

## 1. The first three piles (this is the order the player asked for)
1. **General pile, near the wagon, taking everything off the embark - EXCEPT stone and
   wood** (and corpses/refuse/gems, per the quickstart). Flags written: every category
   true except `stone, wood, corpses, refuse, gems`.
2. **Wood pile close to the entrance/surface and the road** - the wiki is explicit: wood
   travels far and is slow to haul, so keep the pile near the fortress entrance (moving
   down one z-level is only one tile) and near the carpenter. It takes `wood` only, and
   **no bins, no barrels** (wood stockpiles cannot use them).
3. **Refuse/corpse pile OUTSIDE** so miasma decays away from the fort: `corpses` +
   `refuse`, no containers. A refuse *stockpile* is not a garbage *dump* (a zone for items
   you mark to dump), and refuse marked "Dwarves Dump" goes to the dump instead. Standing
   orders must allow gathering refuse from outside.

## 2. Size is not cosmetic - measured on this build
A pile must be **big enough to receive** (each tile = one drop point). Two measured traps:
- `constructBuilding` reads **`width` / `height`**, not `w` / `h`: my piles came out 1×1
  because my spec passed `w=`,`h=` and the library called `setSize(nil,nil)`. The player
  saw it: *"your stockpiles are only one tile, this needs to be bigger"*.
- **`setSize` is legal only before the building is committed**
  (`expected: bld->id == -1`). So the order is `allocInstance -> setSize ->
  constructAbstract`, and `constructAbstract`/`constructWithFilters` fail when called out
  of order. Stockpile is `building_type 29, subtype -1, custom -1` (my guessed `type=21`
  was a building that *needs items*, which is why the commits were rejected).
- `setSize` fails on overlap: pick the rectangle from `/survey` with `building: 0` and
  floors, and check the wagon/depot footprints (x45-49,y118-122 and x38-42,y118-122 here).

## 3. Categories = `settings.flags` (17 names, no `misc`)
`building_stockpilest.settings.flags` exposes exactly the wiki's categories:
`ammo, animals, armor, bars_blocks, cloth, coins, corpses, finished_goods, food,
furniture, gems, leather, refuse, sheet, stone, weapons, wood`. Writing `flags.misc`
**throws** and aborts the loop (`stockpile_group_set` has no `misc` - it is a `mats`
struct). Semantics measured here: all flags true = takes everything; one flag = that
preset; no flags = the wiki's "None" pile. `/configure-stockpile` writes them and prints them
back - read the read-back. **Flags alone are not a stockpile**: the game's custom-settings UI
walks per-category material arrays that the UI allocates when a category is switched on (319 for
the inorganic families, 892 leather, 1327 animals, 14476 food.meat). `flags.wood = true` with
`wood.mats` size 0 crashes the game when the player clicks Custom (`SIGSEGV at 0xa` in
`render_things`, overlay on the stack). So after flags, run `/repair-stockpile-settings`; the
acceptance test is **the Custom button opening**, not a flag read-back.

## 4. Containers and wheelbarrows
Limits live in `b.storage.max_barrels / max_bins / max_wheelbarrows` - not on the
building. Rules: **stone piles want wheelbarrows** (stones are heavy; minimise the hand-haul
distance and keep the pile close to the mason/smelter); wood, refuse and corpse piles take
no bins/barrels. Note the wiki's trap: entering the custom settings UI resets the
wheelbarrow count to 1 - set it after the filters. `configure_stockpile` also supports
container-only updates (`max_bins`, `max_barrels`, `max_wheelbarrows`) without re-declaring
category flags.

## 5. Feeder piles and links (the supply chain)
`b.links.give_to_pile / take_from_pile / give_to_workshop / take_from_workshop`.
Use them the way the wiki prescribes: a **small pile of the input material next to each
workshop** (the crafter takes a few steps; haulers refill it), a **stone pile linked to
the mason** (so he never drags a rock across the map), **ore + flux + fuel** all linked to
a smelter (link only ore and your smelter starves), **barrels** available to the still via
a furniture pile. A pile set to "take from links only" keeps harvesters from wasting time.

## 6. Prove it by hauling
Run `scripts/watch_pile.py --id 15` (or `--x0 --x1 --y0 --y1 --elev`): it counts items
inside the extent, grouped by class, and reports the trend. Expectations to test:
loose stones and boulders **stay off** the general pile (they are denied); food, cloth,
finished goods, weapons arrive; the wood pile only ever gains logs; the outdoor pile gains
refuse/corpses. Nothing arriving = the wiki's checklist below, not a "successful" setup.

## 7. When a pile works on paper and not in the game (wiki checklist)
Idle dwarves with the right hauling labours; a free tile (hidden items and wheelbarrows
tie up tiles); path to both item and pile; "accept from anywhere" (not links only);
**both item type AND material** match the filters; armor/weapon "usable/unusable";
**"Additional Options" (organic / non-organic) left enabled** - disabling them is a
classic broken-pile cause; item not forbidden, not owned, not claimed by a job/location;
standing orders allow the item class (and refuse gathering from outside); containers exist
for items that need them.

## 8. Placement rules of thumb (measured here)
Distance is the cost: a hauler walks **to the item**, so pile placement sets the total
labour of the fort. General pile at the wagon (`x52-57, y116-120, in-game 38`), wood pile
on the road side (`x56-59, y121-122, in-game 38`), refuse/corpse pile outdoors on the
plain (`x73-78, y116-122, in-game 36`), stone pile next to the dig face and later the
mason. Keep piles off walls/ramps (survey first) and never overlap a building footprint.

Measured pile notes (sizes, coverage, loose-item tiles, order-the-room-first): [references/pile-notes.md](references/pile-notes.md)

## 9. Ground first: a pile needs a dug floor under EVERY tile of its footprint

The game accepts a stockpile on cavern air and fog without complaining, and the pile then
floats. Measured on this fort: three piles placed on tiles the survey labels `open`
(OpenSpace - cave air) - the player's words: *"you had build the three stockpiles in mid
air"*. The rule for any pile site, surface or underground:

- read the footprint before placing it: every tile must be dug, walkable floor
  (`floor` / `stairs` / `ramp` / `ramp_top` / `furrowed`); `open`, rock and fog are not ground;
- a surface pile stands on the surface's own floors (grass/dirt floors near the wagon),
  not on the lip of a cavern;
- `ramp_top` is ground - a site with 3 floors and a ramp top is a legal 2x2.

Removing a pile is a mark, not a poof: `deconstruct` marks it and a dwarf finishes the job.
A stockpile usually disappears quickly because it needs no materials; a workshop takes a
mason. Read the tile afterwards - "I sent the removal" is not "the tile is empty".

## 10. "A pile exists" is not "the general pile exists"

The quickstart rung is a **general** pile: everything except stone, wood, corpses, refuse and
gems. Measured: a fort with a single wood-only pile satisfied a rung that only asked "is
there a stockpile", while hauling was doing nothing for the fort. When you check the general
pile, check the categories (about 12 accepted, none of the five excluded) - the pile list
from the game reports them, and a pile accepting nothing is a rectangle of paint.

Sizes on this site: general pile 4x3..6x6 near the entrance on a dug level; refuse pile
outdoors (5x5+, corpses+refuse only, no bins/barrels/wheelbarrows); wood pile 2x2 near the
road, wood only. No stone pile in early game - the player's rule, and it is right: on a
7-dwarf embark a stone pile buys hauling labour and nothing else. Add it when a dig face
out-produces the general pile, with wheelbarrows and Max wheelbarrows=3.

## 11. Workshops near the piles are plans, not paint

A 3x4 still (the wiki's early brewery) is a multi-tile **plan**: the game creates it and a
mason builds it with real materials. It is not an abstract building - asking for the abstract
route is refused (`constructAbstract ... expected !bld->isActual()`). Keep the workshop's
footprint clear of stairs and of other piles' footprints, and give the mason a path.

## 12. This build has 17 pile flags, and `plants` is not one of them

Enumerated live from the game (`pairs(sp.settings.flags)` on a real pile, 2026-11-21):
ammo, animals, armor, bars_blocks, cloth, coins, corpses, finished_goods, food, furniture,
gems, leather, refuse, sheet, stone, weapons, wood. **No plants category exists.** Plump
helmets and plump helmet spawn are stored as **food** - so the farm chain wants
`food` on the pile that feeds the still, and a planting cancellation ("Needs plump
helmet spawn") is a *reachability/hauling* problem (the spawn is not where the sowing
dwarf can path to it), not a missing category. Do not add category names from a wiki
list: a name the writer cannot set answers "success" and changes nothing.

## 13. "Pile 3" in the panel is not id 3 in the tools

The in-game panel numbers piles 1, 2, 3... in the order you made them; the tools report the
**building id**, which is a different number on the same fort (measured 2026-11-21: "the
food stock pile #3 next to the still" - my reads listed ids 1/4/21/22/23, and id 3 does not
exist). When you and an agent talk about a pile, quote the **extent and elevation**
("37..39,120..124 @40") - that is unambiguous, the numbers are not.

## §14 - Custom on an agent-made pile can crash the game (measured)

`crash_2026-11-22-20-02-29.txt`: SIGSEGV at address 0x5, `render_things` -> `overlay.plug.so`,
"no Lua involvement detected". The player clicked **Custom** on an outdoor pile the tool created.
The game's settings screen walks each enabled category's material arrays; **the UI allocates
those arrays only when it switches a category on**, so a pile whose flags were written
programmatically has flags with nothing behind them, and the screen dereferences nil.

Rules that follow:
1. Flag writes and array allocation are ONE operation - a category flag without its arrays is a
   loaded gun aimed at the next player who opens Custom.
2. Sizes come from a pile the game built (measure at runtime), never from a wiki table. A
   measured size of 0 means "allocate one slot", not "skip it".
3. `/pile-arrays` is the tool: `action=check` lists `category.field` arrays of size 0 per pile,
   `action=fix` fills them to a reference pile's measured sizes. **Run check before you tell a
   player to open Custom on an agent-made pile.**

## §15 - corpses/refuse are the player's click, not the agent's flag (measured twice)

Controlled test, one save, three piles, the player's own clicks: **general (12 categories) and
wood opened; `corpses` + `refuse` crashed the game** (`render_things` -> `overlay.plug.so`,
SIGSEGV, no Lua) - the same class as the M8 fort's crash on its outdoor refuse pile.

Why the 1-slot allocation did not help: those categories' arrays are indexed by **race** and
**item type**, and on a UI-made pile they measure **0** - the game allocates them *when its
screen opens that page*. Writing the flag programmatically leaves the page walking an array the
game never filled. So:
- `configure_stockpile` / `create-stockpile` **refuse** `corpses` and `refuse` by default; the
  answer says why and points at the in-game path (`p -> pile -> Custom`).
- The unlock condition is measurement, not courage: `/pile-arrays` must report non-zero reference
  sizes from a pile the game's UI built, and the caller must pass `allow_crash_categories=true`.
- A refuse pile's *categories* are therefore a player action. Placement, size, containers and
  everything else stay the tool's.

## §17 - the reference sizes, measured from a pile the GAME made

From the player's UI pile (75..81,119..124 @36, corpses+refuse):

| field | class | length |
|---|---|---|
| `corpses.corpses` | `vector<char>` | **1327** (race-indexed) |
| `refuse.body_parts/bones/corpses/hair/horns/shells/skulls/teeth` | `vector<char>` | **1327** each |
| `refuse.type` | `vector<char>` | **113** (item-type-indexed) |
| `wood.mats` | `vector<char>` | **0** - and that is LEGAL (a UI pile with wood enabled opens fine) |

Three lessons, each earned the hard way:
1. **Measure from a UI-made pile.** My table's 0s came from a tool-made pile: measuring the crash and
   calling it the reference is how you get a crash twice.
2. **These arrays are `userdata`, not Lua tables.** Any `type(v) == "table"` test silently skips every
   settings array, which is why "the allocation runs" was true in code and false in the game.
3. **A size of 0 is not automatically a defect** (`wood.mats`). `/pile-arrays check` is therefore
   reference-anchored: a field is under-allocated only when the game's own pile has a non-zero size
   there.

## §18 - verified: the player clicked Custom on the tool-made corpses+refuse pile and it did not crash

Same pile, same screen, same operation that had crashed twice (with sizes 0, then with my 1-slot
fallback). With the measured 1327/113 arrays allocated in the create call, the screen opens. The
rule this leaves behind: **allocate to the game's own measured lengths, and verify by having the
player open the screen that crashes** - a passing `check` is necessary, not sufficient.

## §19 - Clean site finding & contained supply counts (32-tool MCP surface)

- **Find clean pile footprints first**: call `fort_plan(action="find_site", elev=..., width=..., height=...)` before `create_stockpile` so the site gate passes on the first attempt.
- **Check free containers and contained supplies**: `workshop_job(action="stock")` counts free `BARREL`/`BIN`/`LARGE_POT` containers AND usable contained `DRINK`, `FOOD`, and `SEEDS` (including stacks inside barrels and bags).
- **HTTP-only diagnostics**: `resize_building` and `pile_arrays` are excluded from the 32-tool MCP surface and remain available over HTTP (`docs/tool-notes.md`).
