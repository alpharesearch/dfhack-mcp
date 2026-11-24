"""Offline tests for the typed Lua layer (no game needed).

The point of these tests is that a model can NEVER name a building type id, a z value,
or a Lua fragment: the server resolves ids from the game's def list, converts elevations,
and composes a fixed template. So the tests check composition and parsing, not the game.
"""
import pathlib
import re
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
    for fn in ("place_building", "building_at", "walkable_between"):
        src = inspect.getsource(getattr(LO, fn))
        assert 'print("FAIL' in src, fn
        assert 'print("OK ' in src, fn
    rb_src = inspect.getsource(LO.remove_building)
    assert 'ERR|' in rb_src and 'VERDICT|' in rb_src, "remove_building"


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
    # The exclusion list grows as debug endpoints are added (observe, observe_stats, ...);
    # the rule is that game_lua is IN it, not that the list has one member.
    m = re.search(r'exclude_operations=\[([^\]]*)\]', main_src)
    assert m is not None, "no exclude_operations on the MCP app"
    assert '"game_lua"' in m.group(1), f"game_lua not excluded: {m.group(1)}"
    assert 'operation_id="game_lua"' in main_src            # endpoint exists (HTTP debug)
    for op in ("place_building", "remove_building", "building_at", "walkable_between",
               "job_queue", "unit_counts"):
        assert f'operation_id="{op}"' in main_src


def test_concurrent_lua_calls_do_not_crosstalk_or_lose_output():
    """Regression test for the _reply_log race condition:
    Previously, _invoke appended RPC_REPLY_TEXT frames to a global _reply_log and _lua
    slept for 0.5-0.8s after releasing _io_lock before calling drain_replies(). Any
    concurrent caller (/observe polling OBSERVE_PICKS_LUA, list_zones, list_stockpiles,
    or an agent tool call) wiped _reply_log on entry and injected its own output into the
    sleeping caller's reply stream.
    """
    import asyncio
    import os
    import struct
    from dfhack_client_python import dfhack_remote
    from dfhack_client_python.py_export.CoreProtocol_pb2 import (
        CoreBindRequest, CoreBindReply, CoreRunCommandRequest,
        CoreTextNotification, CoreTextFragment, EmptyMessage,
    )

    def frame(id_, payload=b""):
        return struct.pack("<hhi", id_, 0, len(payload)) + payload

    def text_frame(s: str) -> bytes:
        msg = CoreTextNotification(fragments=[CoreTextFragment(text=s)])
        return frame(-3, msg.SerializeToString())

    async def handle(reader, writer):
        try:
            head = await reader.read(12)
            if head[:8] != b"DFHack?\n":
                writer.close()
                return
            writer.write(b"DFHack!\n" + struct.pack("<i", 1))
            await writer.drain()
            while True:
                try:
                    h = await reader.readexactly(8)
                except asyncio.IncompleteReadError:
                    return
                mid, _pad, size = struct.unpack("<hhi", h)
                body = await reader.readexactly(size) if size else b""
                if mid == -4:
                    return
                if mid == 0:
                    req = CoreBindRequest()
                    req.ParseFromString(body)
                    writer.write(frame(-1, CoreBindReply(assigned_id=42).SerializeToString()))
                    await writer.drain()
                elif mid == 42:
                    cmd = CoreRunCommandRequest()
                    cmd.ParseFromString(body)
                    tag = cmd.arguments[0] if cmd.arguments else ""
                    if tag == "FAIL_ME":
                        writer.write(text_frame("ERR|script failed detail"))
                        writer.write(struct.pack("<hhi", -2, 0, 1))  # RPC_REPLY_FAIL (CR_FAILURE=1)
                        await writer.drain()
                    else:
                        # Stream two text frames with a brief yield between them, then result
                        writer.write(text_frame(f"HEAD|{tag}"))
                        await writer.drain()
                        await asyncio.sleep(0.005)
                        writer.write(text_frame(f"TAIL|{tag}"))
                        writer.write(frame(-1, EmptyMessage().SerializeToString()))
                        await writer.drain()
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def run_scenario():
        srv = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        old_port = os.environ.get("DFHACK_PORT")
        os.environ["DFHACK_PORT"] = str(port)
        dfhack_remote.drop_connection()
        try:
            async with srv:
                tags = [
                    "OBS2 PICKS 3 SITE 677 DATE 100-0-22",
                    "ROW|1|87|MeetingHall|66|67|110|111|165|0",
                    "PILE 12 id=12 found=1 x0=52 x1=57",
                    "OK building='Still' bt=13 type=building_workshopst id=14",
                ] * 6  # 24 concurrent calls

                async def worker(idx: int, tag: str):
                    out = await LO._lua(f"call_{idx}:{tag}")
                    # Yield after _lua returns and verify drain_replies() is empty in this task
                    await asyncio.sleep(0.01)
                    assert dfhack_remote.drain_replies() == []
                    return idx, tag, out

                results = await asyncio.gather(*(worker(i, t) for i, t in enumerate(tags)))
                for idx, tag, out in results:
                    expected = f"HEAD|call_{idx}:{tag}\nTAIL|call_{idx}:{tag}"
                    assert out == expected, f"cross-talk on call {idx}: got {out!r}, expected {expected!r}"

                # Also verify error text is preserved on RPC_REPLY_FAIL
                try:
                    await LO._lua("FAIL_ME")
                    raise AssertionError("expected LuaOpError on FAIL_ME")
                except LO.LuaOpError as e:
                    assert "ERR|script failed detail" in str(e), f"missing error text: {e}"
                await dfhack_remote.close()
        finally:
            dfhack_remote.drop_connection()
            if old_port is None:
                os.environ.pop("DFHACK_PORT", None)
            else:
                os.environ["DFHACK_PORT"] = old_port

    asyncio.run(run_scenario())


