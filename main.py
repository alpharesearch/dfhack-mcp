"""
A MCP server that uses the dfhack_client_python package to connect to a locally
running Dwarf Fortress game through DFHack's RPC connection.

Known DFHack RPC quirks (learned the hard way, encoded into the tools below):

* RemoteFortressReader.GetBlockList: windowed (x/y) requests return ZERO blocks -
  the plugin's volume filter only matches full-extent requests. Always request the
  full map x/y (from GetMapInfo: block_size * 16 = tile extent) and window by z.
  Reply size limit: a 12x12x191 request overflows (~850 MB) and kills the socket;
  keep z windows <= 12 levels.
* MapBlock.map_x/map_y are TILE coordinates (multiples of 16). Grid arrays are
  row-major: index = (x - map_x)*16 + (y - map_y), i.e. x = map_x + i%16,
  y = map_y + i//16. Verified against in-game designations and terrain.
* RemoteFortressReader.SendDigCommand applies designations with x/y TRANSPOSED
  for many layers (send (x,y) -> lands at (y,x)); send_dig_command compensates by
  swapping them back, verified at the surface (stairwell) and tunnel floor.
  The compensation is NOT uniform: designation acceptance is z-level dependent
  (solid/undiscovered tiles silently drop), and some layers shift z by -1.
  ALWAYS verify with get_block_list after sending, and keep dig-now bounding
  boxes matched to where designations LANDED.
* RELIABLE BULK DESIGNATION: write quickfort blueprint CSVs to
  <gamedir>/dfhack-config/blueprints/, apply with
  run_command quickfort ["run","<file>.csv","-c","<x,y,z>"].
  Designations land at cursor (x, y+3, z) on this map - calibrate cursor by -3 y.
  CSV rules: '#name <bp>' in cell A1 (description must avoid commas), one key per
  cell comma-separated. Dig keys: d=default, c=channel, i=up-down stairs.
  Verify via get_block_list, then dig-now the landed bbox. Ramp-top tiles refuse
  designation (game rule) - route doorways through floor tiles.
* Designation acceptance appears tied to tile discovery and diggability rules
  (floor tiles, known tile below). Room-scale carving through this RPC is
  unreliable - designate big areas in-game (d key) and let miners work, or use
  dig-now with verified designations for surgical cuts.
* Game elevation = RPC z - 128 on this save (see get_map_info game_z_offset).
* RemoteFortressReader only reports/designates DISCOVERED tiles; fog areas read
  empty. Run the DFHack `reveal` command first for map work in dark areas.
* RemoteFortressReader tiletype ids are dynamic per save - resolve names with
  GetTiletypeList (position = id); e.g. 215 was StoneWall here, not a standard enum.
* SendDigCommand designations apply silently (no reply text); verify via map reads
  after `reveal`, in-game reports, or by watching dwarves take mining jobs.
* CoreProtocol RunCommand returns EmptyMessage - command console output arrives as
  streamed RPC_REPLY_TEXT frames (printed to this server's log, not in the tool result).
* CoreProtocol RunLua takes module+function, NOT a code string. The DFHack `lua`
  command also cannot eval code. No arbitrary Lua eval over RPC.
* CoreTextNotification carries `fragments` (CoreTextFragment.text), not `.text`.
* RemoteFortressReader.PassKeyboardEvent injects SDL1.2 key events (type 700/701,
  sym = ascii/SDL1 key code) - this can drive the real game UI, e.g. toggle the
  keyboard cursor (`toggle-kbd-cursor`), move it with arrows, and press `d` to
  designate like a player. Verify cursor position via get_view_info.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi_mcp import FastApiMCP
from dfhack_client_python.dfhack_remote import (connect, close, ensure_connection, drop_connection,
                                                connection_state, DFHackTransportError)
from dfhack_client_python.dfhack import (GetVersion, GetDFVersion, GetUnitList, GetWorldInfo,
GetViewInfo, ListUnits, RunCommand, GetMapInfo, GetBlockList, GetTiletypeList, GetReports,
GetPauseState, SetPauseState, SendDigCommand, PassKeyboardEvent, CopyScreen)
from dfhack_client_python.py_export.BasicApi_pb2 import ListUnitsIn, GetWorldInfoOut
from dfhack_client_python.py_export.CoreProtocol_pb2 import CoreRunCommandRequest
from dfhack_client_python.py_export.RemoteFortressReader_pb2 import (BlockRequest, DigCommand,
Coord, SingleBool, KeyboardEvent)

# SDL1.2 key codes (DF runs on SDL1): KeyboardEvent mirrors SDL_Event.key
# (type, which, state, scancode, sym, mod, unicode). scancode is unused by SDL1.
KEY_TYPE_DOWN, KEY_TYPE_UP = 700, 701
SDL1_SYM = {
    "up": 273, "down": 274, "right": 275, "left": 276,
    "enter": 13, "return": 13, "escape": 27, "backspace": 8, "tab": 9, "space": 32,
    "pageup": 315, "pagedown": 316, "home": 312, "end": 317,
}
for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    SDL1_SYM[c] = 97 + i     # lowercase ascii; uppercase keys use "A" -> 65
for i in range(10):
    SDL1_SYM[str(i)] = 48 + i

# RemoteFortressReader DigDesignation enum (proto): name -> number
DIG_DESIGNATIONS = {v.name: v.number for v in
    DigCommand.DESCRIPTOR.fields_by_name['designation'].enum_type.values}

GAME_Z_OFFSET = 128  # game elevation = rpc_z - GAME_Z_OFFSET (this save)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Do not connect on startup: the game is usually started after this server.
    Every tool call connects lazily, and a dropped socket reconnects by itself."""
    state = connection_state()
    print(f"DFHack RPC endpoint {state['host']}:{state['port']} - "
          f"connecting on first tool call (connected={state['connected']}, "
          f"generation={state['generation']})")
    yield
    await close()

