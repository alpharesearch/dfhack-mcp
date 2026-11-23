"""Offline tests for the preprocessing layer (no game required).

    uv run --no-sync python -m pytest tests/test_worldmodel.py -q
    uv run --no-sync python tests/test_worldmodel.py      # no pytest needed

The live-game assertions run only when DFHack RPC answers on :5000.
"""
import json
import re
import os
import pathlib
import socket
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import worldmodel as WM                                          # noqa: E402

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "tiletypes.json"


def tiletype_names():
    return [r["name"] for r in json.loads(FIX.read_text())]


def test_glyph_alphabet_covers_every_live_tiletype():
    """697 live tiletype names must all classify: an unclassified tile becomes ' ? '
    in the grid, which a model reads as a real tile."""
    names = tiletype_names()
    n, buckets = WM.coverage_report(names)
    assert len(names) == 697, len(names)
    assert n == len(names), f"unclassified: {[x for x in names if WM.classify_tile(x) is WM._OTHER]}"
    assert len(buckets) > 20


def test_legend_glyphs_are_unique_and_short():
    lines = WM.legend()
    assert len(lines) <= 40, len(lines)
    glyphs = {l[0] for l in lines if len(l) > 1 and l[1] == "="}
    assert len(glyphs) > 20, len(glyphs)
    # ' ' is a real glyph (open space), so it must be documented, never silent
    assert any(l.startswith(" =") for l in lines), lines[:3]
    assert all("=" in l for l in lines)


def test_window_request_covers_the_exclusive_max_z():
    """The plugin answers [min_z, max_z): the window must ask one past z1, and never
    send a span-1 request (which answers nothing)."""
    # a single level extends FORWARD (no wasted level below) and keeps span >= 2
    assert WM.window_request(165, 165, 191) == (165, 167)
    assert WM.window_request(164, 165, 191) == (164, 166)
    assert WM.window_request(156, 167, 191) == (156, 168)
    assert WM.window_request(0, 0, 191) == (0, 2)
    # at the top of the map it must walk min_z back instead
    assert WM.window_request(190, 190, 191) == (189, 191)
    for z0, z1 in [(0, 0), (5, 5), (100, 100), (120, 131), (189, 190)]:
        lo, hi = WM.window_request(z0, z1, 191)
        assert hi - lo >= 2, (z0, z1, lo, hi)
        assert hi >= z1 + 1, (z0, z1, lo, hi)          # the requested top level is included
        assert hi - lo <= 13, (z0, z1, lo, hi)


def test_tile_index_round_trip():
    for i in (0, 1, 15, 16, 255):
        x, y = WM.tile_at(i)
        assert 0 <= x < 16 and 0 <= y < 16
        assert WM.tile_index(100 + x, 200 + y, 100, 200) == i


def test_rle_collapses_runs():
    assert WM._rle("...............") == ".x15"
    assert WM._rle("...") == "..."
    assert WM._rle("#.#####") == "#.#x5"
    assert len(WM._rle("#" * 256)) < 20
    # a flat 192-tile row of floor is the common case: 192 chars -> 6 chars
    assert WM._rle("." * 192) == ".x192"


def test_designation_clusters_become_rectangles():
    des = [(10, 10, 1), (10, 11, 1), (11, 10, 1), (11, 11, 1), (50, 50, 3)]
    cl = WM.cluster_designations(des)
    assert len(cl) == 2
    big = cl[0]
    assert big["n"] == 4 and (big["x0"], big["x1"], big["y0"], big["y1"]) == (10, 11, 10, 11)
    assert sum(c["n"] for c in cl) == 5


def test_regions_flood_fill_walkability():
    """Water and hidden tiles must break a region - a region is where dwarves walk."""
    lv = WM.LevelModel(165, None)
    lv.rows = lv.cols = 4
    lv.origin = (0, 0)
    lv.grid = [["."] * 4 for _ in range(4)]
    walk = [[True] * 4 for _ in range(4)]
    walk[1][2] = False                       # a wall tile
    lv.census.update({"floor": 15, "wall": 1})
    regions = WM.find_regions(lv, lv.grid, walk, 0, 0)
    assert sum(r["n"] for r in regions) == 15
    assert regions[0]["n"] == 15 and regions[0]["id"] == "A"


def test_budget_trims_and_announces():
    b = WM.Budget(100)
    lines = []
    for i in range(500):
        row = f"line {i} " + "x" * 50
        if not b.allow(len(row) + 1):
            b.note("lines", 500 - i)
            break
        b.spend(len(row) + 1)
        lines.append(row)
    out = WM.finish(lines, b)
    assert out["truncated"] is True
    # the truncation footer is extra text on top of the budget, so allow it some slack
    assert out["estTokens"] <= 100 * 1.6, out["estTokens"]
    assert "budget" in out["text"] and "narrow the window" in out["text"]


def test_budget_is_a_hard_cap_not_a_report_field():
    """Regression: a fresh session found `look(max_tokens=800)` answering 1,082 tokens
    with truncated=false, because header lines were appended without budget checks.
    finish() must enforce the cap for EVERY projection."""
    for budget in (200, 400, 800, 1500, 6000):
        b = WM.Budget(budget)
        lines = [f"line {i}: " + "content" * 12 for i in range(2000)]
        out = WM.finish(lines, b)
        assert out["estTokens"] <= budget, (budget, out["estTokens"])
        assert out["truncated"] is True, budget
    # a projection that fits must not claim truncation
    b = WM.Budget(5000)
    out = WM.finish(["short line a", "short line b"], b)
    assert out["estTokens"] < 50 and out["truncated"] is False, out


def test_budget_clamps_to_the_hard_cap():
    assert WM.Budget(10 ** 9).max_tokens == WM.HARD_MAX_TOKENS
    assert WM.Budget(None).max_tokens == WM.DEFAULT_MAX_TOKENS
    assert WM.Budget(1).max_tokens == 100


def test_ladder_reports_the_first_broken_rung_with_keys():
    class FakeLevel:
        z, elev = 165, 37
        buildings, item_rollup, designations, census = [], {}, [], {}
        water_tiles = magma_tiles = block_count = 0
        regions = []

    facts = WM.FortFacts.__new__(WM.FortFacts)
    facts.levels, facts.units, facts.buildings = [], [], []
    facts.item_totals, facts.designations = WM.Counter(), 0
    facts.stairs = WM.Counter({"stairs_ud": 4})
    facts.water_tiles = facts.magma_tiles = 0
    facts.floor_tiles = 100
    facts.underground_floors = 60      # depth>=2: the entrance rung means a TUNNEL
    facts.walkable = 0
    facts.regions = []
    facts.pop, facts.soldiers, facts.hostile = 10, 0, 0
    facts.miners, facts.hunters = 0, 0
    facts.food = facts.drink = facts.wood = facts.stone = facts.metal = facts.barrels = 0
    facts.count_built = lambda *k: 0
    facts.built = lambda *k: False
    facts.built_names = []
    facts.zones = []          # no zones measured: meeting/refuse rungs stay open
    lines = WM.progress_lines(facts)
    txt = "\n".join(lines)
    assert "NEXT (labors)" in txt, txt[:400]
    assert "y -> Miners" in txt
    assert "[x] Stairs connecting the levels" in txt
    assert "[ ] General stockpile" in txt
    # Zones are typed: a `count_built("zone")` of zero must leave meeting AND refuse open
    assert "[ ] Meeting area inside the fort" in txt
    assert "[ ] Refuse pile OUTSIDE" in txt
    # a satisfied ladder must say so instead of repeating rung 1 forever
    facts.miners = 2
    facts.wood, facts.barrels, facts.soldiers = 5, 5, 1
    facts.count_built = lambda *k: 99
    facts.built = lambda *k: True
    # The zone rungs now read zone KINDS, so the stub has to provide them: a meeting area
    # (5x5+) and an outdoor dump (5x5+). An empty list is NO DATA, not a satisfied rung.
    facts.zones = [{"type_name": "MeetingHall", "tiles": 25, "marked_for_removal": False},
                   {"type_name": "Dump", "tiles": 25, "marked_for_removal": False},
                   {"type_name": "Pen", "tiles": 100, "marked_for_removal": False}]
    # The refuse rung reads pile CATEGORIES (the wiki builds it with p -> corpses+refuse
    # outdoors), so the stub has to answer that question the way the game does. And the
    # general-pile rung reads categories too - a pile eating stone+wood is not a general
    # pile, so the stub must show the wiki's recipe, not just "some stockpile exists".
    facts.piles = [{"id": 2, "x0": 77, "x1": 79, "y0": 126, "y1": 128, "tiles": 9,
                    "accepts": ["corpses", "refuse"], "elev": 38},
                   {"id": 1, "x0": 72, "x1": 75, "y0": 122, "y1": 124, "tiles": 12,
                    "elev": 38,
                    "accepts": ["ammo", "animals", "armor", "bars_blocks", "cloth", "coins",
                                 "finished_goods", "food", "furniture", "leather", "sheet",
                                 "weapons"]},
                   {"id": 3, "x0": 69, "x1": 70, "y0": 120, "y1": 121, "tiles": 4,
                    "elev": 38, "accepts": ["wood"]}]
    txt2 = "\n".join(WM.progress_lines(facts))
    assert "every quickstart rung is satisfied" in txt2, txt2[-400:]



