import asyncio, os, struct, sys, time
os.environ["DFHACK_RPC_HOST"] = "127.0.0.1"
os.environ["DFHACK_RPC_PORT"] = "15000"      # fake server first
import dfhack_client_python.dfhack_remote as R
from dfhack_client_python.dfhack import GetVersion

async def fake_server(state):
    async def handle(r, w):
        try:
            await r.readexactly(12)                       # handshake request
            w.write(b"DFHack!\n" + struct.pack("<i", 1)); await w.drain()
            state["handshakes"] += 1
            await r.readexactly(8)                        # one request header
            w.close()                                     # game dies mid-response
            state["closes"] += 1
        except Exception:
            pass
    srv = await asyncio.start_server(handle, "127.0.0.1", 15000)
    return srv

async def main():
    state = {"handshakes": 0, "closes": 0}
    srv = await fake_server(state)
    print("=== A. game dies mid-response (must raise, not hang) ===")
    t0 = time.time()
    try:
        await asyncio.wait_for(GetVersion(), timeout=8)
        print("  UNEXPECTED: call succeeded")
    except asyncio.TimeoutError:
        print("  FAIL: hung (old behaviour was an infinite read loop)")
    except R.DFHackTransportError as e:
        print(f"  OK: raised in {time.time()-t0:.2f}s -> {type(e).__name__}")
        print(f"      msg: {str(e)[:110]}")
    print(f"  handshakes={state['handshakes']} closes={state['closes']} "
          f"(2 handshakes = retried once after the drop)")
    srv.close(); await srv.wait_closed()

    print("=== B. game not running at all (clean error) ===")
    R.drop_connection()
    os.environ["DFHACK_RPC_PORT"] = "15999"   # nothing listening
    try:
        await GetVersion()
        print("  UNEXPECTED success")
    except R.DFHackTransportError as e:
        print(f"  OK clean error: {str(e)[:130]}")

    print("=== C. real game: lazy connect, drop, auto-reconnect ===")
    os.environ.pop("DFHACK_RPC_PORT"); os.environ.pop("DFHACK_RPC_HOST")
    print("  state before:", R.connection_state())
    gen_before = R.connection_generation()
    v = await GetVersion()
    gen_first = R.connection_generation()
    print("  first call  ->", v.value, "| generation:", gen_first, f"(lazy connect: {gen_before} -> {gen_first})")
    print("  bound method ids:", R.BindMethod.cache_info().currsize)
    R.drop_connection()                       # simulate DF restart
    print("  after drop    -> connected:", R.is_connected(), "bound ids:", R.BindMethod.cache_info().currsize)
    v2 = await GetVersion()
    gen_second = R.connection_generation()
    print("  auto-reconnect->", v2.value, "| generation:", gen_second, f"({gen_first} -> {gen_second} after drop)")
    print("  bound method ids after rebind:", R.BindMethod.cache_info().currsize)
    assert v.value == v2.value, "version mismatch"
    assert gen_second == gen_first + 1, f"expected exactly one reconnect, got {gen_first}->{gen_second}"
    assert R.BindMethod.cache_info().currsize >= 1, "method ids were not re-bound"
    print("  PASS: server process never restarted, connection self-healed")

    print("=== D. concurrent calls serialize on one socket ===")
    res = await asyncio.gather(*[GetVersion() for _ in range(8)])
    ids = {id(r) for r in res}
    print(f"  8 parallel GetVersion -> {len(res)} results, all ok: {all(x.value=='53.17-r1' for x in res)}")
    print("  final state:", R.connection_state())
    await R.close()
    print("  closed cleanly; connected:", R.is_connected())

asyncio.run(main())