app = FastAPI(
    lifespan=lifespan
)

async def _map_extent():
    """True map tile extents and z count from GetMapInfo (blocks are 16x16 tiles)."""
    mi = await GetMapInfo()
    return mi.block_size_x * 16, mi.block_size_y * 16, mi.block_size_z

@app.get("/get-versions", operation_id="get_versions")
async def get_versions():
    """Get the DFHack and Dwarf Fortress versions for connected server"""
    dfhack_version = (await GetVersion()).value
    dwarf_fortress_version = (await GetDFVersion()).value
    return {
        "dfhackVersion": dfhack_version,
        "dwarfFortressVersion": dwarf_fortress_version
    }

@app.get("/get-status", operation_id="get_status")
async def get_status():
    """Report the DFHack RPC connection state, and the game versions when reachable"""
    try:
        await ensure_connection()
        out = dict(connection_state())          # read state AFTER connecting
        out["dfhackVersion"] = (await GetVersion()).value
        out["dwarfFortressVersion"] = (await GetDFVersion()).value
    except DFHackTransportError as e:
        out = dict(connection_state())
        out["connected"] = False
        out["error"] = str(e)
    return out


@app.post("/reconnect", operation_id="reconnect")
async def reconnect():
    """Drop and reopen the DFHack connection - use it right after restarting the game"""
    drop_connection()
    try:
        generation = await connect()
        return {
            "connected": True,
            "generation": generation,
            "dfhackVersion": (await GetVersion()).value,
            "dwarfFortressVersion": (await GetDFVersion()).value,
        }
    except DFHackTransportError as e:
        return {"connected": False, "error": str(e)}


@app.get("/get-world-info", operation_id="get_world_info")
async def get_world_info():
    """Get the world specific info for the currently running game"""
    world_info = await GetWorldInfo()
    details = {}
    if world_info.mode == GetWorldInfoOut.Mode.MODE_DWARF:
        details = {
            "civId": world_info.civ_id,
            "siteId": world_info.site_id,
            "groupId": world_info.group_id,
            "raceId": world_info.race_id
        }

    return {
        "worldName": {
            "languageId": world_info.world_name.language_id,
            "firstName": world_info.world_name.first_name,
            "lastName": world_info.world_name.last_name,
            "nickname": world_info.world_name.nickname,
            "englishName": world_info.world_name.english_name
        },
        "mode": GetWorldInfoOut.Mode.Name(world_info.mode),
        "details": details
    }

@app.get("/get-view-info", operation_id="get_view_info")
async def get_view_info():
    """Get the view info for the currently running game"""
    view_info = await GetViewInfo()
    return {
        "view_pos_x": view_info.view_pos_x,
        "view_pos_y": view_info.view_pos_y,
        "view_pos_z": view_info.view_pos_z,
        "view_size_x": view_info.view_size_x,
        "view_size_y": view_info.view_size_y,
        "cursor_pos_x": view_info.cursor_pos_x,
        "cursor_pos_y": view_info.cursor_pos_y,
        "cursor_pos_z": view_info.cursor_pos_z,
        "follow_unit_id": view_info.follow_unit_id,
        "follow_item_id": view_info.follow_item_id
    }

