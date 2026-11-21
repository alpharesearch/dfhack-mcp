"""Simulate: Dwarf Fortress restarts (new process, new method ids) while the
MCP server process keeps running. The client must self-heal without a restart."""
import asyncio, os, pathlib, signal, subprocess, sys, time
os.environ["DFHACK_RPC_HOST"] = "127.0.0.1"
os.environ["DFHACK_RPC_PORT"] = "15100"
import dfhack_client_python.dfhack_remote as R
from dfhack_client_python.dfhack import GetVersion, GetDFVersion

MOCK = sys.executable

def start_mock(offset, label):
    log = open(str(pathlib.Path(__file__).resolve().parent / f"mock_{label}.log"), "w")
    p = subprocess.Popen([MOCK, str(pathlib.Path(__file__).resolve().parent / "mock_dfhack.py"), "15100", str(offset), label],
                         stdout=log, stderr=log)
    for _ in range(50):
        time.sleep(0.1)
        with __import__("socket").create_connection(("127.0.0.1", 15100), 1) as s:
            s.sendall(b"DFHack?\n\x01\x00\x00\x00")
            if s.recv(12).startswith(b"DFHack!\n"):
                return p
    raise RuntimeError("mock did not start")

def stop_mock(p):
    p.send_signal(signal.SIGTERM)     # the game exits
    p.wait(timeout=5)
    time.sleep(0.3)

async def main():
    print("=== game #1 running ===")
    m1 = start_mock(100, "GAME1")
    v = await GetVersion(); print("  GetVersion ok:", v.value)
    d = await GetDFVersion(); print("  GetDFVersion ok:", d.value)
    print(f"  versions: {v.value} / {d.value}")
    print(f"  generation={R.connection_generation()} bound ids={R.BindMethod.cache_info().currsize}")
    first_gen = R.connection_generation()

    print("=== game CLOSED (MCP server process untouched) ===")
    stop_mock(m1)
    print(f"  socket state: connected={R.is_connected()}")

    print("=== game #2 started on the same port, with DIFFERENT method ids ===")
    m2 = start_mock(500, "GAME2")
    t0 = time.time()
    v2 = await GetVersion(); d2 = await GetDFVersion()     # must reconnect + rebind
    print(f"  versions: {v2.value} / {d2.value}  (took {time.time()-t0:.2f}s)")
    print(f"  generation={R.connection_generation()} (was {first_gen}) "
          f"bound ids={R.BindMethod.cache_info().currsize}")
    assert "GAME2" in v2.value, "stale connection/method ids in use"
    assert R.connection_generation() > first_gen
    print("  PASS: no MCP server restart, ids re-bound, calls working")

    print("=== extra: no game at all -> clean error, then recovers ===")
    stop_mock(m2)
    try:
        await asyncio.wait_for(GetVersion(), timeout=4)
        print("  UNEXPECTED success with no game")
    except R.DFHackTransportError as e:
        print(f"  OK clean error: {str(e)[:90]}...")
    m3 = start_mock(900, "GAME3")
    v3 = await GetVersion()
    print(f"  recovery call -> {v3.value} | generation={R.connection_generation()}")
    assert "GAME3" in v3.value
    stop_mock(m3)
    await R.close()
    print("\nALL PASS - MCP server never restarted")

asyncio.run(main())