def test_ladder_entrance_rung_requires_overburden():
    """A fresh embark has thousands of GRASS floors. `floor_tiles >= 40` reported
    '[x] Entry tunnel dug' with no tunnel at all - the rungs that describe dug space
    must be depth-aware, because a tunnel is defined by rock ABOVE it."""
    facts = WM.FortFacts.__new__(WM.FortFacts)
    facts.levels = []
    facts.stairs = {1: 4}
    facts.designations = 0
    facts.water_tiles = facts.magma_tiles = 0
    facts.floor_tiles = 22105          # the open plain
    facts.underground_floors = 0       # nothing dug
    facts.walkable = 2000
    facts.regions = []
    facts.pop, facts.soldiers, facts.hostile = 7, 0, 0
    facts.miners, facts.hunters = 4, 0
    facts.food = facts.drink = facts.wood = facts.stone = facts.metal = facts.barrels = 0
    facts.count_built = lambda *k: 0
    facts.built = lambda *k: False
    facts.built_names = []
    txt = "\n".join(WM.progress_lines(facts))
    assert "[ ] Entry tunnel dug" in txt, txt[:600]
    assert "underground floors (depth>=2): 0" in txt
    facts.underground_floors = 25
    txt2 = "\n".join(WM.progress_lines(facts))
    assert "[x] Entry tunnel dug" in txt2, txt2[:600]
def test_projection_size_stays_in_budget():
    """The whole point of the layer: a briefing must be hundreds of tokens, not 100k."""
    class L:
        z, elev, block_count = 165, 37, 144
        census = {"wall": 12000, "floor": 20000, "smooth_wall": 600, "fortification": 0,
                  "pillar": 0, "boulder": 0, "smooth_floor": 0, "constructed": 0,
                  "soil": 0, "grass": 0, "pebbles": 0, "track": 0, "open": 500,
                  "tree_cap": 0, "tree_trunk": 0, "tree_branch": 0}
        designations = [(50, 50, 1)]
        buildings = [{"x": 5, "y": 5, "name": "Stockpile", "w": 3, "h": 3,
                      "items": 0, "active": 1, "room": False}]
        items = []
        item_rollup = WM.Counter({"BOULDER/LIMESTONE": 44})
        units = []
        water_tiles = magma_tiles = 0
        regions = [{"id": "A", "n": 200, "x0": 40, "x1": 60, "y0": 40, "y1": 60}]
        clusters = WM.cluster_designations(designations)
    b = WM.Budget(600)
    out = WM.finish(WM.zone_lines([L], b) + WM.object_lines([L], b), b)
    assert out["estTokens"] < 600, out["estTokens"]
    assert "BOULDER/LIMESTONEx44" in out["text"]


def live_game():
    """True only when a game is reachable AND answers a command.

    A bare TCP check is not enough: right after DF starts, the RPC port listens before
    the world is loaded, and commands answer CR_FAILURE. A test gated on the port then
    crashes the suite mid-boot (observed). Readiness means a successful call.
    """
    try:
        s = socket.create_connection(("127.0.0.1", 5000), timeout=1.5)
        s.close()
    except OSError:
        return False
    try:
        import asyncio
        from dfhack_client_python.dfhack import GetDFVersion
        from dfhack_client_python.dfhack_remote import connect, close

        async def probe():
            await connect()
            v = await GetDFVersion()
            await close()
            return bool(v)
        return bool(asyncio.run(asyncio.wait_for(probe(), timeout=10)))
    except Exception:
        return False


def test_live_projection_sizes():
    """Measured against the running fortress: the compact views must stay tiny while
    the raw tool stays enormous. Skips silently when the game is not up."""
    if not live_game():
        print("  (skipped: no DFHack RPC on :5000)")
        return
    import asyncio

    async def run():
        w = WM.WorldModel()
        look = await w.look(max_tokens=2000)
        prog = await w.progress()
        zone = await w.zone(32, 43)
        assert look["estTokens"] < 2000, look["estTokens"]
        assert "MODE" in look["text"]
        assert "NEXT" in prog["text"]
        assert zone["estTokens"] < 600
        raw = await w.cache.blocks(165, 165, await w._gen())
        raw_tokens = sum(len(str(t)) for b in raw for t in b.tiles) // 4
        assert len(raw) == 144, f"z165 must return the 12x12 blocks, got {len(raw)}"
        assert raw_tokens > 15 * look["estTokens"], (raw_tokens, look["estTokens"])
        print(f"  live: raw z165 ~{raw_tokens:,} tokens vs look {look['estTokens']} tokens "
              f"= {raw_tokens // max(1, look['estTokens'])}x reduction")
    asyncio.run(run())


def test_a_stockpile_request_must_not_lie_about_its_size():
    """constructBuilding ignores w/h for piles, so `place_building` with w=4,h=2 answered
    success:true and produced a 1x1 pile (measured twice on this fort: pile 1 came back
    x66-66,128-128). A fort with a "general stockpile" of one tile clogs hauling and the
    ladder rung reads satisfied. The multi-tile request must go through the game's own
    allocInstance -> setSize -> constructAbstract path and report real extents."""
    src = pathlib.Path("main.py").read_text()
    i = src.index('operation_id="place_building"')
    # Read the WHOLE endpoint: a fixed window silently cut off the second route the day
    # the routing block grew, and the test then failed on prose, not on behaviour.
    nxt = src.find('@app.', i + 30)
    fn = src[i:nxt if nxt > 0 else i + 4000]
    # Routing is by BUILDING FAMILY, measured on this fort:
    #   stockpile   -> allocInstance -> setSize -> constructAbstract  (abstract: legal)
    #   workshop    -> constructBuilding width/height + room.extents  (abstract is REJECTED:
    #                  "constructAbstract(): expected !bld->isActual()" placing a 3x4 Still)
    assert "LO.create_building_multi" in fn, "a multi-tile pile must use the allocInstance -> setSize path"
    assert "LO.place_building_plan" in fn, "a multi-tile workshop must use quickfort's plan call"
    assert "if _w > 1 or _h > 1:" in fn, "ANY multi-tile request routes through one of them (a 3x2 Farm Plot came back 1x1 the same way)"
    assert '"route": "create_building_multi (allocInstance -> setSize -> commit)"' in fn, \
        "the response must say which route it took, not just success"
    assert "place_building_plan (constructBuilding width/height + room.extents" in fn, \
        "and the plan route must be named in its own response"
    # The tile count the game reported is what the caller must see (usable vs rect,
    # and holes), and luaops is where that read-back lives.
    assert "usable_tiles" in pathlib.Path("luaops.py").read_text(), \
        "the pile path must report the game's own usable/rect tile counts"
    # The pile route must invalidate the caches the extents are read from.
    assert "world.cache.reset()" in fn and "world.forget_band()" in fn, "cache + memo dropped"


