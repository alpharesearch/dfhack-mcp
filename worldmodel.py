"""
World model: the preprocessing layer between DFHack's raw RPC protos and the LLM.

Why this file exists
--------------------
Measured on the live save (DF 53.17-r1, region1, 192x192 tiles, 191 z, 22,127
discovered blocks), talking to `global-default` = Qwen3.8 Flash-Next IQ3_XXS with
n_ctx 524,288:

    get_block_list, ONE z-level    ~588 KB   ~147,000 tokens   <- 28% of the whole context
    the whole map, all z           ~120 MB   ~30,000,000 tokens <- 57x the context
    objects-only for that z            3 KB      ~800 tokens   <- same planning content
    block minimap, ALL z              37 KB     ~9,000 tokens

Raw tile arrays must never reach the model. This module reads the protos, aggregates
them into a semantic model (regions, clusters, rollups), and projects that model at
several zoom levels, each under a token budget, as compact plain text (text is 2-4x
cheaper than JSON for the same content).

Design rules
------------
* Coordinates: RPC z = game elevation + GAME_Z_OFFSET. Everything in and out of this
  module uses **elevation**; the offset never reaches the model.
* MapBlock grid arrays are row-major with x fastest: i = (x - map_x) + (y - map_y)*16.
* RemoteFortressReader.GetBlockList treats max_z as EXCLUSIVE, and a span-1 request
  returns nothing. Fetching z-range [a,b] must request max_z = b+1. (Verified live:
  165-165 -> 0 blocks; 164-165 -> only z=164; 163-165 -> z=163,164.)
* A full-map request over all z overflows the plugin reply limit
  ("reply too large: 853660541" -> CR_LINK_FAILURE, the socket dies). Windows are
  capped at MAX_Z_WINDOW levels.
* x/y windowing in the request is broken (returns zero blocks), so we always fetch
  full x/y and crop in Python. The crop is cheap; the fetch is cached.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter, defaultdict, deque

# ONE ELEVATION UNIT: the number in the player's map panel.
# Measured on this save: RPC z=162 shows as "Elevation 33", z=167 as "Elevation 38", so
# in-game Elevation = z - 129. Everything the layer publishes - parameters, band rows,
# hazards, depths, dig boxes, script arguments - uses that number. The conversion lives
# here and nowhere else:   z = elev + GAME_Z_OFFSET   (elev is always the in-game number)
GAME_Z_OFFSET = 129
UI_Z_OFFSET = GAME_Z_OFFSET          # alias so older call sites read correctly


def dec_name(b, it):
    """Cheap item label; the rollup needs a type, not a decode."""
    return str(getattr(it, "type", "?"))


def ui_elev(z: int) -> int:
    """The in-game Elevation for an RPC z - the same number the layer takes as input."""
    return int(z) - UI_Z_OFFSET


MAX_Z_WINDOW = 12            # reply-size guard: 12 z-levels of full map is ~8.6 MB
CACHE_TTL_S = 20.0           # seconds a fetched z-chunk is trusted
CHARS_PER_TOKEN = 4          # rough local-tokenizer estimate
DEFAULT_MAX_TOKENS = 2000
HARD_MAX_TOKENS = 30000

# ---------------------------------------------------------------------------
# Tile classification: 697 tiletype names -> a small, stable glyph alphabet.
#
# Rules are matched in order against the tiletype NAME (first match wins), so the
# specific compound forms come first: `*RampTrack` contains both Ramp and Track and
# must classify as a track; `*WallSmooth` must beat the generic Floor rules, etc.
#
# `walkable` drives region flood-fill. It is deliberately conservative: water/magma
# depth and `hidden` are applied separately in `is_walkable`.
# ---------------------------------------------------------------------------

class TileClass:
    __slots__ = ("key", "glyph", "solid", "walkable", "note")

    def __init__(self, key, glyph, solid, walkable, note):
        self.key = key
        self.glyph = glyph
        self.solid = solid
        self.walkable = walkable
        self.note = note

    def __repr__(self):
        return f"TileClass({self.key!r},{self.glyph!r})"


_RULES = [
    # (pattern, class key, glyph, solid, walkable, legend note)
    ("Unused",             "unused",        "?", True,  False, "unused raw tile (data artifact)"),
    ("UnderworldGate",     "gate",          "G", True,  False, "underworld gate stairs"),
    ("TreeCapInterior",    "tree_interior", "T", False, True,  "inside a tree canopy"),
    ("TreeCapWall",        "tree_cap_wall", "T", True,  False, "tree canopy wall"),
    ("TreeCapPillar",      "tree_pillar",   "p", True,  False, "tree pillar"),
    ("TreeCap",            "tree_cap",      "T", False, True,  "tree canopy"),
    ("TreeTrunk",          "tree_trunk",    "T", False, True,  "tree trunk"),
    ("TreeBranch",         "tree_branch",   "T", False, True,  "tree branch"),
    ("TreeDead",           "tree_dead",     "t", False, True,  "dead tree"),
    ("Shrub",              "shrub",         ",", False, True,  "shrub"),
    ("Chasm",              "chasm",         "O", False, False, "chasm: fall hazard, not floor"),
    ("EeriePit",           "eerie_pit",     "O", False, False, "eerie pit: fall hazard"),
    ("GlowingBarrier",     "barrier",       "X", True,  False, "glowing primordial barrier"),
    ("MagmaFlow",          "magma",         "!", True,  False, "magma flow"),
    ("SemiMoltenRock",     "molten",        "M", True,  False, "semi-molten rock"),
    ("Campfire",           "campfire",      "f", False, True,  "campfire"),
    ("Fire",               "fire",          "f", False, True,  "fire"),
    ("Ashes",              "ashes",         "a", False, True,  "ashes"),
    ("Driftwood",          "driftwood",     ",", False, True,  "driftwood"),
    ("TreeRootSloping",    "roots_ramp",    "/", False, True,  "sloping tree roots (walkable slope)"),
    ("TreeRoots",          "roots",         ",", False, True,  "tree roots"),
    ("TreeTwigs",          "twigs",         ",", False, True,  "tree twigs"),
    ("SaplingDead",        "tree_dead",     "t", False, True,  "dead sapling"),
    ("Sapling",            "sapling",       ",", False, True,  "sapling"),
    ("RampTop",            "ramp_top",      "^", False, True,  "ramp top: refuses designations"),
    ("Track",              "track",         "-", False, True,  "minecart track"),
    ("StairUD",            "stairs_ud",     "=", False, True,  "up-down stairs"),
    ("StairU",             "stairs_up",     "<", False, True,  "up stairs"),
    ("StairD",             "stairs_down",   ">", False, True,  "down stairs"),
    ("Stair",              "stairs",        "=", False, True,  "stairs"),
    ("Ramp",               "ramp",          "/", False, True,  "ramp: carries flow, walkable downhill"),
    ("Fortification",      "fortification", "F", False, True,  "fortified floor"),
    ("WallSmooth",         "smooth_wall",   "#", True,  False, "smoothed wall (engraving target)"),
    ("Wall",               "wall",          "#", True,  False, "natural wall (minable)"),
    ("Pillar",             "pillar",        "p", True,  False, "pillar"),
    ("Boulder",            "boulder",       "B", True,  False, "boulder (removable)"),
    ("Pebbles",            "pebbles",       "c", False, True,  "pebbles"),
    ("ConstructedFloor",   "constructed",   "+", False, True,  "constructed floor"),
    ("FloorSmooth",        "smooth_floor",  "'", False, True,  "smoothed floor"),
    ("Floor",              "floor",         ".", False, True,  "floor"),
    ("SoilWet",            "wet_soil",      "w", False, True,  "wet soil (muddy)"),
    ("Soil",               "soil",          "s", False, True,  "soil (diggable, not minable)"),
    ("Grass",              "grass",         "g", False, True,  "grass"),
    ("RiverSource",        "water",         "~", False, True,  "river source"),
    ("Waterfall",          "falls",         ":", False, True,  "waterfall"),
    ("River",              "water",         "~", False, True,  "river"),
    ("Brook",              "water",         "~", False, True,  "brook"),
    ("MurkyPool",          "water",         "~", False, True,  "murky pool"),
    ("OpenSpace",          "open",          " ", False, True,  "open space (subterranean air)"),
    ("Void",               "void",          "o", False, False, "void: falling hazard, not floor"),
]

_COMPILED = [(re.compile(rule[0]), rule[1:]) for rule in _RULES]
_OTHER = TileClass("other", "?", False, True, "unclassified tiletype")

_CLASS_BY_NAME: dict[str, TileClass] = {}


def classify_tile(name: str) -> TileClass:
    """Map a RemoteFortressReader tiletype name to a semantic class + glyph."""
    hit = _CLASS_BY_NAME.get(name)
    if hit is not None:
        return hit
    for rx, (key, glyph, solid, walkable, note) in _COMPILED:
        if rx.search(name):
            hit = TileClass(key, glyph, solid, walkable, note)
            break
    else:
        hit = _OTHER
    _CLASS_BY_NAME[name] = hit
    return hit


def coverage_report(names) -> tuple[int, dict[str, list[str]]]:
    """(classified_count, {class_key: [example names]}). Used by tests to prove the
    glyph alphabet covers the live 697-name vocabulary."""
    buckets = defaultdict(list)
    n = 0
    for name in names:
        c = classify_tile(name)
        buckets[c.key].append(name)
        if c is not _OTHER:
            n += 1
    return n, dict(buckets)


def legend(max_lines: int = 30) -> list[str]:
    """Stable glyph legend, grouped by glyph so it is short enough to emit once per
    session. Stable prefix text keeps the model's prompt cache warm."""
    by_glyph: dict[str, dict] = {}
    for _pattern, key, glyph, solid, walkable, note in _RULES:
        e = by_glyph.setdefault(glyph, {"keys": [], "notes": [], "solid": solid, "walk": walkable})
        e["keys"].append(key)
        if note not in e["notes"]:
            e["notes"].append(note)
    lines = []
    for glyph in sorted(by_glyph, key=lambda g: (-len(by_glyph[g]["keys"]), g)):
        e = by_glyph[glyph]
        keys = "/".join(e["keys"][:3])
        note = e["notes"][0]
        walk = "walk" if e["walk"] else ("solid" if e["solid"] else "blocked")
        lines.append(f"{glyph}={keys} ({walk}): {note}")
    lines.append("~=water (!=magma) depth 1-7 from the tile arrays, not the tiletype")
    lines.append("?=hidden (undiscovered: reads as empty; run the DFHack `reveal` command)")
    lines.append("d = pending dig designation (overlay, not a tiletype)")
    lines.append("B = building on this tile (overlay: stockpile, workshop, bed, zone, ...)")
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    return lines


# ---------------------------------------------------------------------------
# Decode tables: turn RPC integers into names a model can reason about.
# Loaded lazily per connection generation; each is a cheap single RPC.
# ---------------------------------------------------------------------------

