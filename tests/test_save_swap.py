"""The save-swap detector and the reset it triggers - the "only truth is the DF game" layer.

Measured 2026-11-21: the player loaded an older save of the same fort while the MCP
server kept running. The RPC connection `generation` stayed 1 (a save reload never
drops the socket), so every derived view - the block cache, the stack memo, the fort
band, the delta snapshot - kept serving the PRE-RELOAD world. /survey answered
"96 floors, dug 96" for a box the game reported as 96 hidden walls, and an agent
acting on it was told "the work is intact" by a cache.

These checks run with a stubbed RPC layer: no game needed.
"""
import asyncio
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import dfhack_client_python.dfhack as D                     # noqa: E402
import worldmodel as WM                                      # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append(f"{name}: got {got!r} want {want!r}")


def msg(**fields):
    """A protobuf-shaped stand-in: attribute access only."""
    return types.SimpleNamespace(**fields)


def stub_rpc(state):
    """Point the RPC functions the signature uses at a mutable fake game."""
    async def GetMapInfo():
        return msg(save_name=state["save"], world_name_english=state["world"],
                   block_size_x=12, block_size_y=12, block_size_z=191)

    async def GetWorldInfo():
        return msg(mode=0)                       # MODE_DWARF

    async def GetWorldMap():
        return msg(cur_year=state["year"], cur_year_tick=state["tick"])

    async def ResetMapHashes():
        state["hashes_reset"] = state.get("hashes_reset", 0) + 1
        return msg(Value=True)

    D.GetMapInfo, D.GetWorldInfo, D.GetWorldMap, D.ResetMapHashes = (
        GetMapInfo, GetWorldInfo, GetWorldMap, ResetMapHashes)


async def fresh(state):
    w = WM.WorldModel()
    stub_rpc(state)
    await w.signature(force=True)
    await w.check_reload()                      # baseline the identity, no event
    # Fill the derived views so a clear is observable.
    w.cache._chunks = {0: ["fake block"]}
    w.cache._at = {0: 1.0}
    w.cache._gen = 7
    w._stack_cache = {(30, 43): ({"band": {}}, [])}
    w._band = (160, 172)
    w._prev = {165: {"fake": 1}}
    w._state = {"fake": 1}
    return w


async def test_forward_time_is_not_a_swap():
    state = {"save": "start", "world": "W", "year": 104, "tick": 96910}
    w = await fresh(state)
    state["tick"] = 97999                       # the fort played on
    w._sig_at = 0.0                             # force the probe past the TTL
    out = await w.check_reload()
    check("forward time: no swap", out, None)
    check("forward time: caches intact", w.cache._gen, 7)
    check("forward time: reload count", w.reloads, 0)


async def test_save_name_change_clears_everything():
    state = {"save": "start", "world": "W", "year": 104, "tick": 96910}
    w = await fresh(state)
    state["save"] = "fort2"
    w._sig_at = 0.0
    out = await w.check_reload()
    check("identity swap detected", bool(out), True)
    check("identity swap names the cause", "identity changed" in out["why"], True)
    check("caches dropped: blocks", w.cache._chunks, {})
    check("caches dropped: generation", w.cache._gen, -1)
    check("caches dropped: stack memo", w._stack_cache, {})
    check("caches dropped: fort band", w._band, None)
    check("caches dropped: delta snapshot", w._prev, {})
    check("caches dropped: state", w._state, {})
    check("DFHack map hashes reset", w._reload["cleared"].startswith("cache + DFHack"), True)
    check("reload counted", w.reloads, 1)
    first = w.take_reload()
    check("swap event reported once", bool(first), True)
    check("swap event consumed", w.take_reload(), None)


async def test_same_save_older_clock_is_a_swap():
    """The measured case: both saves are named "start" on the same map."""
    state = {"save": "start", "world": "W", "year": 104, "tick": 96910}
    w = await fresh(state)
    state["year"], state["tick"] = 100, 15
    w._sig_at = 0.0
    out = await w.check_reload()
    check("clock regression detected", bool(out), True)
    check("clock regression names the clock", "backwards" in out["why"], True)
    check("clock regression cleared caches", w._stack_cache, {})
    # A forward jump in the same save stays silent.
    state["year"], state["tick"] = 100, 200
    w._sig_at = 0.0
    check("forward clock silent", await w.check_reload(), None)


async def test_signature_shape_and_ttl():
    state = {"save": "start", "world": "W", "year": 104, "tick": 96910}
    w = await fresh(state)
    sig = await w.signature()
    check("signature carries the save", sig["save"], "start")
    check("signature carries the extent", sig["tiles"], "192x192x191")
    check("signature carries the clock", (sig["year"], sig["tick"]), (104, 96910))
    calls = {"n": 0}
    real = D.GetMapInfo

    async def counting():
        calls["n"] += 1
        return await real()
    D.GetMapInfo = counting
    for _ in range(5):                          # five reads inside the TTL
        await w.signature()
    check("TTL: one probe for five reads", calls["n"], 0)
    w._sig_at = 0.0
    await w.signature()
    check("TTL expiry re-probes", calls["n"], 1)
    D.GetMapInfo = real


async def test_survey_lets_the_block_outrank_the_memo():
    """The memo said 96 dug floors; the block said unexplored. The game wins."""
    state = {"save": "start", "world": "W", "year": 100, "tick": 15}
    w = await fresh(state)

    # The memo (the stale thing from the previous world) claims dug floors.
    band = {(x, y): {36: "."} for x in range(27, 43) for y in range(121, 127)}
    w._stack = lambda e0, e1, gen=None: asyncio.sleep(0, result=(band, []))

    def block_for(z):
        # A 16x16 block per tile position, hidden=1 everywhere.
        blocks = []
        for bx in range(1, 3):
            for by in range(7, 8):
                blocks.append(msg(
                    map_x=bx * 16, map_y=by * 16, map_z=z,
                    tiles=[0] * 256, water=[0] * 256, magma=[0] * 256,
                    hidden=[1] * 256, tile_dig_designation=[0] * 256,
                    buildings=[], items=[]))
        return blocks

    async def fake_blocks(z0, z1, gen, force=False):
        return block_for(z0)
    w.cache.blocks = fake_blocks
    w.dec = types.SimpleNamespace(tile={}, item={}, material={}, building={}, profession={})

    out = await w.survey(36, 27, 121, 42, 126)
    check("survey: fog outranks the memo glyph", out["counts"].get("hidden"), 96)
    check("survey: nothing counted dug", out["dug"], 0)
    check("survey: to_dig counts the fog", out["to_dig"], 96)
    check("survey: the disagreement is labelled",
          any("hidden in the block data" in n for n in out["notes"]), True)


async def main():
    for fn in (test_forward_time_is_not_a_swap, test_save_name_change_clears_everything,
               test_same_save_older_clock_is_a_swap, test_signature_shape_and_ttl,
               test_survey_lets_the_block_outrank_the_memo):
        await fn()
    if FAILS:
        print("FAIL:")
        for f in FAILS:
            print("  -", f)
        return 1
    print("save-swap: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
