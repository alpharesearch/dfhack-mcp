---
name: df-stockpile
description: Set up Dwarf Fortress stockpiles that work - the general pile off the wagon, a wood pile by the entrance, an outdoor refuse/corpse pile - with real sizes, the game's category flags, container and wheelbarrow limits, and hauling as the proof.
whenToUse: When the fort needs storage: items lie scattered, workshops clutter, hauling never finishes, a pile "exists" but nothing goes to it, or you are placing the first stockpiles of a new embark.
metadata:
  version: "1"
  domain: "dwarf-fortress/storage"
disable-model-invocation: false
user-invocable: true
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
preset; no flags = the wiki's "None" pile. `/configure-stockpile` writes these and
**prints the flags back** - read them, don't trust success.

## 4. Containers and wheelbarrows
Limits live in `b.storage.max_barrels / max_bins / max_wheelbarrows` - not on the
building. Rules: **stone piles want wheelbarrows** (stones are heavy; minimise the hand-haul
distance and keep the pile close to the mason/smelter); wood, refuse and corpse piles take
no bins/barrels. Note the wiki's trap: entering the custom settings UI resets the
wheelbarrow count to 1 - set it after the filters.

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
