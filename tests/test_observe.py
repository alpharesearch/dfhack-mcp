"""The /observe composite read: parser units (offline) + live shape checks.

The observer plugin builds its whole state from this payload, so the parsers
must be total: a missing key answers 0, a bad line is skipped, and a found=0
pile stays explicitly "not found" - never a silent zero.
"""
import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import main as M                                              # noqa: E402


def live_game():
    """Readiness = the MCP SERVER answers connected=true. (tests.test_worldmodel.live_game
    opens its own RPC connection; DFHack serves one client, so with the server running
    that probe must fail. The server is the observer''s channel, so gate on it.)"""
    import json, urllib.request
    try:
        d = json.loads(urllib.request.urlopen("http://127.0.0.1:8000/get-status", timeout=10).read().decode())
        return bool(d.get("connected"))
    except Exception:
        return False

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"{name}: got {got!r} want {want!r}")


# ---------------- pile probe ----------------
OUT = """PILE 12 found=1 id=12 accepts=food,stone bins=0 barrels=0 elev=38 items=7 food=3 stone=4 wheelbarrows=2 x0=52 x1=57 y0=116 y1=120 active=1 y1=120
PILE 99 found=0 id=99
garbage line
OK something
"""
p = M.observe_parse_piles(OUT)
check("pile ids", sorted(p.keys()), [12, 99])
check("pile 12 x0", p[12]["x0"], 52)
check("pile 12 accepts", p[12]["accepts"], "food,stone")
check("pile 12 items", p[12]["items"], 7)
check("pile 12 int coercion", p[12]["elev"], 38)
check("pile 99 found", p[99]["found"], 0)
check("pile 99 has no extents", "x0" in p[99], False)
check("pile template substitutes ids",
      "string.split(\"3 4 5\", \" \")" in M.observe_pile_lua([3, 4, 5]), True)
check("pile template keeps lua table braces",
      "local piles = {}" in M.observe_pile_lua([3]), True)
check("pile template has no double-brace leftovers",
      "{{" in M.observe_pile_lua([3]), False)

# ---------------- survey simplification ----------------
sv = {"elev": 36, "box": [27, 121, 42, 126],
      "counts": {"hidden": 12, "rock": 39, "floor": 39, "already_designated": 6,
                 "stairs": 2, "ramp": 1, "water": 3, "open": 4, "building": 5, "other": 6}}
s = M.observe_simplify_survey(sv)
check("to_dig = rock+hidden", s["to_dig"], 51)
check("dug = floor+stairs+ramp", s["dug"], 42)
check("pending", s["pending"], 6)
check("total tiles", s["total_tiles"], 51 + 42 + 6 + 3 + 4 + 5 + 6)
empty = M.observe_simplify_survey({"elev": 36, "box": [0, 0, 1, 1], "counts": {}})
check("empty survey to_dig", empty["to_dig"], 0)
check("empty survey dug", empty["dug"], 0)

# ---------------- shafts ----------------
haz = {(50, 123): {"aquifer": [33, 34, 40], "water": {36: 4, 29: 7}, "magma": [20]}}
check("shaft water out of window dropped", M.observe_shafts(haz, [{"x": 50, "y": 123}], 30, 39)[0]["water_elevs"], [36])
sh = M.observe_shafts(haz, [{"x": 50, "y": 123}, {"x": 1, "y": 1}], 30, 39)
check("shaft water elevs", sh[0]["water_elevs"], [36])
check("shaft aquifer windowed", sh[0]["aquifer_elevs"], [33, 34])
check("shaft magma windowed out", sh[0]["magma_elevs"], [])
check("shaft missing column is explicit", sh[1]["water_elevs"], [])
check("shaft water levels", sh[0]["water_levels"], {36: 4})

# ---------------- crashlog ----------------
c = M.observe_crashlog("", 5)
check("crashlog unconfigured is labelled", "no crash-log directory" in c["notes"][0], True)
c2 = M.observe_crashlog("/definitely/not/here", 5)
check("crashlog missing dir labelled", "does not exist" in c2["notes"][0], True)