class Decoders:
    """Names for tiletypes, item types, materials, buildings, professions, flags."""

    def __init__(self):
        self._gen = -1
        self.tile: dict[int, str] = {}
        self.tile_caption: dict[int, str] = {}
        self.item: dict[tuple[int, int], str] = {}
        self.material: dict[tuple[int, int], str] = {}
        self.building: dict[tuple[int, int, int], str] = {}
        self.building_bt: dict[int, str] = {}
        self.building_bts: dict[tuple[int, int], str] = {}
        self.creature: dict[int, str] = {}
        self.creature_id: dict[int, str] = {}
        self.profession: dict[int, str] = {}
        self.flags: dict[str, dict[int, str]] = {}
        self._map_extent: tuple[int, int, int] | None = None

    async def load(self, gen: int) -> "Decoders":
        if self._gen == gen and self.tile:
            return self
        # imports here keep worldmodel importable (and testable) without the game
        from dfhack_client_python.dfhack import (GetTiletypeList, GetItemList,
                                                GetMaterialList, GetBuildingDefList, ListJobSkills, ListEnums)
        tt = await GetTiletypeList()
        self.tile = {i: t.name for i, t in enumerate(tt.tiletype_list)}
        self.tile_caption = {i: t.caption for i, t in enumerate(tt.tiletype_list)}
        il = await GetItemList()
        self.item = {(m.mat_pair.mat_type, m.mat_pair.mat_index): m.id for m in il.material_list}
        ml = await GetMaterialList()
        self.material = {(m.mat_pair.mat_type, m.mat_pair.mat_index): m.id for m in ml.material_list}
        bd = await GetBuildingDefList()
        # Def list keys are (building_type, subtype, custom) and MANY entries are
        # placeholders named 'NONE' (verified live: ('NONE',-1,-1,-1), ('Chair',0,-1,-1),
        # ('Furnace',5,...) colliding with a 'NONE' def). Instances carry real subtype
        # ints, so an exact key often hits a 'NONE' row. Keep real names, and build
        # fallbacks by (bt, subtype) and bt.
        self.building = {}
        by_bt, by_bts = {}, {}
        for b in bd.building_list:
            k = (b.building_type.building_type, b.building_type.building_subtype,
                 b.building_type.building_custom)
            name = b.name
            if name and name != "NONE":
                self.building.setdefault(k, name)
                by_bt.setdefault(k[0], name)
                by_bts.setdefault((k[0], k[1]), name)
        self.building_bt = by_bt
        self.building_bts = by_bts
        self.creature = {}
        self.creature_id = {}
        # GetCreatureRaws answers 18.8 MB for 1327 raws and the RPC reply limit rejects
        # it, so fetch the index -> species table in small chunks and keep only names.
        try:
            from dfhack_client_python.dfhack import GetCreatureRaws, GetPartialCreatureRaws
            from dfhack_client_python.py_export.RemoteFortressReader_pb2 import ListRequest
            try:
                cr = await GetCreatureRaws()
                raws = list(cr.creature_raws)
            except Exception:
                raws = []
                for start in range(0, 6000, 150):
                    cr = await GetPartialCreatureRaws(input=ListRequest(
                        list_start=start, list_end=start + 149))
                    if not cr.creature_raws:
                        break
                    raws.extend(cr.creature_raws)
            for r in raws:
                self.creature_id[r.index] = r.creature_id
                self.creature[r.index] = (r.name[0] if len(r.name) and r.name[0]
                                           else r.creature_id)
        except Exception:
            pass
        try:
            js = await ListJobSkills()
            self.profession = {p.id: p.caption for p in js.profession}
        except Exception:
            self.profession = {}
        try:
            en = await ListEnums()
            # EnumItemName is {value, name, bit_size} - NOT {id, name}. Reading e.id
            # raises AttributeError, and the old bare `except` silently left every
            # table empty (verified: labor_list answered count 0).
            def table(items):
                out = {}
                for e in items:
                    v = getattr(e, "value", None)
                    if v is None:
                        v = getattr(e, "id", None)
                    if v is not None:
                        out[int(v)] = e.name
                return out
            self.flags = {"flags1": table(en.unit_flags1),
                          "flags2": table(en.unit_flags2),
                          "flags3": table(en.unit_flags3),
                          "unit_labor": table(en.unit_labor)}
        except Exception:
            self.flags = {"flags1": {}, "flags2": {}, "flags3": {}, "unit_labor": {}}
        self._gen = gen
        return self

    # --- accessors, all total (no KeyError at the model boundary) ---
    def tile_name(self, tid: int) -> str:
        return self.tile.get(tid, f"id{tid}")

    def tile_class(self, tid: int) -> TileClass:
        return classify_tile(self.tile_name(tid))

    def item_name(self, mp) -> str:
        return self.item.get((mp.mat_type, mp.mat_index), f"type({mp.mat_type},{mp.mat_index})")

    def material_name(self, mp) -> str:
        full = self.material.get((mp.mat_type, mp.mat_index), "")
        if not full:
            # Generated-creature materials (REMAINS, bones) are absent from the static
            # list (verified live: mat_type 434 has no entry). Say so in words a model
            # can use instead of emitting a raw int pair it cannot interpret.
            return self.MAT_TYPE_HINT.get(mp.mat_type, "an unlisted material")
        return full.split(":")[-1]          # 'INORGANIC:IRON' -> 'IRON'

    MAT_TYPE_HINT = {0: "metal/stone", 1: "wood", 2: "plant", 3: "cloth",
                     4: "gem", 5: "pearl", 6: "skin", 7: "bone", 8: "shell",
                     9: "leather", 10: "tallow", 11: "residual", 12: "silk"}

    def building_name(self, bt) -> str:
        k = (bt.building_type, bt.building_subtype, bt.building_custom)
        return (self.building.get(k)
                or self.building_bts.get(k[:2])
                or self.building_bt.get(k[0])
                or f"building#{k[0]}")

    def labor_name(self, lid: int) -> str:
        return self.flags.get("unit_labor", {}).get(lid, f"labor{lid}")

    def race_name(self, race_index: int) -> str:
        """Species name from the BasicApi race index (RFR's UnitDefinition.race is a
        MatPair and is NOT the species: verified live, a dwarf reports (572,1))."""
        return self.creature.get(race_index) or self.creature_id.get(race_index) or f"race{race_index}"

    def profession_name(self, pid: int) -> str:
        return self.profession.get(pid, f"prof{pid}")

    def flag_names(self, which: str, bits: int, limit: int = 6) -> list[str]:
        table = self.flags.get(which, {})
        out = [table[b] for b in sorted(table) if bits >> b & 1 and table[b]]
        return out[:limit]


# ---------------------------------------------------------------------------
# Fetch layer: z-chunk cache with the exclusive-max_z fix.
# ---------------------------------------------------------------------------

class FetchError(Exception):
    pass


def window_request(z0: int, z1: int, z_count: int) -> tuple[int, int]:
    """Bounds to send to GetBlockList so that elevations [z0..z1] are all returned.

    RemoteFortressReader answers for z in [min_z, max_z) - max_z is EXCLUSIVE - and a
    span-1 request answers NOTHING. Verified live on 53.17-r1:
        165,165 -> 0 blocks        164,165 -> only z=164
        163,165 -> z=163,164       156,167 -> z=156..166
    So: ask for z1+1 to include z1, and keep the span >= 2 by walking min back.
    """
    req_max = min(int(z1) + 1, int(z_count))
    req_min = int(z0)
    if req_max - req_min < 2:
        # Extend forward when the map allows it, and only walk min_z backwards at the
        # very bottom of the map (a span-1 request answers NOTHING).
        req_max = min(req_max + (2 - (req_max - req_min)), int(z_count))
        if req_max - req_min < 2:
            req_min = max(0, req_max - 2)
    return req_min, req_max


class WorldCache:
    """Caches full-x/y z-chunks of MapBlock, because x/y windowing is broken upstream
    and a 12-z full-map fetch costs ~8 MB - we do not want to repeat it per tool call."""

    def __init__(self):
        self._chunks: dict[int, list] = {}
        self._at: dict[int, float] = {}
        self._gen = -1
        self._lock = asyncio.Lock()
        self.tile_x = self.tile_y = self.z_count = 0

    def reset(self):
        self._chunks.clear()
        self._at.clear()
        self._gen = -1

    async def extent(self) -> tuple[int, int, int]:
        import time
        from dfhack_client_python.dfhack import GetMapInfo
        mi = await GetMapInfo()
        self.tile_x, self.tile_y, self.z_count = mi.block_size_x * 16, mi.block_size_y * 16, mi.block_size_z
        return self.tile_x, self.tile_y, self.z_count

    def _chunk_of(self, z: int) -> int:
        return z // MAX_Z_WINDOW

    async def _get_blocks(self, z0: int, z1: int) -> list:
        from dfhack_client_python.dfhack import GetBlockList
        from dfhack_client_python.py_export.RemoteFortressReader_pb2 import BlockRequest
        req_min, req_max = window_request(z0, z1, self.z_count)
        bl = await GetBlockList(input=BlockRequest(
            min_x=0, max_x=self.tile_x - 1, min_y=0, max_y=self.tile_y - 1,
            min_z=req_min, max_z=req_max, force_reload=True))
        return list(bl.map_blocks)

    async def _fetch(self, z0: int, z1: int, depth: int = 0) -> list:
        """Fetch a z window, splitting it in half when the plugin rejects the reply.

        A reply over ~850 MB makes RemoteFortressReader answer CR_LINK_FAILURE, which
        kills the TCP socket - so every failure path must also recover the connection,
        not just report the error.
        """
        from dfhack_client_python.dfhack_remote import (DFHackError, DFHackTransportError,
                                                        drop_connection, connect)
        try:
            return await self._get_blocks(z0, z1)
        except (DFHackError, DFHackTransportError) as e:
            try:
                drop_connection()
                await connect()
            except Exception:
                pass
            if z1 - z0 <= 1 or depth >= 3:
                raise FetchError(f"z{z0}-{z1} rejected by the plugin ({e}); read a smaller window")
            mid = z0 + (z1 - z0) // 2
            out = await self._fetch(z0, mid, depth + 1)
            try:
                out += await self._fetch(mid + 1, z1, depth + 1)
            except FetchError:
                pass
            return out

    async def blocks(self, z0: int, z1: int, gen: int, force: bool = False) -> list:
        """MapBlocks covering elevations [z0-GAME_Z_OFFSET .. z1-GAME_Z_OFFSET] inclusive,
        fetched with the exclusive-max_z fix and cached per 12-z chunk."""
        import time
        from dfhack_client_python.dfhack import GetBlockList
        from dfhack_client_python.py_export.RemoteFortressReader_pb2 import BlockRequest

        if self.z_count == 0:
            await self.extent()
        z0 = max(0, min(int(z0), self.z_count - 1))
        z1 = max(0, min(int(z1), self.z_count - 1))
        if z1 < z0:
            z0, z1 = z1, z0

        if gen != self._gen:                       # game restarted: nothing is trustworthy
            self.reset()
            self._gen = gen
            if self.z_count == 0:
                await self.extent()

        now = time.monotonic()
        async with self._lock:
            for c in range(self._chunk_of(z0), self._chunk_of(z1) + 1):
                stale = c not in self._chunks or (now - self._at.get(c, 0.0)) > CACHE_TTL_S
                if not (stale or force):
                    continue
                lo = c * MAX_Z_WINDOW
                hi = min(lo + MAX_Z_WINDOW - 1, self.z_count - 1)
                blocks = await self._fetch(lo, hi)
                # The +2 overshoot spills into the NEXT chunk's z-levels. Keep only this
                # chunk's nominal range, or a shared level is counted twice (measured
                # live: a boundary level showed 288 blocks on a 12x12 map).
                self._chunks[c] = [b for b in blocks if lo <= b.map_z <= hi]
                self._at[c] = time.monotonic()
            out = []
            for c in range(self._chunk_of(z0), self._chunk_of(z1) + 1):
                out.extend(b for b in self._chunks.get(c, []) if z0 <= b.map_z <= z1)
        return out


# ---------------------------------------------------------------------------
# Aggregation: blocks -> per-level models with regions, clusters and rollups.
# ---------------------------------------------------------------------------

def tile_index(x: int, y: int, map_x: int, map_y: int) -> int:
    """Index into a MapBlock's 16x16 arrays. Verified against in-game designations."""
    return (x - map_x) + (y - map_y) * 16


def tile_at(i: int) -> tuple[int, int]:
    return i % 16, i // 16


def block_origin(b) -> tuple[int, int]:
    """map_x/map_y are TILE coordinates (multiples of 16)."""
    return b.map_x, b.map_y


class LevelModel:
    """One z-level, aggregated: glyph grid, census, regions, designations, objects."""

    def __init__(self, z: int, dec: Decoders):
        self.z = z
        self.elev = z - GAME_Z_OFFSET
        self.dec = dec
        self.glyph: list[str] = []          # rows of chars, 1 per tile
        self.rows = 0
        self.cols = 0
        self.census: Counter = Counter()    # class key -> tile count
        self.walkable: list[bool] = []
        self.regions: list[dict] = []
        self.designations: list[tuple[int, int, int]] = []   # (x, y, designation)
        self.clusters: list[dict] = []
        self.buildings: list[dict] = []
        self.items: list[dict] = []
        self.item_rollup: Counter = Counter()
        self.units: list[dict] = []
        self.water_tiles = 0
        self.magma_tiles = 0
        self.hidden_tiles = 0
        self.flowing = 0
        self.block_count = 0

    def obj_count(self) -> int:
        return len(self.designations) + len(self.buildings) + len(self.items) + len(self.units)


_DIG = None


def dig_names():
    """RemoteFortressReader DigDesignation enum, number -> name."""
    global _DIG
    if _DIG is None:
        from dfhack_client_python.py_export.RemoteFortressReader_pb2 import DigCommand
        _DIG = {v.number: v.name for v in
                DigCommand.DESCRIPTOR.fields_by_name['designation'].enum_type.values}
    return _DIG


def build_level(z: int, blocks: list, dec: Decoders) -> LevelModel:
    """Aggregate the MapBlocks of one z-level. O(tiles), pure CPU, no RPC."""
    lv = LevelModel(z, dec)
    by_block = {}
    ys = sorted({b.map_y for b in blocks})
    xs = sorted({b.map_x for b in blocks})
    if not xs or not ys:
        return lv
    y0, x0 = ys[0], xs[0]
    ny = (ys[-1] - y0) // 16 + 1
    nx = (xs[-1] - x0) // 16 + 1
    lv.rows, lv.cols = ny * 16, nx * 16
    grid = [[" "] * lv.cols for _ in range(lv.rows)]
    walk = [[False] * lv.cols for _ in range(lv.rows)]
    dig = dig_names()

    for b in blocks:
        by_block[(b.map_x, b.map_y)] = b
        ox, oy = block_origin(b)
        gx, gy = (ox - x0) // 16, (oy - y0) // 16
        water = list(b.water) if len(b.water) >= 256 else [0] * 256
        magma = list(b.magma) if len(b.magma) >= 256 else [0] * 256
        hidden = list(b.hidden) if len(b.hidden) >= 256 else [False] * 256
        des = list(b.tile_dig_designation)
        for i, tid in enumerate(b.tiles):
            cls = dec.tile_class(tid)
            lv.census[cls.key] += 1
            w, m = water[i], magma[i]
            lv.water_tiles += 1 if w > 0 else 0
            lv.magma_tiles += 1 if m > 0 else 0
            lv.hidden_tiles += 1 if hidden[i] else 0
            g = cls.glyph
            if m > 0:
                g = "!"
            elif w > 0:
                g = "~"
            if hidden[i]:
                g = "?"
            tx, ty = tile_at(i)
            grid[gy * 16 + ty][gx * 16 + tx] = g
            walk[gy * 16 + ty][gx * 16 + tx] = cls.walkable and w == 0 and m == 0 and not hidden[i]
        for i, v in enumerate(des):
            if v:
                tx, ty = tile_at(i)
                lv.designations.append((ox + tx, oy + ty, v))
        for bd in b.buildings:
            lv.buildings.append({"x": bd.pos_x_min, "y": bd.pos_y_min, "z": bd.pos_z_min,
                                 "name": dec.building_name(bd.building_type),
                                 "w": bd.pos_x_max - bd.pos_x_min + 1,
                                 "h": bd.pos_y_max - bd.pos_y_min + 1,
                                 "items": len(bd.items), "active": bd.active,
                                 "room": bool(bd.is_room)})
        for it in b.items:
            nm = dec.item_name(it.type)
            mat = dec.material_name(it.material)
            key = f"{nm}/{mat}"
            lv.item_rollup[key] += max(1, it.stack_size)
            lv.items.append({"x": it.pos.x, "y": it.pos.y, "name": nm, "mat": mat,
                             "stack": it.stack_size})
        lv.flowing += len(b.flows)
    lv.block_count = len(blocks)
    lv.grid = grid
    lv.walk = walk
    lv.origin = (x0, y0)
    lv.regions = find_regions(lv, grid, walk, x0, y0)
    lv.clusters = cluster_designations(lv.designations)
    return lv