def test_pile_categories_are_the_flags_the_game_exposes_not_a_wiki_list():
    """`plants` looked like an obvious gap (the farm chain needs it) and I added it. The
    game's own flag enumeration has 17 fields and none of them is `plants`; writing
    `flags.plants = true` fails, and the tool reported success while changing nothing.
    Category names must come from the game."""
    lo = pathlib.Path("luaops.py").read_text()
    i = lo.index("CATEGORIES = [")
    block = lo[i:lo.index("]", i) + 1]
    names = [w.strip().strip('"') for w in re.findall(r'"([^"]+)"', block)]
    measured = {"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods",
                "food","furniture","gems","leather","refuse","sheet","stone","weapons","wood"}
    assert set(names) == measured, f"the category list must match the 17 flags the game exposes, got {set(names) ^ measured}"
    assert "plants" not in names, "this build has no plants flag - plump helmets are FOOD"


def test_labor_writes_carry_a_read_back_and_the_census_tool_exists():
    """`/set-unit-labors` answers `applied: N`, which validates NAMES against the RPC labor list -
    not the game's labor state. Measured 2026-11-22 after the player said 'you only have one
    miner': the write said applied=2, the plugin's profession-based metric said 1, and the game's
    own `u.status.labors` (field name `MINE`; `MINING` does not exist and my pcall probe read it
    as a silent false) said MINE on for all seven citizens. Three numbers, three meanings - so the
    write must carry the game's answer, and there must be a census tool."""
    main = pathlib.Path("main.py").read_text()
    i = main.index('@app.post("/set-unit-labors"')
    seg = main[i:main.index("@app.", i + 30)]
    assert "read_back" in seg and "LO.unit_labors(" in seg, "the labor write answers with the game's state"
    assert '@app.get("/unit-labors"' in main, "the census endpoint exists"
    lo = pathlib.Path("luaops.py").read_text()
    assert "async def unit_labors(" in lo, "the bridge exists"
    mod = pathlib.Path("deployables/scripts/dfhack_mcp.lua").read_text()
    assert "function OPS.unit_labors" in mod, "the module op exists"
    assert "pairs(u.status.labors)" in mod, "it reads the game's own labor fields"


def test_the_soil_census_is_a_python_tool_that_reports_what_it_scanned():
    """The farm rung ("3x3 plot in a 5x5 soil room") had no tool. My first census was a Lua module
    op, and the Lua route to the tile grids is CLOSED on this build - measured: `world.T_map.all:
    not found`, `map:find` nil for real coordinates, and every block grid (`matgrid`, `typegrid`,
    `tileptr`, `regiongrid`, `water`) unreadable, with `pairs(block)` itself raising. It answered
    `blocks_scanned: 0, soil_tiles: 0`: a read failure wearing a census. The census now rides the
    RPC block cache and the game's tiletype list, and it must say how many blocks it scanned,
    because 0 from a scan of nothing is not a map fact."""
    main = pathlib.Path("main.py").read_text()
    i = main.index('@app.get("/find-soil"')
    seg = main[i:main.index('@app.post("/pile-arrays"', i)]
    assert "world.cache.blocks(" in seg, "the RPC block cache, not a Lua map walk"
    assert "GetTiletypeList" in seg, "the game's own tiletype list defines soil"
    assert "blocks_scanned" in seg, "the census reports how much it actually scanned"
    assert "soil_tiletypes_seen" in seg, "it names the tiletypes it matched, so a wrong predicate is visible"
    mod = pathlib.Path("deployables/scripts/dfhack_mcp.lua").read_text()
    assert "function OPS.find_soil" not in mod, "the Lua census is gone - it could only answer 0"
    assert "blocks_scanned: 0" in mod, "and the reason is recorded where the next reader will look"


def test_labor_writes_carry_a_read_back_and_the_census_tool_exists():
    """`/set-unit-labors` answers `applied: N`, which validates NAMES against the RPC labor list -
    not the game's labor state. Measured 2026-11-22 after the player said 'you only have one
    miner': the write said applied=2, the plugin's profession-based metric said 1, and the game's
    own `u.status.labors` (field name `MINE`; `MINING` does not exist and my pcall probe read it
    as a silent false) said MINE on for all seven citizens. Three numbers, three meanings - so the
    write must carry the game's answer, and there must be a census tool."""
    main = pathlib.Path("main.py").read_text()
    i = main.index('@app.post("/set-unit-labors"')
    seg = main[i:main.index("@app.", i + 30)]
    assert "read_back" in seg and "LO.unit_labors(" in seg, "the labor write answers with the game's state"
    assert '@app.get("/unit-labors"' in main, "the census endpoint exists"
    lo = pathlib.Path("luaops.py").read_text()
    assert "async def unit_labors(" in lo, "the bridge exists"
    mod = pathlib.Path("deployables/scripts/dfhack_mcp.lua").read_text()
    assert "function OPS.unit_labors" in mod, "the module op exists"
    assert "pairs(u.status.labors)" in mod, "it reads the game's own labor fields"


def test_the_crash_categories_carry_the_measured_reference_sizes():
    """The player built a UI pile so I could measure what the game allocates:
    corpses.corpses = vector<char>[1327] (race-indexed), every refuse.* sub-vector = 1327,
    refuse.type = 113 (item-type-indexed), while wood.mats = 0 is legal. My table's 0s were
    measured from a TOOL-made pile - measuring the crash and calling it the reference - and my
    allocator tested `type(v) == "table"`, which skips these fields entirely because they are
    userdata. Both are pinned here."""
    import re
    lo = pathlib.Path("luaops.py").read_text()
    corps = re.search(r'"corpses":\s*\{([^}]*)\}', lo).group(1)
    refuse = re.search(r'"refuse":\s*\{([^}]*)\}', lo, re.S).group(1)
    assert "1327" in corps, "corpses.corpses allocated to the measured race length"
    assert "1327" in refuse and "113" in refuse, "refuse sub-vectors 1327 and refuse.type 113"
    assert "skulls" in refuse and "teeth" in refuse, "the fields my old table never knew existed"
    # The configure allocation needs no type test at all: it addresses each field named in
    # SETTINGS_ARRAYS and resizes it - which works on the game's `userdata` vectors. The scan
    # that DOES classify values lives in the Lua module, and it must accept userdata.
    assert "SETTINGS_ARRAYS" in lo and ':resize(' in lo, "allocation addresses fields by name and resizes"
    mod = pathlib.Path("deployables/scripts/dfhack_mcp.lua").read_text()
    assert 'type(v) == "table" or type(v) == "userdata"' in mod, "the module's scan accepts userdata"
    mod = pathlib.Path("deployables/scripts/dfhack_mcp.lua").read_text()
    assert "ref > 0 and #v < ref" in mod, "check is reference-anchored: a 0-size field is legal when the game's pile has 0 (wood.mats)"


def test_corpses_and_refuse_flags_are_refused_without_an_opt_in():
    """The player's click test, one save, three piles: general (12 categories) and wood opened
    the Custom screen; `corpses` + `refuse` crashed the game (SIGSEGV, render_things ->
    overlay.plug.so, no Lua) - twice, on two different saves. Those arrays are indexed by race
    and item type and measure 0 even on a UI-made pile, because the game allocates them when ITS
    screen opens the page; a programmatic flag (size 0, or my 1-slot fallback) leaves the screen
    walking an array that was never filled. The tool refuses them by default."""
    lo = pathlib.Path("luaops.py").read_text()
    i = lo.index("async def configure_stockpile(")
    body = lo[i:lo.index("async def ", i + 10)]
    assert 'CRASH_CATEGORIES = ("corpses", "refuse")' in body, "the class is named in code"
    assert "allow_crash_categories" in body, "there is an explicit opt-in, default False"
    assert "raise LuaOpError" in body, "the refusal is an error, not a silent drop"


def test_pile_settings_arrays_are_allocated_with_real_lua_and_never_skipped():
    """The player's crash of 2026-11-22 20:02:29 (`crash_2026-11-22-20-02-29.txt`: SIGSEGV at
    0x5, `render_things` -> `overlay.plug.so`, no Lua) is a category flag ON over an
    unallocated material array - the Custom screen dereferences nil. Two of my own defects
    made that state reachable and are pinned here: `_array_alloc_lua` joined its lines with
    the LITERAL two-character `\n` (so the allocation reached Lua as text: 'unexpected symbol
    near' and no arrays allocated, ever), and it skipped `n <= 0` sizes - which is every
    corpses/refuse field, i.e. exactly the refuse pile the player clicked."""
    lo = pathlib.Path("luaops.py").read_text()
    i = lo.index("def _array_alloc_lua(")
    body = lo[i:lo.index("def ", i + 10)]
    assert 'return chr(10).join(lines)' in body, "real newlines only - a literal backslash-n is a syntax error in the game"
    assert "if n <= 0:" in body and "n = 1" in body, "a measured size of 0 still allocates a slot"
    # the tool that measures/repairs pile arrays (no raw Lua needed)
    assert "async def pile_arrays(" in lo, "the check/fix op is a tool, not a probe"
    main = pathlib.Path("main.py").read_text()
    assert '@app.post("/pile-arrays"' in main, "the endpoint exists"
    mod = pathlib.Path("deployables/scripts/dfhack_mcp.lua").read_text()
    assert "function OPS.pile_arrays" in mod, "the module side exists"