@app.get("/get-map-info", operation_id="get_map_info")
async def get_map_info():
    """Get map dimensions (blocks are 16x16 tiles), save name, and the game elevation offset (game elev = rpc z - offset)"""
    mi = await GetMapInfo()
    return {
        "mapTilesX": mi.block_size_x * 16,
        "mapTilesY": mi.block_size_y * 16,
        "mapZLevels": mi.block_size_z,
        "blocksX": mi.block_size_x,
        "blocksY": mi.block_size_y,
        "saveName": mi.save_name,
        "worldNameEnglish": mi.world_name_english,
        "gameZOffset": GAME_Z_OFFSET
    }

@app.post("/list-units", operation_id="list_units")
async def list_units(request: Request):
    """Get details for the units with the given ids. Parameters: unit_ids[int]"""
    unit_ids = (await request.json())["unit_ids"]
    print("Unit IDs: " + str(unit_ids))
    units = await ListUnits(input = ListUnitsIn(id_list = unit_ids))
    unit_details = []
    for unit in units.value:
        unit_details.append({
            "unitId": unit.unit_id,
            "name": {
                "firstName": unit.name.first_name,
                "languageId": unit.name.language_id,
                "lastName": unit.name.last_name,
                "englishName": unit.name.english_name
            },
            "flags1": unit.flags1,
            "flags2": unit.flags2, 
            "flags3": unit.flags3,
            "race": unit.race,
            "caste": unit.caste,
            "gender": unit.gender,
            "civId": unit.civ_id,
            "histfigId": unit.histfig_id,
            "position": {
                "x": unit.pos_x,
                "y": unit.pos_y, 
                "z": unit.pos_z,
                "gameElevation": unit.pos_z - GAME_Z_OFFSET
            },
            "profession": unit.profession
        })
    return {"units": unit_details}

@app.get("/get-unit-list", operation_id="get_unit_list")
async def get_unit_list():
    """Get the list of units in the currently running game"""
    units = await GetUnitList()
    unit_details = []
    for unit in units.creature_list:
        unit_details.append({
            "id": unit.id,
            "position": {
                "x": unit.pos_x,
                "y": unit.pos_y,
                "z": unit.pos_z,
                "gameElevation": unit.pos_z - GAME_Z_OFFSET
            },
            "isSoldier": unit.is_soldier,
            "name": unit.name,
            "physicalDescription": unit.appearance.physical_description,
            "professionId": unit.profession_id,
            "age": unit.age
        })
    return { "unitCount": len(units.creature_list), "units": unit_details }

@app.post("/run-command", operation_id="run_command")
async def run_command(request: Request):
    """Run a DFHack command in the currently running game. Command output is not returned (it streams to this server's log); keep commands small. Parameters: command[str], arguments[list[str]] (optional)"""
    body = await request.json()
    command = body["command"]
    arguments = body.get("arguments", [])
    await RunCommand(input = CoreRunCommandRequest(command = command, arguments = arguments))
    return {"command": command, "arguments": list(arguments), "success": True}

@app.get("/get-reports", operation_id="get_reports")
async def get_reports():
    """Get the in-game report list (announcements, job reports); most recent last"""
    status = await GetReports()
    reports = []
    for r in status.reports:
        reports.append({
            "type": type(r.type).__name__ + ":" + str(int(r.type)),
            "text": str(r.text),
            "color": {"red": int(r.color.red), "green": int(r.color.green), "blue": int(r.color.blue)},
            "year": int(r.year),
            "time": int(r.time),
            "pos": {"x": int(r.pos.x), "y": int(r.pos.y), "z": int(r.pos.z) - GAME_Z_OFFSET},
            "repeatCount": int(r.repeat_count)
        })
    return {"reportCount": len(reports), "reports": reports[-100:]}