def find_regions(lv: LevelModel, grid, walk, x0: int, y0: int) -> list[dict]:
    """4-connected flood fill over walkable tiles. Regions are how a player model
    should think: 'the stairwell room', not 'x52-60,y114-120'."""
    rows, cols = lv.rows, lv.cols
    seen = [[False] * cols for _ in range(rows)]
    out = []
    for sy in range(rows):
        for sx in range(cols):
            if not walk[sy][sx] or seen[sy][sx]:
                continue
            q = deque([(sx, sy)])
            seen[sy][sx] = True
            n = 0
            minx = maxx = x0 + sx
            miny = maxy = y0 + sy
            while q:
                cx, cy = q.popleft()
                n += 1
                minx = min(minx, x0 + cx); maxx = max(maxx, x0 + cx)
                miny = min(miny, y0 + cy); maxy = max(maxy, y0 + cy)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx2, ny2 = cx + dx, cy + dy
                    if 0 <= nx2 < cols and 0 <= ny2 < rows and walk[ny2][nx2] and not seen[ny2][nx2]:
                        seen[ny2][nx2] = True
                        q.append((nx2, ny2))
            out.append({"n": n, "x0": minx, "x1": maxx, "y0": miny, "y1": maxy})
    out.sort(key=lambda r: -r["n"])
    for i, r in enumerate(out[:26]):
        r["id"] = chr(ord("A") + i)
    return out[:26]


def cluster_designations(des: list[tuple[int, int, int]]) -> list[dict]:
    """Group loose designation tiles into connected clusters with bounding boxes, so
    the model sees 'dig 24 tiles at x54-55,y118' instead of 24 coordinates."""
    if not des:
        return []
    cells = {(x, y): d for x, y, d in des}
    seen = set()
    out = []
    for (x, y) in cells:
        if (x, y) in seen:
            continue
        q = deque([(x, y)])
        seen.add((x, y))
        group = []
        while q:
            cx, cy = q.popleft()
            group.append((cx, cy))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                p = (cx + dx, cy + dy)
                if p in cells and p not in seen:
                    seen.add(p)
                    q.append(p)
        kinds = Counter(cells[p] for p in group)
        xs = [p[0] for p in group]; ys = [p[1] for p in group]
        out.append({"n": len(group), "x0": min(xs), "x1": max(xs),
                    "y0": min(ys), "y1": max(ys),
                    "kinds": {dig_names().get(k, str(k)): c for k, c in kinds.items()}})
    out.sort(key=lambda c: -c["n"])
    return out


# ---------------------------------------------------------------------------
# Token budget: every projection is capped, and truncation is announced.
# ---------------------------------------------------------------------------

class Budget:
    def __init__(self, max_tokens: int | None):
        t = int(max_tokens or DEFAULT_MAX_TOKENS)
        t = max(100, min(t, HARD_MAX_TOKENS))
        self.max_tokens = t
        self.left = t * CHARS_PER_TOKEN
        self.truncated = []

    def allow(self, need: int) -> bool:
        return self.left >= need

    def spend(self, n: int):
        self.left -= n

    def note(self, what: str, dropped: int):
        self.truncated.append(f"{what} +{dropped} more")

    def footer(self) -> list[str]:
        if not self.truncated:
            return []
        used = self.max_tokens - max(0, self.left) // CHARS_PER_TOKEN
        return [f"[budget {used}/{self.max_tokens} tokens: {'; '.join(self.truncated[:6])}"
                f" - narrow the window or raise max_tokens]"]


def est_tokens(payload) -> int:
    import json
    return len(json.dumps(payload)) // CHARS_PER_TOKEN


def blocks_to_lines(lv: LevelModel, x0: int, x1: int, y0: int, y1: int,
                    rle: bool = True) -> list[str]:
    """Crop the level glyph grid to an x/y window, optionally run-length encoded."""
    ox, oy = lv.origin
    lo_x, hi_x = max(x0, ox), min(x1, ox + lv.cols - 1)
    lo_y, hi_y = max(y0, oy), min(y1, oy + lv.rows - 1)
    lines = []
    for gy in range(lo_y - oy, hi_y - oy + 1):
        row = "".join(lv.grid[gy][lo_x - ox:hi_x - ox + 1])
        if rle:
            row = _rle(row)
        lines.append(f"{gy + oy:3d}|{row}")
    return lines


def _rle(row: str) -> str:
    out = []
    i = 0
    while i < len(row):
        j = i
        while j < len(row) and row[j] == row[i]:
            j += 1
        n = j - i
        if n > 3:
            out.append(f"{row[i]}x{n}")
        else:
            out.append(row[i:j])
        i = j
    return "".join(out)


# ---------------------------------------------------------------------------
# Units and reports: rollups, never raw dumps.
#
# get_unit_list in main.py returns every unit with a free-text physical description
# (~2,000 tokens for this 88-unit fort; the same tool on a 2,000-unit world is 40k+).
# A player model needs rollups plus a way to name the interesting few.
# ---------------------------------------------------------------------------

async def load_units(dec: Decoders, gen: int, cache: dict) -> list[dict]:
    """Compact per-unit facts, cached per connection generation.

    Two RPCs, merged: RemoteFortressReader.GetUnitList (names, positions, profession,
    age, wounds, soldier flag) and BasicApi.ListUnits scan_all (the race INDEX, which
    is the only way to name a species - RFR's race MatPair is a material reference).
    Both are cheap: 92 units cost ~4 KB.
    """
    if cache.get("units_gen") == gen:
        return cache["units"]
    from dfhack_client_python.dfhack import GetUnitList, ListUnits
    from dfhack_client_python.py_export.BasicApi_pb2 import ListUnitsIn
    basic = {}
    try:
        from dfhack_client_python.py_export.Basic_pb2 import BasicUnitInfoMask
        lu = await ListUnits(input=ListUnitsIn(scan_all=True, mask=BasicUnitInfoMask(
            labors=True, profession=True)))
        for u in lu.value:
            basic[u.unit_id] = u
    except Exception:
        basic = {}
    ul = await GetUnitList()
    units = []
    for u in ul.creature_list:
        b = basic.get(u.id)
        race_index = int(getattr(b, "race", -1)) if b is not None else -1
        f1 = int(getattr(b, "flags1", 0)) if b is not None else int(getattr(u, "flags1", 0) or 0)
        names = dec.flag_names("flags1", f1, limit=8)
        units.append({
            "id": u.id,
            "name": (getattr(u, "name", "") or "").strip(),
            "prof": dec.profession_name(int(getattr(u, "profession_id", -1))),
            "race": dec.race_name(race_index) if race_index >= 0 else "unknown",
            "x": u.pos_x, "y": u.pos_y, "z": u.pos_z, "elev": u.pos_z - GAME_Z_OFFSET,
            "soldier": bool(getattr(u, "is_soldier", False)),
            "age": int(getattr(u, "age", 0)),
            "wounded": len(getattr(u, "wounds", []) or []) > 0,
            "hostile": any("HOSTILE" in n for n in names),
            "caged": any("CAGED" in n for n in names),
            "labors": [dec.labor_name(l) for l in (list(b.labors) if b is not None else [])],
            "flags": names,
        })
    cache["units_gen"] = gen
    cache["units"] = units
    return units


def rollup_units(units: list[dict]) -> dict:
    """Population rollup: role counts, soldiers, wounded, hostiles, species."""
    prof = Counter(u["prof"] for u in units)
    race = Counter(u["race"] for u in units)
    return {
        "total": len(units),
        "byProfession": prof.most_common(12),
        "bySpecies": race.most_common(8),
        "soldiers": sum(1 for u in units if u["soldier"]),
        "wounded": sum(1 for u in units if u["wounded"]),
        "hostile": sum(1 for u in units if u["hostile"]),
        "interesting": sorted((u for u in units if u["wounded"] or u["hostile"]),
                              key=lambda u: (not u["hostile"], not u["wounded"]))[:8],
    }


async def load_reports(gen: int, cache: dict, limit: int = 12) -> list[str]:
    if cache.get("reports_gen") == gen:
        return cache["reports"]
    from dfhack_client_python.dfhack import GetReports
    st = await GetReports()
    seen, out = set(), []
    for r in reversed(list(st.reports)):          # newest first
        txt = str(r.text).strip()
        if txt and txt not in seen:
            seen.add(txt)
            out.append(txt)
        if len(out) >= limit:
            break
    cache["reports_gen"] = gen
    cache["reports"] = out[::-1]                  # oldest -> newest
    return cache["reports"]


