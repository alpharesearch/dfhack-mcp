"""Offline unit tests for fortplan.py and main.py fort_strata / fort_plan endpoints."""

import asyncio
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fortplan as FP
import main as M
import worldmodel as WM


def _make_fake_block(bx: int, by: int, bz: int, tt: int, des: int = 0, occ: int = 0):
    return SimpleNamespace(
        map_x=bx,
        map_y=by,
        map_z=bz,
        tile_types=[tt] * 256,
        designation=[des] * 256,
        occupancy=[occ] * 256,
    )


class _FakeDecoders:
    def shape(self, tt: int) -> str:
        return {
            1: "OPEN",
            2: "FLOOR",
            3: "WALL",  # soil wall
            4: "WALL",  # stone wall
        }.get(tt, "WALL")


class _FakeCache:
    def __init__(self, blocks, tx=48, ty=48, tz=160):
        self._blocks = blocks
        self.tile_x = tx
        self.tile_y = ty
        self.z_count = tz

    async def extent(self):
        return (self.tile_x, self.tile_y, self.z_count)

    async def blocks(self, z0, z1, gen):
        return [b for b in self._blocks if z0 <= int(b.map_z) < z1]


class _FakeWorld:
    def __init__(self, blocks, hazards=None, box_states=None):
        self.cache = _FakeCache(blocks)
        self._hazards = hazards or {}
        self._box_states = box_states or {}

    async def _gen(self):
        return 1

    async def _dec(self, gen):
        return _FakeDecoders()

    async def fort_band(self):
        return (WM.GAME_Z_OFFSET - 2, WM.GAME_Z_OFFSET + 2)

    async def levels(self, e0, e1):
        return []

    async def signature(self):
        return {"key": "test_save_key"}

    async def hazard_map(self, e0, e1):
        return self._hazards

    async def box_state(self, elev, x0, y0, x1, y1):
        key = (int(elev), int(x0), int(y0), int(x1), int(y1))
        if key in self._box_states:
            return self._box_states[key]
        return {"dug": 0, "to_dig": 0, "total_tiles": (x1 - x0 + 1) * (y1 - y0 + 1)}


class TestFortPlanStore(unittest.TestCase):
    def test_store_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "fp.db")
            store = FP.FortPlanStore(db_path=db)
            empty = store.load("save_a")
            self.assertIsNone(empty["hub"])
            self.assertEqual(empty["rooms"], [])

            FP.ensure_hub(empty, 24, 24, elev_top=0, elev_bottom=-4)
            store.save("save_a", empty)
            loaded = store.load("save_a")
            self.assertEqual(loaded["hub"]["x"], 24)
            self.assertEqual(loaded["hub"]["elev_bottom"], -4)
            self.assertIn("save_a", store.list_keys())

            store.clear("save_a")
            self.assertEqual(store.list_keys(), [])


class TestGeometryAndTemplates(unittest.TestCase):
    def test_rect_overlaps_and_attached_room(self):
        self.assertTrue(FP.rect_overlaps((0, 0, 4, 4), (4, 4, 8, 8), pad=0))
        self.assertFalse(FP.rect_overlaps((0, 0, 4, 4), (6, 0, 8, 4), pad=1))
        self.assertTrue(FP.rect_overlaps((0, 0, 4, 4), (5, 0, 8, 4), pad=1))

        plan = {"hub": None, "rooms": []}
        hub = FP.ensure_hub(plan, 50, 50, elev_top=0, elev_bottom=-3, radius=1)
        for d in FP.VALID_DIRECTIONS:
            room, corr = FP.compute_attached_room(hub, d, width=5, height=5, corridor_len=2, elev=-1)
            room["name"] = f"r_{d}"
            room["parent"] = "hub"
            self.assertIsNotNone(corr)
            cols = FP.check_plan_collisions(plan, room, corr, wall_thickness=1)
            self.assertEqual(cols, [], f"Unexpected collision for dir={d}: {cols}")
            room["corridor"] = corr
            plan["rooms"].append(room)

    def test_all_templates_all_rotations_collision_free(self):
        for tmpl in FP.VALID_TEMPLATES:
            for d in FP.VALID_DIRECTIONS:
                plan = {"hub": None, "rooms": []}
                hub = FP.ensure_hub(plan, 50, 50, elev_top=0, elev_bottom=-3, radius=1)
                rooms = FP.build_template_rooms(tmpl, hub, direction=d, elev=-2)
                self.assertGreaterEqual(len(rooms), 2)
                for r in rooms:
                    cols = FP.check_plan_collisions(plan, r, r.get("corridor"), wall_thickness=1)
                    self.assertEqual(cols, [], f"Collision in {tmpl} dir={d} room={r['name']}: {cols}")
                    plan["rooms"].append(r)

    def test_render_plan_view(self):
        plan = {"save_key": "k", "hub": None, "rooms": []}
        hub = FP.ensure_hub(plan, 20, 20, elev_top=0, elev_bottom=-2, radius=1)
        rooms = FP.build_template_rooms("civic_hall", hub, direction="E", elev=-2)
        plan["rooms"].extend(rooms)
        out = FP.render_plan_view(plan, elev=-2)
        self.assertEqual(len(out["views"]), 1)
        ascii_map = out["views"][0]["ascii"]
        self.assertIn("X", ascii_map)
        self.assertIn("H", ascii_map)
        self.assertIn(".", ascii_map)
        self.assertTrue(len(out["views"][0]["legend"]) >= 4)