@app.get("/copy-screen", operation_id="copy_screen")
async def copy_screen():
    """Capture the current game screen as ASCII text (rows joined) - shows menus, designation UI, announcements"""
    sc = await CopyScreen()
    w, h = sc.width, sc.height
    tiles = list(sc.tiles)
    def cp437(c):
        if 32 <= c < 127:
            return chr(c)
        try:
            return bytes([c]).decode("cp437")
        except Exception:
            return "?"
    # ScreenCapture tiles are COLUMN-major (index = x*height + y).
    # Map/graphics cells are duplicated in pairs; collapse consecutive repeats.
    rows = []
    for y in range(h):
        out = []
        last = None
        for x in range(w):
            c = tiles[x*h+y].character
            ch = cp437(c) if c else ' '
            if ch != last:
                out.append(ch)
            last = ch
        rows.append(''.join(out).rstrip())
    return {"width": w, "height": h, "rows": rows}

@app.post("/pass-keyboard-event", operation_id="pass_keyboard_event")
async def pass_keyboard_event(request: Request):
    """Inject a key press into the game - drives the real UI (menus, d-designation with the keyboard cursor). Parameters: key[str] (up/down/left/right/enter/escape/pageup/pagedown/home/end/tab/space/backspace/a-z/0-9), repeat[int] (optional, default 1), hold[int] (optional, send N presses without releases)"""
    body = await request.json()
    key = str(body["key"]).lower()
    if key not in SDL1_SYM:
        return {"error": f"unknown key '{key}'", "known": sorted(SDL1_SYM)}
    sym = SDL1_SYM[key]
    repeat = int(body.get("repeat", 1))
    for _ in range(max(1, min(repeat, 500))):
        await PassKeyboardEvent(input = KeyboardEvent(type = KEY_TYPE_DOWN, which = 0, state = 1,
            scancode = 0, sym = sym, mod = 0, unicode = sym))
        await PassKeyboardEvent(input = KeyboardEvent(type = KEY_TYPE_UP, which = 0, state = 0,
            scancode = 0, sym = sym, mod = 0, unicode = sym))
    return {"key": key, "sym": sym, "sent": repeat, "success": True}

import os
BLUEPRINT_DIR = os.getenv("DFHACK_BLUEPRINT_DIR", "")

@app.post("/apply-blueprint", operation_id="apply_blueprint")
async def apply_blueprint(request: Request):
    """Write a quickfort dig blueprint and apply it (the reliable bulk-designation path). grid: list of rows, each a list of single dig keys (d=default, c=channel, i=up-down stairs, x=up, u=down...); cursor: [x,y,z] where the TOP-LEFT cell should land - this tool pre-compensates the known +3 y offset. Set DFHACK_BLUEPRINT_DIR to the game's dfhack-config/blueprints path to enable. Returns quickfort stats; verify with get_block_list then run dig-now on the landed box."""
    body = await request.json()
    if not BLUEPRINT_DIR:
        return {"error": "DFHACK_BLUEPRINT_DIR not configured; set it to <gamedir>/dfhack-config/blueprints"}
    name = str(body["name"]).replace(" ", "_")
    grid = body["grid"]
    cur = body["cursor"]
    rows = ["#name " + name] + [",".join(str(c) for c in r) for r in grid]
    path = os.path.join(BLUEPRINT_DIR, name + ".csv")
    with open(path, "w") as f:
        f.write("\n".join(rows) + "\n")
    out = await RunCommand(input = CoreRunCommandRequest(command = "quickfort",
        arguments = ["run", name + ".csv", "-c", f"{int(cur[0])},{int(cur[1])-3},{int(cur[2])}"]))
    return {"blueprint": path, "target_top_left": cur, "success": True}

@app.get("/get-pause-state", operation_id="get_pause_state")
async def get_pause_state():
    """True when the game is currently paused"""
    state = await GetPauseState()
    return {"paused": state.Value}

@app.post("/set-pause-state", operation_id="set_pause_state")
async def set_pause_state(request: Request):
    """Pause (true) or unpause (false) the game. Parameters: paused[bool]"""
    body = await request.json()
    await SetPauseState(input = SingleBool(Value = bool(body["paused"])))
    state = await GetPauseState()
    return {"paused": state.Value}