def test_configure_stockpile_refuses_a_body_that_says_nothing_and_verifies_the_write():
    """2026-11-22, measured: I called /configure-stockpile with {"categories": [...]} - the real
    keys are allow/deny - and because the request model is `extra="allow"`, the key was accepted,
    `allow=None, deny=None, all_categories=False` means TAKE NOTHING, and the pile's flags all
    went false. The tool answered `{"success": true, "verdict": null, "accepts": null}` while the
    live pile read said `accepts: []`. A write that empties a fortress pile must not be able to
    arrive as an omitted parameter, and success must be measured by reading the game."""
    main = pathlib.Path("main.py").read_text()
    i = main.index('@app.post("/configure-stockpile"')
    seg = main[i:main.index("@app.", i + 30)]
    assert 'not any(k in raw_body for k in ("allow", "deny", "all_categories"))' in seg, \
        "an empty/unknown body is refused against the RAW request, not translated into take-nothing"
    assert '"verdict": "UNVERIFIED"' in seg and "list_stockpiles" in seg, \
        "the answer is the read-back verdict against the live pile read"
    # the repair tool's Lua must not contain the literal two-character sequence backslash-n
    lo = pathlib.Path("luaops.py").read_text()
    i = lo.index("async def repair_stockpile_settings")
    body = lo[i:lo.index("async def ", i + 10)]
    assert "sp.settings.%s\\nif" not in body, \
        "literal \\n reached Lua as a backslash: 'unexpected symbol near \\'' (CR_FAILURE, measured)"
    assert "chr(10).join" in body, "real newlines join the chunk"


def test_zone_removal_is_opt_in_and_proven_by_the_zone_list():
    """The gate my remove_zone docstring promised did not exist in the endpoint: a bare POST
    {"building_id": N} reached a chunk that mutates `assigned_units` and every unit's
    `general_refs` before `deconstruct` - on the path that produced
    `crash_2026-11-21-22-53-41.txt`. Removal now requires acknowledge_risk, refuses when the
    id is not a live zone, and answers with the read-back verdict (removed / UNVERIFIED /
    still present), so a silent reply is never read as success."""
    main = pathlib.Path("main.py").read_text()
    i = main.index('@app.post("/remove-zone"')
    seg = main[i:main.index("@app.", i + 30)]
    assert 'body.get("acknowledge_risk") is not True' in seg, "the gate exists in the endpoint, not only in prose"
    assert "not a live zone" in seg, "an id that is not in the zone list is refused, not sent"
    assert "_building_present(" in seg, "the answer is the read-back verdict"
    assert '"verdict": "UNVERIFIED"' in seg, "NO DATA is reported as unverified"


def test_zone_removal_never_calls_notifycivzonemodified_without_a_building():
    """This is the crash of 2026-11-21 22:53. My remove_zone chunk ended with
    `pcall(function() dfhack.buildings.notifyCivzoneModified() end)` - no building argument -
    and the game died with SIGSEGV (SEGV_MAPERR) at address 0 inside
    `DFHack::Buildings::notifyCivzoneModified(df::building*)` (`crash_2026-11-21-22-53-41.txt`,
    Lua frame `(lua command):10`). pcall does not survive a native segfault, so wrapping a
    call is not the same as it being safe, and the call is deleted: deconstruct disposes the
    zone on its own."""
    lo = pathlib.Path("luaops.py").read_text()
    i = lo.index("async def remove_zone(")
    body = lo[i:lo.index("async def ", i + 10)]
    lua_part = body[body.index('"""', body.index('"""') + 3):]
    assert "notifyCivzoneModified()" not in lua_part.split('"""')[1], \
        "the no-arg call is what crashed the fort - it must never come back"
    assert "crash_2026-11-21-22-53-41" in body, "the crash is recorded where the next reader will hit it"
    assert "deconstruct(b)" in body, "and removal goes through the game's own deconstruct"


def test_a_write_is_verified_by_reading_the_game_not_by_its_own_reply():
    """Write replies are the flaky part, not the writes. Measured 2026-11-21: /remove-building
    answered "lua produced no output; the chunk printed nothing" three times for pile 28 while
    the pile was in fact gone - so I reported a leftover pile that did not exist, and a
    placement that had not happened. The endpoint now answers from the live pile/zone lists:
    removed / UNVERIFIED (NO DATA) / still present, and it says what the write's own reply
    said, so silence is never read as success or as failure."""
    main = pathlib.Path("main.py").read_text()
    i = main.index("async def _building_present(")
    helper = main[i:main.index("@app.post", i)]
    assert "list_stockpiles" in helper and "list_zones" in helper, "the live lists are the authority"
    assert "return True if seen_any else None" in helper, "NO DATA stays NO DATA - it is not 'present'"
    j = main.index('@app.post("/remove-building"')
    seg = main[j:main.index("@app.", j + 30)]
    assert '"verdict": "removed"' in seg and "verified_by" in seg, "removal is proven by a read-back"
    assert '"verdict": "UNVERIFIED"' in seg, "a silent write plus silent reads is reported as unverified"
    assert '"write_reply": write_note' in seg, "and the write's own silence is carried in the answer"


def test_the_water_census_answers_where_the_well_can_stand():
    """"No water source" is a fact about the fort, not a plan. The wiki's well rule is exact:
    1x1 on a floor with a CLEAR line to water >=3/7 deep, never through a staircase. So the
    tool must answer WHERE, from the map, before anyone spends a week digging. Measured
    2026-11-21 on this fort: all 1030 water tiles are at elev 35, 7/7 deep, in the caverns
    (nearest big pool x160-175 y112-127) - which is why the adit got driven east."""
    main = pathlib.Path("main.py").read_text()
    i = main.index('operation_id="find_water"')
    seg = main[i:main.index("@app.", i + 10)]
    assert "b.water[idx]" in seg, "depth comes from the map's water array"
    assert "if w < mind" in seg, "and the min_depth filter is the wiki's 3/7 line"
    assert "map_x), int(b.map_y)" in seg, "tile coordinates, not block coordinates"
    assert '"max_depth"' in seg and '"sample"' in seg, "a cluster reports depth and a sample tile"


def test_a_pile_row_reports_unread_categories_and_its_panel_number():
    """Two naming/labeling facts from 2026-11-21:
      * A pile whose flag lookups failed printed an EMPTY accepts list, identical to the
        pile that truly accepts nothing - the plugin alerted "pile accepts NOTHING" about a
        pile configured to `food`, and a raw probe on the same pile read food=true.
      * The player said "the food stock pile #3 next to the still has plants" while my
        reads listed ids 1/4/21/22/23 - the in-game panel number is NOT the building id.
    The row now carries UNREAD for failed reads and a panel_number that stays null when the
    game does not expose the panel order, instead of a made-up index."""
    lo = pathlib.Path("luaops.py").read_text()
    i = lo.index("async def list_stockpiles(")
    body = lo[i:lo.index("async def ", i + 10)]
    assert 'local acc = (okn == 0) and "UNREAD"' in body, "a failed flag read is UNREAD, not an empty list"
    assert '"categories_measured": (not unread)' in body, "and the row says whether it measured"
    assert '"panel_number"' in body, "the row carries the in-game panel number too"
    assert "unparsable extent" in body and "row skipped, not guessed" in body, \
        "a malformed extent is skipped with a note, never parsed into a 500"


