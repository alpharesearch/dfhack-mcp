"""Underground fortress CAD planner and vertical strata scanner for DFHack MCP.

Provides:
- ``FortPlanStore``: SQLite-backed persistence of fortress plans keyed by save_key.
- Pure geometry & collision engine (``rect_overlaps``, ``ensure_hub``,
  ``compute_attached_room``, ``check_plan_collisions``).
- Parametric rotatable district templates (``food_district``, ``workshop_wing``,
  ``bedroom_wing``, ``civic_hall`` across ``N``, ``S``, ``E``, ``W``).
- ``fort_strata``: vertical geology scanner classifying soil, potential soil,
  stone, aquifers, water, magma, and caverns across elevations.
- ``validate_rooms_terrain`` and ``render_plan_view`` (non-RLE aligned ASCII).
- ``commit_plan``: access-first phased execution (shaft -> hub_and_corridors -> rooms).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Awaitable, Callable

import worldmodel as WM

# DFHack TiletypeMaterial enum values for soil & grass.
SOIL_MATS = {1, 8, 9, 10, 11}

VALID_DIRECTIONS = ("N", "S", "E", "W")
VALID_TEMPLATES = ("food_district", "workshop_wing", "bedroom_wing", "civic_hall", "bootstrap_camp")
VALID_PHASES = ("auto", "shaft", "hub_and_corridors", "rooms", "all")

ROOM_DEFAULTS: dict[str, dict[str, Any]] = {
    "hub": {"width": 3, "height": 3, "needs_soil": False, "purpose": "Central stair landing"},
    "corridor": {"width": 1, "height": 3, "needs_soil": False, "purpose": "Connecting hallway"},
    "workshop": {"width": 5, "height": 5, "needs_soil": False, "purpose": "3x3 workshop + walking ring"},
    "stockpile": {"width": 5, "height": 5, "needs_soil": False, "purpose": "General or specialized stockpile"},
    "farm": {"width": 5, "height": 5, "needs_soil": True, "purpose": "Underground farm plot (requires soil)"},
    "still": {"width": 5, "height": 5, "needs_soil": False, "purpose": "Brewery / still + drink storage"},
    "kitchen": {"width": 5, "height": 5, "needs_soil": False, "purpose": "Kitchen + prepared food storage"},
    "bedroom": {"width": 3, "height": 3, "needs_soil": False, "purpose": "Individual dwarf bedroom"},
    "dining": {"width": 7, "height": 5, "needs_soil": False, "purpose": "Dining hall / tavern"},
    "office": {"width": 3, "height": 3, "needs_soil": False, "purpose": "Noble / manager office"},
    "temple": {"width": 5, "height": 5, "needs_soil": False, "purpose": "Generic temple / meeting hall"},
    "barracks": {"width": 5, "height": 5, "needs_soil": False, "purpose": "Military barracks & armor stand"},
    "hospital": {"width": 5, "height": 5, "needs_soil": False, "purpose": "Fortress hospital"},
    "depot": {"width": 7, "height": 7, "needs_soil": False, "purpose": "Trade depot (requires 3-wide wagon access)"},
}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _default_db_path() -> str:
    base = os.path.expanduser("~/.dfhack_mcp")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "fortplan.db")


def _empty_plan(save_key: str) -> dict[str, Any]:
    return {
        "save_key": save_key,
        "hub": None,
        "rooms": [],
        "updated_at": time.time(),
    }


class FortPlanStore:
    """SQLite persistence for fortress floorplans, keyed by save_key."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or _default_db_path()
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fort_plans (
                    save_key TEXT PRIMARY KEY,
                    plan_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def load(self, save_key: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT plan_json FROM fort_plans WHERE save_key = ?",
                (save_key,),
            ).fetchone()
        if not row:
            return _empty_plan(save_key)
        try:
            data = json.loads(row[0])
            if not isinstance(data, dict):
                return _empty_plan(save_key)
            data.setdefault("save_key", save_key)
            data.setdefault("hub", None)
            data.setdefault("rooms", [])
            return data
        except json.JSONDecodeError:
            return _empty_plan(save_key)

    def save(self, save_key: str, plan: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        payload = dict(plan)
        payload["save_key"] = save_key
        payload["updated_at"] = now
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO fort_plans (save_key, plan_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(save_key) DO UPDATE SET
                    plan_json = excluded.plan_json,
                    updated_at = excluded.updated_at
                """,
                (save_key, json.dumps(payload), now),
            )
        return payload

    def clear(self, save_key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM fort_plans WHERE save_key = ?", (save_key,))

    def list_keys(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT save_key FROM fort_plans ORDER BY updated_at DESC"
            ).fetchall()
        return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Pure geometry & collision engine
# ---------------------------------------------------------------------------


def _norm_rect(rect: dict[str, Any] | tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    if isinstance(rect, tuple):
        x0, y0, x1, y1 = rect
    else:
        x0, y0, x1, y1 = int(rect["x0"]), int(rect["y0"]), int(rect["x1"]), int(rect["y1"])
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def rect_overlaps(
    a: dict[str, Any] | tuple[int, int, int, int],
    b: dict[str, Any] | tuple[int, int, int, int],
    pad: int = 0,
) -> bool:
    """Return True if inclusive rectangles ``a`` and ``b`` overlap (with optional ``pad``)."""
    ax0, ay0, ax1, ay1 = _norm_rect(a)
    bx0, by0, bx1, by1 = _norm_rect(b)
    return not (
        ax1 + pad < bx0
        or bx1 + pad < ax0
        or ay1 + pad < by0
        or by1 + pad < ay0
    )


def ensure_hub(
    plan: dict[str, Any],
    x: int,
    y: int,
    elev_top: int,
    elev_bottom: int,
    radius: int = 1,
) -> dict[str, Any]:
    """Set or update the central vertical shaft + landing hub on ``plan``."""
    e_top = max(int(elev_top), int(elev_bottom))
    e_bot = min(int(elev_top), int(elev_bottom))
    r = max(0, int(radius))
    hub = {
        "name": "hub",
        "kind": "hub",
        "x": int(x),
        "y": int(y),
        "elev_top": e_top,
        "elev_bottom": e_bot,
        "radius": r,
        "x0": int(x) - r,
        "y0": int(y) - r,
        "x1": int(x) + r,
        "y1": int(y) + r,
    }
    plan["hub"] = hub
    return hub


def compute_attached_room(
    anchor: dict[str, Any],
    direction: str,
    width: int,
    height: int,
    corridor_len: int = 2,
    corridor_width: int = 1,
    offset: int = 0,
    elev: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Compute a room rectangle and optional connecting corridor attached to ``anchor``.

    ``direction`` is one of ``N`` (-y), ``S`` (+y), ``E`` (+x), ``W`` (-x).
    ``offset`` shifts the attached room along the perpendicular axis relative
    to the anchor's center.
    """
    d = direction.upper()
    if d not in VALID_DIRECTIONS:
        raise ValueError(f"direction must be one of {VALID_DIRECTIONS}, got {direction!r}")
    w = max(1, int(width))
    h = max(1, int(height))
    clen = max(0, int(corridor_len))
    cw = max(1, int(corridor_width))

    ax0, ay0, ax1, ay1 = _norm_rect(anchor)
    acx = (ax0 + ax1) // 2
    acy = (ay0 + ay1) // 2
    room_elev = int(elev) if elev is not None else int(anchor.get("elev", anchor.get("elev_bottom", 0)))

    corridor: dict[str, Any] | None = None
    if d == "N":
        cy1 = ay0 - 1
        cy0 = ay0 - clen
        cx0 = acx - (cw // 2) + int(offset)
        cx1 = cx0 + cw - 1
        if clen > 0:
            corridor = {"x0": cx0, "y0": cy0, "x1": cx1, "y1": cy1, "elev": room_elev}
        ry1 = ay0 - clen - 1
        ry0 = ry1 - h + 1
        rx0 = acx - (w // 2) + int(offset)
        rx1 = rx0 + w - 1
    elif d == "S":
        cy0 = ay1 + 1
        cy1 = ay1 + clen
        cx0 = acx - (cw // 2) + int(offset)
        cx1 = cx0 + cw - 1
        if clen > 0:
            corridor = {"x0": cx0, "y0": cy0, "x1": cx1, "y1": cy1, "elev": room_elev}
        ry0 = ay1 + clen + 1
        ry1 = ry0 + h - 1
        rx0 = acx - (w // 2) + int(offset)
        rx1 = rx0 + w - 1
    elif d == "E":
        cx0 = ax1 + 1
        cx1 = ax1 + clen
        cy0 = acy - (cw // 2) + int(offset)
        cy1 = cy0 + cw - 1
        if clen > 0:
            corridor = {"x0": cx0, "y0": cy0, "x1": cx1, "y1": cy1, "elev": room_elev}
        rx0 = ax1 + clen + 1
        rx1 = rx0 + w - 1
        ry0 = acy - (h // 2) + int(offset)
        ry1 = ry0 + h - 1
    else:  # W
        cx1 = ax0 - 1
        cx0 = ax0 - clen
        cy0 = acy - (cw // 2) + int(offset)
        cy1 = cy0 + cw - 1
        if clen > 0:
            corridor = {"x0": cx0, "y0": cy0, "x1": cx1, "y1": cy1, "elev": room_elev}
        rx1 = ax0 - clen - 1
        rx0 = rx1 - w + 1
        ry0 = acy - (h // 2) + int(offset)
        ry1 = ry0 + h - 1

    room_rect = {"x0": rx0, "y0": ry0, "x1": rx1, "y1": ry1, "elev": room_elev}
    return room_rect, corridor


def check_plan_collisions(
    plan: dict[str, Any],
    new_room: dict[str, Any],
    new_corridor: dict[str, Any] | None = None,
    wall_thickness: int = 1,
    ignore_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return collision records if ``new_room`` or ``new_corridor`` overlaps existing features."""
    collisions: list[dict[str, Any]] = []
    elev = int(new_room["elev"])
    pad = max(0, int(wall_thickness))
    ignored = ignore_names or set()
    parent_name = new_room.get("parent")

    hub = plan.get("hub")
    if hub and hub.get("elev_bottom", elev) <= elev <= hub.get("elev_top", elev):
        if rect_overlaps(new_room, hub, pad=pad):
            collisions.append({"with": "hub", "part": "room", "elev": elev})
        if new_corridor and rect_overlaps(new_corridor, hub, pad=0):
            # Corridor is expected to touch hub edge if parent == "hub", but must not overlap interior.
            collisions.append({"with": "hub", "part": "corridor", "elev": elev})

    for existing in plan.get("rooms", []):
        ename = existing.get("name", "")
        if ename in ignored:
            continue
        if int(existing["elev"]) != elev:
            continue
        if rect_overlaps(new_room, existing, pad=pad):
            collisions.append({"with": ename, "part": "room_vs_room", "elev": elev})
        ex_corr = existing.get("corridor")
        if ex_corr and rect_overlaps(new_room, ex_corr, pad=0):
            collisions.append({"with": ename, "part": "room_vs_corridor", "elev": elev})
        if new_corridor:
            # Allow corridor to touch its own parent's wall without overlapping its interior.
            corr_pad = 0 if ename == parent_name else 0
            if rect_overlaps(new_corridor, existing, pad=corr_pad):
                collisions.append({"with": ename, "part": "corridor_vs_room", "elev": elev})
            if ex_corr and rect_overlaps(new_corridor, ex_corr, pad=0):
                collisions.append({"with": ename, "part": "corridor_vs_corridor", "elev": elev})

    return collisions


# ---------------------------------------------------------------------------
# Parametric rotatable district templates
# ---------------------------------------------------------------------------


def _rotate_dir(base: str, facing: str) -> str:
    """Rotate a local direction (assuming base facing 'E') to target ``facing``."""
    order = ["E", "S", "W", "N"]
    idx_base = order.index(base)
    idx_face = order.index(facing)
    return order[(idx_base + idx_face) % 4]


def _make_room(
    name: str,
    kind: str,
    rect: dict[str, Any],
    corridor: dict[str, Any] | None,
    parent: str,
    direction: str,
    notes: str = "",
) -> dict[str, Any]:
    defaults = ROOM_DEFAULTS.get(kind, {})
    return {
        "name": name,
        "kind": kind,
        "elev": int(rect["elev"]),
        "x0": int(rect["x0"]),
        "y0": int(rect["y0"]),
        "x1": int(rect["x1"]),
        "y1": int(rect["y1"]),
        "corridor": corridor,
        "parent": parent,
        "direction": direction,
        "needs_soil": bool(defaults.get("needs_soil", False)),
        "notes": notes or defaults.get("purpose", ""),
    }


def build_template_rooms(
    template: str,
    anchor: dict[str, Any],
    direction: str = "E",
    prefix: str = "",
    elev: int | None = None,
) -> list[dict[str, Any]]:
    """Build a collision-free multi-room district attached to ``anchor`` facing ``direction``."""
    t = template.lower()
    d = direction.upper()
    if t not in VALID_TEMPLATES:
        raise ValueError(f"Unknown template {template!r}; expected one of {VALID_TEMPLATES}")
    if d not in VALID_DIRECTIONS:
        raise ValueError(f"Unknown direction {direction!r}; expected one of {VALID_DIRECTIONS}")

    pfx = f"{prefix}_" if prefix else ""
    parent_name = str(anchor.get("name", "hub"))
    left_dir = _rotate_dir("N", d)
    right_dir = _rotate_dir("S", d)

    rooms: list[dict[str, Any]] = []

    if t == "food_district":
        # Central food stockpile spine connected to anchor, with farm, still, and kitchen branching off.
        spine_rect, spine_corr = compute_attached_room(
            anchor, d, width=5, height=5, corridor_len=2, corridor_width=1, elev=elev
        )
        spine = _make_room(
            f"{pfx}food_stockpile",
            "stockpile",
            spine_rect,
            spine_corr,
            parent_name,
            d,
            "Central food & drink stockpile",
        )
        rooms.append(spine)

        farm_rect, farm_corr = compute_attached_room(
            spine, d, width=5, height=5, corridor_len=1, corridor_width=1, elev=elev
        )
        rooms.append(
            _make_room(
                f"{pfx}farm_plot",
                "farm",
                farm_rect,
                farm_corr,
                spine["name"],
                d,
                "Underground plump helmet farm (requires soil)",
            )
        )

        still_rect, still_corr = compute_attached_room(
            spine, left_dir, width=5, height=5, corridor_len=1, corridor_width=1, elev=elev
        )
        rooms.append(
            _make_room(
                f"{pfx}brewery",
                "still",
                still_rect,
                still_corr,
                spine["name"],
                left_dir,
                "Still + drink barrels",
            )
        )

        kitchen_rect, kitchen_corr = compute_attached_room(
            spine, right_dir, width=5, height=5, corridor_len=1, corridor_width=1, elev=elev
        )
        rooms.append(
            _make_room(
                f"{pfx}kitchen",
                "kitchen",
                kitchen_rect,
                kitchen_corr,
                spine["name"],
                right_dir,
                "Kitchen + butcher/fishery support",
            )
        )

    elif t == "workshop_wing":
        # Central craft stockpile with 3x3+ring workshops on front, left, and right.
        hub_rect, hub_corr = compute_attached_room(
            anchor, d, width=5, height=5, corridor_len=2, corridor_width=1, elev=elev
        )
        craft_hub = _make_room(
            f"{pfx}craft_stockpile",
            "stockpile",
            hub_rect,
            hub_corr,
            parent_name,
            d,
            "Wood, stone, and bar stockpile feeding workshops",
        )
        rooms.append(craft_hub)

        for suffix, sub_dir, note in (
            ("carpenter", left_dir, "Carpenter workshop (beds, barrels, bins)"),
            ("mason", right_dir, "Stoneworker workshop (doors, tables, chairs)"),
            ("craftsdwarf", d, "Craftsdwarf / mechanic / forge bay"),
        ):
            w_rect, w_corr = compute_attached_room(
                craft_hub, sub_dir, width=5, height=5, corridor_len=1, corridor_width=1, elev=elev
            )
            rooms.append(
                _make_room(
                    f"{pfx}{suffix}_shop",
                    "workshop",
                    w_rect,
                    w_corr,
                    craft_hub["name"],
                    sub_dir,
                    note,
                )
            )

    elif t == "bedroom_wing":
        # Long 1-wide spine hall with 4 3x3 bedrooms alternating left & right.
        # Ensure spine length along `d` is 9 tiles so two 3x3 bedrooms with 1-tile wall fit cleanly.
        spine_w, spine_h = (9, 1) if d in ("E", "W") else (1, 9)
        spine_rect, spine_corr = compute_attached_room(
            anchor, d, width=spine_w, height=spine_h, corridor_len=2, corridor_width=1, elev=elev
        )
        spine = _make_room(
            f"{pfx}bedroom_hall",
            "corridor",
            spine_rect,
            spine_corr,
            parent_name,
            d,
            "Bedroom wing hallway",
        )
        rooms.append(spine)

        # Offsets -2 and +2 along spine give 3-wide rooms at [-3..-1] and [1..3], leaving 1-tile wall at 0.
        idx = 1
        for off in (-2, 2):
            for sub_dir in (left_dir, right_dir):
                b_rect, b_corr = compute_attached_room(
                    spine,
                    sub_dir,
                    width=3,
                    height=3,
                    corridor_len=1,
                    corridor_width=1,
                    offset=off,
                    elev=elev,
                )
                rooms.append(
                    _make_room(
                        f"{pfx}bedroom_{idx}",
                        "bedroom",
                        b_rect,
                        b_corr,
                        spine["name"],
                        sub_dir,
                        f"Dwarf bedroom #{idx}",
                    )
                )
                idx += 1

    elif t == "bootstrap_camp":
        # Compact ~47-tile early-game starter dig: 7x5 workshop/stockpile hall + 3x3 Manager office.
        hall_w, hall_h = (7, 5) if d in ("E", "W") else (5, 7)
        hall_rect, hall_corr = compute_attached_room(
            anchor, d, width=hall_w, height=hall_h, corridor_len=2, corridor_width=1, elev=elev
        )
        hall = _make_room(
            f"{pfx}bootstrap_hall",
            "workshop",
            hall_rect,
            hall_corr,
            parent_name,
            d,
            "Starter bay for Carpenter, Stoneworker, Still, and general stockpile",
        )
        rooms.append(hall)

        off_rect, off_corr = compute_attached_room(
            hall, left_dir, width=3, height=3, corridor_len=1, corridor_width=1, elev=elev
        )
        rooms.append(
            _make_room(
                f"{pfx}manager_office",
                "office",
                off_rect,
                off_corr,
                hall["name"],
                left_dir,
                "Manager office (place Chair + Office zone)",
            )
        )

    elif t == "civic_hall":
        # Central dining hall (7x5 oriented along facing) with manager office & temple off sides.
        hall_w, hall_h = (7, 5) if d in ("E", "W") else (5, 7)
        hall_rect, hall_corr = compute_attached_room(
            anchor, d, width=hall_w, height=hall_h, corridor_len=2, corridor_width=1, elev=elev
        )
        hall = _make_room(
            f"{pfx}dining_hall",
            "dining",
            hall_rect,
            hall_corr,
            parent_name,
            d,
            "Great dining hall / meeting area",
        )
        rooms.append(hall)

        off_rect, off_corr = compute_attached_room(
            hall, left_dir, width=3, height=3, corridor_len=1, corridor_width=1, elev=elev
        )
        rooms.append(
            _make_room(
                f"{pfx}manager_office",
                "office",
                off_rect,
                off_corr,
                hall["name"],
                left_dir,
                "Manager / bookkeeper office (chair + table)",
            )
        )

        tpl_rect, tpl_corr = compute_attached_room(
            hall, right_dir, width=5, height=5, corridor_len=1, corridor_width=1, elev=elev
        )
        rooms.append(
            _make_room(
                f"{pfx}temple",
                "temple",
                tpl_rect,
                tpl_corr,
                hall["name"],
                right_dir,
                "Shrine / temple",
            )
        )

    return rooms


# ---------------------------------------------------------------------------
# Vertical geology scanner (fort_strata) & terrain validation
# ---------------------------------------------------------------------------


async def _load_tiletype_materials() -> dict[int, int]:
    """Fetch DFHack tiletype -> material enum map via GetTiletypeList."""
    try:
        from dfhack_client_python.dfhack import GetTiletypeList  # type: ignore

        resp = await GetTiletypeList()
        return {int(t.id): int(t.material) for t in resp.tiletype_list}
    except Exception:
        return {}


async def _resolve_center(
    world: Any,
    x: int | None,
    y: int | None,
    elev: int | None,
) -> tuple[int, int, int]:
    tx, ty, tz = await world.cache.extent()
    if x is not None and y is not None and elev is not None:
        return int(x), int(y), int(elev)

    z0, z1 = await world.fort_band()
    e0 = z0 - WM.GAME_Z_OFFSET
    e1 = z1 - WM.GAME_Z_OFFSET
    gen = await world._gen()
    dec = await world._dec(gen)
    levels = await world.levels(e0, e1)
    units = await WM.load_units(dec, gen, {})
    fc = WM.fort_center(WM.FortFacts(levels, units, dec))
    if fc is not None:
        cx, cy, ce = fc
    else:
        cx = tx // 2
        cy = ty // 2
        ce = (e0 + e1) // 2

    return (
        int(x) if x is not None else int(cx),
        int(y) if y is not None else int(cy),
        int(elev) if elev is not None else int(ce),
    )


async def fort_strata(
    world: Any,
    x: int | None = None,
    y: int | None = None,
    elev: int | None = None,
    radius: int = 12,
    above: int = 3,
    below: int = 8,
    tiletype_materials: dict[int, int] | None = None,
) -> dict[str, Any]:
    """Scan vertical geological strata around ``(x, y, elev)``."""
    cx, cy, ce = await _resolve_center(world, x, y, elev)
    tx, ty, tz = await world.cache.extent()
    rad = max(2, min(32, int(radius)))

    min_elev = -WM.GAME_Z_OFFSET
    max_elev = tz - 1 - WM.GAME_Z_OFFSET
    e_top = min(max_elev, ce + max(0, int(above)))
    e_bot = max(min_elev, ce - max(0, int(below)))

    z_bot = e_bot + WM.GAME_Z_OFFSET
    z_top = e_top + WM.GAME_Z_OFFSET
    # Fetch one extra z above e_top so potential_soil can inspect e+1.
    fetch_z_top = min(tz - 1, z_top + 1)

    gen = await world._gen()
    dec = await world._dec(gen)
    blocks = await world.cache.blocks(z_bot, fetch_z_top + 1, gen)
    mats = tiletype_materials if tiletype_materials is not None else await _load_tiletype_materials()

    rx0 = max(0, cx - rad)
    rx1 = min(tx - 1, cx + rad)
    ry0 = max(0, cy - rad)
    ry1 = min(ty - 1, cy + rad)

    # Index tiles by (z, x, y) -> (tt, hidden, water_level, is_magma, is_aquifer)
    by_z: dict[int, dict[tuple[int, int], tuple[int, bool, int, bool, bool]]] = {}
    blocks_scanned = 0
    for b in blocks:
        bz = int(b.map_z)
        if bz < z_bot or bz > fetch_z_top:
            continue
        bx0 = int(b.map_x)
        by0 = int(b.map_y)
        if bx0 + 15 < rx0 or bx0 > rx1 or by0 + 15 < ry0 or by0 > ry1:
            continue
        blocks_scanned += 1
        zmap = by_z.setdefault(bz, {})
        tts = getattr(b, "tiles", None)
        if tts is None:
            tts = getattr(b, "tile_types", [])
        hid_arr = getattr(b, "hidden", [])
        wat_arr = getattr(b, "water", [])
        mag_arr = getattr(b, "magma", [])
        haq_arr = getattr(b, "heavy_aquifer", [])
        laq_arr = getattr(b, "light_aquifer", [])
        des_arr = getattr(b, "designation", [])
        for i in range(min(256, len(tts))):
            lx = i % 16
            ly = i // 16
            gx = bx0 + lx
            gy = by0 + ly
            if gx < rx0 or gx > rx1 or gy < ry0 or gy > ry1:
                continue
            tt = int(tts[i])
            hidden = bool(hid_arr[i]) if i < len(hid_arr) else (bool(int(des_arr[i]) & 0x200) if i < len(des_arr) else False)
            water_lvl = int(wat_arr[i]) if i < len(wat_arr) else 0
            is_magma = bool(mag_arr[i]) if i < len(mag_arr) else False
            is_aquifer = (
                (bool(haq_arr[i]) if i < len(haq_arr) else False)
                or (bool(laq_arr[i]) if i < len(laq_arr) else False)
                or (bool(int(des_arr[i]) & 0x400000) if i < len(des_arr) else False)
            )
            zmap[(gx, gy)] = (tt, hidden, water_lvl, is_magma, is_aquifer)

    layers: list[dict[str, Any]] = []
    for e in range(e_top, e_bot - 1, -1):
        z = e + WM.GAME_Z_OFFSET
        z_above = z + 1
        cur = by_z.get(z, {})
        above_map = by_z.get(z_above, {})

        total = 0
        open_space = 0
        dug_floors = 0
        soil_walls = 0
        potential_soil = 0
        stone_walls = 0
        aquifer_tiles = 0
        water_tiles = 0
        magma_tiles = 0
        hidden_tiles = 0

        for (gx, gy), (tt, hidden, water_lvl, is_magma, is_aquifer) in cur.items():
            total += 1
            if hidden:
                hidden_tiles += 1
            if is_aquifer:
                aquifer_tiles += 1
            if is_magma:
                magma_tiles += 1
            if water_lvl > 0:
                water_tiles += 1

            mat = mats.get(tt, -1)
            if dec and hasattr(dec, "tile_class"):
                tc = dec.tile_class(tt)
                is_open = tc.key in ("open", "ramp_top", "chasm", "eerie_pit") or (not tc.solid and not tc.walkable)
                is_floor = (not tc.solid) and tc.walkable and not is_open
            else:
                shape_name = dec.shape(tt) if dec and hasattr(dec, "shape") else ""
                is_open = shape_name in ("OPEN", "RAMP_TOP", "ENDLESS_PIT")
                is_floor = shape_name in ("FLOOR", "BOULDER", "PEBBLES", "STAIR_UPDOWN", "STAIR_DOWN", "STAIR_UP", "RAMP")

            if is_open:
                open_space += 1
            elif is_floor:
                dug_floors += 1
            else:
                # Solid wall/pillar/unrevealed tile
                if mat in SOIL_MATS:
                    soil_walls += 1
                else:
                    stone_walls += 1

                # Potential soil when dug: tile directly above is soil/grass
                ab = above_map.get((gx, gy))
                if ab is not None and mats.get(ab[0], -1) in SOIL_MATS:
                    potential_soil += 1

        if magma_tiles > 0:
            layer_class = "magma"
        elif aquifer_tiles > 0:
            layer_class = "aquifer"
        elif soil_walls > 0 or potential_soil >= max(4, total // 8):
            layer_class = "soil"
        elif open_space > total // 2:
            layer_class = "surface" if e >= ce else "cavern"
        elif stone_walls > 0:
            layer_class = "stone"
        else:
            layer_class = "mixed"

        layers.append(
            {
                "elev": e,
                "z": z,
                "tiles": total,
                "open_space": open_space,
                "dug_floors": dug_floors,
                "soil_walls": soil_walls,
                "potential_soil": potential_soil,
                "stone_walls": stone_walls,
                "aquifer_tiles": aquifer_tiles,
                "water_tiles": water_tiles,
                "magma_tiles": magma_tiles,
                "hidden_tiles": hidden_tiles,
                "layer_class": layer_class,
            }
        )

    # Recommendations: prefer covered underground layers (open_space == 0) over surface/open layers.
    def _is_safe(l: dict[str, Any]) -> bool:
        return l["aquifer_tiles"] == 0 and l["magma_tiles"] == 0 and l["water_tiles"] == 0

    def _is_covered(l: dict[str, Any]) -> bool:
        return l["open_space"] == 0

    def _mostly_covered(l: dict[str, Any]) -> bool:
        return l["open_space"] <= max(4, l["tiles"] // 10)

    soil_layers = [l for l in layers if l["potential_soil"] > 0 or l["soil_walls"] > 0]
    safe_soil = [l for l in soil_layers if _is_safe(l)]
    covered_soil = [l for l in safe_soil if _is_covered(l)] or [l for l in safe_soil if _mostly_covered(l)]
    farm_pool = covered_soil or safe_soil or soil_layers

    safe_stone = [l for l in layers if l["layer_class"] == "stone" and _is_safe(l)]
    covered_stone = [l for l in safe_stone if _is_covered(l) and l["stone_walls"] > 0] or [
        l for l in safe_stone if _mostly_covered(l) and l["stone_walls"] > 0
    ]
    stone_pool = covered_stone or safe_stone

    rec_farm = (
        max(farm_pool, key=lambda l: (l["potential_soil"] + l["soil_walls"], l["elev"]))["elev"]
        if farm_pool
        else None
    )
    rec_workshop = stone_pool[0]["elev"] if stone_pool else (farm_pool[0]["elev"] if farm_pool else ce - 1)
    rec_bedroom = stone_pool[1]["elev"] if len(stone_pool) > 1 else rec_workshop

    warnings: list[str] = []
    if any(l["aquifer_tiles"] > 0 for l in layers):
        aq_elevs = [l["elev"] for l in layers if l["aquifer_tiles"] > 0]
        warnings.append(f"Aquifer detected at elevations {aq_elevs}; avoid placing shafts or rooms there.")
    if any(l["magma_tiles"] > 0 for l in layers):
        mg_elevs = [l["elev"] for l in layers if l["magma_tiles"] > 0]
        warnings.append(f"Magma detected at elevations {mg_elevs}.")
    if rec_farm is None:
        warnings.append("No soil/potential-soil layer found in scanned window; widen above/below.")

    return {
        "center": {"x": cx, "y": cy, "elev": ce},
        "radius": rad,
        "elev_range": [e_bot, e_top],
        "blocks_scanned": blocks_scanned,
        "layers": layers,
        "recommendations": {
            "farm_elev": rec_farm,
            "workshop_elev": rec_workshop,
            "bedroom_elev": rec_bedroom,
            "warnings": warnings,
        },
    }


async def validate_rooms_terrain(
    world: Any,
    rooms: list[dict[str, Any]],
    tiletype_materials: dict[int, int] | None = None,
) -> list[dict[str, Any]]:
    """Check each planned room against hazards and soil availability."""
    if not rooms:
        return []
    elevs = sorted({int(r["elev"]) for r in rooms})
    e0, e1 = min(elevs), max(elevs)
    hazards = await world.hazard_map(e0, e1)
    mats = tiletype_materials if tiletype_materials is not None else await _load_tiletype_materials()

    # Load blocks for soil verification if needed
    gen = await world._gen()
    need_soil = any(r.get("needs_soil") for r in rooms)
    soil_above_by_z: dict[int, dict[tuple[int, int], int]] = {}
    if need_soil and mats:
        blocks = await world.cache.blocks(e0 + WM.GAME_Z_OFFSET + 1, e1 + WM.GAME_Z_OFFSET + 2, gen)
        for b in blocks:
            bz = int(b.map_z)
            bx0, by0 = int(b.map_x), int(b.map_y)
            zmap = soil_above_by_z.setdefault(bz, {})
            tts = getattr(b, "tiles", None)
            if tts is None:
                tts = getattr(b, "tile_types", [])
            for i in range(min(256, len(tts))):
                zmap[(bx0 + i % 16, by0 + i // 16)] = int(tts[i])

    results: list[dict[str, Any]] = []
    for r in rooms:
        re = int(r["elev"])
        x0, y0, x1, y1 = _norm_rect(r)
        aq_hits = 0
        water_hits = 0
        magma_hits = 0
        soil_tiles = 0
        total_tiles = (x1 - x0 + 1) * (y1 - y0 + 1)
        above_map = soil_above_by_z.get(re + WM.GAME_Z_OFFSET + 1, {})

        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                h = hazards.get((gx, gy))
                if h:
                    if re in h.get("aquifer", []):
                        aq_hits += 1
                    if re in h.get("water", {}):
                        water_hits += 1
                    if re in h.get("magma", []):
                        magma_hits += 1
                if r.get("needs_soil") and above_map:
                    tt_ab = above_map.get((gx, gy))
                    if tt_ab is not None and mats.get(tt_ab, -1) in SOIL_MATS:
                        soil_tiles += 1

        issues: list[str] = []
        if aq_hits > 0:
            issues.append(f"aquifer ({aq_hits} tiles)")
        if water_hits > 0:
            issues.append(f"water ({water_hits} tiles)")
        if magma_hits > 0:
            issues.append(f"magma ({magma_hits} tiles)")
        if r.get("needs_soil") and above_map and soil_tiles < total_tiles // 2:
            issues.append(f"insufficient potential_soil ({soil_tiles}/{total_tiles})")

        results.append(
            {
                "name": r.get("name"),
                "elev": re,
                "ok": len(issues) == 0,
                "issues": issues,
                "soil_tiles": soil_tiles if r.get("needs_soil") else None,
            }
        )
    return results


# ---------------------------------------------------------------------------
# Non-RLE aligned ASCII blueprint renderer
# ---------------------------------------------------------------------------


def _render_single_level(plan: dict[str, Any], elev: int, padding: int = 2) -> dict[str, Any]:
    rooms = [r for r in plan.get("rooms", []) if int(r["elev"]) == int(elev)]
    hub = plan.get("hub")
    hub_here = bool(hub and int(hub["elev_bottom"]) <= int(elev) <= int(hub["elev_top"]))

    rects: list[tuple[int, int, int, int]] = []
    if hub_here and hub:
        rects.append(_norm_rect(hub))
    for r in rooms:
        rects.append(_norm_rect(r))
        if r.get("corridor"):
            rects.append(_norm_rect(r["corridor"]))

    if not rects:
        return {
            "elev": int(elev),
            "bounds": None,
            "ascii": f"(elevation {elev}: empty)",
            "legend": [],
        }

    min_x = min(r[0] for r in rects) - padding
    min_y = min(r[1] for r in rects) - padding
    max_x = max(r[2] for r in rects) + padding
    max_y = max(r[3] for r in rects) + padding

    w = max_x - min_x + 1
    h = max_y - min_y + 1
    grid = [[" " for _ in range(w)] for _ in range(h)]

    def _paint(rect: dict[str, Any] | tuple[int, int, int, int], ch: str) -> None:
        rx0, ry0, rx1, ry1 = _norm_rect(rect)
        for gy in range(ry0, ry1 + 1):
            for gx in range(rx0, rx1 + 1):
                grid[gy - min_y][gx - min_x] = ch

    legend: list[dict[str, Any]] = []
    symbols = "123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

    for idx, r in enumerate(rooms):
        if r.get("corridor"):
            _paint(r["corridor"], ".")
        sym = symbols[idx % len(symbols)]
        _paint(r, sym)
        legend.append(
            {
                "symbol": sym,
                "name": r.get("name"),
                "kind": r.get("kind"),
                "rect": [int(r["x0"]), int(r["y0"]), int(r["x1"]), int(r["y1"])],
                "notes": r.get("notes", ""),
            }
        )

    if hub_here and hub:
        _paint(hub, "H")
        sx0, sy0, sx1, sy1 = _hub_shaft_rect(hub)
        for gy in range(sy0, sy1 + 1):
            for gx in range(sx0, sx1 + 1):
                grid[gy - min_y][gx - min_x] = "X"
        legend.insert(
            0,
            {
                "symbol": "X/H",
                "name": "hub",
                "kind": "hub",
                "rect": [int(hub["x0"]), int(hub["y0"]), int(hub["x1"]), int(hub["y1"])],
                "notes": f"Shaft ({hub['x']},{hub['y']}) z={hub['elev_bottom']}..{hub['elev_top']}",
            },
        )

    # Format with aligned x/y ruler (non-RLE)
    x_tens = "     " + "".join(str((x // 10) % 10) if x % 5 == 0 else " " for x in range(min_x, max_x + 1))
    x_ones = "     " + "".join(str(abs(x) % 10) for x in range(min_x, max_x + 1))
    lines = [x_tens.rstrip(), x_ones]
    for gy in range(min_y, max_y + 1):
        row_str = "".join(grid[gy - min_y])
        lines.append(f"{gy:>4d} {row_str}")

    return {
        "elev": int(elev),
        "bounds": {"x0": min_x, "y0": min_y, "x1": max_x, "y1": max_y},
        "ascii": "\n".join(lines),
        "legend": legend,
    }


def render_plan_view(
    plan: dict[str, Any],
    elev: int | None = None,
    padding: int = 2,
) -> dict[str, Any]:
    """Render non-RLE aligned floorplan(s) and vertical context for ``plan``."""
    hub = plan.get("hub")
    elevs_set: set[int] = {int(r["elev"]) for r in plan.get("rooms", [])}
    if hub:
        elevs_set.add(int(hub["elev_top"]))
        elevs_set.add(int(hub["elev_bottom"]))

    if elev is not None:
        target_elevs = [int(elev)]
    else:
        target_elevs = sorted(elevs_set, reverse=True)

    views = [_render_single_level(plan, e, padding=padding) for e in target_elevs]
    vertical_context: list[dict[str, Any]] = []
    for e in sorted(elevs_set, reverse=True):
        r_names = [r["name"] for r in plan.get("rooms", []) if int(r["elev"]) == e]
        has_hub = bool(hub and int(hub["elev_bottom"]) <= e <= int(hub["elev_top"]))
        vertical_context.append({"elev": e, "has_shaft": has_hub, "rooms": r_names})

    return {
        "save_key": plan.get("save_key"),
        "hub": hub,
        "views": views,
        "vertical_context": vertical_context,
    }


# ---------------------------------------------------------------------------
# Access-first phased execution (commit_plan)
# ---------------------------------------------------------------------------


def _hub_shaft_rect(hub: dict[str, Any]) -> tuple[int, int, int, int]:
    """Return inclusive (x0, y0, x1, y1) for the central stair shaft (2x2 when radius >= 1)."""
    hx, hy = int(hub["x"]), int(hub["y"])
    if int(hub.get("radius", 1)) >= 1:
        return (hx, hy, hx + 1, hy + 1)
    return (hx, hy, hx, hy)


def _hub_shaft_points(
    hub: dict[str, Any],
    expand_box_fn: Callable[..., list[list[int]]],
) -> list[tuple[str, list[list[int]]]]:
    """Return ordered (designation, points) batches for the central shaft."""
    sx0, sy0, sx1, sy1 = _hub_shaft_rect(hub)
    e_top = int(hub["elev_top"])
    e_bot = int(hub["elev_bottom"])
    if e_top == e_bot:
        return [("UP_DOWN_STAIR_DIG", expand_box_fn(sx0, sy0, sx1, sy1, e_top, e_top, middle_first=False))]

    batches: list[tuple[str, list[list[int]]]] = [
        ("DOWN_STAIR_DIG", expand_box_fn(sx0, sy0, sx1, sy1, e_top, e_top, middle_first=False)),
    ]
    if e_top - 1 >= e_bot + 1:
        batches.append(
            (
                "UP_DOWN_STAIR_DIG",
                expand_box_fn(sx0, sy0, sx1, sy1, e_top - 1, e_bot + 1, middle_first=False),
            )
        )
    batches.append(
        ("UP_STAIR_DIG", expand_box_fn(sx0, sy0, sx1, sy1, e_bot, e_bot, middle_first=False))
    )
    return batches


def _order_rooms_topological(rooms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order rooms parent-before-child so child doorways always connect to dug parents."""
    by_name = {r.get("name"): r for r in rooms if r.get("name")}
    ordered: list[dict[str, Any]] = []
    emitted: set[str] = set()
    remaining = list(rooms)

    while remaining:
        progressed = False
        next_rem: list[dict[str, Any]] = []
        for r in remaining:
            parent = r.get("parent", "hub")
            if parent == "hub" or parent not in by_name or parent in emitted:
                ordered.append(r)
                if r.get("name"):
                    emitted.add(str(r["name"]))
                progressed = True
            else:
                next_rem.append(r)
        if not progressed:
            ordered.extend(next_rem)
            break
        remaining = next_rem
    return ordered


async def commit_plan(
    world: Any,
    plan: dict[str, Any],
    designate_fn: Callable[[str, list[list[int]]], Awaitable[int]],
    filter_safe_fn: Callable[[list[list[int]]], Awaitable[tuple[list[list[int]], dict[str, Any]]]],
    expand_box_fn: Callable[..., list[list[int]]],
    phase: str = "auto",
    dry_run: bool = False,
    elev: int | None = None,
) -> dict[str, Any]:
    """Commit planned dig designations in access-first order.

    Phases:
    - ``shaft``: central staircase from ``elev_top`` to ``elev_bottom``.
    - ``hub_and_corridors``: hub landings at room elevations + room corridors.
    - ``rooms``: room interiors (filtered by optional ``elev``).
    - ``auto``: inspects ``world.box_state`` and runs the next incomplete phase.
    - ``all``: runs all three phases in order.
    """
    p = phase.lower()
    if p not in VALID_PHASES:
        raise ValueError(f"phase must be one of {VALID_PHASES}, got {phase!r}")

    hub = plan.get("hub")
    rooms = [
        r for r in plan.get("rooms", [])
        if elev is None or int(r["elev"]) == int(elev)
    ]

    # Determine which phases to run
    if p == "auto":
        if hub:
            sx0, sy0, sx1, sy1 = _hub_shaft_rect(hub)
            st = await world.box_state(int(hub["elev_bottom"]), sx0, sy0, sx1, sy1)
            if int(st.get("dug", 0)) == 0:
                phases_to_run = ["shaft"]
            else:
                # Check if any hub-attached corridor is dug
                corr_dug = 0
                for r in rooms:
                    if r.get("parent", "hub") != "hub":
                        continue
                    c = r.get("corridor")
                    if c:
                        cst = await world.box_state(
                            int(c["elev"]), int(c["x0"]), int(c["y0"]), int(c["x1"]), int(c["y1"])
                        )
                        corr_dug += int(cst.get("dug", 0))
                phases_to_run = ["rooms"] if corr_dug > 0 else ["hub_and_corridors"]
        else:
            phases_to_run = ["rooms"]
    elif p == "all":
        phases_to_run = ["shaft", "hub_and_corridors", "rooms"]
    else:
        phases_to_run = [p]

    executed: list[dict[str, Any]] = []
    total_designated = 0
    blocked_summary: dict[str, int] = {}

    async def _run_batch(label: str, desig: str, raw_pts: list[list[int]]) -> None:
        nonlocal total_designated
        if not raw_pts:
            return
        if desig == "DEFAULT_DIG":
            safe_pts, blocked = await filter_safe_fn(raw_pts)
            for k, v in blocked.items():
                if isinstance(v, int):
                    blocked_summary[k] = blocked_summary.get(k, 0) + v
        else:
            # Stair designations can start on walkable surface floor tiles at elev_top.
            safe_pts = raw_pts
        count = 0
        if not dry_run and safe_pts:
            count = await designate_fn(desig, safe_pts)
            total_designated += int(count)
        executed.append(
            {
                "step": label,
                "designation": desig,
                "requested": len(raw_pts),
                "safe": len(safe_pts),
                "designated": count if not dry_run else 0,
                "dry_run": dry_run,
            }
        )

    for ph in phases_to_run:
        if ph == "shaft" and hub:
            for desig, pts in _hub_shaft_points(hub, expand_box_fn):
                await _run_batch(f"shaft_{desig}", desig, pts)

        elif ph == "hub_and_corridors":
            if hub:
                room_elevs = sorted({int(r["elev"]) for r in rooms})
                sx0, sy0, sx1, sy1 = _hub_shaft_rect(hub)
                for re in room_elevs:
                    pts = expand_box_fn(
                        int(hub["x0"]), int(hub["y0"]), int(hub["x1"]), int(hub["y1"]), re
                    )
                    # Exclude shaft tiles so we don't overwrite stairs with floor dig.
                    pts = [pt for pt in pts if not (sx0 <= pt[0] <= sx1 and sy0 <= pt[1] <= sy1)]
                    await _run_batch(f"hub_landing_e{re}", "DEFAULT_DIG", pts)
            for r in rooms:
                if r.get("parent", "hub") != "hub":
                    continue
                c = r.get("corridor")
                if c:
                    pts = expand_box_fn(
                        int(c["x0"]), int(c["y0"]), int(c["x1"]), int(c["y1"]), int(c["elev"])
                    )
                    await _run_batch(f"corridor_{r['name']}", "DEFAULT_DIG", pts)
                if r.get("kind") == "corridor":
                    pts = expand_box_fn(
                        int(r["x0"]), int(r["y0"]), int(r["x1"]), int(r["y1"]), int(r["elev"])
                    )
                    await _run_batch(f"room_{r['name']}", "DEFAULT_DIG", pts)

        elif ph == "rooms":
            for r in _order_rooms_topological(rooms):
                if r.get("parent", "hub") != "hub":
                    c = r.get("corridor")
                    if c:
                        pts = expand_box_fn(
                            int(c["x0"]), int(c["y0"]), int(c["x1"]), int(c["y1"]), int(c["elev"])
                        )
                        await _run_batch(f"corridor_{r['name']}", "DEFAULT_DIG", pts)
                pts = expand_box_fn(
                    int(r["x0"]), int(r["y0"]), int(r["x1"]), int(r["y1"]), int(r["elev"])
                )
                await _run_batch(f"room_{r['name']}", "DEFAULT_DIG", pts)

    return {
        "phase_requested": p,
        "phases_run": phases_to_run,
        "dry_run": dry_run,
        "total_designated": total_designated,
        "blocked": blocked_summary,
        "batches": executed,
    }


# ---------------------------------------------------------------------------
# Quickfort integration & Bootstrap Readiness Gate
# ---------------------------------------------------------------------------


def is_dig_only_blueprint(blueprint: str, label: str | None = None) -> bool:
    """Return True for dig-only blueprints/labels (safe before Manager/buildingplan)."""
    lbl = (label or "").strip().lower()
    if lbl:
        if lbl in ("/dig", "/dig_all", "/central_stairs", "/central_stairs_constructed"):
            return True
        if lbl.startswith("/") and lbl.endswith("1") and not lbl.startswith("/surface1"):
            return True
        return False
    bp = (blueprint or "").strip().lower()
    return "dig" in bp


def check_quickfort_readiness(
    nobles_res: dict[str, Any],
    workshops_res: dict[str, Any],
    zones_res: list[dict[str, Any]],
    automation_res: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate whether the bootstrap fort has workshops, Manager, Office, and buildingplan."""
    positions = nobles_res.get("positions") or []
    has_manager = any(
        str(p.get("code", "")).upper() == "MANAGER" and not bool(p.get("vacant", True))
        for p in positions
    )
    has_office = any(
        "office" in str(z.get("type_name") or z.get("kind") or "").lower()
        for z in (zones_res or [])
    )
    ws_names = [
        str(w.get("name") or w.get("type") or "").lower()
        for w in (workshops_res.get("workshops") or [])
    ]
    has_carpenter = any("carpenter" in n for n in ws_names)
    has_stoneworker = any("mason" in n or "stoneworker" in n for n in ws_names)
    has_still = any("still" in n for n in ws_names)

    plugins = automation_res.get("plugins") or {}
    buildingplan_enabled = str(plugins.get("buildingplan", "")).strip().lower() == "on"

    missing: list[str] = []
    if not (has_carpenter and has_stoneworker):
        missing.append(
            "Cut trees (find_trees + cut_trees) and build Carpenter and Stoneworker workshops (place_building)."
        )
    if not has_still:
        missing.append("Build a Still (place_building) so dwarves have drink.")
    if not has_office:
        missing.append(
            "Craft a Chair (workshop_job action='queue'), place it (place_building), and create an Office zone (create_zone kind='office')."
        )
    if not has_manager:
        missing.append(
            "Appoint a skilled Manager (manage_nobles action='auto_assign', position='MANAGER') and assign the Office (assign_zone_owner)."
        )
    if not buildingplan_enabled:
        missing.append(
            "Enable buildingplan + early fort helpers (dfhack_automation action='apply_preset', preset='early_fort')."
        )

    return {
        "ready_for_dig": True,
        "ready_for_meta_and_orders": has_manager and has_carpenter and has_stoneworker and buildingplan_enabled,
        "checks": {
            "has_manager": has_manager,
            "has_office": has_office,
            "has_carpenter": has_carpenter,
            "has_stoneworker": has_stoneworker,
            "has_still": has_still,
            "buildingplan_enabled": buildingplan_enabled,
        },
        "missing_prerequisites": missing,
    }


def build_quickfort_args(
    subcmd: str,
    blueprint: str,
    label: str | None = None,
    x: int | None = None,
    y: int | None = None,
    elev: int | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Build argument list for DFHack `quickfort` with `elev -> z` conversion."""
    args = [subcmd, blueprint]
    if label:
        args.extend(["-n", label])
    if subcmd == "run" and x is not None and y is not None and elev is not None:
        args.extend(["-c", f"{int(x)},{int(y)},{int(elev) + WM.GAME_Z_OFFSET}"])
    if dry_run:
        args.append("--dry-run")
    return args


def parse_quickfort_list(output: str, limit: int = 40) -> list[str]:
    """Extract non-empty blueprint lines from `quickfort list` output."""
    lines = [ln.strip() for ln in (output or "").splitlines() if ln.strip()]
    return lines[: max(1, int(limit))]


# ---------------------------------------------------------------------------
# Clean flat site finder (find_clean_sites)
# ---------------------------------------------------------------------------

_CLEAN_FLOOR_GLYPHS = {".", "s", "S", "g", ",", "b", "'"}


async def find_clean_sites(
    world: Any,
    x: int,
    y: int,
    elev: int,
    width: int = 3,
    height: int = 3,
    radius: int = 16,
    max_results: int = 8,
    zones: list[dict[str, Any]] | None = None,
    require_soil: bool = False,
    tiletype_materials: dict[int, int] | None = None,
) -> dict[str, Any]:
    """Find clean, dry, flat `width x height` walkable floor footprints near `(x, y, elev)`."""
    w = max(1, int(width))
    h = max(1, int(height))
    rad = max(2, min(32, int(radius)))
    cx, cy, ce = int(x), int(y), int(elev)
    z = ce + WM.GAME_Z_OFFSET

    tx, ty, tz = await world.cache.extent()
    rx0 = max(0, cx - rad)
    rx1 = min(tx - 1, cx + rad)
    ry0 = max(0, cy - rad)
    ry1 = min(ty - 1, cy + rad)

    gen = await world._gen()
    dec = await world._dec(gen)
    z_hi = min(tz, z + 2) if require_soil else z + 1
    blocks = await world.cache.blocks(z, z_hi, gen)
    hazards = await world.hazard_map(ce, ce)
    mats = (
        tiletype_materials
        if tiletype_materials is not None
        else (await _load_tiletype_materials() if require_soil else {})
    )

    blocked: set[tuple[int, int]] = set()
    walkable: set[tuple[int, int]] = set()
    soil_floor: set[tuple[int, int]] = set()
    rock_candidates: set[tuple[int, int]] = set()
    above_soil: set[tuple[int, int]] = set()
    pending_dig = 0

    for zn in (zones or []):
        if zn.get("elev") is not None and int(zn["elev"]) != ce:
            continue
        zx0 = int(zn.get("x0", zn.get("x1", 0)))
        zy0 = int(zn.get("y0", zn.get("y1", 0)))
        zx1 = int(zn.get("x1", zx0))
        zy1 = int(zn.get("y1", zy0))
        for gx in range(min(zx0, zx1), max(zx0, zx1) + 1):
            for gy in range(min(zy0, zy1), max(zy0, zy1) + 1):
                blocked.add((gx, gy))

    if require_soil:
        for b in blocks:
            if int(b.map_z) != z + 1:
                continue
            bx0, by0 = int(b.map_x), int(b.map_y)
            if bx0 + 15 < rx0 or bx0 > rx1 or by0 + 15 < ry0 or by0 > ry1:
                continue
            tts = getattr(b, "tiles", None)
            if tts is None:
                tts = getattr(b, "tile_types", [])
            for i in range(min(256, len(tts))):
                gx = bx0 + (i % 16)
                gy = by0 + (i // 16)
                if rx0 <= gx <= rx1 and ry0 <= gy <= ry1:
                    if mats.get(int(tts[i]), -1) in SOIL_MATS:
                        above_soil.add((gx, gy))

    for b in blocks:
        if int(b.map_z) != z:
            continue
        bx0, by0 = int(b.map_x), int(b.map_y)
        if bx0 + 15 < rx0 or bx0 > rx1 or by0 + 15 < ry0 or by0 > ry1:
            continue
        for bb in getattr(b, "buildings", []) or []:
            for gx in range(int(bb.pos_x_min), int(bb.pos_x_max) + 1):
                for gy in range(int(bb.pos_y_min), int(bb.pos_y_max) + 1):
                    blocked.add((gx, gy))
        tts = getattr(b, "tiles", None)
        if tts is None:
            tts = getattr(b, "tile_types", [])
        hid_arr = getattr(b, "hidden", [])
        wat_arr = getattr(b, "water", [])
        mag_arr = getattr(b, "magma", [])
        dig_arr = getattr(b, "dig", [])
        for i in range(min(256, len(tts))):
            gx = bx0 + (i % 16)
            gy = by0 + (i // 16)
            if gx < rx0 or gx > rx1 or gy < ry0 or gy > ry1:
                continue
            if i < len(dig_arr) and int(dig_arr[i]) > 0:
                pending_dig += 1
                blocked.add((gx, gy))
                continue
            if (i < len(wat_arr) and int(wat_arr[i]) > 0) or (i < len(mag_arr) and bool(mag_arr[i])):
                blocked.add((gx, gy))
                continue
            hz = hazards.get((gx, gy))
            if hz and (ce in hz.get("aquifer", []) or ce in hz.get("water", {}) or ce in hz.get("magma", [])):
                blocked.add((gx, gy))
                continue
            if i < len(hid_arr) and int(hid_arr[i]):
                blocked.add((gx, gy))
                if require_soil and (gx, gy) in above_soil:
                    rock_candidates.add((gx, gy))
                continue
            tt = int(tts[i])
            glyph = dec.tile_class(tt).glyph if hasattr(dec, "tile_class") else "."
            if glyph in _CLEAN_FLOOR_GLYPHS and (gx, gy) not in blocked:
                walkable.add((gx, gy))
                if require_soil and mats.get(tt, -1) in SOIL_MATS:
                    soil_floor.add((gx, gy))
            else:
                if require_soil and glyph in ("#", "?") and (gx, gy) not in blocked and (gx, gy) in above_soil:
                    rock_candidates.add((gx, gy))
                blocked.add((gx, gy))

    def _pick_rects(valid_tiles: set[tuple[int, int]]) -> list[dict[str, Any]]:
        cands: list[tuple[float, dict[str, Any]]] = []
        for x0 in range(rx0, rx1 - w + 2):
            for y0 in range(ry0, ry1 - h + 2):
                if all((gx, gy) in valid_tiles for gx in range(x0, x0 + w) for gy in range(y0, y0 + h)):
                    rcx = x0 + (w - 1) / 2.0
                    rcy = y0 + (h - 1) / 2.0
                    d2 = (rcx - cx) ** 2 + (rcy - cy) ** 2
                    cands.append(
                        (
                            d2,
                            {
                                "x": x0,
                                "y": y0,
                                "x1": x0 + w - 1,
                                "y1": y0 + h - 1,
                                "dist": round(d2**0.5, 1),
                            },
                        )
                    )
        cands.sort(key=lambda item: (item[0], item[1]["y"], item[1]["x"]))
        picked: list[dict[str, Any]] = []
        for _, cand in cands:
            if any(
                not (cand["x1"] < s["x"] or s["x1"] < cand["x"] or cand["y1"] < s["y"] or s["y1"] < cand["y"])
                for s in picked
            ):
                continue
            picked.append(cand)
            if len(picked) >= max(1, int(max_results)):
                break
        return picked

    floor_pool = soil_floor if require_soil else (walkable - blocked)
    selected = _pick_rects(floor_pool)
    dig_to_soil_sites = _pick_rects(rock_candidates) if require_soil else []

    hint = ""
    if require_soil:
        if not selected and dig_to_soil_sites:
            hint = "Dig one of dig_to_soil_sites to create an underground soil floor, then place_building(building='Farm Plot')."
        elif not selected and pending_dig > 0:
            hint = "Wait for miners to finish excavating (pending_dig_tiles > 0) or scan another elevation."
        elif not selected:
            hint = "No ready soil floor or dig-to-soil footprint found in window; try fort_strata farm_elev."
    else:
        if not selected and pending_dig > 0:
            hint = "Wait for miners to finish excavating (pending_dig_tiles > 0) or scan another elevation."
        elif not selected:
            hint = "No flat dry floor footprint found in window; try another elevation or widen radius."

    out: dict[str, Any] = {
        "elev": ce,
        "size": [w, h],
        "center": [cx, cy],
        "radius": rad,
        "require_soil": require_soil,
        "pending_dig_tiles": pending_dig,
        "sites_found": len(selected),
        "sites": selected,
        "hint": hint,
    }
    if require_soil:
        out["dig_to_soil_sites"] = dig_to_soil_sites
    return out