class TestStrataAndCommit(unittest.TestCase):
    def test_fort_strata_and_validate(self):
        z0 = WM.GAME_Z_OFFSET
        # e=+1 (z0+1): open surface; e=0 (z0): soil wall; e=-1 (z0-1): stone below soil (potential_soil!);
        # e=-2 (z0-2): pure stone; e=-3 (z0-3): aquifer stone
        blocks = [
            _make_fake_block(16, 16, z0 + 1, tt=1),
            _make_fake_block(16, 16, z0, tt=3),
            _make_fake_block(16, 16, z0 - 1, tt=4),
            _make_fake_block(16, 16, z0 - 2, tt=4),
            _make_fake_block(16, 16, z0 - 3, tt=4, des=0x400000),
        ]
        tt_mats = {1: 0, 3: 1, 4: 2}  # tt=3 -> SOIL(1), tt=4 -> stone(2)
        w = _FakeWorld(blocks)

        res = asyncio.run(
            FP.fort_strata(w, x=24, y=24, elev=0, radius=4, above=1, below=3, tiletype_materials=tt_mats)
        )
        self.assertEqual(res["blocks_scanned"], 5)
        by_e = {l["elev"]: l for l in res["layers"]}
        self.assertEqual(by_e[1]["layer_class"], "surface")
        self.assertEqual(by_e[0]["layer_class"], "soil")
        self.assertGreater(by_e[-1]["potential_soil"], 0)
        self.assertEqual(by_e[-2]["layer_class"], "stone")
        self.assertEqual(by_e[-3]["layer_class"], "aquifer")
        self.assertEqual(res["recommendations"]["workshop_elev"], -2)
        self.assertTrue(any("Aquifer" in msg for msg in res["recommendations"]["warnings"]))

        # validate_rooms_terrain
        w_haz = _FakeWorld(blocks, hazards={(20, 20): {"aquifer": [-3], "water": {}, "magma": []}})
        checks = asyncio.run(
            FP.validate_rooms_terrain(
                w_haz,
                [
                    {"name": "bad_room", "elev": -3, "x0": 19, "y0": 19, "x1": 21, "y1": 21, "needs_soil": False},
                    {"name": "good_farm", "elev": -1, "x0": 18, "y0": 18, "x1": 22, "y1": 22, "needs_soil": True},
                ],
                tiletype_materials=tt_mats,
            )
        )
        self.assertFalse(checks[0]["ok"])
        self.assertTrue(checks[1]["ok"])

    def test_commit_plan_phased_and_dry_run(self):
        plan = {"hub": None, "rooms": []}
        hub = FP.ensure_hub(plan, 20, 20, elev_top=0, elev_bottom=-2, radius=1)
        r, c = FP.compute_attached_room(hub, "E", width=3, height=3, corridor_len=2, elev=-2)
        r.update({"name": "shop", "kind": "workshop", "corridor": c, "parent": "hub"})
        plan["rooms"].append(r)

        calls = []

        async def fake_designate(desig, pts):
            calls.append((desig, list(pts)))
            return len(pts)

        async def fake_filter(pts):
            return pts, {"open_space": 0}

        def fake_expand(x0, y0, x1, y1, e0, e1=None, middle_first=True):
            e_hi = e0 if e1 is None else max(e0, e1)
            e_lo = e0 if e1 is None else min(e0, e1)
            return [
                [x, y, e + WM.GAME_Z_OFFSET]
                for e in range(e_hi, e_lo - 1, -1)
                for x in range(x0, x1 + 1)
                for y in range(y0, y1 + 1)
            ]

        # 1) dry_run does not call fake_designate
        w0 = _FakeWorld([])
        dry = asyncio.run(
            FP.commit_plan(w0, plan, fake_designate, fake_filter, fake_expand, phase="auto", dry_run=True)
        )
        self.assertEqual(dry["phases_run"], ["shaft"])
        self.assertEqual(calls, [])

        # 2) auto when shaft dug=0 -> shaft (2x2 shaft = 4 tiles per level, real DFHack enum names)
        res1 = asyncio.run(
            FP.commit_plan(w0, plan, fake_designate, fake_filter, fake_expand, phase="auto", dry_run=False)
        )
        self.assertEqual(res1["phases_run"], ["shaft"])
        self.assertEqual([c[0] for c in calls], ["DOWN_STAIR_DIG", "UP_DOWN_STAIR_DIG", "UP_STAIR_DIG"])
        self.assertEqual([len(c[1]) for c in calls], [4, 4, 4])
        self.assertEqual(calls[0][1][0][2], 0 + WM.GAME_Z_OFFSET)

        # 3) auto when shaft dug=4, corridor dug=0 -> hub_and_corridors (DEFAULT_DIG)
        calls.clear()
        w1 = _FakeWorld([], box_states={(-2, 20, 20, 21, 21): {"dug": 4, "to_dig": 0, "total_tiles": 4}})
        res2 = asyncio.run(
            FP.commit_plan(w1, plan, fake_designate, fake_filter, fake_expand, phase="auto", dry_run=False)
        )
        self.assertEqual(res2["phases_run"], ["hub_and_corridors"])
        self.assertTrue(len(calls) >= 2)
        self.assertTrue(all(c[0] == "DEFAULT_DIG" for c in calls))

        # 4) auto when shaft dug=4 and corridor dug=1 -> rooms (DEFAULT_DIG)
        calls.clear()
        w2 = _FakeWorld(
            [],
            box_states={
                (-2, 20, 20, 21, 21): {"dug": 4, "to_dig": 0, "total_tiles": 4},
                (-2, c["x0"], c["y0"], c["x1"], c["y1"]): {"dug": 1, "to_dig": 0, "total_tiles": 2},
            },
        )
        res3 = asyncio.run(
            FP.commit_plan(w2, plan, fake_designate, fake_filter, fake_expand, phase="auto", dry_run=False)
        )
        self.assertEqual(res3["phases_run"], ["rooms"])
        self.assertEqual(res3["total_designated"], 9)
        self.assertEqual(calls[0][0], "DEFAULT_DIG")

    def test_hub_and_corridors_never_designates_disconnected_child_doorways(self):
        plan = {"hub": None, "rooms": []}
        hub = FP.ensure_hub(plan, 50, 50, elev_top=0, elev_bottom=-2, radius=1)
        plan["rooms"].extend(FP.build_template_rooms("food_district", hub, direction="W", elev=-2))

        calls = []

        async def fake_designate(desig, pts):
            calls.append((desig, list(pts)))
            return len(pts)

        async def fake_filter(pts):
            return pts, {}

        def fake_expand(x0, y0, x1, y1, e0, e1=None, middle_first=True):
            return [[x, y, e0 + WM.GAME_Z_OFFSET] for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]

        w = _FakeWorld([])
        res_hc = asyncio.run(
            FP.commit_plan(w, plan, fake_designate, fake_filter, fake_expand, phase="hub_and_corridors", dry_run=False)
        )
        steps_hc = [b["step"] for b in res_hc["batches"]]
        self.assertEqual(steps_hc, ["hub_landing_e-2", "corridor_food_stockpile"])

        res_rm = asyncio.run(
            FP.commit_plan(w, plan, fake_designate, fake_filter, fake_expand, phase="rooms", dry_run=False)
        )
        steps_rm = [b["step"] for b in res_rm["batches"]]
        self.assertEqual(
            steps_rm,
            [
                "room_food_stockpile",
                "corridor_farm_plot", "room_farm_plot",
                "corridor_brewery", "room_brewery",
                "corridor_kitchen", "room_kitchen",
            ],
        )