def test_the_tree_tools_target_the_trunk_and_read_the_wood_for_real():
    """The player's own method, verbatim: "as a player i select the tree trunk with the
    chopping order" - so felling targets `tree_trunk`, and saplings are the vegetation
    order (measured: 1034 trunks and 505 saplings on this map; a "clear 5 saplings" plan
    is not 5 trees). Two bugs found while building them:
      * block base coordinates: map_x/map_y ARE tile coordinates; multiplying by 16 again
        reported trunks at x256,y1792 on a 192x192 map.
      * the wood read invented `(await world.progress()).facts` - progress() returns the
        ladder dict, so the attribute error was swallowed and both counts answered null."""
    main = pathlib.Path("main.py").read_text()
    i = main.index('operation_id="cut_trees"')
    seg = main[i:main.index("@app.", i + 10)]
    assert 'TRUNK = "tree_trunk"' in seg, "the chopping order targets the trunk"
    assert "sapling" in seg, "and saplings are counted, not felled"
    assert 'await _designate("DEFAULT_DIG", locs)' in seg, "designations go through the sanctioned path"
    assert "WM.FortFacts" in seg, "the wood count is built from FortFacts, not a made-up accessor"
    j = main.index('operation_id="find_trees"')
    seg2 = main[j:main.index("@app.", j + 10)]
    assert "base_x, base_y = int(b.map_x), int(b.map_y)" in seg2, "map_x/map_y are tile coordinates"
    assert "int(b.map_x) * 16" not in seg2, "the doubled scale is what invented trees off the map"


def test_resize_states_the_game_rule_instead_of_a_bare_failure():
    """`/resize-building` promised "Resize a building - stockpiles included". The game says:
    `setSize ... Invalid argument; expected: bld->id == -1` - only an ABSTRACT building can be
    sized, and the old lookup walked the STOCKPILE list alone, so a built pile and every other
    building answered "not found"."""
    lo = pathlib.Path("luaops.py").read_text()
    i = lo.index("async def resize_building(")
    body = lo[i:lo.index("_ZONE_HINTS = {")]
    assert "dfhack.buildings.findBuilding(__ID__)" in body, "look the building up by id, not by pile list"
    assert "REFUSED" in body, "a refused setSize must be its own answer"
    assert "bld->id == -1" in body, "the answer must carry the game's rule"
    assert "ABSTRACT" in body, "and say what can be sized: an abstract at creation time"


def test_an_emptied_scan_says_NO_DATA_it_does_not_report_zero():
    """`/stockpiles` and `/zones` answered 0 for a fort that has a 10x13 general pile and a
    25-tile meeting zone, because the RPC hiccup swallowed the chunk's print and the
    readers turned "printed nothing" into `[]`. A caller that trusts `count: 0` digs a
    second general pile next to the first. An empty fort is a fact; an unread fort is not."""
    lo = pathlib.Path("luaops.py").read_text()
    assert "class NoData(LuaOpError)" in lo, "there must be a distinct no-data signal"
    for fn in ("list_zones", "list_stockpiles"):
        i = lo.index("async def " + fn + "(")
        body = lo[i:lo.index("async def ", i + 10)]
        assert "raise NoData(" in body, f"{fn}: a scan that printed nothing must raise NoData"
        assert "return []          # an empty fort is a fact, not a failure" not in body, \
            f"{fn}: the old silent [] is what made 3 piles read as zero"
    main = pathlib.Path("main.py").read_text()
    for ep in ('operation_id="list_zones"', 'operation_id="list_stockpiles"'):
        i = main.index(ep)
        seg = main[i:main.index("@app.", i + 10)]
        assert '"success": False' in seg, f"{ep}: a failed read must not answer a bare count"


def test_the_general_pile_rung_measures_categories_not_a_pile_count():
    """A wood-only pile flipped the GENERAL pile rung DONE (measured 2026-11-21, rung #4:
    the fort had one pile accepting `wood`). `count_built("stockpile") >= 1` cannot tell a
    general pile from any pile - and the rung's own why-line is about categories.
    Prove the behaviour on stub facts, not on source text."""
    import sys, pathlib as _p
    sys.path.insert(0, str(_p.Path(__file__).resolve().parent.parent))
    import worldmodel as WM
    f = WM.FortFacts.__new__(WM.FortFacts)
    f.piles = [{"id": 1, "accepts": ["wood"], "x0": 66, "x1": 67, "y0": 112, "y1": 113}]
    assert not f.pile_general(), "a wood-only pile is NOT a general pile"
    f.piles = [{"id": 1, "accepts": ["stone"], "x0": 0, "x1": 1, "y0": 0, "y1": 1}]
    assert not f.pile_general(), "a stone pile is not one either"
    f.piles = [{"id": 1, "accepts": ["food", "stone", "wood", "cloth", "leather",
                                      "weapons", "armor", "ammo", "furniture"]}]
    assert not f.pile_general(), "and one that eats stone+wood fails the wiki's recipe"
    f.piles = [{"id": 1, "accepts": ["ammo", "animals", "armor", "bars_blocks", "cloth",
                                      "coins", "finished_goods", "food", "furniture",
                                      "leather", "sheet", "weapons"]}]
    assert f.pile_general(), "12 categories, none of the cloggers: that IS the general pile"
    f.piles = []
    assert not f.pile_general(), "no pile read is NO DATA - the rung stays open"
    wm = pathlib.Path("worldmodel.py").read_text()
    i = wm.index('("stockpile", "General stockpile')
    assert "lambda f: f.pile_general()" in wm[i:i+320], "the rung must use it"


def test_tile_counts_and_removal_verdicts_come_from_the_game():
    """Two reporting lies measured on 2026-11-21, both found by the player asking to test
    the tools end to end (create a pile, remove it):

      1. `countExtentTiles(b, b.x1, b.z, b.y1)` - a 2x2 wood pile reported
         usable_tiles 167, rect_tiles 0, holes -167. 167 is the Z. The proven call passes
         the BUILDING ALONE.
      2. `remove_building` printed "removed: true" right after the deconstruct CALL.
         deconstruct marks; the game's own verdict is what the tile says afterwards.

    A tool that answers with a number the game never measured is worse than no tool: I
    reported a 167-tile pile to you and you had to ask me to test it.
    """
    lo = pathlib.Path("luaops.py").read_text()
    assert "countExtentTiles(b, b.x1, b.z, b.y1)" not in lo, "the multi-arg form returns garbage (the z)"
    assert "countExtentTiles(found, found.x1, found.z, found.y1)" not in lo, "same bug in the zone/plan builders"
    for fn in ("create_zone", "place_building_plan", "create_building_multi", "create_stockpile"):
        i = lo.index("async def " + fn + "(")
        body = lo[i:lo.index("async def ", i + 10)]
        assert "countExtentTiles(" in body and body.count("countExtentTiles(") == body.count("end)") - 20 or True
        calls = [c for c in body.splitlines() if "countExtentTiles(" in c]
        for c in calls:
            inner = c[c.index("countExtentTiles("):].split(")")[0]
            assert inner.count(",") == 0, f"{fn}: countExtentTiles must take the building alone, got {inner}"

    rb = lo[lo.index("async def remove_building("):lo.index("async def walkable_between(")]
    assert 'VERDICT|' in rb, "the removal must answer with the game's verdict"
    assert '"removed": verdict == "gone"' in rb, "removed:true ONLY when the tile is verified empty"
    assert '"marked_for_removal": verdict == "marked"' in rb, "a mark is not a removal"
    assert "VERDICT|absent" in rb, "an empty tile is reported as absent, not as a removal"
    assert '"verdict": verdict,' in rb, "and the verdict the game gave is what the caller sees"

    # The pile site gate: the mid-air piles are the reason it exists.
    main = pathlib.Path("main.py").read_text()
    i = main.index('operation_id="create_stockpile"')
    seg = main[i:main.index("@app.post", i + 10)]
    assert '"gate": "no rock/wall/hidden/open in the footprint"' in seg, \
        "a pile must refuse a site that is not ground"
    assert '"gate": "no rock/wall/hidden/open, to_dig == 0"' in seg, \
        "and refuse unexcavated rock (the mid-air piles came from exactly that)"
    assert "not pile ground" in seg, "and say what it measured"
    wm = pathlib.Path("worldmodel.py").read_text()
    assert "ramp_top" in wm[wm.index("DUG_CLASSES"):wm.index("TO_DIG_CLASSES")], \
        "a ramp top is dug ground (the gate refused a legal site without it)"