def test_pruned_mcp_surface_and_run_command_allowlist():
    """Verify Phase 1 tool hygiene (PLAN-TOOLS.md):
    1. Raw/footgun and redundant tools are excluded from FastApiMCP.
    2. run_command enforces an allowlist and explicitly denies raw lua/script, cheats,
       and destructive commands.
    3. make_stockpile delegates to create_stockpile so the mid-air site gate cannot be bypassed.
    """
    main_src = pathlib.Path(__file__).resolve().parents[1].joinpath("main.py").read_text()
    m = re.search(r'exclude_operations=\[([^\]]*)\]', main_src)
    assert m is not None
    excluded = m.group(1)
    for op in ("game_lua", "observe", "observe_stats", "get_block_list", "world_raw",
               "get_versions", "get_world_info", "get_map_info", "get_view_info",
               "get_unit_list", "world_zone", "building_at", "make_stockpile",
               "resize_building", "repair_stockpile_settings",
               "overburden", "column", "cross_section", "depth_profile", "magma_depth",
               "probe_plan", "tunnel_sites", "find_soil", "apply_blueprint",
               "pass_keyboard_event", "buildings_live", "list_buildings",
               "work_order_link", "pile_arrays", "tile_legend", "labor_list",
               "get_building_def_list"):
        assert f'"{op}"' in excluded, f"{op} should be excluded from the MCP tool surface"

    # Verify exposed MCP tool count (<= 32) and concise docstrings (< 9,500 chars)
    import main as M
    excl_set = set(re.findall(r'"([^"]+)"', excluded))
    exposed_ops = {}
    for r in M.app.routes:
        op = getattr(r, "operation_id", None)
        if not op or op in excl_set or op == "mcp_http":
            continue
        exposed_ops[op] = (r.endpoint.__doc__ or "").strip()
    assert len(exposed_ops) <= 32, f"expected <= 32 exposed MCP tools, got {len(exposed_ops)}: {sorted(exposed_ops)}"
    for core_op in ("fort_strata", "fort_plan", "trade_depot", "workshop_job",
                    "dfhack_automation", "look", "send_dig_command", "place_building"):
        assert core_op in exposed_ops, f"core tool {core_op} must remain exposed on MCP"
    total_doc_chars = sum(len(d) for d in exposed_ops.values())
    assert total_doc_chars < 9500, f"exposed docstring bloat too high: {total_doc_chars} chars"

    notes_path = pathlib.Path(__file__).resolve().parents[1].joinpath("docs", "tool-notes.md")
    assert notes_path.is_file() and notes_path.stat().st_size > 1000, "docs/tool-notes.md must exist"

    assert "RUN_COMMAND_DENY_EXACT" in main_src and "RUN_COMMAND_ALLOWLIST" in main_src
    for denied in ('"lua"', '"luap"', '"script"', '"die"', '"exterminate"', '"dig-now"', '"build-now"'):
        assert denied in main_src, f"{denied} must be in RUN_COMMAND_DENY_EXACT"

    # make_stockpile must route through create_stockpile so the site gate runs
    i = main_src.index('operation_id="make_stockpile"')
    ms_seg = main_src[i:main_src.index('@app.post("/create-stockpile"', i)]
    assert "return await create_stockpile(" in ms_seg


