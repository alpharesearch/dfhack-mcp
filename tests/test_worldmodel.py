"""Offline tests for the preprocessing layer (no game required).

    uv run --no-sync python -m pytest tests/test_worldmodel.py -q
    uv run --no-sync python tests/test_worldmodel.py      # no pytest needed

The live-game assertions run only when DFHack RPC answers on :5000.
"""
import json
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
    lines = WM.progress_lines(facts)
    txt = "\n".join(lines)
    assert "NEXT (labors)" in txt, txt[:400]
    assert "y -> Miners" in txt
    assert "[x] Stairs connecting the levels" in txt
    assert "[ ] General stockpile" in txt
    # a satisfied ladder must say so instead of repeating rung 1 forever
    facts.miners = 2
    facts.wood, facts.barrels, facts.soldiers = 5, 5, 1
    facts.count_built = lambda *k: 99
    facts.built = lambda *k: True
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