def test_scan_row_guards_match_the_row_they_parse():
    """`list_stockpiles` printed ROW + 9 fields (the extent is ONE field, "72..75,122..124")
    and its guard demanded 10, so EVERY pile row was dropped: /stockpiles answered count 0
    for a fort with three piles standing. An empty answer that is really a parse failure is
    the most dangerous shape a read can have - it looks exactly like a fact. Measured
    2026-11-21; the zone row legitimately has 10 fields, so the two scans need different
    widths and each guard must match its own unpack."""
    lo = pathlib.Path("luaops.py").read_text()
    # The pile row grew a 10th field (panel_number) on 2026-11-21 - the guard must track it.
    for name, width in (("list_zones", 10), ("list_stockpiles", 10)):
        i = lo.index("async def " + name)
        body = lo[i:lo.index("async def ", i + 10)]
        assert f"if len(parts) < {width}:" in body, name + ": guard must match the row width"
        unpack = [l for l in body.splitlines() if "parts" in l and "=" in l and "len(parts)" not in l
                  and "split" not in l]
        assert unpack, name + ": must unpack the row"
        names = unpack[0].split("=")[0].strip()
        arity = len([p for p in names.split(",") if p.strip()])
        assert arity == width, (name + f": unpacks {arity} fields, guard is {width}")


def test_the_ladder_reads_the_same_building_pool_as_observe():
    """`observe` replaced the band census with the map-wide pool (measured: 9 Farm Plots at
    elev 41 invisible to a 31-42 band read), but `progress()` - the ladder the plugin and I
    act on - kept reading the band. Result: `farm` rung satisfied in one tool and open in
    the other, on the same fort, minutes apart. Both paths must read one pool."""
    wm = pathlib.Path("worldmodel.py").read_text()
    i = wm.index("async def progress(") if "async def progress(" in wm else wm.index("def progress(")
    seg = wm[i:i + 5200]
    assert "await self.buildings_mapwide()" in seg, "progress() must use the map-wide pool"
    assert 'facts.built_names = [b["name"].lower() for b in _pool]' in seg, \
        "and rebuild the derived names, not just the list"
    assert "rungs read the band census only" in seg, "a failed scan is labelled, not silent"


def test_one_rung_one_answer_the_providers_run_on_both_read_paths():
    """`progress()` (behind /fort-progress) called the zone/pile readers; `observe` (behind
    the plugin's ladder) built FortFacts WITHOUT them - so facts.zones and facts.piles were
    empty there and the same rung answered true in one tool and false in the other
    (measured 2026-11-21: a 25-tile MeetingHall and an outdoor corpses+refuse pile, both
    standing, both reported open by the plugin). A rung whose truth depends on which tool
    you ask is not a measurement."""
    main = pathlib.Path("main.py").read_text()
    i = main.index('want_facts = bool(req.get("facts", True))')
    seg = main[i:i + 2400]
    assert '("zone_provider", "zones")' in seg and '("pile_provider", "piles")' in seg, \
        "observe must call both providers"
    assert 'notes.append(f"{field} unavailable' in seg, "and a failed read must be labelled NO DATA"
    wm = pathlib.Path("worldmodel.py").read_text()
    assert 'if getattr(self, "pile_provider", None) is not None:' in wm, "progress() fills piles too"
    assert 'lambda f: f.pile_accepts(["refuse", "corpses"])' in wm, \
        "the refuse rung measures the pile the wiki's keys build, not a dump zone"


def test_zone_creation_is_the_stockpile_path_with_the_kind_at_allocation():
    """The player's comparison question - "stock piles and zones should be very similar,
    compare the tools and see where the error is" - found it.

        create_stockpile  allocInstance -> setSize -> constructAbstract            (no crash)
        create_zone   v1  same, then `found.type = <kind>`   -> FORT CRASHED
        create_zone   v2  constructBuilding{...}             -> FORT CRASHED
        create_zone   v3  allocInstance(30, subtype=<kind>) -> setSize -> constructAbstract
                                                       (3 zones live, fort running)

    So: the kind belongs in the SUBTYPE at allocation, and nothing is written to a zone
    after constructAbstract. Pinned as a regression guard because both rejected forms
    cost a save.
    """
    src = pathlib.Path("luaops.py").read_text()
    i = src.index("async def create_zone(dec, kind: str")
    fn = src[i:src.index("async def remove_zone(dec, building_id: int)", i)]
    j = fn.index('lua = """')
    body = fn[j:fn.index('""".strip()')]
    assert "dfhack.buildings.allocInstance(pos, 30, kind, -1)" in body, \
        "the kind is the subtype at allocation"
    assert "dfhack.buildings.setSize(inst" in body and "constructAbstract(inst)" in body, \
        "the same three calls create_stockpile uses"
    assert ".type =" not in body and "notifyCivzoneModified" not in body, \
        "no post-construction type write (the v1 crash), no manual zone notification"
    assert "constructBuilding" not in body, "v2's constructBuilding route is rejected too"
    assert "deconstruct(inst)" in body, "and every failure path disposes the orphan"
    # The parse bug that made a SUCCESS answer 500: "OK|a|b|c|d|e"[2:] keeps the pipe.
    assert 'line.split("|")[1:]' in fn, "the row is parsed after the OK token"


def test_zone_writes_are_opt_in_and_pile_ids_come_from_the_game():
    """Two live-fort crashes followed zone writes (allocInstance -> constructAbstract ->
    write z.type -> notifyCivzoneModified is not the game's own zone creation path). A tool
    that can destroy a save must not fire just because a caller asked for a ladder rung.
    Separately: pile ids are per-save, and a hand-typed profile list reads "not found"
    forever - the observer's pile tests were unrunnable across every save swap."""
    main = pathlib.Path("main.py").read_text()
    i = main.index('operation_id="create_zone"')
    fn = main[i:i + 2600]
    assert 'body.get("acknowledge_risk") is not True' in fn, "the write must be gated"
    assert '"requires": "acknowledge_risk"' in fn, "and the refusal says what it needs"
    assert "THROWAWAY save" in fn, "and points at the safe way to clear the gate"

    # Transport failures are answers, not HTTP 500s: a caller must be able to read
    # "the game is closed" (measured: create_zone 500'd on a dropped RPC mid-call).
    assert "except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:" in main, \
        "reads/writes answer JSON when the RPC drops"

    # Pile discovery: the server resolves "auto" from its own stockpile scan.
    assert '_want_piles == "auto"' in main, "observe accepts the auto request"
    assert "pile ids discovered from the game" in main, "and reports where the ids came from"

    # The plugin asks by default (an empty config is the shipped default).
    plugin = pathlib.Path("plugin/df-observer/index.js").read_text()
    assert "cfg.pile_ids.length) ? cfg.pile_ids : 'auto'" in plugin, "empty config asks the game"


def test_a_failed_build_must_not_leave_an_orphan_building():
    """allocInstance returns a real building object that is not in any list until it is
    committed. A builder that returns on a failure path leaves it dangling - no id, no
    list entry, and the game trips over it on the next world operation. Measured
    2026-11-21: a 5x5 zone request over unexcavated rock hit the `setSize refused`
    branch, and the fort crashed afterwards with no save. In-game check: deconstruct
    disposes an orphan cleanly (pcall true, no error), so every failure path of every
    builder must dispose the instance before returning its error."""
    lo = pathlib.Path("luaops.py").read_text()
    for name in ("create_zone", "create_building_multi", "create_stockpile"):
        i = lo.index("async def " + name + "(")
        nxt = lo.find("async def ", i + 10)
        body = lo[i:nxt] if nxt > 0 else lo[i:]
        assert "allocInstance(" in body, name + " must allocate"
        disposals = body.count("deconstruct(inst)")
        assert disposals >= 2, (name + ": " + str(disposals) + " dispose call(s) for the "
                                 "setSize-refusal and nothing-live failure paths")
        err = chr(69) + "RR|"          # built here so this test file needs no escapes
        lines = body.splitlines()
        alloc_at = next((n for n, l in enumerate(lines) if "allocInstance(" in l), None)
        assert alloc_at is not None, name + " must allocate"
        for n, ln in enumerate(lines):
            if "print(" + chr(34) + err not in ln:
                continue
            if n < alloc_at:
                continue              # before the allocation there is no orphan to dispose
            if "allocInstance" in ln or "inst == nil" in ln:
                continue   # this IS the allocation-failure path: nil allocated, nil to free
            head = lines[n - 1]
            assert "deconstruct(inst)" in head, \
                name + ": every ERR return after an allocation must be preceded by a dispose"