@app.post("/send-dig-command", operation_id="send_dig_command")
async def send_dig_command(request: Request):
    """Designate digging. designation: one of NO_DIG, DEFAULT_DIG, UP_DOWN_STAIR_DIG, CHANNEL_DIG, RAMP_DIG, DOWN_STAIR_DIG, UP_STAIR_DIG; locations: list of [x,y,z] (RPC z; RPC z = game elevation + 128). Only DISCOVERED, diggable tiles accept designations (run 'reveal' first for dark areas) and acceptance is z-dependent; NO replies are sent: ALWAYS verify with get_block_list (designations read at their LANDED coords, which may be x/y-transposed) before running dig-now with matching bounds."""
    body = await request.json()
    designation = body["designation"].upper()
    if designation not in DIG_DESIGNATIONS:
        return {"error": f"unknown designation '{designation}'", "known": sorted(DIG_DESIGNATIONS)}
    locs = [Coord(x=int(p[1]), y=int(p[0]), z=int(p[2])) for p in body["locations"]]
    # The game transposes x/y when applying designations; swap them here so the
    # designation lands at the requested (x, y).
    await SendDigCommand(input = DigCommand(designation = DIG_DESIGNATIONS[designation], locations = locs))
    return {"designation": designation, "tiles": len(locs), "success": True}

@app.post("/get-block-list", operation_id="get_block_list")
async def get_block_list(request: Request):
    """Read map blocks (explored tiles only) in a z window. Full map x/y is always used (windowed x/y requests return nothing - plugin bug); z windows <= 12 levels (larger overflows the RPC reply limit). Returns per-z tiletype census, dig designations found, and a compact per-block tile grid using tiletype NAMES. Parameters: min_z[int], max_z[int], census_top[int] (optional, default 8)"""
    body = await request.json()
    min_z = int(body["min_z"]); max_z = int(body["max_z"])
    census_top = int(body.get("census_top", 8))
    tiles_x, tiles_y, z_count = await _map_extent()
    min_z = max(0, min(min_z, z_count - 1)); max_z = max(0, min(max_z, z_count - 1))
    if max_z < min_z: min_z, max_z = max_z, min_z
    if max_z - min_z > 11: max_z = min_z + 11   # reply size guard
    tt = await GetTiletypeList()
    names = {i: t.name for i, t in enumerate(tt.tiletype_list)}
    bl = await GetBlockList(input = BlockRequest(min_x = 0, max_x = tiles_x - 1,
        min_y = 0, max_y = tiles_y - 1, min_z = min_z, max_z = max_z, force_reload = True))
    from collections import Counter
    census = {}; designations = []; blocks_out = []
    for b in bl.map_blocks:
        census.setdefault(b.map_z, Counter()).update(names.get(t, f"id{t}") for t in b.tiles)
        des = list(b.tile_dig_designation)
        # verified mapping (matches in-game designations, e.g. the x50,y120-126 stairwell)
        nz = [(b.map_x + i%16, b.map_y + i//16, v) for i, v in enumerate(des) if v]
        if nz: designations.extend({"x": x, "y": y, "z": b.map_z, "designation": v} for x, y, v in nz)
        blocks_out.append({
            "x": b.map_x, "y": b.map_y, "z": b.map_z,
            "tiles": [names.get(t, f"id{t}") for t in b.tiles],
            "hidden": list(b.hidden),
            "buildingCount": len(b.buildings), "itemCount": len(b.items)
        })
    return {
        "requestedZ": [min_z, max_z],
        "gameElevations": [min_z - GAME_Z_OFFSET, max_z - GAME_Z_OFFSET],
        "blockCount": len(bl.map_blocks),
        "censusPerZ": {z: c.most_common(census_top) for z, c in sorted(census.items())},
        "digDesignations": designations[:200],
        "blocks": blocks_out
    }

mcp = FastApiMCP(
    app,
    name="Dwarf Fortress MCP Server",
    description="A MCP server that uses the dfhack_client_python package to connect to a locally running Dwarf Fortress game through DFHack's RPC connection."
)
# Streamable HTTP (POST/GET /mcp) - the transport dsh-mcp-client speaks.
# mcp.mount() is deprecated and mounts the legacy SSE pair (GET /mcp + POST /mcp/messages/).
mcp.mount_http()

if __name__ == "__main__":
    import os
    import uvicorn
    # 127.0.0.1 only: DFHack's own RPC is localhost-bound (allow_remote: false),
    # and this server can drive the game. Bind 0.0.0.0 deliberately if you want LAN use.
    uvicorn.run(app, host=os.getenv("DFHACK_MCP_HOST", "127.0.0.1"),
                port=int(os.getenv("DFHACK_MCP_PORT", "8000")))
