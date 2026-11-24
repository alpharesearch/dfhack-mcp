"""Non-destructive end-to-end live MCP protocol test against running DF + DFHack.

Speaks Streamable HTTP MCP JSON-RPC (`POST /mcp`: `initialize` -> `tools/list` ->
`tools/call`) via `httpx.ASGITransport(app=main.app)`.
All calls are strictly read-only or `dry_run=True`, and `fort_plan` uses an isolated
temporary `FortPlanStore` so the live save and `~/.dfhack_mcp/fortplan.db` are untouched.
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fortplan as FP
import main as M


def _parse_mcp_response(resp: httpx.Response) -> dict:
    txt = resp.text.strip()
    for line in txt.splitlines():
        if line.startswith("data: "):
            txt = line[6:].strip()
            break
    return json.loads(txt)


async def mcp_handshake(client: httpx.AsyncClient) -> dict[str, str]:
    base_headers = {"Accept": "application/json, text/event-stream"}
    r = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "live-mcp-test", "version": "1.0"},
            },
        },
        headers=base_headers,
    )
    assert r.status_code == 200, f"initialize failed: {r.status_code} {r.text}"
    hdrs = dict(base_headers)
    sid = r.headers.get("mcp-session-id")
    if sid:
        hdrs["mcp-session-id"] = sid
    await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        headers=hdrs,
    )
    return hdrs


async def mcp_tools_list(client: httpx.AsyncClient, hdrs: dict[str, str], req_id: int = 2) -> list[dict]:
    r = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": req_id, "method": "tools/list", "params": {}},
        headers=hdrs,
    )
    assert r.status_code == 200, f"tools/list failed: {r.status_code} {r.text}"
    data = _parse_mcp_response(r)
    assert "error" not in data, f"tools/list error: {data}"
    return data["result"]["tools"]


async def mcp_call(
    client: httpx.AsyncClient,
    hdrs: dict[str, str],
    req_id: int,
    name: str,
    arguments: dict,
) -> tuple[dict, float]:
    t0 = time.perf_counter()
    r = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=hdrs,
    )
    dt = time.perf_counter() - t0
    assert r.status_code == 200, f"{name} HTTP {r.status_code}: {r.text}"
    data = _parse_mcp_response(r)
    assert "error" not in data, f"{name} JSON-RPC error: {data}"
    res = data["result"]
    assert not res.get("isError"), f"{name} isError=True: {res}"
    content = res.get("content", [])
    assert content and content[0].get("text"), f"{name} empty content: {res}"
    payload = json.loads(content[0]["text"])
    return payload, dt


class TestLiveMCP(unittest.TestCase):
    def test_live_mcp_end_to_end(self):
        async def run():
            orig_store = M.plan_store
            with tempfile.TemporaryDirectory() as tmp:
                M.plan_store = FP.FortPlanStore(db_path=os.path.join(tmp, "live_test_fp.db"))
                try:
                    async with M.app.router.lifespan_context(M.app):
                        async with httpx.AsyncClient(
                            transport=httpx.ASGITransport(app=M.app),
                            base_url="http://test",
                            timeout=30.0,
                        ) as client:
                            hdrs = await mcp_handshake(client)
                            tools = await mcp_tools_list(client, hdrs, req_id=2)
                            names = {t["name"] for t in tools}

                            self.assertEqual(len(tools), 32, f"Expected 32 MCP tools, got {len(tools)}: {sorted(names)}")
                            for required in (
                                "look", "fort_progress", "alerts_and_wellbeing",
                                "fort_strata", "fort_plan", "trade_depot",
                                "workshop_job", "manage_nobles", "dfhack_automation",
                                "send_dig_command", "place_building", "run_command",
                            ):
                                self.assertIn(required, names)
                            for excluded in (
                                "game_lua", "overburden", "get_block_list",
                                "buildings_live", "work_order_link",
                            ):
                                self.assertNotIn(excluded, names)

                            rid = 10
                            def next_id() -> int:
                                nonlocal rid
                                rid += 1
                                return rid

                            # 2. Core perception & map tools
                            look_res, dt_look = await mcp_call(client, hdrs, next_id(), "look", {})
                            self.assertTrue(look_res, "look returned empty")

                            prog_res, dt_prog = await mcp_call(client, hdrs, next_id(), "fort_progress", {})
                            self.assertTrue(prog_res, "fort_progress returned empty")

                            alerts_res, dt_alerts = await mcp_call(client, hdrs, next_id(), "alerts_and_wellbeing", {})
                            self.assertTrue(alerts_res.get("success"), f"alerts failed: {alerts_res}")

                            strata_res, dt_strata = await mcp_call(client, hdrs, next_id(), "fort_strata", {"radius": 10})
                            self.assertGreater(strata_res.get("blocks_scanned", 0), 0)
                            self.assertTrue(strata_res.get("layers"))
                            recs = strata_res.get("recommendations", {})
                            center = strata_res["center"]
                            cx, cy, ce = int(center["x"]), int(center["y"]), int(center["elev"])

                            wmap_res, dt_wmap = await mcp_call(
                                client, hdrs, next_id(), "world_map",
                                {"e": ce, "x0": cx - 6, "x1": cx + 6, "y0": cy - 6, "y1": cy + 6},
                            )
                            self.assertTrue(wmap_res, "world_map empty")

                            surv_res, dt_surv = await mcp_call(
                                client, hdrs, next_id(), "survey",
                                {"elev": ce, "x0": cx - 3, "y0": cy - 3, "x1": cx + 3, "y1": cy + 3},
                            )
                            self.assertIn("counts", surv_res)

                            # 3. Underground CAD planner (fort_plan) end-to-end live (dry_run=True)
                            farm_e = recs.get("farm_elev") if recs.get("farm_elev") is not None else ce - 1
                            shop_e = recs.get("workshop_elev") if recs.get("workshop_elev") is not None else ce - 2
                            bed_e = recs.get("bedroom_elev") if recs.get("bedroom_elev") is not None else ce - 2
                            e_top = max(int(farm_e), int(shop_e), int(bed_e), ce)
                            e_bot = min(int(farm_e), int(shop_e), int(bed_e), ce - 2)

                            hub_res, _ = await mcp_call(
                                client, hdrs, next_id(), "fort_plan",
                                {"action": "set_hub", "x": cx, "y": cy, "elev_top": e_top, "elev_bottom": e_bot},
                            )
                            self.assertTrue(hub_res.get("success"), hub_res)

                            fd_res, _ = await mcp_call(
                                client, hdrs, next_id(), "fort_plan",
                                {"action": "add_template", "template": "food_district", "direction": "E", "elev": int(farm_e)},
                            )
                            self.assertTrue(fd_res.get("success"), fd_res)

                            ws_dir = "W" if int(shop_e) == int(farm_e) else "E"
                            ws_res, _ = await mcp_call(
                                client, hdrs, next_id(), "fort_plan",
                                {"action": "add_template", "template": "workshop_wing", "direction": ws_dir, "elev": int(shop_e)},
                            )
                            self.assertTrue(ws_res.get("success"), ws_res)

                            bd_dir = "S" if int(bed_e) in (int(farm_e), int(shop_e)) else "E"
                            bd_res, _ = await mcp_call(
                                client, hdrs, next_id(), "fort_plan",
                                {"action": "add_template", "template": "bedroom_wing", "direction": bd_dir, "elev": int(bed_e)},
                            )
                            self.assertTrue(bd_res.get("success"), bd_res)

                            view_res, _ = await mcp_call(
                                client, hdrs, next_id(), "fort_plan",
                                {"action": "view", "elev": int(shop_e)},
                            )
                            self.assertTrue(view_res.get("success"), view_res)
                            ascii_view = view_res["views"][0]["ascii"]
                            self.assertIn("X", ascii_view)
                            self.assertIn("H", ascii_view)
                            self.assertIn(".", ascii_view)

                            val_res, dt_val = await mcp_call(
                                client, hdrs, next_id(), "fort_plan",
                                {"action": "validate"},
                            )
                            self.assertTrue(val_res.get("success"), val_res)
                            self.assertTrue(val_res.get("rooms"))

                            dry_res, dt_dry = await mcp_call(
                                client, hdrs, next_id(), "fort_plan",
                                {"action": "commit", "phase": "auto", "dry_run": True},
                            )
                            self.assertTrue(dry_res.get("success"), dry_res)
                            self.assertTrue(dry_res.get("dry_run"))
                            self.assertEqual(dry_res.get("total_designated"), 0)
                            self.assertTrue(dry_res.get("batches"))

                            clr_res, _ = await mcp_call(
                                client, hdrs, next_id(), "fort_plan",
                                {"action": "clear"},
                            )
                            self.assertTrue(clr_res.get("success"), clr_res)

                            # 4. Economy, nobles, workshops, trade, automation & allowlist
                            wj_list, dt_wj = await mcp_call(client, hdrs, next_id(), "workshop_job", {"action": "list"})
                            self.assertTrue(wj_list.get("success"), wj_list)
                            wj_stock, _ = await mcp_call(client, hdrs, next_id(), "workshop_job", {"action": "stock"})
                            self.assertTrue(wj_stock.get("success"), wj_stock)

                            nob_list, dt_nob = await mcp_call(client, hdrs, next_id(), "manage_nobles", {"action": "list"})
                            self.assertTrue(nob_list.get("success"), nob_list)
                            nob_mgr, _ = await mcp_call(client, hdrs, next_id(), "manage_nobles", {"action": "candidates", "position": "MANAGER"})
                            self.assertTrue(nob_mgr.get("success"), nob_mgr)
                            nob_brk, _ = await mcp_call(client, hdrs, next_id(), "manage_nobles", {"action": "candidates", "position": "BROKER"})
                            self.assertTrue(nob_brk.get("success"), nob_brk)

                            wo_res, _ = await mcp_call(client, hdrs, next_id(), "work_order", {"action": "list"})
                            self.assertTrue(wo_res.get("success"), wo_res)
                            self.assertIn("has_manager", wo_res)

                            wd_res, _ = await mcp_call(client, hdrs, next_id(), "work_details", {"action": "list"})
                            self.assertTrue(wd_res.get("success"), wd_res)

                            td_res, dt_td = await mcp_call(client, hdrs, next_id(), "trade_depot", {"action": "status"})
                            self.assertTrue(
                                td_res.get("success") or "no Trade Depot found" in str(td_res.get("error", "")),
                                td_res,
                            )

                            auto_res, dt_auto = await mcp_call(client, hdrs, next_id(), "dfhack_automation", {"action": "status"})
                            self.assertTrue(auto_res.get("success"), auto_res)

                            bs_res, _ = await mcp_call(client, hdrs, next_id(), "building_status", {"only_incomplete": False, "limit": 10})
                            self.assertTrue(bs_res.get("success"), bs_res)
                            self.assertIn("diagnostics", bs_res)

                            sp_res, _ = await mcp_call(client, hdrs, next_id(), "list_stockpiles", {})
                            self.assertTrue(sp_res.get("success"), sp_res)

                            zn_res, _ = await mcp_call(client, hdrs, next_id(), "list_zones", {})
                            self.assertTrue(zn_res.get("success"), zn_res)

                            tr_res, _ = await mcp_call(client, hdrs, next_id(), "find_trees", {"elev0": ce - 1, "elev1": ce + 2})
                            self.assertTrue(tr_res.get("success"), tr_res)

                            wt_res, _ = await mcp_call(client, hdrs, next_id(), "find_water", {"elev0": ce - 4, "elev1": ce + 1})
                            self.assertTrue(wt_res.get("success"), wt_res)

                            ver_res, _ = await mcp_call(client, hdrs, next_id(), "run_command", {"command": "version"})
                            self.assertTrue(ver_res.get("success"), ver_res)

                            deny_res, _ = await mcp_call(client, hdrs, next_id(), "run_command", {"command": "lua", "arguments": ["print(1)"]})
                            self.assertTrue(deny_res.get("blocked") or not deny_res.get("success"), deny_res)

                            print("\n=== LIVE MCP TEST SUMMARY ===")
                            print(f"MCP tools/list: {len(tools)} tools")
                            print(f"look ({dt_look:.2f}s) | fort_progress ({dt_prog:.2f}s) | alerts ({dt_alerts:.2f}s)")
                            print(f"fort_strata ({dt_strata:.2f}s): center=({cx},{cy},{ce}), blocks={strata_res['blocks_scanned']}, recs={recs}")
                            print(f"world_map ({dt_wmap:.2f}s) | survey ({dt_surv:.2f}s)")
                            print(f"fort_plan: validate ({dt_val:.2f}s, {len(val_res['rooms'])} rooms), commit dry_run ({dt_dry:.2f}s, {len(dry_res['batches'])} batches)")
                            print(f"workshop_job ({dt_wj:.2f}s): workshops={wj_list.get('count', len(wj_list.get('workshops', [])))}, stock_keys={list((wj_stock.get('stock') or {}).keys())[:6]}")
                            mgr_cands = (nob_mgr.get("candidates") or {}).get("MANAGER") or [{}]
                            brk_cands = (nob_brk.get("candidates") or {}).get("BROKER") or [{}]
                            print(f"manage_nobles ({dt_nob:.2f}s): top_mgr={mgr_cands[0].get('name')}, top_brk={brk_cands[0].get('name')}")
                            print(f"trade_depot ({dt_td:.2f}s): success={td_res.get('success')}, note={td_res.get('error') or len(td_res.get('depots', []))}")
                            print(f"dfhack_automation ({dt_auto:.2f}s): plugins={len(auto_res.get('plugins', {}))}")
                            print(f"stockpiles={sp_res.get('count')}, zones={zn_res.get('count')}, trees={tr_res.get('trunk_tiles_found')}, water={wt_res.get('water_tiles_found')}")
                finally:
                    M.plan_store = orig_store

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main(verbosity=2)