def test_post_endpoints_use_typed_pydantic_models_and_fog_dig_helpers():
    """Verify POST endpoints expose typed Pydantic schemas, fog docstrings reflect
    the proven fog designation workflow, and send_dig_command supports box/boxes/stair_shaft.
    """
    main_src = pathlib.Path(__file__).resolve().parents[1].joinpath("main.py").read_text()
    for model_name in (
        "ListUnitsRequest", "RunCommandRequest", "PassKeyboardEventRequest",
        "ApplyBlueprintRequest", "SetPauseStateRequest", "CutTreesRequest",
        "DigBox", "StairShaftSpec", "SendDigCommandRequest",
        "PlaceBuildingRequest", "RemoveBuildingRequest", "CreateStockpileRequest",
        "ConfigureStockpileRequest", "CreateZoneRequest", "WorkOrderRequest", "ConfigureFarmplotRequest", "AssignZoneOwnerRequest",
        "AssignPastureRequest", "ManageNoblesRequest", "GatherAndItemsRequest",
        "TradeDepotRequest",
    ):
        assert f"class {model_name}(BaseModel):" in main_src, f"missing Pydantic schema {model_name}"

    dig_idx = main_src.index('operation_id="send_dig_command"')
    dig_seg = main_src[dig_idx:main_src.index('@app.post("/get-block-list"', dig_idx)]
    assert "FOG WORKFLOW" in dig_seg and "WORKS and is the intended workflow" in dig_seg
    assert "unexplored tiles are rejected SILENTLY" not in dig_seg
    assert '"mode": "stair_shaft"' in dig_seg
    assert '"DOWN_STAIR_DIG"' in dig_seg and '"UP_DOWN_STAIR_DIG"' in dig_seg and '"UP_STAIR_DIG"' in dig_seg
    assert "_expand_dig_box(" in dig_seg