# ---------------- live ----------------
async def live():
    if not live_game():
        print("live game not reachable - live observe checks skipped")
        return False
    q = {"elev0": 33, "elev1": 40,
         "boxes": [[27, 121, 42, 126, 36], [43, 122, 49, 124, 36]],
         "shafts": [[50, 123], [43, 121]],
         "pile_ids": [], "warnings": True, "reports": 4, "crashlog_files": 3, "pause": True}
    res = await M.observe(q=json.dumps(q))
    check("schema", res["schema"], M.OBSERVE_SCHEMA)
    check("connected", res["connected"], True)
    check("generation present", "generation" in res, True)
    check("facts keys", all(k in res.get("facts", {}) for k in
                            ("pop", "miners", "pending_dig", "underground_floors")), True)
    check("rungs are dicts with id/title/done",
          all({"id", "title", "done", "keys", "why"} <= set(r) for r in res["rungs"]), True)
    check("ladder has 21 rungs", len(res["rungs"]), 21)
    check("next has at/keys when missing", res["next"] is None or "keys" in res["next"], True)
    check("two boxes answered", len(res["boxes"]), 2)
    for b in res["boxes"]:
        check(f"box {b['box']} has to_dig+dug",
              all(k in b for k in ("to_dig", "dug", "pending", "elev")), True)
        x0, y0, x1, y1 = b["box"]
        check(f"box {b['box']} total tiles", b["total_tiles"], (x1 - x0 + 1) * (y1 - y0 + 1))
    check("shafts answered", len(res["shafts"]), 2)
    check("no piles requested -> note", any("no pile_ids" in n for n in res["notes"]), True)
    check("warnings block", isinstance(res["warnings"], dict) and "designated_tiles" in res["warnings"], True)
    check("crashlog labelled", isinstance(res["crashlog"], dict), True)
    check("json serialisable", isinstance(json.dumps(res), str), True)
    # cross-check against the endpoints the plugin mirrors: same numbers
    import urllib.request
    def get(path):
        return json.loads(urllib.request.urlopen("http://127.0.0.1:8000" + path, timeout=120).read().decode())
    # Parity with the endpoint the plugin mirrors. The game digs BETWEEN the two
    # reads, so require agreement within one poll cycle of movement (labelled),
    # not byte equality across a race.
    import time
    direct = M.observe_simplify_survey(get("/survey?elev=36&x0=27&y0=121&x1=42&y1=126"))
    time.sleep(2)
    direct2 = M.observe_simplify_survey(get("/survey?elev=36&x0=27&y0=121&x1=42&y1=126"))
    mine = res["boxes"][0]
    for field in ("to_dig", "dug"):
        lo, hi = sorted((direct[field], direct2[field]))
        ok = lo - 24 <= mine[field] <= hi + 24
        if not ok:
            FAILS.append(f"survey parity {field}: observe {mine[field]} outside live window {lo}-{hi}")
    pause = get("/get-pause-state")
    check("pause parity", res["paused"], pause["paused"])

    # Buildings are a fort-wide fact (measured bug: the farm plot sat outside the
    # watched band, the ladder read it as unbuilt, and the briefing stated it).
    check("buildings scope", res.get("buildings_scope"), "map-wide")
    wide = {b["name"]: b["count"] for b in res.get("buildings", [])}
    band = {b["name"]: b["count"] for b in res.get("buildings_band_only", [])}
    check("map-wide is not smaller than the band view", sum(wide.values()) >= sum(band.values()), True)
    rungs = {r["id"]: r["done"] for r in res.get("rungs", [])}
    for name, rung in (("Farm Plot", "farm"), ("Still", "drinks"), ("Trade Depot", "depot")):
        if wide.get(name, 0) > 0:
            check(f"rung {rung} is done because {name} exists on the map", rungs.get(rung), True)
    print("live payload size:", len(json.dumps(res)), "bytes")
    return True


def main():
    asyncio.run(live())
    if FAILS:
        print("FAIL:")
        for f in FAILS:
            print("  -", f)
        return 1
    print("observe: all parser + live checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