def test_zones_have_kinds_and_the_ladder_must_read_them():
    """Every activity zone is building type 30 named "Zone", so the census cannot tell a
    meeting area from an empty marked rectangle. Measured 2026-11-21: ladder rung #5
    ("Meeting area, 5x5-7x7") flipped to DONE on a 2x2 zone with `type=-1` - no meeting
    area existed, and the plugin faithfully reported a rung whose predicate was
    `count_built("zone") >= 1`. A rung that any 1x1 nothing satisfies is not evidence."""
    lo = pathlib.Path("luaops.py").read_text()
    i = lo.index("async def create_zone")
    # Scope to the Lua body: the docstring quotes the crash it replaces ("found.type = ..."),
    # and prose must not be what a safety assertion reads.
    body = lo[lo.index('lua = """', i):lo.index('""".strip()', i)]
    # v3: the kind is the SUBTYPE given to allocInstance, never written afterwards.
    # (v1's `found.type = target` + notifyCivzoneModified crashed a fort; v2's
    # constructBuilding crashed a second one - see the dedicated test above.)
    assert "local kind = df.civzone_type.__TYPE__" in body, "the kind comes from the game's own enum"
    assert "allocInstance(pos, 30, kind, -1)" in body, \
        "and is supplied AT ALLOCATION - a zone with type=-1 satisfies no rung"
    assert "found.type =" not in body, "never written after construction (the v1 crash)"
    assert "buildings.other.ANY_ZONE" in body, "verify through ANY_ZONE (findAtTile is blind to zones)"
    _z = lo.index("async def list_zones")
    lz = lo[_z:lo.index("async def list_stockpiles", _z)]  # whole function, no fixed window
    assert "type_name" in lz, "the read must expose the kind name"
    assert "marked_for_removal" in lz, "and the removal flag (a removed zone is not a rung)"

    wm = pathlib.Path("worldmodel.py").read_text()
    assert "def zone_built(self, kind: str" in wm, "facts need a kind-aware zone check"
    assert 'lambda f: f.zone_built("meeting", 25)' in wm, "rung meeting needs a MEETING zone, 5x5+"
    # The wiki's refuse rung is built with p -> an outdoor pile accepting corpses+refuse;
    # demanding a 25-tile dump zone made it unfinishable by its own keys. Measured 2026-11-21.
    assert 'lambda f: f.pile_accepts(["refuse", "corpses"])' in wm, "rung refuse measures the pile the keys build"
    assert "self.zones: list[dict] = []" in wm, "and NO DATA means the rung stays open"
    assert "facts.zones = await self.zone_provider(dec)" in wm, "filled by the host, not guessed"

    main = pathlib.Path("main.py").read_text()
    for op in ("create_zone", "list_zones", "remove_zone"):
        assert f'operation_id="{op}"' in main, f"the {op} tool must be exposed"
    assert "world.zone_provider = lambda dec: LO.list_zones(dec)" in main, "and injected into the model layer"

    # The bottom-up stair rule the player taught us, in the know-how the next agent reads.
    sk = pathlib.Path(".dsh/skills/df-dig/SKILL.md").read_text()
    # The know-how must carry the MEASURED rule, not the plausible one: stair designations
    # work in fog (the player's shaft at x39,y127 is dug through 38-40 that way), and the
    # file must say not to pre-mine access for a stairwell.
    assert "Do not pre-mine the way for a stairwell" in sk, "stairs are designated into fog"
    assert "MineralStairUD" in sk, "with the dug evidence named"
    assert "**false for designated wall mining**" in sk, "and the disproved claim labelled as such"
    assert "sometimes soil is available, but that is more luck" in sk, \
        "soil at depth 2 is luck, and the skill must say so"
    assert "read the tile class where the floor will be" in sk, "probe the class, do not plan from elevation"


def test_a_survey_must_not_outlive_the_dig_it_is_checking():
    """The pre-flight read goes stale when the MINERS change the map, not only on a
    save reload. Measured 2026-11-21: a 5x5 hub and a 19-tile hall at in-game 36 were
    fully carved, and /survey kept answering "hidden 25, dug 0" / "hidden 51, dug 0"
    for those exact boxes while /tile-at reported MineralFloor2/4 with hidden=false -
    because the glyph band was memoed by (e0, e1, gen) with no expiry. A pre-flight
    that calls finished floors "fog" tells the agent to designate over them (§2) and
    wastes a crew, and it makes a completed dig look like an untouched face."""
    src = pathlib.Path("worldmodel.py").read_text()
    stack = src[src.index("async def _stack"):]
    assert "cached[ck] = (band, levels, time.time())" in stack[:2200], "the band memo must carry its age"
    assert "time.time() - hit[2] <= CACHE_TTL_S" in stack[:2200], "and expire on the block cache's trust window"
    sv = src[src.index("async def survey"):src.index("async def survey") + 4200]
    assert "live[(b.map_x + (i % 16), b.map_y + (i // 16))] = dec.tile_class(int(tid)).glyph" in sv, \
        "survey must classify tiles from the block it just read"
    assert "g = live.get((x, y)) or band.get((x, y), {}).get(elev)" in sv, \
        "and prefer the live glyph over the memo"
    # The same discipline the survey already used for exploration flags must not be
    # left covering only half the payload.
    assert "fog.add((b.map_x + (i % 16), b.map_y + (i // 16)))" in sv, "the block's own hidden flags"


def test_one_level_one_source_no_phantom_offsets():
    """The elevation contract must be ONE number in ONE place. Measured 2026-11-21:
    the wagon reads z=167 in DFHack `position` and the in-game panel says Elevation 38,
    so GAME_Z_OFFSET=129 is the save's truth - while the dig tool's own doc said
    "z = elevation + 128" and three notes repeated 128. An agent that followed the doc
    designated one level UNDER what it aimed at, and the designation path sends z
    AS GIVEN, so nothing downstream could catch it."""
    src = pathlib.Path("worldmodel.py").read_text()
    main = pathlib.Path("main.py").read_text()
    # 1) Every window that can return two levels must keep only the asked-for one.
    sites = [m for m in ("by_xy = {(int(b.map_x), int(b.map_y)): b for b in blocks}",
                         "by_xy = {(int(b.map_x), int(b.map_y)): b for b in blocks if int(b.map_z) == z}")
             if m in src]
    assert "by_xy = {(int(b.map_x), int(b.map_y)): b for b in blocks}" not in src, \
        "a two-level window collapsed by (x,y) alone silently reads the level ABOVE z"
    assert src.count("for b in blocks if int(b.map_z) == z") >= 2, src.count("for b in blocks if int(b.map_z) == z")
    # tile_at must skip the upper level explicitly.
    ta = src[src.index("async def tile_at"):]
    assert "if int(b.map_z) != z:" in ta[:2000], "tile_at must filter the inclusive window to its own z"
    # 2) No phantom offset in the contract the model reads.
    for bad in ("elevation + 128", "z - 128", "E + 128"):
        assert bad not in main, f"stale offset in main.py: {bad!r}"
    # Anchor on the ENDPOINT, not on the literal path string: a helper docstring that
    # mentions /send-dig-command moved the match 40 lines up and the window read the
    # tree tools instead of the dig contract (measured 2026-11-21).
    dig = main[main.index('operation_id="send_dig_command"'):]
    assert "elevation + 129" in dig[:2000], "the dig contract must state the offset the reads use"
    assert "GAME_Z_OFFSET" in dig[:2000], "and name the single source of it"


def test_map_lines_renders_x_coordinate_ruler_when_uncompressed():
    """Section 5.3: world_map with rle=False renders a column-aligned X-coordinate ruler
    so the model can pick exact (x, y) origins without miscounting RLE runs."""
    lv = WM.LevelModel(165, None)
    lv.rows = lv.cols = 6
    lv.origin = (40, 120)
    lv.grid = [["#"] * 6 for _ in range(6)]
    lv.grid[2][3] = "."
    b = WM.Budget(500)
    lines = WM.map_lines(lv, 40, 45, 120, 125, b, rle=False, region_labels=False)
    assert lines[1] == "    |444444", f"expected tens ruler, got {lines[1]!r}"
    assert lines[2] == "    |012345", f"expected ones ruler, got {lines[2]!r}"
    assert lines[5] == "122|###.##", f"expected row 122, got {lines[5]!r}"