def test_server_side_lua_module_bridge_and_json_dispatch():
    """Verify deployables/scripts/dfhack_mcp.lua and luaops.call_module:
    1. dfhack_mcp.lua exists in deployables/scripts/ and defines the JSON dispatcher + OPS.
    2. call_module auto-bootstraps _G.__dfhack_mcp once, sends JSON arguments, and parses
       MCP_JSON|{...} replies into dicts (or raises LuaOpError on ok=false).
    """
    import asyncio
    import json

    assert LO.LUA_MODULE_PATH.is_file(), f"missing {LO.LUA_MODULE_PATH}"
    lua_src = LO.LUA_MODULE_PATH.read_text(encoding="utf-8")
    for required in ("--@ module = true", "M.VERSION", "function M.dispatch(",
                     "function OPS.game_paths(", "function OPS.observe_status(",
                     "function OPS.building_status(", "function OPS.configure_farmplot(",
                     "function OPS.assign_zone_owner(", "function OPS.assign_pasture(",
                     "function OPS.manage_nobles(", "function OPS.manage_work_orders(",
                     "function OPS.alerts_and_wellbeing(", "function OPS.gather_and_items(",
                     "function OPS.trade_depot(",
                     "MCP_JSON|"):
        assert required in lua_src, f"dfhack_mcp.lua missing {required}"

    calls = []

    async def fake_lua(expr, settle=0.0):
        calls.append(expr)
        if "load(" in expr and "dfhack_mcp.lua" in expr:
            return 'MCP_JSON|{"ok":true,"result":{"bootstrapped":true,"version":"2026.11.22-v1"}}'
        if '"game_paths"' in expr:
            return 'MCP_JSON|{"ok":true,"op":"game_paths","result":{"df_path":"/df","save_path":"/df/data/save/region1","script_deployed":true}}'
        if '"building_status"' in expr:
            return 'MCP_JSON|{"ok":true,"op":"building_status","result":{"count":1,"only_incomplete":true,"buildings":[{"id":30,"name":"Well","complete":false,"missing_item_filters":[{"item_type":"TRAPPARTS","quantity_needed":1}]}]}}'
        if '"configure_farmplot"' in expr:
            if "NO_SUCH_PLANT" in expr:
                return 'MCP_JSON|{"ok":false,"op":"configure_farmplot","error":"no plant raw matching \'NO_SUCH_PLANT\'"}'
            return 'MCP_JSON|{"ok":true,"op":"configure_farmplot","result":{"building_id":19,"crop_set":"MUSHROOM_HELMET_PLUMP","seasons_updated":["spring","summer","autumn","winter"]}}'
        if '"assign_zone_owner"' in expr:
            return 'MCP_JSON|{"ok":true,"op":"assign_zone_owner","result":{"building_id":7,"zone_type":"Office","owner_id":6488,"owner_name":"Urist McManager"}}'
        if '"assign_pasture"' in expr:
            return 'MCP_JSON|{"ok":true,"op":"assign_pasture","result":{"building_id":8,"newly_assigned_count":6,"total_assigned":6,"unassigned_grazers_remaining":0}}'
        if '"manage_nobles"' in expr:
            return 'MCP_JSON|{"ok":true,"op":"manage_nobles","result":{"action":"auto_assign","bookkeeper_settings":4,"changes":[{"position":"MANAGER","unit_id":6488}]}}'
        if '"manage_work_orders"' in expr:
            return 'MCP_JSON|{"ok":true,"op":"manage_work_orders","result":{"action":"list","count":1,"has_manager":true,"orders":[{"order_id":0,"job":"MakeBed","amount_left":5,"amount_total":5}]}}'
        if '"alerts_and_wellbeing"' in expr:
            return 'MCP_JSON|{"ok":true,"op":"alerts_and_wellbeing","result":{"alerts_count":1,"alerts":[{"kind":"unpastured_grazers","severity":"warning"}],"wellbeing":{"citizens":7,"thirsty":0,"hungry":0}}}'
        if '"gather_and_items"' in expr:
            return 'MCP_JSON|{"ok":true,"op":"gather_and_items","result":{"action":"protect_seeds","ban_seeds_output":"banned","ban_brew_output":"banned"}}'
        if '"trade_depot"' in expr:
            return 'MCP_JSON|{"ok":true,"op":"trade_depot","result":{"action":"status","building_id":1,"complete":true,"trader_requested":false,"goods_at_depot_count":0,"pending_haul_count":0,"tradeable_candidates_total":12}}'
        raise AssertionError(f"unexpected lua call: {expr[:120]}")

    real_lua = LO._lua
    LO._lua = fake_lua
    LO._module_gen = -1
    LO._module_sha = ""
    try:
        gp = asyncio.run(LO.game_paths())
        assert gp["df_path"] == "/df" and gp["script_deployed"] is True
        bs = asyncio.run(LO.building_status(building_id=30))
        assert bs["count"] == 1 and bs["buildings"][0]["name"] == "Well"
        fp = asyncio.run(LO.configure_farmplot(building_id=19, crop="MUSHROOM_HELMET_PLUMP"))
        assert fp["crop_set"] == "MUSHROOM_HELMET_PLUMP"
        zo = asyncio.run(LO.assign_zone_owner(building_id=7, unit_id=6488))
        assert zo["owner_id"] == 6488 and zo["zone_type"] == "Office"
        pa = asyncio.run(LO.assign_pasture(building_id=8, all_grazers=True))
        assert pa["newly_assigned_count"] == 6 and pa["unassigned_grazers_remaining"] == 0
        nb = asyncio.run(LO.manage_nobles(action="auto_assign"))
        assert nb["bookkeeper_settings"] == 4 and nb["changes"][0]["position"] == "MANAGER"
        wo = asyncio.run(LO.manage_work_orders(action="list"))
        assert wo["count"] == 1 and wo["has_manager"] is True
        al = asyncio.run(LO.alerts_and_wellbeing())
        assert al["alerts_count"] == 1 and al["wellbeing"]["citizens"] == 7
        gi = asyncio.run(LO.gather_and_items(action="protect_seeds"))
        assert gi["action"] == "protect_seeds"
        td = asyncio.run(LO.trade_depot(action="status"))
        assert td["building_id"] == 1 and td["complete"] is True
        try:
            asyncio.run(LO.configure_farmplot(building_id=19, crop="NO_SUCH_PLANT"))
            raise AssertionError("expected LuaOpError on unknown plant")
        except LO.LuaOpError as e:
            assert "NO_SUCH_PLANT" in str(e)
    finally:
        LO._lua = real_lua