class TestMainEndpoints(unittest.TestCase):
    def test_main_fort_plan_flow(self):
        orig_store = M.plan_store
        orig_world = M.world
        try:
            with tempfile.TemporaryDirectory() as tmp:
                M.plan_store = FP.FortPlanStore(db_path=os.path.join(tmp, "m.db"))
                M.world = _FakeWorld([])

                r1 = asyncio.run(
                    M.fort_plan(M.FortPlanRequest(action="set_hub", x=40, y=40, elev_top=0, elev_bottom=-3))
                )
                self.assertTrue(r1["success"])

                r2 = asyncio.run(
                    M.fort_plan(
                        M.FortPlanRequest(
                            action="add_template",
                            template="workshop_wing",
                            direction="E",
                            elev=-2,
                        )
                    )
                )
                self.assertTrue(r2["success"])
                self.assertEqual(len(r2["added"]), 4)

                r3 = asyncio.run(M.fort_plan(M.FortPlanRequest(action="view", elev=-2)))
                self.assertTrue(r3["success"])
                self.assertIn("X", r3["views"][0]["ascii"])

                r4 = asyncio.run(M.fort_plan(M.FortPlanRequest(action="clear")))
                self.assertTrue(r4["success"])
        finally:
            M.plan_store = orig_store
            M.world = orig_world

    def test_quickfort_helpers_and_bootstrap_gate(self):
        self.assertTrue(FP.is_dig_only_blueprint("library/dreamfort.csv", "/industry1"))
        self.assertTrue(FP.is_dig_only_blueprint("library/dreamfort.csv", "/dig_all"))
        self.assertFalse(FP.is_dig_only_blueprint("library/dreamfort.csv", "/industry2"))
        self.assertFalse(FP.is_dig_only_blueprint("library/dreamfort.csv", "/farming2"))

        args = FP.build_quickfort_args("run", "library/dreamfort.csv", label="/industry1", x=57, y=118, elev=35, dry_run=True)
        self.assertEqual(args, ["run", "library/dreamfort.csv", "-n", "/industry1", "-c", f"57,118,{35 + WM.GAME_Z_OFFSET}", "--dry-run"])

        unready = FP.check_quickfort_readiness(
            {"positions": [{"code": "MANAGER", "vacant": True}]},
            {"workshops": []},
            [],
            {"plugins": {"buildingplan": "off"}},
        )
        self.assertFalse(unready["ready_for_meta_and_orders"])
        self.assertGreaterEqual(len(unready["missing_prerequisites"]), 4)

        ready = FP.check_quickfort_readiness(
            {"positions": [{"code": "MANAGER", "vacant": False}]},
            {"workshops": [{"name": "Carpenter's Workshop"}, {"name": "Mason's Workshop"}, {"name": "Still"}]},
            [{"type_name": "Office"}],
            {"plugins": {"buildingplan": "on"}},
        )
        self.assertTrue(ready["ready_for_meta_and_orders"])
        self.assertEqual(ready["missing_prerequisites"], [])

        orig_store = M.plan_store
        orig_world = M.world
        orig_readiness = M._quickfort_readiness
        orig_runcmd = M._run_dfhack_cmd
        try:
            with tempfile.TemporaryDirectory() as tmp:
                M.plan_store = FP.FortPlanStore(db_path=os.path.join(tmp, "qf.db"))
                M.world = _FakeWorld([])

                async def fake_unready():
                    return unready

                async def fake_cmd(cmd, args=None):
                    return {"command": f"{cmd} {' '.join(args or [])}", "ok": True, "output": "ok"}

                M._quickfort_readiness = fake_unready
                M._run_dfhack_cmd = fake_cmd

                st = asyncio.run(M.fort_plan(M.FortPlanRequest(action="qf_status")))
                self.assertTrue(st["success"])
                self.assertFalse(st["ready_for_meta_and_orders"])

                blk_ord = asyncio.run(
                    M.fort_plan(M.FortPlanRequest(action="qf_orders", blueprint="library/dreamfort.csv", label="/farming2"))
                )
                self.assertFalse(blk_ord["success"])
                self.assertTrue(blk_ord["blocked"])

                blk_meta = asyncio.run(
                    M.fort_plan(
                        M.FortPlanRequest(
                            action="qf_run",
                            blueprint="library/dreamfort.csv",
                            label="/industry2",
                            x=57,
                            y=118,
                            elev=35,
                            dry_run=False,
                        )
                    )
                )
                self.assertFalse(blk_meta["success"])
                self.assertTrue(blk_meta["blocked"])

                ok_dry = asyncio.run(
                    M.fort_plan(
                        M.FortPlanRequest(
                            action="qf_run",
                            blueprint="library/dreamfort.csv",
                            label="/industry1",
                            x=57,
                            y=118,
                            elev=33,
                            dry_run=True,
                        )
                    )
                )
                self.assertTrue(ok_dry["success"])
                self.assertTrue(ok_dry["dig_only"])
                self.assertEqual(ok_dry["cursor"]["z"], 33 + WM.GAME_Z_OFFSET)
        finally:
            M.plan_store = orig_store
            M.world = orig_world
            M._quickfort_readiness = orig_readiness
            M._run_dfhack_cmd = orig_runcmd

    def test_find_clean_sites_avoids_walls_buildings_zones_and_reports_pending_dig(self):
        z = WM.GAME_Z_OFFSET - 2
        blk = _make_fake_block(16, 16, z, tt=2)  # tt=2 -> FLOOR
        blk.dig = [0] * 256
        # Mark one tile pending dig and one building footprint
        blk.dig[0] = 1
        blk.buildings = [SimpleNamespace(pos_x_min=18, pos_x_max=20, pos_y_min=18, pos_y_max=20)]

        class _GlyphDec:
            def tile_class(self, tt):
                return SimpleNamespace(glyph="." if tt == 2 else "#")

        w = _FakeWorld([blk])
        async def _dec(gen):
            return _GlyphDec()
        w._dec = _dec

        res = asyncio.run(
            FP.find_clean_sites(
                w,
                x=20,
                y=20,
                elev=-2,
                width=3,
                height=3,
                radius=6,
                zones=[{"elev": -2, "x0": 22, "y0": 16, "x1": 24, "y1": 18}],
            )
        )
        self.assertEqual(res["pending_dig_tiles"], 1)
        self.assertGreater(res["sites_found"], 0)
        for s in res["sites"]:
            # Must not overlap building (18..20, 18..20) or zone (22..24, 16..18) or pending dig (16, 16)
            self.assertFalse(FP.rect_overlaps((s["x"], s["y"], s["x1"], s["y1"]), (18, 18, 20, 20)))
            self.assertFalse(FP.rect_overlaps((s["x"], s["y"], s["x1"], s["y1"]), (22, 16, 24, 18)))
            self.assertFalse(FP.rect_overlaps((s["x"], s["y"], s["x1"], s["y1"]), (16, 16, 16, 16)))

    def test_find_clean_sites_require_soil_and_dig_to_soil(self):
        z = WM.GAME_Z_OFFSET - 2
        # z+1 has SOIL(tt=3) everywhere; z has stone wall(tt=4) everywhere except a 4x4 stone floor(tt=2) patch at x=16..19, y=16..19
        b_above = _make_fake_block(16, 16, z + 1, tt=3)
        b_cur = _make_fake_block(16, 16, z, tt=4)
        for gx in range(16, 20):
            for gy in range(16, 20):
                b_cur.tile_types[(gy - 16) * 16 + (gx - 16)] = 2  # stone floor (not soil)

        class _GlyphDec:
            def tile_class(self, tt):
                return SimpleNamespace(glyph="." if tt in (2, 3) else "#")

        w = _FakeWorld([b_above, b_cur])
        async def _dec(gen):
            return _GlyphDec()
        w._dec = _dec

        res = asyncio.run(
            FP.find_clean_sites(
                w,
                x=22,
                y=22,
                elev=-2,
                width=3,
                height=3,
                radius=6,
                require_soil=True,
                tiletype_materials={2: 2, 3: 1, 4: 2},  # tt=3 is SOIL(1), tt=2,4 are stone(2)
            )
        )
        self.assertTrue(res["require_soil"])
        self.assertEqual(res["sites_found"], 0)  # stone floor at 16..19 is rejected for farm!
        self.assertGreater(len(res["dig_to_soil_sites"]), 0)  # stone wall below soil is returned!


if __name__ == "__main__":
    unittest.main(verbosity=2)
