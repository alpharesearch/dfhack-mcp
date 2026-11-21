"""Offline tests for the typed Lua layer (no game needed).

The point of these tests is that a model can NEVER name a building type id, a z value,
or a Lua fragment: the server resolves ids from the game's def list, converts elevations,
and composes a fixed template. So the tests check composition and parsing, not the game.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import luaops as LO                                            # noqa: E402


class FakeDec:
    """A stand-in for worldmodel.Decoders' building tables (RFR-shaped)."""
    def __init__(self):
        self.building = {(6, -1, -1): "Trade Depot", (29, -1, -1): "Stockpile",
                         (13, 15, -1): "Still", (1, -1, -1): "Bed", (0, -1, -1): "Chair",
                         (30, -1, -1): "Zone", (18, -1, -1): "Well", (4, -1, -1): "Farm Plot"}
        self.building_bts = {(13, 15): "Still", (13, -1): "Workshop"}
        self.building_bt = {6: "Trade Depot", 13: "Workshop", 0: "Chair"}


def test_alias_resolution_uses_the_games_own_defs():
    dec = FakeDec()
    assert LO.resolve_building(dec, "depot") == (6, -1, -1, "Trade Depot")
    assert LO.resolve_building(dec, "Trade Depot") == (6, -1, -1, "Trade Depot")
    assert LO.resolve_building(dec, "still") == (13, 15, -1, "Still")
    assert LO.resolve_building(dec, "bed") == (1, -1, -1, "Bed")
    assert LO.resolve_building(dec, "Chair")[0] == 0


def test_unnamed_building_is_rejected_before_any_lua():
    dec = FakeDec()
    try:
        LO.resolve_building(dec, "Imprisonment Machine")
        raise AssertionError("expected rejection")
    except LO.LuaOpError as e:
        assert "no building definition" in str(e)


def test_elevation_is_converted_to_rpc_z_internally():
    """The model speaks in-game Elevations; Lua needs z = elev + GAME_Z_OFFSET (=129). Templates must convert."""
    import inspect
    src = inspect.getsource(LO.place_building)
    assert "GAME_Z_OFFSET" in src and "z = int(elev) + GAME_Z_OFFSET" in src
    src2 = inspect.getsource(LO.walkable_between)
    assert "GAME_Z_OFFSET" in src2


def test_stockpile_config_initialises_the_ui_arrays():
    """Contract: configuring a stockpile must allocate the material arrays the game's
    custom-settings UI walks, in the same pass as the flags. Flags-only config crashes the
    player's Custom click (SIGSEGV at 0xa, render_things/overlay)."""
    import inspect
    src = inspect.getsource(LO.configure_stockpile)
    assert "SETTINGS_ARRAYS" in src and "resize" in src
    assert "arrays:" in src                                  # read-back reports the array sizes
    assert hasattr(LO, "make_stockpile") and hasattr(LO, "repair_stockpile_settings")
    sizes = LO.SETTINGS_ARRAYS
    assert sizes["ammo"]["mats"] == 319 and sizes["leather"]["mats"] == 892
    assert sizes["animals"]["enabled"] == 1327 and sizes["food"]["meat"] == 14476
    # misc has no flag in the game (writing flags.misc throws) - it must not be in CATEGORIES
    assert "misc" not in LO.CATEGORIES and "misc" in LO.MATS_ONLY


def test_templates_only_emit_a_single_status_line():
    import inspect
    for fn in ("place_building", "building_at", "remove_building", "walkable_between"):
        src = inspect.getsource(getattr(LO, fn))
        assert 'print("FAIL' in src, fn
        assert 'print("OK ' in src, fn


def test_place_template_carries_the_resolved_ids_and_coords():
    """Deterministic check on the composed Lua: ids, coords, and the verify step."""
    import asyncio, inspect
    captured = {}

    async def fake_lua(expr, settle=0.5):
        captured["expr"] = expr
        return 'OK building=\'Chair\' bt=0 type=building_chainst id=1234'
    real = LO._lua
    LO._lua = fake_lua
    try:
        res = asyncio.run(LO.place_building(FakeDec(), "Chair", 113, 31, 37))
    finally:
        LO._lua = real
    e = captured["expr"]
    assert "type=0" in e and "x=113" in e and "y=31" in e and "z=166" in e   # in-game 37 + 129
    assert "constructBuilding" in e and "completeBuild" in e
    assert "findAtTile(113, 31, 166)" in e   # in-game 37 + 129                      # verification is inlined
    assert res["game_type"] == "building_chainst" and res["building_id"] == 1234
    assert res["at"] == [113, 31, 37]                            # elevation echoed back


def test_failure_line_becomes_an_error_not_a_silent_success():
    import asyncio
    async def fake_lua(expr, settle=0.5):
        return "FAIL construct: nil"
    real = LO._lua
    LO._lua = fake_lua
    try:
        try:
            asyncio.run(LO.place_building(FakeDec(), "Chair", 1, 1, 37))
            raise AssertionError("expected LuaOpError")
        except LO.LuaOpError as e:
            assert "construct" in str(e)
    finally:
        LO._lua = real


def test_no_lua_is_reachable_from_the_mcp_surface():
    """The whole design rule: raw Lua must not be a tool."""
    main_src = pathlib.Path(__file__).resolve().parents[1].joinpath("main.py").read_text()
    assert 'exclude_operations=["game_lua"]' in main_src
    assert 'operation_id="game_lua"' in main_src            # endpoint exists (HTTP debug)
    for op in ("place_building", "remove_building", "building_at", "walkable_between",
               "job_queue", "unit_counts"):
        assert f'operation_id="{op}"' in main_src


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
