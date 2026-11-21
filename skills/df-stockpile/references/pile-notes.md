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
