# Stockpile measurements (53.17-r1, this fort)

## A pile only covers FLOOR tiles - walls and ramps are silently excluded
Requested a 6x4 stone pile at x44-49,y113-116 (in-game 38): `setSize` and
`constructAbstract` both answered true, extents read x44..49,y113..116 - and per-tile
`building-at` mapped **6 tiles of 24** (only the y116 row is floor). The wiki says it
plainly: tiles that cannot be stockpiled (wall, workshop, existing pile) are not part of
the pile. So a pile's real size = the floors inside its rectangle, and the build call will
happily report success on a 1-tile pile.

Rule: pick the rect from `/survey` with `floor == w*h`, `building == 0`, `ramp == 0`,
`stairs == 0`; then verify coverage tile by tile with `/building-at`. Ramps count as
buildings (they are constructions), so the road and the sloped hillside are not pile ground.

## The dig has to come first
On this fort the only clean rects near the entrance were the wagon footprint itself
(x45-49,y118-122) and the depot (x38-42,y118-122). Storage belongs on a **dug room floor**:
the 7x5 room at x28-34,y125-129 (in-game 36) came out 12/12 clean at x30-33,y126-128, and
the pile there (id 20) covers all 12 tiles. Order the room, let it finish, then place the
pile - the pile never drives the dig.

## Loose items at creation block tiles
The wiki's rule, seen live: when a pile is created, movable items standing on its tiles
become part of it, are considered stored there, and mark the tile **full** until they move -
even if the pile's filters disallow them. My general pile (id 15) started with 5 boulders on
5 of its 30 tiles; because stone is denied, only a stone pile (id 20) can take them away.
So: create the stone pile early, next to the dig face, or the general pile quietly loses
tile capacity.

## Flags alone are not a stockpile: the UI walks material arrays (this is the crash)
Player report: clicking the **Custom** button on my general pile crashed the game. Crash logs
(23:48:06, 23:56:23, identical): `SIGSEGV (SEGV_MAPERR) at address 0xa`, `render_things` ->
`overlay.plug.so+0xf9fb`, "no Lua involvement".

Diff against a UI-made pile (stockpile **number** 6 - the UI numbers piles by
`stockpile_number`, not building id) shows the mechanism:

```
UI pile 6 : ammo{mats=319, other_mats=2, quality_core=7, quality_total=7, type=10}
            animals{enabled=1327}  armor{body=20, color=136, head=16, legs=17, mats=319, ...}
            food{meat=14476, glob=2420, egg=307, seeds=157, ...}  weapons{weapon_type=45, ...}
my pile 15 (before): every array = 0  <- flags true, vectors never allocated
```

The UI **allocates the arrays for the categories you enable** (319 for the inorganic
families, 892 for leather/parchment, 1327 animals.enabled, 14476 food.meat...). Writing
`settings.flags.wood = true` on a pile whose `wood.mats` is size 0 is a landmine: the custom
UI enumerates `mats[material]` and dereferences an empty vector.

Rule for tools: after setting flags, **allocate every array field the UI walks** for each
enabled category, with the game's own sizes (`/repair-stockpile-settings`), and the
acceptance test is **the custom button opening**, not a flag read-back. My earlier
"fresh `df.new(df.stockpile_settings)` also has mats=0" test misled me: a fresh object has
no categories enabled - the arrays are sized *when a category is switched on*.

## Verified closed (player test)
After `/configure-stockpile` (flags + array allocation in the same pass), the player clicked
**Custom** on the general pile (id 15) and the repaired piles: **no crash**. The mechanism -
`SIGSEGV at 0xa` in `render_things` via `overlay.plug.so` - is closed by allocating the arrays
the UI walks. `repair_stockpile_settings` remains only as a rescue for piles built before the
fix; normal construction (`/make-stockpile`) never needs it.
