#!/usr/bin/env python3
"""Drive the real Dwarf Fortress UI over DFHack RPC, like a player's hands.

The world layer (worldmodel.py) reads the game; this drives it. Both go through the
same RPC socket, so they can be interleaved: designate from the map model, place a
building through the real menu, then read the result back from the map model.

    ./scripts/dfui.py screen                  # dump the current screen as text
    ./scripts/dfui.py key escape              # one key
    ./scripts/dfui.py keys "b,t,enter"        # a sequence, 0.4s apart
    ./scripts/dfui.py goto 54 118 167         # keyboard-cursor to x,y,rpc-z
    ./scripts/dfui.py cmd quickfort run bp.csv -c 54,118,167
    ./scripts/dfui.py status                  # view + cursor, decoded

Key names: escape enter tab space backspace up down left right pageup pagedown
home end, plus any single character. Uppercase means Shift+key (SDL1 sym 65-90).

Notes learned the hard way:
  * RemoteFortressReader.PassKeyboardEvent injects SDL1.2 events: type 700/701
    (KEY_DOWN/KEY_UP), sym = SDL1 key code. Lowercase letters are ascii 97-122,
    uppercase 65-90 (which is how DF sees Shift+letter).
  * The in-game keyboard cursor is OFF by default: GetViewInfo reports the sentinel
    -30000. `goto` enables it with DFHack `toggle-kbd-cursor` and then walks it with
    arrow keys, re-reading GetViewInfo to know where it landed.
  * PageUp/PageDown move the cursor by the view height (10 tiles), arrows by 1.
"""
import asyncio
import sys

sys.path.insert(0, __import__("pathlib").Path(__file__).resolve().parents[1])

from dfhack_client_python.dfhack_remote import connect, close                    # noqa: E402
from dfhack_client_python.dfhack import (CopyScreen, GetViewInfo, PassKeyboardEvent,   # noqa: E402
                                         RunCommand)
from dfhack_client_python.py_export.CoreProtocol_pb2 import CoreRunCommandRequest      # noqa: E402
from dfhack_client_python.py_export.RemoteFortressReader_pb2 import KeyboardEvent      # noqa: E402

SPECIAL = {
    "escape": 27, "enter": 13, "return": 13, "tab": 9, "space": 32, "backspace": 8,
    "up": 273, "down": 274, "right": 275, "left": 276,
    "pageup": 315, "pagedown": 316, "home": 312, "end": 317,
}


def sym_of(key: str) -> int:
    k = key.strip().lower()
    if k in SPECIAL:
        return SPECIAL[k]
    if len(key.strip()) == 1:
        return ord(key.strip())
    raise SystemExit(f"unknown key '{key}' (known: {sorted(SPECIAL)} or a single character)")


async def press(sym: int, hold: int = 1, gap: float = 0.05):
    """One key press (down+up). `hold` sends that many presses - used to walk the
    keyboard cursor several tiles with a single call."""
    for _ in range(max(1, int(hold))):
        await PassKeyboardEvent(input=KeyboardEvent(type=700, which=0, state=1, scancode=0,
                                                    sym=sym, mod=0, unicode=sym))
        await asyncio.sleep(gap)
        await PassKeyboardEvent(input=KeyboardEvent(type=701, which=0, state=0, scancode=0,
                                                    sym=sym, mod=0, unicode=sym))
        await asyncio.sleep(gap)


async def screen(max_lines: int = 70) -> list[str]:
    sc = await CopyScreen()
    h, w, tiles = sc.height, sc.width, list(sc.tiles)
    out = []
    for y in range(h):
        row = "".join(chr(tiles[x * h + y].character)
                      if 32 <= tiles[x * h + y].character < 127 else " " for x in range(w))
        if row.strip():
            out.append(row.rstrip())
        if len(out) >= max_lines:
            break
    return out


async def view() -> dict:
    vi = await GetViewInfo()
    return {"x": vi.view_pos_x, "y": vi.view_pos_y, "z": vi.view_pos_z,
            "size": f"{vi.view_size_x}x{vi.view_size_y}",
            "cursor": (vi.cursor_pos_x, vi.cursor_pos_y, vi.cursor_pos_z)}


async def run(command: str, args: list[str]):
    await RunCommand(input=CoreRunCommandRequest(command=command, arguments=args))


async def goto(tx: int, ty: int, tz: int, max_steps: int = 400) -> dict:
    """Walk the keyboard cursor to a tile. Enables the cursor if it is off."""
    v = await view()
    cx, cy, cz = v["cursor"]
    if cx < -1000:                      # sentinel: keyboard cursor is off
        await run("toggle-kbd-cursor", [])
        await asyncio.sleep(0.6)
        v = await view()
        cx, cy, cz = v["cursor"]
        if cx < -1000:
            raise SystemExit("cursor stayed off: toggle-kbd-cursor did not take")
    # SDL1 arrow syms: 273=up 274=down 275=left 276=right. In the DF map view,
    # right = +x and down = +y; PageUp/PageDown (315/316) change z. Mapping was
    # calibrated by watching GetViewInfo's cursor after each press, not assumed.
    moves = {"up": 273, "down": 274, "left": 275, "right": 276}
    steps = 0
    while steps < max_steps:
        v = await view()
        cx, cy, cz = v["cursor"]
        if (cx, cy, cz) == (tx, ty, tz):
            return v
        if abs(tx - cx) >= 10:
            sym, nxt = ("right", tx - cx) if tx > cx else ("left", cx - tx)
            await press(moves[sym], hold=min(abs(nxt) // 10, 20))
        elif abs(ty - cy) >= 10:
            sym = ("down" if ty > cy else "up")
            await press(moves[sym], hold=min(abs(ty - cy) // 10, 20))
        elif cx != tx:
            await press(moves["right" if tx > cx else "left"])
        elif cy != ty:
            await press(moves["down" if ty > cy else "up"])
        elif cz != tz:
            await press(316 if cz < tz else 315)     # PageDown/PageUp change z
        steps += 1
    v = await view()
    print(f"  cursor stalled at {v['cursor']} (wanted {tx},{ty},{tz})")
    return v


async def main():
    await connect()
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "screen":
        for row in await screen():
            print(row)
    elif cmd == "keys":
        for k in args[1].split(","):
            s = sym_of(k)
            await press(s)
            print(f"key {k} (sym {s}) -> view {await view()}")
            await asyncio.sleep(0.4)
    elif cmd == "key":
        await press(sym_of(args[1]))
        print(f"key {args[1]} sent; view {await view()}")
    elif cmd == "goto":
        v = await goto(int(args[1]), int(args[2]), int(args[3]))
        print("cursor:", v["cursor"], "view:", f"{v['x']},{v['y']} z{v['z']}")
    elif cmd == "status":
        print(await view())
    elif cmd == "cmd":
        await run(args[1], args[2:])
        print("command sent:", " ".join(args[1:]))
    else:
        print(__doc__)
    await close()


if __name__ == "__main__":
    asyncio.run(main())