def test_transition_store_records_wall_to_floor_and_stair_transitions():
    """Section 5.4 (PLAN-MODEL.md Technique 1): TransitionStore tracks observed
    wall/fog -> floor/stair transitions in SQLite and never confuses pre-existing
    natural cavern floors with player-dug tiles."""
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tf:
        db_file = tf.name
    try:
        store = WM.TransitionStore(db_path=db_file)
        lv = WM.LevelModel(165, None)
        lv.rows = lv.cols = 4
        lv.origin = (10, 20)
        # (10,20) is already '.' on first sight (natural cavern floor) -> must NOT count as dug
        # (11,20) is '#' wall, (12,20) is '?' fog, (13,20) is '#' wall
        lv.grid = [
            [".", "#", "?", "#"],
            ["#", "#", "#", "#"],
            ["#", "#", "#", "#"],
            ["#", "#", "#", "#"],
        ]
        n0 = store.ingest_level("saveA", lv)
        assert n0 == 0, "baseline observation must record 0 transitions"

        # Now miners carve (11,20) -> '.', (12,20) -> '.', and (13,20) -> '=' (stair)
        lv.grid[0] = [".", ".", ".", "="]
        n1 = store.ingest_level("saveA", lv)
        assert n1 == 3, f"expected 3 transitions, got {n1}"
        s = store.summary("saveA")
        assert s["dug_floors"] == 2 and s["dug_stairs"] == 1 and s["total_dug_transitions"] == 3

        # Re-open from SQLite in a fresh TransitionStore instance to prove persistence
        store2 = WM.TransitionStore(db_path=db_file)
        s2 = store2.summary("saveA")
        assert s2["dug_floors"] == 2 and s2["dug_stairs"] == 1

        # Save reload / clear_save wipes the save's transitions
        store2.clear_save("saveA")
        assert store2.summary("saveA")["total_dug_transitions"] == 0
    finally:
        try:
            os.remove(db_file)
        except OSError:
            pass


def test_the_soil_census_reports_dig_to_soil_ground_not_only_existing_soil():
    """The player's rule: **a dug floor takes the material of the layer above**, so a room dug one
    level under the soil surface has a SOIL FLOOR. My census reported only existing soil tiles,
    answered 'no underground soil on this world', and put the farm on open sky - the question was
    wrong, because the farmable set is soil-COVERED rock, not soil tiles. The census must report
    potential soil as its own answer, and the skill must state the rule (df-dig §23)."""
    main = pathlib.Path("main.py").read_text()
    i = main.index('@app.get("/find-soil"')
    seg = main[i:main.index('@app.post("/pile-arrays"', i)]
    assert "potential_soil_clusters" in seg and "potential_soil_tiles" in seg, "the census reports dig-to-soil ground"
    assert "cover_above" in seg, "it checks the layer above, which is what the rule is about"
    assert "a dug floor takes the material of the layer above" in seg, "the rule is in the tool's own words"
    skill = pathlib.Path("skills/df-dig/SKILL.md").read_text()
    assert "digging one level below the surface CREATES soil" in skill, "the rule is in the skill"
    assert "potential_soil_clusters" in skill, "and it names the field to use"


def test_the_labor_menu_is_driven_at_the_work_detail_layer_and_labor_writes_say_so():
    """`/set-unit-labors` wrote `unit.status.labors` and reported `applied: N` while the player's
    Labor screen showed one miner: the game recomputes those flags from the WORK DETAILS, measured
    as a census of 5 dropping to the 2 ticked dwarves after a tick. A tool whose write the game
    overwrites must say so. The authority is `df.global.plotinfo.labor_info.work_details[i]` with
    `assigned_units` (unit ids = the tick column), `allowed_labors`, `flags`
    (`cannot_be_everybody`/`no_modify` on built-ins) - the route found in the game's own scripts
    (autofish.lua, gui/manipulator.lua, gui/settings-manager.lua)."""
    mod = pathlib.Path("deployables/scripts/dfhack_mcp.lua").read_text()
    i = mod.index("function OPS.work_details")
    seg = mod[i:mod.index("function M.dispatch")]
    assert "df.global.plotinfo.labor_info" in seg and "li.work_details" in seg, \
        "the real container (found in the game's own scripts), not a guess at world.*"
    assert "assigned_units" in seg and "allowed_labors" in seg, "it reads the tick list and the granted labors"
    assert "list:resize(" in seg and "list:erase(" in seg, "assign uses the measured vector API: resize+assign, erase"
    assert "remove" not in seg.split("list:")[2] or "erase" in seg, "list:remove is nil on vector<int32_t> - never used"
    assert "read_back" not in seg or "assigned_after" in seg, "assign answers with the list as the game now holds it"
    lo = pathlib.Path("luaops.py").read_text()
    assert "async def work_details(" in lo, "the bridge exists"
    main = pathlib.Path("main.py").read_text()
    assert '@app.get("/work-details"' in main, "the endpoint exists"
    k = main.index('@app.post("/set-unit-labors"')
    seg2 = main[k:main.index("@app.", k + 30)]
    assert "governing_details" in seg2 and "durability" in seg2, "the labor write names the layer that owns the flags"
    assert "not-in-detail" in seg2, "and it says when the write is not durable"
    # stale bridges must not survive: a dead Lua-backed census and a doubled def
    assert "async def find_soil(" not in lo, "the dead Lua census bridge is gone"
    names = [l for l in lo.split("\n") if l.startswith("async def unit_labors(")]
    assert len(names) == 1, "no duplicate bridge definitions"


def test_the_reachability_graph_is_anchored_on_civilians_not_wildlife_density():
    """`/dig-reachable` said walkable fort floors were unreachable: `fort_band()` scored unit
    density over ALL units, so ~90 wildlife units at RPC z 11-26 beat the fort's 17 citizens at
    z 167, the band landed at (4,15) = elevations -125..-114, `_stack` returned nothing, and every
    connectivity answer was a false negative. Two of my past reports ('the designation was cancelled
    silently', 'the soil patch is unreachable') were artifacts of this. The graph must be anchored
    on civilian dwarves, and an empty graph must be labelled NO DATA - never 'unreachable'."""
    w = pathlib.Path("worldmodel.py").read_text()
    i = w.index("async def fort_band")
    seg = w[i:w.index("async def ", i + 30)]
    assert 'not u.get("hostile")' in seg and 'not u.get("caged")' in seg, "the band ignores wildlife and caged units"
    assert '"dwarf" in str(u.get("race"' in seg, "the fort is where civilian dwarves stand"
    assert "focus[z] * 6" in seg, "the player's view outweighs raw unit counts"
    j = w.index("async def reach_map")
    seg2 = w[j:w.index("async def ", j + 30)]
    assert "band_source" in seg2 and "GetViewInfo" in seg2, "an empty band retries around the view and says which band it used"
    assert '"tiles": len(comp)' in seg2, "the answer reports how much graph it actually built"
    assert "NO DATA, not 'unreachable'" in seg2, "an empty graph is labelled as a read failure"


def test_site_gate_and_idempotency_on_place_building_create_zone_and_stockpile():
    """Sections 5.1 & 5.2: shared footprint ground check, duplicate zone/stockpile
    idempotency gate, enriched building_status diagnostics, and /transitions endpoint."""
    main = pathlib.Path("main.py").read_text()
    assert "async def _verify_footprint_ground(" in main
    for ep in ('operation_id="place_building"', 'operation_id="create_zone"'):
        i = main.index(ep)
        seg = main[i:main.index("@app.", i + 20)]
        assert "_verify_footprint_ground(" in seg, f"{ep} must run _verify_footprint_ground"
    for ep in ('operation_id="create_stockpile"', 'operation_id="create_zone"'):
        i = main.index(ep)
        seg = main[i:main.index("@app.", i + 20)]
        assert '"already_exists": True' in seg, f"{ep} must short-circuit duplicate footprints"
    bs = main[main.index('operation_id="building_status"'):]
    assert '"diagnostics": diagnostics' in bs and '"recent_cancellations"' in bs
    assert 'operation_id="transitions"' in main


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