def test_order_set_workshop_interpolates_workshop_id():
    import asyncio
    captured = {}

    async def fake_lua(expr, settle=0.0):
        captured["expr"] = expr
        return "OK before: cat=0 mat=-1 ws=-1 | after: cat=0 mat=-1 ws=12"

    real = LO._lua
    LO._lua = fake_lua
    try:
        res = asyncio.run(LO.order_set_workshop(4, 12))
    finally:
        LO._lua = real
    assert "target.workshop_id = 12" in captured["expr"]
    assert "{int(workshop_id)}" not in captured["expr"]
    assert res["order_id"] == 4


def test_place_building_nominal_controls_dims():
    import asyncio
    captured = []

    async def fake_lua(expr, settle=0.0):
        captured.append(expr)
        return "OK building='Chair' bt=0 type=building_chairst id=99 rect=10..10,10..10"

    real = LO._lua
    LO._lua = fake_lua
    try:
        asyncio.run(LO.place_building(FakeDec(), "Chair", 10, 10, 37, w=1, h=1, nominal=True))
        asyncio.run(LO.place_building(FakeDec(), "Chair", 10, 10, 37, w=1, h=1, nominal=False))
    finally:
        LO._lua = real
    assert "width=" not in captured[0] and "height=" not in captured[0]
    assert "width=1, height=1" in captured[1]


def test_dfhack_mcp_lua_fixes_nobles_professions_and_labors():
    lua_src = LO.LUA_MODULE_PATH.read_text(encoding="utf-8")
    assert "aptaludes" not in lua_src
    assert "current_soul.skills" in lua_src
    assert "collect_known_professions" in lua_src
    assert lua_src.index("local function collect_known_professions()") < lua_src.index('if action == "list" then', lua_src.index("function OPS.unit_profession"))
    assert "local want = {}" in lua_src
    assert "unknown labor '" in lua_src


def test_workshop_job_and_noble_candidates_in_lua_module():
    import asyncio
    lua_src = LO.LUA_MODULE_PATH.read_text(encoding="utf-8")
    for req in (
        "function OPS.workshop_job(", "ConstructThrone", "MakeBarrel", "ITEM_TOOL_LARGE_POT",
        "dfhack.job.createLinked", "dfhack.job.assignToWorkshop",
        'action == "candidates"', "reassign_unskilled",
    ):
        assert req in lua_src, f"dfhack_mcp.lua missing {req}"

    async def fake_lua(expr, settle=0.0):
        if "load(" in expr and "dfhack_mcp.lua" in expr:
            return 'MCP_JSON|{"ok":true,"result":{"bootstrapped":true,"version":"2026.11.24"}}'
        if '"workshop_job"' in expr:
            return 'MCP_JSON|{"ok":true,"op":"workshop_job","result":{"action":"queue","building_id":5,"task":"chair","job_type":"ConstructThrone","queued_count":1}}'
        if '"manage_nobles"' in expr:
            return 'MCP_JSON|{"ok":true,"op":"manage_nobles","result":{"action":"candidates","candidates":{"MANAGER":[{"unit_id":6494,"skill_score":6}]}}}'
        raise AssertionError(f"unexpected lua: {expr[:120]}")

    real = LO._lua
    LO._lua = fake_lua
    LO._module_gen = -1
    LO._module_sha = ""
    try:
        wj = asyncio.run(LO.workshop_job(action="queue", task="chair", count=1))
        assert wj["job_type"] == "ConstructThrone" and wj["queued_count"] == 1
        nb = asyncio.run(LO.manage_nobles(action="candidates", position="MANAGER"))
        assert nb["candidates"]["MANAGER"][0]["unit_id"] == 6494
    finally:
        LO._lua = real


def test_place_building_uses_crafted_furniture_item_by_default():
    import asyncio
    captured = []

    async def fake_lua(expr, settle=0.0):
        captured.append(expr)
        return "OK building='Chair' bt=0 type=building_chairst id=101 rect=10..10,10..10"

    real = LO._lua
    LO._lua = fake_lua
    try:
        asyncio.run(LO.place_building(FakeDec(), "Chair", 10, 10, 37))
        asyncio.run(LO.place_building(FakeDec(), "Chair", 10, 10, 37, material="log"))
    finally:
        LO._lua = real
    assert 'resolve_crafted_item("CHAIR", "chair"' in captured[0]
    assert 'resolve_material("log"' not in captured[0]
    assert 'resolve_material("log"' in captured[1]


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