def date_from_ticks(year: int, tick: int) -> str:
    """DF calendar: 1,200,000 ticks/year, 12 months, 20 days/month.
    Derived, not authoritative - labelled as such in the briefing."""
    month = tick // 100000 + 1
    day = (tick % 100000) // 5000 + 1
    season = ("Spring", "Summer", "Autumn", "Winter")[(month - 1) // 3]
    hour = (tick % 5000) * 24 // 5000
    return f"year {year}, {season} month {month} day {day}, ~{hour:02d}:00"


# ---------------------------------------------------------------------------
# Projections: compact plain text under a token budget.
# ---------------------------------------------------------------------------

def zone_lines(levels: list[LevelModel], b: Budget) -> list[str]:
    # `ui` is the number in the player's map panel ("Elevation N"), measured from the
    # player's own report: RPC z=162 shows as Elevation 33. Print it so agent and player
    # never talk past each other about which level they mean.
    # elev below IS the in-game Elevation panel number (z - 129): one unit, no translation.
    lines = [f"{'z':>4} {'elev':>4} {'blk':>3} | {'wall':>5} {'floor':>5} {'open':>5} {'tree':>4} {'water':>5} {'magma':>5} "
             f"| {'dig':>4} {'bld':>3} {'item':>5} | regions"]
    lines.append("-" * 96)
    for lv in levels:
        c = lv.census
        wall = c["wall"] + c["smooth_wall"] + c["fortification"] + c["pillar"] + c["boulder"]
        floor = c["floor"] + c["smooth_floor"] + c["constructed"] + c["soil"] + c["grass"] + c["pebbles"] + c["track"]
        open_ = c["open"]
        row = (f"{lv.z:4d} {lv.elev:4d} {lv.block_count:3d} | {wall:5d} {floor:5d} {open_:5d} "
               f"{c['tree_cap'] + c['tree_trunk'] + c['tree_branch']:4d} {lv.water_tiles:5d} {lv.magma_tiles:5d} "
               f"| {len(lv.designations):4d} {len(lv.buildings):3d} {sum(lv.item_rollup.values()):5d} "
               f"| {len(lv.regions)}")
        if not b.allow(len(row) + 1):
            b.note("z-levels", len(levels) - levels.index(lv))
            break
        b.spend(len(row) + 1)
        lines.append(row)
    return lines


def map_lines(lv: LevelModel, x0: int, x1: int, y0: int, y1: int, b: Budget,
              rle: bool = True, region_labels: bool = True, overlay: bool = True) -> list[str]:
    """Glyph grid of a window. `overlay` marks the actionable tiles - pending dig (`d`)
    and buildings (`B`) - on top of the terrain glyph, because a map view that shows
    only terrain hides the very things a player acts on (verified live: a 12-tile
    channel design at x21 read as blank space)."""
    lines = [f"z{lv.z} elev{lv.elev} window x{x0}-{x1} y{y0}-{y1} "
             f"({x1 - x0 + 1}x{y1 - y0 + 1} tiles)"]
    region_of = {}
    if region_labels:
        for r in lv.regions:
            for yy in range(r["y0"], r["y1"] + 1):
                for xx in range(r["x0"], r["x1"] + 1):
                    region_of.setdefault((xx, yy), r["id"])
    marks = {}
    if overlay:
        for x, y, kind in lv.designations:
            marks[(x, y)] = "d"
        for bd in lv.buildings:
            for yy in range(bd["y"], bd["y"] + max(1, bd["h"])):
                for xx in range(bd["x"], bd["x"] + max(1, bd["w"])):
                    marks[(xx, yy)] = "B"
    for gy in range(y0, y1 + 1):
        row = ""
        for gx in range(x0, x1 + 1):
            ch = marks.get((gx, gy))
            if ch is None:
                oy, ox = lv.origin
                iy, ix = gy - oy, gx - ox
                ch = lv.grid[iy][ix] if 0 <= iy < lv.rows and 0 <= ix < lv.cols else " "
            row += ch
        txt = f"{gy:3d}|{_rle(row) if rle else row}"
        if region_labels:
            tags = "".join(region_of.get((gx, gy), " ") for gx in range(x0, x1 + 1))
            if tags.strip():
                txt = f"{txt}  {tags}"
        if not b.allow(len(txt) + 1):
            b.note("rows below", max(0, y1 - gy))
            break
        b.spend(len(txt) + 1)
        lines.append(txt)
    return lines


def object_lines(levels: list[LevelModel], b: Budget, max_items: int = 12) -> list[str]:
    """The planning-relevant content of each level: designations, buildings, item
    wealth, notable units. Measured: ~800 tokens for the whole active fort band."""
    lines = []
    dig = dig_names()
    for lv in levels:
        head = f"z{lv.z} elev{lv.elev}"
        if not b.allow(len(head) + 1):
            break
        b.spend(len(head) + 1)
        lines.append(head)
        for cl in lv.clusters[:6]:
            kinds = ",".join(f"{k}x{v}" for k, v in cl["kinds"].items())
            row = f"  dig {cl['n']} tiles x{cl['x0']}-{cl['x1']} y{cl['y0']}-{cl['y1']} [{kinds}]"
            if not b.allow(len(row) + 1):
                b.note("designation clusters", len(lv.clusters) - 6)
                break
            b.spend(len(row) + 1)
            lines.append(row)
        for bd in lv.buildings[:8]:
            row = f"  bld {bd['name']} @ {bd['x']},{bd['y']} {bd['w']}x{bd['h']}" \
                  f"{' room' if bd['room'] else ''}{' active' if bd['active'] else ''}"
            if not b.allow(len(row) + 1):
                b.note("buildings", len(lv.buildings) - 8)
                break
            b.spend(len(row) + 1)
            lines.append(row)
        top = lv.item_rollup.most_common(max_items)
        if top:
            row = "  items: " + ", ".join(f"{k}x{v}" for k, v in top)
            if not b.allow(len(row) + 1):
                b.note("item rollup", len(lv.item_rollup) - max_items)
            else:
                b.spend(len(row) + 1)
                lines.append(row)
        for r in lv.regions[:4]:
            row = f"  region {r['id']}: {r['n']} walkable tiles x{r['x0']}-{r['x1']} y{r['y0']}-{r['y1']}"
            if not b.allow(len(row) + 1):
                break
            b.spend(len(row) + 1)
            lines.append(row)
    return lines


def finish(lines: list[str], b: Budget) -> dict:
    """Assemble the reply and ENFORCE the budget as a hard cap.

    Projectors guard the big lists, but header/rollup lines were appended unguarded,
    so `look` could answer 1,082 tokens when asked for 800 with truncated=false. The
    cap is applied here for every tool: content is trimmed from the tail, announced,
    and the footer is counted in what it costs.
    """
    budget = b.max_tokens
    # keep room for the footer we are about to add
    room = (budget * CHARS_PER_TOKEN) - 160
    body = list(lines)
    dropped = 0
    size = sum(len(x) + 1 for x in body)
    while size > room and len(body) > 1:
        size -= len(body[-1]) + 1
        body.pop()
        dropped += 1
    if dropped:
        b.note("lines dropped to honour the cap", dropped)
    lines = body + b.footer()
    text = "\n".join(lines)
    est = len(text) // CHARS_PER_TOKEN
    out = {"text": text, "estTokens": est, "budgetTokens": budget,
           "truncated": bool(b.truncated)}
    if est > budget:                      # single line bigger than the whole budget
        out["note"] = f"single line exceeds the {budget}-token budget; raise max_tokens"
    return out


class Out:
    """Budget-aware line accumulator: every append is checked, so no section of a
    projection can silently overrun the caller's max_tokens."""

    def __init__(self, b: Budget):
        self.b = b
        self.lines: list[str] = []
        self.dropped = 0

    def line(self, s: str):
        if self.b.allow(len(s) + 1):
            self.b.spend(len(s) + 1)
            self.lines.append(s)
        else:
            self.dropped += 1

    def result(self) -> dict:
        if self.dropped:
            self.b.note("lines", self.dropped)
        return finish(self.lines, self.b)


def snapshot_level(lv: LevelModel) -> dict:
    """The comparable fingerprint of a level, for delta reporting."""
    return {
        "des": {(x, y, d) for x, y, d in lv.designations},
        "bld": {(b["x"], b["y"], b["name"]) for b in lv.buildings},
        "items": dict(lv.item_rollup),
        "census": dict(lv.census),
        "water": lv.water_tiles,
        "magma": lv.magma_tiles,
    }


def diff_snapshot(prev: dict, cur: dict, elev: int) -> list[str]:
    """What changed on a level since the last look. Deltas are what make a long game
    playable in a small context: the model reads the news, not the world.

    An empty `prev` means this level has never been read: that is a baseline, not a
    change, so it reports nothing (a first `look` must not look like 60 events).
    """
    if not prev:
        return []
    out = []
    added = cur["des"] - prev.get("des", set())
    gone = prev.get("des", set()) - cur["des"]
    if added:
        xs = [a[0] for a in added]; ys = [a[1] for a in added]
        kinds = Counter(dig_names().get(a[2], str(a[2])) for a in added)
        out.append(f"+{len(added)} dig designation(s) x{min(xs)}-{max(xs)} y{min(ys)}-{max(ys)} "
                   f"elev{elev} [{','.join(f'{k}x{v}' for k, v in kinds.items())}]")
    if gone:
        out.append(f"-{len(gone)} dig designation(s) cleared on elev{elev} (miners finished?)")
    for k in cur["bld"] - prev.get("bld", set()):
        out.append(f"+building {k[2]} @ {k[0]},{k[1]} elev{elev}")
    for k in prev.get("bld", set()) - cur["bld"]:
        out.append(f"-building {k[2]} @ {k[0]},{k[1]} elev{elev}")
    for key, cnt in cur["items"].items():
        old = prev.get("items", {}).get(key, 0)
        if cnt != old:
            out.append(f"{'+' if cnt > old else '-'}{abs(cnt - old)} {key} on elev{elev} (now {cnt})")
    for key, label in (("water", "water"), ("magma", "MAGMA")):
        if key in prev and cur[key] != prev[key]:
            out.append(f"{label} tiles on elev{elev}: {prev[key]} -> {cur[key]}"
                       + ("  <-- FLOOD: check for a wall tile between you and the water"
                          if label == "water" and cur[key] > prev[key] * 2 and prev[key] else ""))
    return out


# ---------------------------------------------------------------------------
# The orchestrator: one per MCP process. Owns the decode tables, the z-chunk
# cache, and the previous-snapshot used for deltas.
# ---------------------------------------------------------------------------

class WorldModel:
    def __init__(self):
        self.dec = Decoders()
        self.cache = WorldCache()
        self._state: dict = {}
        self._prev: dict[int, dict] = {}
        self._band: tuple[int, int] | None = None

    async def _gen(self) -> int:
        from dfhack_client_python.dfhack_remote import ensure_connection, connection_state
        await ensure_connection()
        return connection_state()["generation"]

    async def _dec(self, gen: int) -> Decoders:
        return await self.dec.load(gen)

    # ---- vertical model ----------------------------------------------------
    # A tunnel is defined by what is ABOVE it. Digging at a z with no rock over it is a
    # surface cut, not a tunnel - the lesson from the first play test (my site at
    # 62,120,elev39 had overburden 0; the player's at 46,119 had 2).

    AIR = (" ", "~")          # open air / liquid: nothing to dig through above

    async def _stack(self, e0: int, e1: int, gen: int | None = None):
        """(x,y) -> {elev: glyph} for a band of levels, from the cached grids."""
        if gen is None:
            gen = await self._gen()
        ck = (e0, e1, gen)
        cached = getattr(self, "_stack_cache", None)
        if cached is None:
            cached = self._stack_cache = {}
        if ck in cached:
            return cached[ck]          # already the (band, levels) tuple
        levels = await self.levels(e0, e1, force=True) if False else await self.levels(e0, e1)
        band = {}
        for lv in levels:
            ox, oy = lv.origin
            for y in range(lv.rows):
                row = lv.grid[y]
                for x in range(lv.cols):
                    band.setdefault((ox + x, oy + y), {})[lv.elev] = row[x]
        if len(cached) > 4:
            cached.clear()
        cached[ck] = (band, levels)
        return cached[ck]

    async def column(self, x: int, y: int, e0: int | None = None,
                     e1: int | None = None) -> dict:
        """The tile column at (x,y), top elevation to bottom, as compact glyphs.

        This is how a model reads a mountain: '43:o 42:f 41:w 40:w 39:w 38:w' says the
        tile at 39 has rock over it. Glyphs: # wall, . floor, ' ' open, ~ water, ? hidden.
        """
        if e0 is None or e1 is None:
            z0, z1 = await self.fort_band()
            e0, e1 = z0 - GAME_Z_OFFSET, z1 - GAME_Z_OFFSET
        band, _ = await self._stack(e0, e1)
        col = band.get((int(x), int(y)), {})
        profile = " ".join(f"{e}:{col.get(e, ' ')}" for e in sorted(col, reverse=True))
        return {"x": int(x), "y": int(y), "elev_range": [e0, e1], "column": profile,
                "open_top": min([e for e, g in col.items() if g not in self.AIR] or [None])
                if any(g not in self.AIR for g in col.values()) else None}

    async def surface_map(self, e0: int | None = None, e1: int | None = None) -> dict:
        """(x,y) -> top-most NON-AIR explored elevation: the local ground surface.

        The tunnel rule falls out of this in one subtraction: a dig at z is a tunnel
        (mountain on top) when surface_z(x,y) - z >= 2; at depth 0 you are trenching the
        surface. Unexplored columns answer None and must not be planned against.
        """
        if e0 is None or e1 is None:
            z0, z1 = await self.fort_band()
            e0, e1 = z0 - GAME_Z_OFFSET, z1 - GAME_Z_OFFSET
        band, _ = await self._stack(e0, e1)
        out = {}
        for xy, col in band.items():
            top = None
            for e in sorted(col, reverse=True):
                g = col[e]
                if g == "?":
                    continue
                if g not in self.AIR:
                    top = e
                    break
            out[xy] = top
        return out

    async def depth(self, x: int, y: int, elev: int) -> dict:
        """Depth under the local surface at (x,y,elev), i.e. mountain-on-top thickness."""
        z0, z1 = await self.fort_band()
        surf = await self.surface_map(z0 - GAME_Z_OFFSET, z1 - GAME_Z_OFFSET)
        top = surf.get((int(x), int(y)))
        return {"x": int(x), "y": int(y), "elev": int(elev),
                "surface_elev": top,
                "depth_under_surface": (top - int(elev)) if top is not None else None}

    # ---- hazard model (damp / hot) ------------------------------------------
    # The game warns a player at designation time: an area that is DAMP is aquifer or
    # flowing water, an area that is HOT is near the magma crust. Digging into either
    # floods the fort with water or lava, so a plan must see the hazard BEFORE designating.

    # ---- reachability: computed by us, because the RPC has no connectivity oracle -------
    # Measured on 53.17-r1: dfhack.maps.getWalkableGroup is NOT callable from the lua RPC
    # channel ("Cannot write field ... complex object"), dfcoord does not exist, and
    # canWalkBetween THROWS for two adjacent stair tiles. So the layer builds its own
    # 3D walk graph from tile semantics. This is what catches silently-cancelled
    # designations: a designation on rock nobody can reach is not a plan, it is a no-op.

    WALK = (".", "g", "s", "S", "=", "/")   # floors, grass, smooth, constructed, stairs, ramps

    async def reach_map(self, e0: int | None = None, e1: int | None = None) -> dict:
        """(x,y,z) -> component id over EXPLORED walkable tiles, with z-links.

        Links: the 4-neighbourhood on the same level between walkable tiles; between levels
        at the same column for stairs ('=' is walkable from both sides) and ramps ('/' links
        z to z+1, which is how DF slopes work). Unexplored tiles are ABSENT from the map:
        unknown, not blocked - the difference matters for planning.
        """
        if e0 is None or e1 is None:
            z0, z1 = await self.fort_band()
            e0, e1 = z0 - GAME_Z_OFFSET, z1 - GAME_Z_OFFSET
        band, _ = await self._stack(e0, e1)
        W = self.WALK
        comp: dict = {}
        cid = 0
        seed_all = [(x, y, z) for (x, y), col in band.items()
                    for z, g in col.items() if g in W]
        seen = set(seed_all)
        for s0 in seed_all:
            if s0 in comp:
                continue
            cid += 1
            stack = [s0]
            comp[s0] = cid
            while stack:
                x, y, z = stack.pop()
                g = band[(x, y)].get(z, " ")
                neigh = []
                # DWARVES MOVE DIAGONALLY in DF, and so do water, vermin, miasma and ambush
                # - so diagonal contact between open areas IS a path. A 4-neighbour graph
                # reported rooms as separated when the game treats them as connected.
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                               (1, 1), (1, -1), (-1, 1), (-1, -1)):
                    neigh.append((x + dx, y + dy, z))
                if g == "=":                 # up-down stair: the level above and below
                    neigh.append((x, y, z + 1))
                    neigh.append((x, y, z - 1))
                if g == "/":                 # ramp: same column, one level up
                    neigh.append((x, y, z + 1))
                for nx, ny, nz in neigh:
                    cell = band.get((nx, ny))
                    if cell is None:
                        continue
                    ng = cell.get(nz)
                    if ng in W and (nx, ny, nz) not in comp:
                        comp[(nx, ny, nz)] = cid
                        stack.append((nx, ny, nz))
        return {"components": comp, "count": cid, "elev_range": [e0, e1]}

    async def reachable(self, x: int, y: int, elev: int,
                        from_x: int | None = None, from_y: int | None = None,
                        from_elev: int | None = None) -> dict:
        """Is (x,y,elev) on the same walk component as a dwarf-occupied tile?

        Pass the fort/wagon tile as from_*; omit them to get the component id of the tile.
        A designation outside the dwarves' component is cancelled by the game, so this is
        the check that must run before ordering a dig.
        """
        r = await self.reach_map()
        comp = r["components"]
        me = comp.get((int(x), int(y), int(elev)))
        if None in (from_x, from_y, from_elev):
            return {"x": int(x), "y": int(y), "elev": int(elev), "component": me,
                    "components_total": r["count"]}
        ref = comp.get((int(from_x), int(from_y), int(from_elev)))
        return {"x": int(x), "y": int(y), "elev": int(elev), "component": me,
                "from_component": ref,
                "reachable": (me is not None and me == ref),
                "components_total": r["count"]}

    WAGON_WALK = (".", "g", "s", "S", "/")   # floors, grass, smooth, constructed, RAMPS - no stairs

    async def wagon_path(self, x: int, y: int, elev: int, no_stairs: bool = True) -> dict:
        """Can a wagon get from here to the map edge? BFS over floors and ramps, diagonals
        included, stairs excluded (wagons refuse them) - the depot rung as evidence, not a
        presence check. Reports the reachable tile count, the edge tiles reached, and the
        3-wide sample columns so a route can be judged as a road, not a goat track.
        """
        band, _ = await self._stack(30, 43)
        walk = self.WAGON_WALK
        start = (int(x), int(y), int(elev))
        seen = {start}
        stack = [start]
        edges = []
        while stack:
            cx, cy, cz = stack.pop()
            g = band.get((cx, cy), {}).get(cz, " ")
            if g not in walk:
                continue
            if cx in (0, 191) or cy in (0, 191):
                edges.append((cx, cy, cz))
            nbs = [(cx+dx, cy+dy, cz) for dx, dy in
                   ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1))]
            nbs += [(cx, cy, cz+1), (cx, cy, cz-1)]      # ramps link levels; stairs are not usable
            for n in nbs:
                ng = band.get((n[0], n[1]), {}).get(n[2], " ")
                if ng in walk and n not in seen:
                    seen.add(n)
                    stack.append(n)
        return {"from": list(start), "wagon_reachable_tiles": len(seen),
                "edge_tiles": edges[:12], "reaches_map_edge": bool(edges),
                "stairs_allowed": not no_stairs}

    async def dig_reachable(self, x: int, y: int, elev: int,
                            from_x: int, from_y: int, from_elev: int) -> dict:
        """Can a dwarf actually work this tile? A wall tile is not walkable, so the test is:
        a 4-neighbour at the same level (or the stair link below/above) is walkable AND in
        the dwarves' component. Designations that fail this are cancelled silently by the
        game - the exact failure that ate my 3-wide adit designation.
        """
        r = await self.reach_map()
        comp = r["components"]
        ref = comp.get((int(from_x), int(from_y), int(from_elev)))
        if ref is None:
            return {"x": int(x), "y": int(y), "elev": int(elev),
                    "reachable": False, "why": f"from-tile ({from_x},{from_y},{from_elev}) is not walkable"}
        via = []
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            c = comp.get((int(x) + dx, int(y) + dy, int(elev)))
            if c == ref:
                via.append(f"({x+dx},{y+dy},{elev})")
        for dz in (-1, 1):        # stair/ramp links into this tile
            c = comp.get((int(x), int(y), int(elev) + dz))
            if c == ref:
                via.append(f"({x},{y},{elev+dz}) via stairs/ramp")
        return {"x": int(x), "y": int(y), "elev": int(elev),
                "reachable": bool(via), "from_component": ref,
                "access_via": via[:4] or ["none"],
                "note": "workable from a walkable neighbour in the dwarves' component"}


    async def survey(self, elev: int, x0: int, y0: int, x1: int, y1: int) -> dict:
        """Pre-flight check for a designation box: what is on every tile, and what is safe to
        order. NEVER designate over stuff - floors, stairs, ramps, buildings, items, and
        existing designations are not rock, and ordering them wastes labour (on a floor) or is
        meaningless (on a building). Categories are counted, with samples, so a plan can be
        filtered before a single designation is sent.
        """
        band, _ = await self._stack(min(elev - 6, 30), max(elev + 6, 43))
        gen = await self._gen()
        blocks = await self.cache.blocks(elev + GAME_Z_OFFSET, elev + GAME_Z_OFFSET + 1, gen)
        bld: dict = {}
        items: dict = {}
        des: dict = {}
        want_z = elev + GAME_Z_OFFSET
        for b in blocks:
            # The block window is INCLUSIVE, so it hands us the level above as well - and a
            # survey that mixes levels reports the floor above's designations as this one's
            # (measured: in-game 35 claimed 77 designations that all belong to 36).
            if int(b.map_z) != want_z:
                continue
            for bb in b.buildings:
                for xx in range(bb.pos_x_min, bb.pos_x_max + 1):
                    for yy in range(bb.pos_y_min, bb.pos_y_max + 1):
                        bld[(xx, yy)] = str(bb.building_type)
            for it in b.items:
                pos = getattr(it, "pos", None)
                if pos is not None:
                    items.setdefault((int(pos.x), int(pos.y)), []).append(dec_name(b, it))
            for i, d in enumerate(b.tile_dig_designation):
                if int(d):
                    des[(b.map_x + (i % 16), b.map_y + (i // 16))] = int(d)
        cats: dict = {}
        samples: dict = {}
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                g = band.get((x, y), {}).get(elev)
                if g is None or g == "?":
                    c = "hidden"          # '?' is fog - my first version let it fall to "other"
                elif (x, y) in bld:
                    c = "building"
                elif g == "#":
                    c = "rock"
                elif g == "=":
                    c = "stairs"
                elif g == "/":
                    c = "ramp"
                elif g == " ":
                    c = "open"            # open air (cavern): nothing to dig, never designate
                elif g in ("~",):
                    c = "water"
                elif g in (".", "g", "s", "S", "b", "v"):
                    c = "floor"
                else:
                    c = "other"
                if (x, y) in des:
                    c = "already_designated"
                cats[c] = cats.get(c, 0) + 1
                samples.setdefault(c, []).append((x, y, g, bld.get((x, y)), len(items.get((x, y), []))))
        safe = cats.get("rock", 0) + cats.get("hidden", 0)
        blocked = {k: v for k, v in cats.items() if k not in ("rock", "hidden")}
        return {"elev": elev, "ui_elev": ui_elev(elev + GAME_Z_OFFSET),
                "box": [x0, y0, x1, y1], "counts": cats,
                "safe_to_designate": safe, "blocked": blocked,
                "building_tiles": len(bld), "item_tiles": len(items),
                "samples": {k: v[:4] for k, v in samples.items()}}


    async def hazard_map(self, e0: int | None = None, e1: int | None = None) -> dict:
        """(x,y) -> {aquifer:[elevs], water:{elev:level}, magma:[elevs], salt:[elevs]} from
        the cached block arrays. RFR gives per-tile aquifer/water/magma flags, so this costs
        no extra RPC once the band is cached."""
        if e0 is None or e1 is None:
            z0, z1 = await self.fort_band()
            e0, e1 = z0 - GAME_Z_OFFSET, z1 - GAME_Z_OFFSET
        gen = await self._gen()
        blocks = await self.cache.blocks(e0 + GAME_Z_OFFSET, e1 + GAME_Z_OFFSET + 1, gen)
        out: dict = {}
        for b in blocks:
            elev = b.map_z - GAME_Z_OFFSET
            aq = list(b.aquifer) if len(b.aquifer) >= 256 else []
            wat = list(b.water) if len(b.water) >= 256 else []
            mag = list(b.magma) if len(b.magma) >= 256 else []
            salt = list(b.water_salt) if len(b.water_salt) >= 256 else []
            for i in range(min(256, len(b.tiles))):
                x = b.map_x + (i % 16)
                y = b.map_y + (i // 16)
                rec = out.setdefault((x, y), {"aquifer": [], "water": {}, "magma": [], "salt": []})
                if i < len(aq) and aq[i]:
                    rec["aquifer"].append(elev)
                if i < len(wat) and wat[i]:
                    rec["water"][elev] = int(wat[i])
                if i < len(mag) and mag[i]:
                    rec["magma"].append(elev)
                if i < len(salt) and salt[i] and i < len(wat) and wat[i]:
                    rec["salt"].append(elev)
        return out

    async def magma_depth_below(self, x: int, y: int, below_elev: int,
                                e_min: int = -120, step: int = 2) -> dict:
        """How far down the magma is, from (x,y,below_elev): the HOT warning as a number.

        Scans downward in coarse windows (magma crust is far below the fort band, so this
        is a bounded search, not a full map read). Reports the first elevation carrying
        magma and the distance from the requested dig elevation.
        """
        gen = await self._gen()
        start = int(below_elev)
        # Contiguous 12-level windows (the RPC reply-size limit). A stepping scan SKIPS
        # levels, and the magma sea is a thin band - the first version jumped 8 levels at
        # a time and reported "no magma" on a world that has one.
        win = 12
        e = start - 1
        last = None
        while e >= e_min:
            lo = max(e_min, e - win + 1)
            try:
                blocks = await self.cache.blocks(lo + GAME_Z_OFFSET, e + GAME_Z_OFFSET + 1, gen)
            except Exception as ex:
                return {"x": int(x), "y": int(y), "from_elev": start,
                        "error": f"scan stopped at elev {e}: {ex}", "magma_elev": None}
            hit = None
            for b in blocks:
                mag = list(b.magma) if len(b.magma) >= 256 else []
                for i in range(min(256, len(b.tiles))):
                    if mag[i] if i < len(mag) else False:
                        if b.map_x + (i % 16) == int(x) and b.map_y + (i // 16) == int(y):
                            hit = b.map_z - GAME_Z_OFFSET
                            break
                if hit is not None:
                    break
            if hit is not None:
                return {"x": int(x), "y": int(y), "from_elev": start, "magma_elev": hit,
                        "levels_to_magma": start - hit}
            last = lo
            e = lo - 1
        return {"x": int(x), "y": int(y), "from_elev": start, "magma_elev": None,
                "scanned_to": last, "note": "no magma found in scanned range"}

    async def annotate_depth(self, facts: "FortFacts") -> "FortFacts":
        """Count floor tiles that sit at depth >=2 under their local surface.

        That number - not raw floor_tiles - is what distinguishes a dug tunnel from a
        grassland, and it is what the ladder's entrance rung now checks.
        """
        z0, z1 = await self.fort_band()
        e0, e1 = z0 - GAME_Z_OFFSET, z1 - GAME_Z_OFFSET
        surf = await self.surface_map(e0, e1)
        band, _ = await self._stack(e0, e1)
        n = 0
        for (x, y), col in band.items():
            top = surf.get((x, y))
            if top is None:
                continue
            for e, g in col.items():
                if top - e >= 2 and g in (".", "s", "S"):     # floor / smooth / constructed
                    n += 1
        facts.underground_floors = n
        return facts

    async def warnings(self, e0: int | None = None, e1: int | None = None) -> dict:
        """Live designations the game would warn a player about, found without being told.

        A designation is DAMP when its column carries aquifer or standing water at/above the
        dig level (mining it floods the works - this is exactly what stopped the player's
        elev-34 line) and HOT when magma sits below it. Returns the flagged tiles clustered
        into rectangles with the hazard elevations, so clearing them (NO_DIG) is a click-list,
        not an investigation.
        """
        if e0 is None or e1 is None:
            z0, z1 = await self.fort_band()
            e0, e1 = z0 - GAME_Z_OFFSET, z1 - GAME_Z_OFFSET
        gen = await self._gen()
        hz = await self.hazard_map(e0, e1)
        blocks = await self.cache.blocks(e0 + GAME_Z_OFFSET, e1 + GAME_Z_OFFSET + 1, gen)
        dig_names = None
        damp: list[tuple] = []
        hot: list[tuple] = []
        total = 0
        for b in blocks:
            elev = b.map_z - GAME_Z_OFFSET
            des = list(b.tile_dig_designation)
            for i in range(min(256, len(des))):
                d = int(des[i])
                if not d:
                    continue
                total += 1
                x = b.map_x + (i % 16); y = b.map_y + (i // 16)
                rec = hz.get((x, y), {"aquifer": [], "water": {}, "magma": []})
                aq = [a for a in rec["aquifer"] if a >= elev]
                wa = [a for a in rec["water"] if a >= elev]
                mg = [a for a in rec["magma"] if a < elev]
                if aq or wa:
                    damp.append((x, y, elev, aq[:3], sorted(wa)[:3]))
                elif mg:
                    hot.append((x, y, elev, mg[:3]))
        def cluster(pts):
            if not pts:
                return []
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; zs = [p[2] for p in pts]
            return [{"x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys),
                     "elev0": min(zs), "elev1": max(zs), "tiles": len(pts)}]
        return {"designated_tiles": total, "damp_tiles": len(damp), "hot_tiles": len(hot),
                "damp_boxes": cluster(damp), "hot_boxes": cluster(hot),
                "samples": damp[:6] + hot[:3]}

    async def probe_plan(self, x: int, y: int, elev0: int, elev1: int,
                         min_depth: int = 2) -> list[dict]:
        """Rank elevations at one (x,y) column for a PROBE dig - how a player beats fog.

        In DF the plan changes because the map is fog: you dig a short 1-wide adit to
        reveal strata, then decide deeper / shallower / somewhere else. So the answer to
        "where do we dig" is a per-elevation ladder with the hazard facts of that column
        (surface depth, aquifer elevations, water elevations) and a verdict per level:
        probe-here, avoid (damp), not diggable (open), or unknown (fog at the dig tile).
        """
        z0, z1 = await self.fort_band()
        e0, e1 = z0 - GAME_Z_OFFSET, z1 - GAME_Z_OFFSET
        # Honour the REQUESTED range, not just the fort band: a descent to elev 25 needs the
        # stack to cover 25, and outside the cached band every tile used to read as fog.
        e0 = min(e0, int(elev0))
        e1 = max(e1, int(elev1))
        surf = await self.surface_map(e0, e1)
        hz = await self.hazard_map(e0, e1)
        band, _ = await self._stack(e0, e1)
        col = band.get((int(x), int(y)), {})
        rec = hz.get((int(x), int(y)), {"aquifer": [], "water": {}, "magma": []})
        top = surf.get((int(x), int(y)))
        out = []
        for e in range(int(elev1), int(elev0) - 1, -1):
            # DATA ABSENCE IS NOT OPEN AIR. col.get(e, " ") made an unexplored level read as
            # a void, so a descent stopped at fog and reported a cavern that did not exist.
            g = col.get(e)
            if g is None:
                g = "?"
            d = (top - e) if top is not None else None
            aq_above = [a for a in rec["aquifer"] if a >= e]
            wat_above = [a for a in rec["water"] if a >= e]
            wet = (f" - and DAMP: aquifer{aq_above[:3]} water{sorted(wat_above)[:3]} in this "
                   "column at/above" if (aq_above or wat_above) else "")
            if g in self.AIR:
                verdict = "not diggable (open air)"
            elif g == "?":
                # Fog and damp are not mutually exclusive: an unexplored tile in an aquifer
                # column is the worst kind of surprise, so say both.
                verdict = ("fog: unexplored - the game ACCEPTS a designation here and it "
                           "queues (measured); it stays unworkable until digging reveals it"
                           + wet)
            elif aq_above or wat_above:
                verdict = ("avoid: DAMP - aquifer/water in this column at/above "
                           f"(aquifer{aq_above[:3]} water{sorted(wat_above)[:3]}); digging "
                           "here floods the works")
            elif d is not None and d < min_depth:
                verdict = f"shallow: depth {d} < {min_depth} - a surface cut, not a tunnel"
            elif d is None:
                # The LOCAL SURFACE is unknown here (fog in this column), so depth cannot be
                # computed. That is not "shallow" and not a veto: the game accepts the
                # designation and it queues. Judging fog as shallow silently cancelled 732
                # tiles of a 50-room grid - the same absence-as-fact bug family.
                verdict = ("fog surface: depth unknown (no explored surface in this column) - "
                           "designation is accepted and queues; re-check depth after the reveal")
            else:
                verdict = f"probe here: solid, depth {d}"
            out.append({"elev": e, "in_game_elev": ui_elev(e + GAME_Z_OFFSET), "glyph": g,
                        "depth": d, "verdict": verdict})
        return out

    async def tunnel_sites(self, length: int = 12, width: int = 3, min_depth: int = 2,
                           near: tuple | None = None, max_sites: int = 8,
                           require_dry: bool = True) -> list[dict]:
        """Rank candidate tunnel lines by the rule a player actually uses.

        A site is: `length` tiles long and `width` rows wide on ONE elevation, every tile
        solid rock ('#'), every tile at least `min_depth` under its local surface (so the
        mountain stays on top), the run enclosed by rock at the far end, and an entrance
        tile at the near end that is explored, walkable ground (floor/ramp). Ranked by
        depth then by distance to `near` (the fort/wagon), because a tunnel you cannot
        reach is scenery.
        """
        z0, z1 = await self.fort_band()
        e0, e1 = z0 - GAME_Z_OFFSET, z1 - GAME_Z_OFFSET
        surf = await self.surface_map(e0, e1)
        levels = await self.levels(e0, e1)
        hz = await self.hazard_map(e0, e1) if require_dry else {}
        sites = []
        L, W, MD = int(length), int(width), int(min_depth)
        for lv in levels:
            ox, oy = lv.origin
            for y in range(0, lv.rows - (W - 1)):
                x = 0
                while x < lv.cols - (L - 1):
                    ok = True
                    depths = []
                    # Real geology is mixed: soil, stone, veins, ramps. A tunnel is defined
                    # by a ROCK ROOF and depth under the surface, not by every tile being
                    # wall. So accept any diggable tile (not open air, not water) provided
                    # the tile directly above is solid - the ceiling must survive.
                    DIGGABLE = ("#", ".", "g", "s", "S", "O", "b", "v")
                    ROOF = ("#", ".", "S", "O", "b", "v")
                    for dy in range(W):
                        for dx in range(L):
                            g = lv.grid[y + dy][x + dx]
                            if g not in DIGGABLE:
                                ok = False
                                break
                            # No per-tile roof test: in a mountain the rock above is
                            # unexplored by definition, and DF lets you designate with fog
                            # above. Depth under the local SURFACE is the roof guarantee,
                            # and it is checked below via min_depth.
                            d = surf.get((ox + x + dx, oy + y + dy))
                            d = (lv.elev - d) if d is None else (d - lv.elev)
                            depths.append(d)
                        if not ok:
                            break
                    if not ok:
                        x += 1
                        continue
                    if any(d is None or d < MD for d in depths):
                        x += 1
                        continue
                    # DAMP check: an aquifer at or above the dig level in any column of the
                    # run means water inflow when dug - the game warns "damp" for this.
                    if require_dry:
                        damp = False
                        for dy in range(W):
                            for dx in range(L):
                                rec = hz.get((ox + x + dx, oy + y + dy))
                                if rec and any(e >= lv.elev for e in rec["aquifer"]):
                                    damp = True
                                    break
                            if damp:
                                break
                        if damp:
                            x += 1
                            continue
                    # entrance: explored walkable ground just east of the run
                    ex, ey = x + L, y + (W // 2)
                    entrance = lv.grid[y + (W // 2)][ex] if ex < lv.cols else " "
                    if entrance not in (".", "g", "s", "/"):
                        x += 1
                        continue
                    xs, ys = ox + x, oy + y
                    dist = None
                    if near:
                        nx, ny, ne = near
                        dist = abs(xs - nx) + abs(ys - ny) + abs(lv.elev - ne) * 2
                    dmin = min(d for d in depths if d is not None)
                    sites.append({"elev": lv.elev, "x0": xs, "y0": ys,
                                  "x1": xs + L - 1, "y1": ys + W - 1,
                                  "length": L, "width": W, "min_depth": dmin,
                                  "entrance": [ox + ex, oy + y + (W // 2), lv.elev],
                                  "tiles": L * W, "dist_to_near": dist})
                    x += L
        sites.sort(key=lambda r: (-r["min_depth"], r["dist_to_near"] if r["dist_to_near"] is not None else 10**9))
        return sites[:max_sites]

    async def overburden(self, x: int, y: int, elev: int, e_top: int | None = None) -> dict:
        """How many levels of non-air sit ABOVE (x,y,elev): the tunnel-vs-trench test.

        Hidden tiles ('?') are counted as unknown and reported: you cannot trust a site
        whose overburden is unexplored, and designations there are rejected anyway.
        """
        z0, z1 = await self.fort_band()
        e0 = z0 - GAME_Z_OFFSET
        e1 = e_top if e_top is not None else z1 - GAME_Z_OFFSET
        band, _ = await self._stack(e0, e1)
        col = band.get((int(x), int(y)), {})
        solid = 0
        unknown = 0
        for e in range(int(elev) + 1, e1 + 1):
            g = col.get(e)
            if g is None:
                g = "?"
            if g == "?":
                unknown += 1
            elif g not in self.AIR:
                solid += 1
        below = 0
        for e in range(e0, int(elev)):
            g = col.get(e)
            if g is None:
                g = "?"
            if g == "?":
                below += 1
            elif g not in self.AIR:
                below += 1
        return {"x": int(x), "y": int(y), "elev": int(elev),
                "overburden": solid, "overburden_unknown": unknown,
                "underburden_solid": below, "at": col.get(int(elev), " ")}

    async def cross_section(self, axis: str, at: int, a0: int, a1: int,
                            e0: int | None = None, e1: int | None = None) -> dict:
        """A vertical slice through the map - the only way to see a mountain as text.

        axis='y' fixes y=at and walks x from a0..a1; axis='x' fixes x=at and walks y.
        Rows are elevations (top first), so overburden is visible as the column of '#'
        above the dig line.
        """
        z0, z1 = await self.fort_band()
        e0 = e0 if e0 is not None else z0 - GAME_Z_OFFSET
        e1 = e1 if e1 is not None else z1 - GAME_Z_OFFSET
        band, _ = await self._stack(e0, e1)
        a0, a1 = int(a0), int(a1)
        rows = []
        for e in range(e1, e0 - 1, -1):
            cells = []
            for a in range(a0, a1 + 1):
                xy = (a, int(at)) if axis == "y" else (int(at), a)
                gv = band.get(xy, {}).get(e)
                cells.append("?" if gv is None else gv)
            rows.append(f"{e:>3} |" + "".join(cells))
        ruler = "    |" + "".join(str((a % 10)) for a in range(a0, a1 + 1))
        head = f"section axis={axis} at={at} span {a0}-{a1} in-game elev {e0}-{e1}\n{ruler}"
        return {"head": head, "rows": rows}

    async def tile_at(self, x: int, y: int, elev: int, force: bool = False) -> dict:
        """One tile read from the block cache: tiletype, its decoded name and glyph class,
        water/magma, hidden, dig designation, construction material, item count, and the
        buildings whose footprint covers it. RFR's per-tile arrays are row-major within a
        block (index = (y%16)*16 + (x%16)); block origins are tile coords in map_x/map_y."""
        z = int(elev) + GAME_Z_OFFSET        # module constant: WorldModel has no z_offset
        gen = await self._gen()
        dec = await self._dec(gen)
        blocks = await self.cache.blocks(z, z + 1, gen, force=force)
        bx, by = x - (x % 16), y - (y % 16)
        for b in blocks:
            if b.map_x != bx or b.map_y != by:
                continue
            i = (y % 16) * 16 + (x % 16)
            out = {"x": x, "y": y, "elev": int(elev), "z": z, "ui_elev": ui_elev(z)}
            if i < len(b.tiles):
                tid = b.tiles[i]
                out["tiletype"] = tid
                out["tiletype_name"] = dec.tile_name(tid)
                cls = dec.tile_class(tid)          # TileClass, not a tuple
                out["class"] = cls.key
                out["glyph"] = cls.glyph
                out["solid"] = cls.solid
                out["walkable"] = cls.walkable
            if i < len(b.water):
                out["water"] = b.water[i]
            if i < len(b.magma):
                out["magma"] = b.magma[i]
            if i < len(b.aquifer) and b.aquifer[i]:
                out["aquifer"] = True          # the game's DAMP warning, per tile
            if i < len(b.hidden):
                out["hidden"] = bool(b.hidden[i])
            if i < len(b.tile_dig_designation):
                dname = dig_names().get(int(b.tile_dig_designation[i]))
                if dname and dname != "NO_DIG":
                    out["dig_designation"] = dname
            if i < len(b.construction_items):
                ci = b.construction_items[i]
                if ci.mat_type or ci.mat_index:
                    out["construction"] = dec.material_name(ci)
            here_items = []
            for it in b.items:
                pos = getattr(it, "pos", None)
                if pos is not None and int(pos.x) == x and int(pos.y) == y and int(pos.z) == z:
                    here_items.append(dec.item_name(it.material))
            if here_items:
                out["items_here"] = here_items[:8]
                out["items_here_count"] = len(here_items)
            on_tile = []
            for bd in b.buildings:
                xmin, xmax = int(bd.pos_x_min), int(bd.pos_x_max)
                ymin, ymax = int(bd.pos_y_min), int(bd.pos_y_max)
                if xmin <= x <= xmax and ymin <= y <= ymax:
                    bt = getattr(bd, "building_type", None)
                    entry = {"name": dec.building_name(bt) if bt is not None else "building?",
                             "index": int(bd.index),
                             "footprint": [xmin, ymin, xmax, ymax]}
                    if bd.material.mat_type or bd.material.mat_index:
                        entry["material"] = dec.material_name(bd.material)
                    if bd.is_room:
                        entry["is_room"] = True
                    on_tile.append(entry)
            if on_tile:
                out["buildings"] = on_tile
            return out
        return {"x": x, "y": y, "elev": int(elev), "error": "block not in cache"}

    async def levels_z(self, z0: int, z1: int, force: bool = False) -> list[LevelModel]:
        """Levels by RPC z (the layer's internal unit; elevations are z - offset)."""
        gen = await self._gen()
        dec = await self._dec(gen)
        blocks = await self.cache.blocks(z0, z1, gen, force=force)
        byz = defaultdict(list)
        for b in blocks:
            byz[b.map_z].append(b)
        return [build_level(z, byz[z], dec) for z in sorted(byz)]

    async def levels(self, e0: int, e1: int, force: bool = False) -> list[LevelModel]:
        """Levels between game elevations e0..e1 inclusive."""
        return await self.levels_z(e0 + GAME_Z_OFFSET, e1 + GAME_Z_OFFSET, force)

    async def fort_band(self, window: int = MAX_Z_WINDOW,
                        force_recompute: bool = False) -> tuple[int, int]:
        """The z-window of `window` levels where the action is, scored from unit density,
        the player's view/cursor, and cached map activity. Returns RPC z bounds.

        Units are spread across the whole map (wildlife, invaders), so a min..max span
        is useless; and the cursor is a sentinel (-30000) when the keyboard cursor is
        off. Scoring a sliding window over both signals is what actually finds the fort.
        """
        if self._band and not force_recompute:
            return self._band
        gen = await self._gen()
        dec = await self._dec(gen)
        units = await load_units(dec, gen, self._state)
        hist: Counter = Counter(u["z"] for u in units)
        focus: Counter = Counter()
        try:
            from dfhack_client_python.dfhack import GetViewInfo
            vi = await GetViewInfo()
            for v, w in ((vi.view_pos_z, 12), (vi.cursor_pos_z, 8)):
                if -1000 < v < self.cache.z_count + 1000:
                    focus[v] += w
                    focus[v - 1] += w // 2
                    focus[v + 1] += w // 2
        except Exception:
            pass
        activity: Counter = Counter()
        for z, snap in self._prev.items():
            activity[z] += len(snap.get("des", ())) + len(snap.get("bld", ()))
            activity[z] += sum(snap.get("items", {}).values()) // 20
        if not self.cache.z_count:
            await self.cache.extent()
        top = max(0, self.cache.z_count - window)
        best, best_z = -1, top
        for lo in range(0, top + 1):
            s = sum(hist[z] * 3 + activity[z] * 2 + focus[z] for z in range(lo, lo + window))
            if s > best:
                best, best_z = s, lo
        self._band = (best_z, best_z + window - 1)
        return self._band

    # ---- Tier 0: the briefing. ~400-900 tokens, answers "what now?" ----
    async def look(self, max_tokens: int = DEFAULT_MAX_TOKENS, with_legend: bool = False,
                   e0: int | None = None, e1: int | None = None) -> dict:
        gen = await self._gen()
        dec = await self._dec(gen)
        b = Budget(max_tokens)
        lines: list[str] = []
        from dfhack_client_python.dfhack import GetMapInfo, GetWorldMap, GetPauseState, GetWorldInfo
        from dfhack_client_python.py_export.BasicApi_pb2 import GetWorldInfoOut
        mode = "unknown"
        try:
            wi = await GetWorldInfo()
            mode = GetWorldInfoOut.Mode.Name(wi.mode)
        except Exception:
            pass
        mi = await GetMapInfo()
        if not self.cache.z_count:
            await self.cache.extent()
        lines.append(f"MODE {mode} | FORT {mi.save_name} | world {mi.world_name_english} | "
                     f"map {self.cache.tile_x}x{self.cache.tile_y} tiles, {self.cache.z_count} z "
                     f"(elev {0 - GAME_Z_OFFSET}..{self.cache.z_count - 1 - GAME_Z_OFFSET})")
        b.spend(len(lines[-1]))
        if mode not in ("MODE_DWARF", "unknown"):
            # Fortress map views are meaningless in adventure/legend mode: say so in one
            # line rather than emitting a fortress briefing the model would act on.
            lines.append(f"adventure/legend mode: fortress map projections are not applicable "
                         f"(use the AdventureControl tools: menu, movement, world map)")
            return finish(lines, b)
        try:
            wm = await GetWorldMap()
            lines.append(f"DATE {date_from_ticks(wm.cur_year, wm.cur_year_tick)} "
                         f"(derived from cur_year_tick)")
            b.spend(len(lines[-1]))
        except Exception as e:
            lines.append(f"DATE unavailable: {type(e).__name__}")
        try:
            ps = await GetPauseState()
            lines.append("PAUSED - the game is not advancing" if ps.Value else "unpaused")
        except Exception:
            pass
        if e0 is None or e1 is None:
            z_lo, z_hi = await self.fort_band()
            if e0 is None:
                e0 = z_lo - GAME_Z_OFFSET
            if e1 is None:
                e1 = e0 + (z_hi - z_lo)
        e0, e1 = min(e0, e1), max(e0, e1)
        if e1 - e0 > MAX_Z_WINDOW - 1:
            e1 = e0 + MAX_Z_WINDOW - 1
        lv = await self.levels(e0, e1)
        units = await load_units(dec, gen, self._state)
        ru = rollup_units(units)
        prof = ", ".join(f"{c} {p}" for p, c in ru["byProfession"][:8])
        lines.append(f"POP {ru['total']} world-wide: {prof}")
        lines.append(f"  soldiers {ru['soldiers']}, wounded {ru['wounded']}, hostile {ru['hostile']}")
        species = ", ".join(f"{c} {r}" for r, c in ru["bySpecies"][:5])
        lines.append(f"  species: {species}")
        reports = await load_reports(gen, self._state)
        lines.append("REPORTS (newest last):")
        for r in reports[-8:]:
            lines.append(f"  - {r[:120]}")
        lines.append(f"ELEV BAND {e0}-{e1} ({len(lv)} levels read)")
        lines.extend(zone_lines(lv, b))
        baseline = not self._prev
        deltas = []
        for l in lv:
            snap = snapshot_level(l)
            deltas += diff_snapshot(self._prev.get(l.z, {}), snap, l.elev)
            self._prev[l.z] = snap
        if baseline:
            lines.append("SINCE LAST LOOK: first read - baseline recorded, deltas start next call")
        elif deltas:
            lines.append("SINCE LAST LOOK:")
        else:
            lines.append("SINCE LAST LOOK: nothing changed")
        for d in deltas[:12]:
            lines.append(f"  {d}")
        if len(deltas) > 12:
            lines.append(f"  ... {len(deltas) - 12} more changes (world_delta for all)")
        lines.extend(object_lines(lv, b))
        notable = ru["interesting"]
        if notable:
            lines.append("NOTABLE UNITS:")
            for u in notable[:6]:
                tags = " ".join(x for x in ("hostile" if u["hostile"] else "",
                                            "wounded" if u["wounded"] else "",
                                            "soldier" if u["soldier"] else "") if x)
                lines.append(f"  #{u['id']} {u['name'] or '(unnamed)'} {u['prof']} ({u['race']}) "
                             f"@ {u['x']},{u['y']} elev{u['elev']} {tags}")
        facts = FortFacts(lv, units, dec)
        pl = progress_lines(facts)
        lines.append(pl[0])
        nxt = next((i for i, x in enumerate(pl) if x.startswith("NEXT")), None)
        if nxt is not None:
            lines.extend(pl[nxt:nxt + 4])
        if with_legend:
            lines.append("LEGEND:")
            lines.extend(f"  {l}" for l in legend())
        lines.append("next: world_zone(e0,e1) | world_map(e,x0,x1,y0,y1) | world_objects(e0,e1)"
                     " | world_delta | world_raw(file) | fort_progress for the whole ladder")
        return finish(lines, b)

    # ---- Tier 1: per-level strips ----
    async def zone(self, e0: int, e1: int, max_tokens: int = DEFAULT_MAX_TOKENS) -> dict:
        lv = await self.levels(e0, e1)
        b = Budget(max_tokens)
        lines = [f"zone elev {e0}-{e1}, {len(lv)} levels"]
        lines.extend(zone_lines(lv, b))
        return finish(lines, b)

    # ---- Tier 2: glyph map of an explicit window ----
    async def view(self, e: int, x0: int, x1: int, y0: int, y1: int,
                   max_tokens: int = DEFAULT_MAX_TOKENS, rle: bool = True,
                   with_legend: bool = False) -> dict:
        lv = await self.levels(e, e)
        if not lv:
            return {"text": f"elev {e}: no discovered blocks in this window "
                            f"(fog? run the DFHack `reveal` command)", "estTokens": 12}
        b = Budget(max_tokens)
        lines = []
        if with_legend:
            lines.extend(legend())
        lines.extend(map_lines(lv[0], x0, x1, y0, y1, b, rle=rle))
        return finish(lines, b)

    # ---- Tier 3: objects only (cheapest useful view) ----
    async def objects(self, e0: int, e1: int, max_tokens: int = DEFAULT_MAX_TOKENS) -> dict:
        lv = await self.levels(e0, e1)
        b = Budget(max_tokens)
        lines = [f"objects elev {e0}-{e1}"]
        lines.extend(object_lines(lv, b))
        return finish(lines, b)

    # ---- Progression ladder: how a player learns the game, evaluated live ----
    async def progress(self, e0: int | None = None, e1: int | None = None,
                       max_tokens: int = DEFAULT_MAX_TOKENS) -> dict:
        if e0 is None or e1 is None:
            z_lo, z_hi = await self.fort_band()
            e0 = e0 if e0 is not None else z_lo - GAME_Z_OFFSET
            e1 = e1 if e1 is not None else z_hi - GAME_Z_OFFSET
        gen = await self._gen()
        dec = await self._dec(gen)
        lv = await self.levels(e0, e1)
        facts = FortFacts(lv, await load_units(dec, gen, self._state), dec)
        await self.annotate_depth(facts)        # depth >=2 floors: the tunnel truth
        b = Budget(max_tokens)
        lines = [f"progress elev {e0}-{e1}"]
        for l in progress_lines(facts):
            if not b.allow(len(l) + 1):
                b.note("ladder rungs", 1)
                break
            b.spend(len(l) + 1)
            lines.append(l)
        return finish(lines, b)

    # ---- Whole-map block minimap: 1 char per 16x16 block, all z at once ----
    async def minimap(self, max_tokens: int = DEFAULT_MAX_TOKENS,
                      all_levels: bool = False, span: int = 48) -> dict:
        """Block-level map, one char per 16x16 block. Default window is `span` levels
        around the fort: walking ALL 191 levels costs ~120 MB of RPC replies, which the
        plugin rejects and the socket dies (verified live), so full-map is opt-in."""
        b = Budget(max_tokens)
        z_lo, z_hi = 0, self.cache.z_count - 1
        if not all_levels:
            lo, hi = await self.fort_band()
            center = (lo + hi) // 2
            z_lo = max(0, center - span // 2)
            z_hi = min(self.cache.z_count - 1, z_lo + span - 1)
        lines = [f"block minimap z{z_lo}-{z_hi} (elev {z_lo - GAME_Z_OFFSET}-{z_hi - GAME_Z_OFFSET}); "
                 f"1 char = 16x16 block; all_levels=true for every z"]
        nx = max(1, self.cache.tile_x // 16)
        ny = max(1, self.cache.tile_y // 16)
        gen = await self._gen()
        dec = await self._dec(gen)
        for c in range(z_lo // MAX_Z_WINDOW, z_hi // MAX_Z_WINDOW + 1):
            lo, hi = c * MAX_Z_WINDOW, min((c + 1) * MAX_Z_WINDOW - 1, z_hi)
            try:
                blocks = await self.cache.blocks(lo, hi, gen)
            except FetchError as e:
                lines.append(f"  [z{lo}-{hi} skipped: {e}]")
                continue
            byz = defaultdict(list)
            for bl in blocks:
                byz[bl.map_z].append(bl)
            for z in sorted(byz):
                grid = [["."] * nx for _ in range(ny)]
                for bl in byz[z]:
                    gx, gy = bl.map_x // 16, bl.map_y // 16
                    kinds = Counter(dec.tile_class(t).key for t in bl.tiles)
                    solid = kinds["wall"] + kinds["smooth_wall"]
                    wat = sum(1 for w in bl.water if w > 0)
                    mag = sum(1 for m in bl.magma if m > 0)
                    des = sum(1 for d in bl.tile_dig_designation if d)
                    bld = len(bl.buildings)
                    ch = "."
                    if solid > 240:
                        ch = "#"
                    elif kinds["open"] > 128:
                        ch = " "
                    if wat > 0:
                        ch = "~"
                    if mag > 0:
                        ch = "!"
                    if bld:
                        ch = "B"
                    if des:
                        ch = "x"
                    grid[gy][gx] = ch
                for row in grid:
                    line = f"z{z:3d} e{z - GAME_Z_OFFSET:4d}|{''.join(row)}"
                    if not b.allow(len(line) + 1):
                        b.note(f"levels from z{z}", 1)
                        break
                    b.spend(len(line) + 1)
                    lines.append(line)
                if not b.allow(1):
                    break
        return finish(lines, b)

    # ---- Deltas without reading the briefing ----
    async def delta(self, e0: int | None = None, e1: int | None = None,
                    max_tokens: int = DEFAULT_MAX_TOKENS) -> dict:
        if e0 is None or e1 is None:
            z_lo, z_hi = await self.fort_band()          # band is in RPC z
            e0 = e0 if e0 is not None else z_lo - GAME_Z_OFFSET
            e1 = e1 if e1 is not None else z_hi - GAME_Z_OFFSET
        lv = await self.levels(e0, e1)
        b = Budget(max_tokens)
        out = []
        for l in lv:
            snap = snapshot_level(l)
            out += diff_snapshot(self._prev.get(l.z, {}), snap, l.elev)
            self._prev[l.z] = snap
        lines = [f"changes elev {e0}-{e1} since previous snapshot"] if out else \
                [f"nothing changed on elev {e0}-{e1} since previous snapshot"]
        for d in out[:200]:
            if not b.allow(len(d) + 3):
                b.note("changes", len(out) - 200)
                break
            b.spend(len(d) + 3)
            lines.append(f"  {d}")
        return finish(lines, b)

    def forget_band(self):
        self._band = None
        # The stack memo is keyed by (e0, e1, gen) and **the generation does not change on a
        # save reload** - measured: after reloading a save, /survey kept answering with the
        # pre-reload world (dug floors reported as fog). Reloading a save MUST invalidate
        # every derived view, so clear the memo here, not just the fort band.
        self._stack_cache = {}


# ---------------------------------------------------------------------------
# The progression ladder.
#
# Design note (from the DF wiki Quickstart guide, the sequence a human player
# learns by): a player does not learn Dwarf Fortress by looking at a map. They
# learn a LADDER of milestones - pause, labors, entry tunnel, 5x5 room, stairwell,
# stockpile, meeting area, refuse, farm, wood, still, pasture, workshops, barrels,
# brewing, garbage dump, depot, bedrooms, dining, offices, metal, military - each
# expressed as a short key sequence with a reason attached.
#
# So the layer evaluates every rung against the live world and emits the NEXT rung
# with its keys and concrete coordinates. A local model does not have to invent a
# plan; it picks up where the ladder is broken.
# ---------------------------------------------------------------------------

FOOD_ITEMS = ("PLANT", "PREPMEAT", "REMAINS", "FISH_RAW", "FISH", "MEAT", "EGG",
              "CHEESE", "GLOBED", "SNOW_CONSTRUCTED", "LIQUID_CRAFTY")
DRINK_ITEMS = ("BOOZE", "LIQUID_PLANT", "LIQUID_ANIMAL", "LIQUID_CREATURE", "LIQUID_MISC")
WOOD_ITEMS = ("LOG", "WOOD")
STONE_ITEMS = ("BOULDER", "COBBLESTON", "ROUGH")
METAL_ITEMS = ("BAR", "NAIL", "METALSCROLL", "SMALLBAR")


class FortFacts:
    """Everything the ladder needs, computed once per projection."""

    def __init__(self, levels: list[LevelModel], units: list[dict], dec: Decoders):
        self.levels = levels
        self.units = units
        self.dec = dec
        self.buildings: list[dict] = []
        self.item_totals: Counter = Counter()
        self.designations = 0
        self.stairs: Counter = Counter()
        self.water_tiles = 0
        self.magma_tiles = 0
        self.floor_tiles = 0
        self.underground_floors = 0   # floors at depth >=2: a dug tunnel, not a plain
        self.walkable = 0
        self.regions: list[tuple[int, dict]] = []
        for lv in levels:
            for b in lv.buildings:
                b = dict(b); b["elev"] = lv.elev
                self.buildings.append(b)
            for k, v in lv.item_rollup.items():
                self.item_totals[k.split("/")[0]] += v
            self.designations += len(lv.designations)
            for key in ("stairs_ud", "stairs_up", "stairs_down"):
                self.stairs[key] += lv.census[key]
            self.water_tiles += lv.water_tiles
            self.magma_tiles += lv.magma_tiles
            self.floor_tiles += (lv.census["floor"] + lv.census["smooth_floor"]
                                  + lv.census["constructed"] + lv.census["soil"] + lv.census["grass"])
            self.walkable += sum(r["n"] for r in lv.regions)
            for r in lv.regions:
                self.regions.append((lv.elev, r))
        self.pop = len([u for u in units if not u["hostile"]])
        # "pop" counts pets and livestock too (a fort of 21 dwarves reads 96).
        # The dwarf number is the one a death event moves.
        self.dwarves = sum(1 for u in units if u["race"] == "dwarf" and not u["hostile"])
        self.soldiers = sum(1 for u in units if u["soldier"])
        # The labor name is MINE (not MINING) and hunting is HUNTING - verified against
        # ListEnums.unit_labor. Matching the wrong string made `miners 0` a false alarm.
        self.miners = sum(1 for u in units if "MINE" in u.get("labors", []))
        self.hunters = sum(1 for u in units if "HUNTING" in u.get("labors", []))
        self.hostile = sum(1 for u in units if u["hostile"])
        self.food = sum(v for k, v in self.item_totals.items() if k in FOOD_ITEMS)
        self.drink = sum(v for k, v in self.item_totals.items() if k in DRINK_ITEMS)
        self.wood = sum(v for k, v in self.item_totals.items() if k in WOOD_ITEMS)
        self.stone = sum(v for k, v in self.item_totals.items() if k in STONE_ITEMS)
        self.metal = sum(v for k, v in self.item_totals.items() if k in METAL_ITEMS)
        self.barrels = self.item_totals.get("BARREL", 0) + self.item_totals.get("BUCKET", 0)
        self.built_names = [b["name"].lower() for b in self.buildings]

    def count_built(self, *keywords) -> int:
        return sum(1 for n in self.built_names if any(k in n for k in keywords))

    def built(self, *keywords) -> bool:
        return self.count_built(*keywords) > 0


def fort_center(facts: FortFacts) -> tuple[int, int, int] | None:
    """The modal citizen tile (x, y, elev): the fort is where the dwarves STAND.
    Anchoring placement here is the rule PLAN-TOOLS.md forced on us - the first
    depot failed because it sat in a huge region no dwarf could reach.
    """
    from collections import Counter
    c = Counter((u["x"] // 8, u["y"] // 8, u["elev"]) for u in facts.units
               if not u["hostile"] and not u.get("caged"))
    if not c:
        return None
    (bx, by, elev) = c.most_common(1)[0][0]
    return bx * 8 + 4, by * 8 + 4, elev


def suggest_room(facts: FortFacts, w: int, h: int, prefer_elev: int | None = None) -> dict | None:
    """A concrete rectangle for the next step, anchored at the FORT CENTRE and
    centred inside the walkable region that contains it - not at the region's
    origin corner, which for a giant region can be 90 tiles from any dwarf.
    """
    cand = [(e, r) for e, r in facts.regions if r["n"] >= max(4, w * h // 2)]
    if not cand:
        return None
    center = fort_center(facts)
    if center:
        inside = [(e, r) for (e, r) in cand
                  if r["x0"] <= center[0] <= r["x1"] and r["y0"] <= center[1] <= r["y1"]
                  and abs(e - center[2]) <= 2]
        if inside:
            cand = inside
    if prefer_elev is not None:
        near = [c for c in cand if abs(c[0] - prefer_elev) <= 2]
        if near:
            cand = near
    if center:
        cand.sort(key=lambda er: (abs(er[0] - center[2]), -er[1]["n"]))
    else:
        cand.sort(key=lambda er: -er[1]["n"])
    elev, r = cand[0]
    if center:
        cx, cy = center[0], center[1]
        x0 = min(max(cx - w // 2, r["x0"]), r["x1"] - w + 1)
        y0 = min(max(cy - h // 2, r["y0"]), r["y1"] - h + 1)
    else:
        x0, y0 = r["x0"], r["y0"]
    return {"elev": elev, "x0": x0, "y0": y0,
            "x1": min(x0 + w - 1, r["x1"]), "y1": min(y0 + h - 1, r["y1"]),
            "region": r.get("id", "?"), "region_tiles": r["n"],
            "anchored": "fort_center" if center else "region_origin"}


# (id, rung, done-predicate, keys, why)
_LADDER = [
    ("labors", "Two dwarves on Miners (embark has 3 picks); Hunters/Fisherdwarves = nobody",
     lambda f: f.miners >= 2, "y -> Miners -> tick 2 dwarves -> Esc",
     "one miner is the default; a 1-wide tunnel takes one miner per end"),
    ("entrance", "Entry tunnel dug with mountain on top (>=20 floor tiles at depth >=2)",
     # floor_tiles >= 40 was satisfied by a fresh embark's 22,105 GRASS floors, so the
     # ladder cried [x] with no tunnel at all. A tunnel is defined by OVERBURDEN.
     lambda f: getattr(f, "underground_floors", 0) >= 20, "m -> m (mine) in a cliff face, 3 wide, >=10 long, with >=2 levels of rock above every tile",
     "the entrance is the fort's only defended opening; a cut on the surface is not a tunnel"),
    ("stairwell", "Stairs connecting the levels",
     lambda f: sum(f.stairs.values()) >= 2, "m -> t: click the room floor, scroll one level down, click again",
     "designate EVERY z the shaft passes: top=down-stairs, middle=up-down, bottom=up-stairs"),
    ("stockpile", "General stockpile, excluding Corpses/Refuse/Stone/Gems/Wood",
     lambda f: f.count_built("stockpile") >= 1, "p -> draw rect -> Accept -> Custom: All, then None on Corpses/Refuse/Stone/Gems/Wood",
     "a general pile that eats stone and wood clogs every workshop"),
    ("meeting", "Meeting area inside the fort, 5x5-7x7, on the 2nd level below ground",
     lambda f: f.count_built("zone") >= 1, "z -> Meeting Area -> draw rect filling the room -> Accept",
     "keeps idle dwarves inside; dig the room out FIRST, zones need mined space"),
    ("refuse", "Refuse pile OUTSIDE (miasma)",
     lambda f: f.count_built("dump") >= 1, "p -> outside the entrance, at least 5x5",
     "refuse inside the fort rots and miasma kills"),
    ("farm", "Farm plot (3x3 in a 5x5 soil room), plump helmets in ALL seasons",
     lambda f: f.built("farm plot", "plot"), "b -> o -> f -> p, then click the plot and enable plump helmets in every season tab",
     "a plot planted in one season idles 3/4 of the year"),
    ("wood", "Trees felled (3-5) and a wood stockpile near the entrance",
     lambda f: f.wood >= 1, "l -> designate 3-5 trees; p -> 5x3 wood pile outside",
     "3 logs also come from deconstructing the wagon: click it, top-right button"),
    ("drinks", "Still built near the farm",
     lambda f: f.built("still"), "b -> o -> f -> l in a 3x3 area next to the farm plot",
     "no alcohol means slow, unhappy dwarves"),
    ("pasture", "Pen/pasture ~10x10 on grass for your grazers",
     lambda f: f.built("pen", "pasture"), "z -> Pen/Pasture -> draw over grass -> assign animals",
     "draft animals starve away from grass"),
    ("workshops", "Mechanic's, Stoneworker's, Carpenter's, Jeweler's workshops",
     lambda f: f.count_built("workshop") >= 3, "b -> o -> h (mechanic), t (stoneworker), p (carpenter), j (jeweler)",
     "each is 3x3 and needs 1 building material; keep material piles adjacent"),
    ("furniture", "2 tables + 2 chairs queued at the stoneworker/carpenter",
     lambda f: f.built("table") and f.built("chair"), "click the shop -> queue Table, Chair",
     "needed for offices, dining and bedrooms later"),
    ("wheelbarrows", "2-3 wheelbarrows, Max wheelbarrows=3 on the stone stockpile",
     lambda f: f.built("cabinet") >= 1, "carpenter: queue 2-3 Wheelbarrow (bottom of the list)",
     "hauling logs and stone without wheelbarrows is the classic early slowdown"),
    ("barrels", "5 barrels, then brew",
     lambda f: f.barrels >= 3, "carpenter: queue Barrel x5; then the still -> Add new task -> Brew drink from plant",
     "brewing plump helmets also yields seeds"),
    ("garbage", "Garbage dump zone",
     lambda f: f.count_built("dump", "garbage") >= 1, "z -> Garbage dump",
     "garbage (mud, bones, bits) is not refuse: it clogs workshops and floods floors"),
    ("depot", "Trade depot with 3-wide wagon access",
     lambda f: f.built("depot", "trade depot"), "z -> Trade Depot in a 5x5 room; the passage AND the turn must be 3 wide",
     "wagons cannot turn in a 2-wide passage"),
    ("bedrooms", "One bed per dwarf, plus chests and doors",
     lambda f: f.count_built("bed") >= max(1, f.pop), "b -> o -> f -> e (bed), b -> o -> f -> c (chest), b -> o -> f -> d (door)",
     "no bedroom means dwarves sleep in the tunnels"),
    ("dining", "Dining area with tables and chairs",
     lambda f: f.count_built("table") >= 2, "b -> o -> f -> t (table), b -> o -> f -> c (chair), then set Max dining in the area",
     "feasts and moods revolve around the dining area"),
    ("offices", "Offices for Manager, Broker, Bookkeeper (1x1 with table+chair+door)",
     lambda f: f.built("office"), "z -> Office -> assign the noble",
     "no manager means no auto-queued jobs"),
    ("metal", "Smelter + forge, with iron, flux and fuel",
     lambda f: f.built("smelt") and f.built("forge", "furnace"), "b -> o -> f -> s (smelter), b -> o -> f -> m (forge)",
     "wood burning for charcoal, then iron bars; picks break"),
    ("military", "Weapon rack + armor stand + a drafted squad",
     lambda f: f.built("weapon rack") and f.soldiers >= 1, "b -> o -> f -> w (weapon rack), r (armor stand); n -> draft",
     "caravans and invaders arrive; 0 soldiers is a fort that cannot answer"),
]


def progress_lines(facts: FortFacts, with_next: bool = True) -> list[str]:
    lines = [f"LADDER (quickstart order) | pop {facts.pop}, soldiers {facts.soldiers}, "
             f"hostile {facts.hostile} | food {facts.food}, drink {facts.drink}, "
             f"wood {facts.wood}, stone {facts.stone}, metal {facts.metal} | "
             f"miners {facts.miners}, pending dig {facts.designations}",
             f"underground floors (depth>=2): {getattr(facts, 'underground_floors', 0)}"]
    missing = []
    for rid, rung, done, keys, why in _LADDER:
        ok = done(facts)
        lines.append(f"[{'x' if ok else ' '}] {rung}")
        if not ok:
            missing.append((rid, rung, keys, why))
    if with_next and missing:
        rid, rung, keys, why = missing[0]
        lines.append(f"NEXT ({rid}): {rung}")
        lines.append(f"  keys: {keys}")
        lines.append(f"  why: {why}")
        want = {"farm": (5, 5), "meeting": (7, 7), "stockpile": (11, 11), "bedrooms": (9, 9),
                "workshops": (9, 9), "depot": (5, 5), "drinks": (5, 5)}.get(rid, (7, 7))
        elev = facts.levels[len(facts.levels) // 2].elev if facts.levels else None
        room = suggest_room(facts, want[0], want[1], prefer_elev=elev)
        if room:
            lines.append(f"  at: x{room['x0']}-{room['x1']} y{room['y0']}-{room['y1']} "
                         f"elev{room['elev']} (inside region {room['region']}, "
                         f"{room['region_tiles']} walkable tiles)")
        if facts.magma_tiles:
            lines.append(f"  ALERT: {facts.magma_tiles} magma tiles in this band")
        if facts.water_tiles > 400:
            lines.append(f"  ALERT: {facts.water_tiles} water tiles in this band - leave one wall "
                         f"tile between a dig and water; damp tiles auto-cancel designations")
    elif with_next:
        lines.append("NEXT: every quickstart rung is satisfied - fortify, expand, defend")
    return lines
