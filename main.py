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
  (Offset is anchor-dependent: some anchors land y+1 - ALWAYS verify landing with
  get_block_list before dig-now.)
  CSV rules: '#name <bp>' in cell A1 (description must avoid commas), one key per
  cell comma-separated. Dig keys: d=default, c=channel, i=up-down stairs.
  Verify via get_block_list, then dig-now the landed bbox. Ramp-top tiles refuse
  designation (game rule) - route doorways through floor tiles.
  This build's quickfort supports the DIG mode only (no stockpile mode registered).
  Surface tiles west of ~x56 refuse ALL designation vectors (RPC and quickfort) -
  they are beyond the designable border; the dwarves-side band (~x50-55, y114-126)
  accepts stairs. The fort's surface->tunnel link is a down-stair shaft at
  x54-55,y118 (elev 39 -> 38 -> tunnel floor elev 37); tunnel/rooms are connected
  (ramps y116 -> room y117).
* STAIR SHAFTS REQUIRE MULTI-Z DESIGNATION: designating the top tile alone digs a
  1-level pit. Send DOWN_STAIR_DIG at every z the shaft passes through (one
  designate+dig-now cycle per level; stair tiles at z carve their floor from z-1).
  Proven: shaft from room floor z165 now descends stairs to z160 (elev 32).
  RPC stair designations at room/tunnel bands land TRANSPOSED (send (20..22,117..119)
  -> lands (117..119,20..22) at same z) - dig-now the landed box.
 * STAIRS ON CARVED VOID: once the material is gone you cannot DESIGNATE stairs
   (no dig target). Build them instead: quickfort build-mode blueprint
   ('#build ...' modeline, cells b(stairs/up-down)) applied on the floor tiles,
   then run_command build-now to finish instantly. Proven: stair line built along
   the elev-34 tunnel at y21 (18 buildings via 9 stepped blueprint applies).
   Build applies land at cursor y+1..y+3; a 1-wide floor band yields 1 tile per
   apply - step the cursor along the band.
   (User lesson, wiki: https://dwarffortresswiki.org/index.php/Building)
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
* CoreProtocol RunLua takes module+function, NOT a code string - but the DFHack **`lua`
  command DOES evaluate code over RunCommand**. Verified on 53.17-r1: `lua print(1+1)`
  returns the text "2". That reopens what this file called unreachable:
  `reqscript("quickfort").apply_blueprint` (rectangle designation AND building placement
  at an explicit pos), `dfhack.zone`, `dfhack.stockpile`, `df.global.world.jobs.list`
  (the job queue), `devel/query` (evaluate an expression against live internals).
  Nothing in the proto list is required when `lua` is reachable.
* Why that stayed invisible: RunCommand answers EmptyMessage and the transport *printed*
  RPC_REPLY_TEXT frames into the server log. With dfhack_remote.drain_replies() the text
  is returned to the caller - the `lua` door becomes visible in the tool output.
* CoreTextNotification carries `fragments` (CoreTextFragment.text), not `.text`.
* GetBlockList z BOUNDS ARE HALF-OPEN: it answers [min_z, max_z), so an inclusive
  request silently LOSES ITS TOP z-LEVEL, and a span-1 request (min_z == max_z)
  answers ZERO blocks. Verified live: 165-165 -> 0; 164-165 -> only z=164;
  163-165 -> z=163,164. worldmodel.window_request() adds the +1 and keeps span >= 2.
* The +1 overshoot spills into the next 12-z cache chunk: filter cached blocks to the
  chunk's nominal z range, or a boundary level is counted TWICE (measured live: 288
  blocks for one level of a 12x12 map, i.e. a doubled census).
* RemoteFortressReader rejects big replies: ~68 MB logs "reply too large" and ~850 MB
  answers CR_LINK_FAILURE, which KILLS the TCP socket - so every fetch failure must
  drop and rebind the connection, then retry a smaller window (worldmodel._fetch).
  Reading all 191 z-levels costs ~120 MB: that is opt-in, never a default.
* Unit SPECIES is not in RemoteFortressReader: UnitDefinition.race is a MatPair
  material reference (a dwarf reports (572,1)), not a species. Decode species from the
  BasicApi ListUnits race INDEX against GetCreatureRaws index -> name. GetCreatureRaws
  answers 18.8 MB and is rejected, so fetch it in 150-raw chunks
  (GetPartialCreatureRaws) and keep only the names.
* GetBuildingDefList is full of placeholder defs named 'NONE' whose
  (building_type, subtype, custom) keys COLLIDE with real buildings (verified: 'Furnace'
  shares keys with a 'NONE' row). Keep real names only and resolve instances with a
  fallback chain: exact key -> (bt,subtype) -> bt.
* Item materials: generated-creature materials (REMAINS, CORPSEPIECE) are absent from
  GetMaterialList (mat_type 434 has no entry). Label them in words, never as ints.
* Labors ARE readable: ListUnitsIn(mask=BasicUnitInfoMask(labors=True)) fills
  BasicUnitInfo.labors (labor ids; name them via ListEnums.unit_labor). This is how
  "do I have 2 miners?" becomes detectable instead of guessable.
* GetViewInfo cursor is a SENTINEL (-30000) when the keyboard cursor is off - bound
  check it before using it as a location.
* QUICKFORT BUILD MODE (verified building a trade depot, 53.17-r1):
  - The modeline sets the mode: '#name' is a DIG blueprint, so build keys answer
    'invalid key sequence'. apply_blueprint takes mode:"dig"|"build" and writes the
    matching modeline.
  - Build keys come from hack/docs/docs/guides/quickfort-user-guide.txt; the trade
    depot key is 'D' and its footprint is 5x5 (one cell covers 25 tiles, so a second
    apply on the same site reports 'Unsuitable tiles for building: 25' - the first
    blueprint is occupying it).
  - Cells routinely contain quotes/commas, so the CSV must be csv.writer-quoted
    ("D{name=""x""}"), exactly like the shipped dreamfort.csv. A raw "," join corrupts
    cells.
  - 'mat="Boulder"' is NOT a build-cell property: 'unhandled property: "mat"="Boulder"'.
  - THE MATERIAL DIAGNOSTIC: 'quickfort orders <file> --dry-run' prints what quickfort
    would MANUFACTURE for the build ('Would order blocks: 1'), NOT a hard game
    requirement. It misled me here: the trade depot was built from loose boulders with
    no blocks at all. Read it as "quickfort's plan", never as "the game's rule".
  - CURSOR ANCHOR (verified): a multi-tile building anchors its footprint CENTRED on the
    cursor, so the top-LEFT lands at (cursor_x-2, cursor_y-2) for a 5x5, and on this map
    a '#build' apply passed -c 108,9,165 produced a building at x106,y7 ELEV 39 (z167):
    z lands +2 from what is sent. To target elevation E, send z = E + 128 - 2.
  - Queued blueprints DO get built by dwarves given game time (unpause). 'build-now'
    only finishes buildings already under construction.
  - quickfort 'run' DESIGNATES a building; it is then a job that needs a builder dwarf
    and materials. 'build-now' completes only buildings already UNDER CONSTRUCTION
    (verified: it reported 'Completed 9 construction jobs' and left the depot alone).
  - RemoteFortressReader does NOT report queued building blueprints: a designated-but-
    unbuilt building is invisible to world_objects/world_map/fort_progress. A rung flips
    to [x] only when the building exists.
  - DFHack console output for an unknown command arrives as 'X is not a recognized
    command.' plus a CR_NOT_IMPLEMENTED reply; run_command now returns that as data
    instead of an HTTP 500 stack trace.
  - Trade depot gameplay (wiki Quickstart): 'b-T', needs 3 logs/boulders, in a 5x5 room,
    and needs a 3-WIDE path from the depot to the map edge - wagons cannot cross stairs
    or traps. This fort's only surface link is a 1-wide down-stair shaft at x56,y114-118
    (elev 39), verified from the glyph map, so a depot on elev 37 is wagon-inaccessible.
* Fortress and adventure mode are DIFFERENT GAMES: gate every map projection on
  GetWorldInfo mode (MODE_DWARF). Zones/stockpiles/pastures ARE buildings in DF, so the
  building list covers them.
* RemoteFortressReader.PassKeyboardEvent injects SDL1.2 key events (type 700/701,
  sym = ascii/SDL1 key code) - this can drive the real game UI, e.g. toggle the
  keyboard cursor (`toggle-kbd-cursor`), move it with arrows, and press `d` to
  designate like a player. Verify cursor position via get_view_info.
"""

import asyncio
import shutil
import time
from collections import Counter
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, Request
from fastapi_mcp import FastApiMCP
from dfhack_client_python.dfhack_remote import (connect, close, ensure_connection, drop_connection,
                                                connection_state, DFHackError, DFHackTransportError,
                                                drain_replies)
import json
from dfhack_client_python.dfhack import (GetVersion, GetDFVersion, GetUnitList, GetWorldInfo,
GetViewInfo, ListUnits, RunCommand, GetMapInfo, GetBlockList, GetTiletypeList, GetReports,
GetPauseState, SetPauseState, SendDigCommand, PassKeyboardEvent, CopyScreen, SetUnitLabors,
GetBuildingDefList, ResetMapHashes)
from dfhack_client_python.py_export.Basic_pb2 import UnitLaborState
from dfhack_client_python.py_export.BasicApi_pb2 import SetUnitLaborsIn
from dfhack_client_python.py_export.BasicApi_pb2 import ListUnitsIn, GetWorldInfoOut
from dfhack_client_python.py_export.CoreProtocol_pb2 import CoreRunCommandRequest
from dfhack_client_python.py_export.RemoteFortressReader_pb2 import (BlockRequest, DigCommand,
Coord, SingleBool, KeyboardEvent)
import worldmodel as WM
import luaops as LO

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

GAME_Z_OFFSET = WM.GAME_Z_OFFSET   # 129: in-game Elevation = z - 129 (one unit, one source)

# The preprocessing layer (see worldmodel.py). One instance per process: it owns the
# decode tables (tiletype/item/material/building/profession names), the z-chunk block
# cache, and the previous-snapshot used for delta reporting.
world = WM.WorldModel()

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
    # DFHack answers CR_NOT_IMPLEMENTED for an unknown command and CR_LINK_FAILURE for a
    # dead socket; both arrived as unhandled exceptions (HTTP 500 + a stack trace in the
    # log). Report them as data so the caller can read the failure and move on.
    drain_replies()                                   # drop anything from an earlier call
    try:
        await RunCommand(input = CoreRunCommandRequest(command = command, arguments = arguments))
    except DFHackError as e:
        out = drain_replies()
        return {"command": command, "arguments": list(arguments), "success": False,
                "error": str(e),
                "output": "\n".join(out),
                "hint": "console text (including the reason for a failure) is returned in "
                        "'output' - see 'help <command>' for usage"}
    except DFHackTransportError as e:
        return {"command": command, "arguments": list(arguments), "success": False,
                "error": f"transport: {e}", "hint": "the game died mid-command; the next "
                                                     "call reconnects by itself"}
    return {"command": command, "arguments": list(arguments), "success": True,
            "output": "\n".join(drain_replies()),
            "note": "RunCommand's reply body is EmptyMessage; DFHack's console text is "
                    "captured from RPC_REPLY_TEXT frames into 'output'"}

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
# Blueprint authoring happens INSIDE the repo (git-controlled, workspace permissions);
# the game directory is a copy target, because quickfort only reads from
# <gamedir>/dfhack-config/blueprints. So the server needs no host write access at all -
# only `scripts/deploy.sh` touches the game tree, and that is an explicit step.
REPO_BLUEPRINT_DIR = os.getenv("DFHACK_BLUEPRINT_REPO_DIR",
                               os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "deployables", "blueprints"))
DEPLOY_BLUEPRINT_DIR = os.getenv("DFHACK_BLUEPRINT_DIR", "")   # <gamedir>/dfhack-config/blueprints

@app.post("/apply-blueprint", operation_id="apply_blueprint")
async def apply_blueprint(request: Request):
    """Write a quickfort blueprint INTO THE REPO (deployables/blueprints, git-controlled) and apply it: the reliable bulk path for BOTH digging and building. mode: "dig" (default) writes a #dig modeline; "build" writes #build - build keys are only valid there (D=trade depot, b=stairs, h=hatch, d=door, f=forge, w=well...). quickfort modelines set the mode, so a build key in a dig blueprint answers "invalid key sequence". grid: list of rows, each a list of single dig keys (d=default, c=channel, i=up-down stairs, x=up, u=down...); cursor: [x,y,z] where the TOP-LEFT cell should land - this tool pre-compensates the known +3 y offset. Set DFHACK_BLUEPRINT_DIR to the game's dfhack-config/blueprints path to enable. Returns quickfort stats; verify with get_block_list then run dig-now on the landed box."""
    body = await request.json()
    os.makedirs(REPO_BLUEPRINT_DIR, exist_ok=True)
    deploy_ok = True
    name = str(body["name"]).replace(" ", "_")
    grid = body["grid"]
    cur = body["cursor"]
    # The modeline sets the blueprint MODE. "#name" is a DIG blueprint, so build keys
    # (D=trade depot, b=stairs, ...) come back as "invalid key sequence". mode must be
    # explicit: "dig" (default) or "build". Verified live against quickfort 53.17-r1.
    if not grid or not any(len(r) for r in grid):
        return {"error": "grid is empty; it must be a list of rows of cells"}
    mode = str(body.get("mode", "dig")).lower().strip()
    if mode not in ("dig", "build"):
        return {"error": f"mode must be 'dig' or 'build', got '{mode}'"}
    import csv, io
    # Cells routinely contain quotes and commas (quickfort options: D{name="x" mat="y"}),
    # so they MUST be CSV-quoted - a raw "," join corrupts them (verified: quickfort
    # reads a corrupted cell as an invalid key sequence).
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([f"#{mode} {name}"])
    for r in grid:
        w.writerow([str(c) for c in r])
    path = os.path.join(REPO_BLUEPRINT_DIR, name + ".csv")
    with open(path, "w") as f:
        f.write(buf.getvalue())
    # Ship it: quickfort resolves the filename against the game's blueprints dir, so the
    # file must be copied there before the run. Copy failure is reported, not thrown.
    shipped = None
    if DEPLOY_BLUEPRINT_DIR:
        shipped = os.path.join(DEPLOY_BLUEPRINT_DIR, name + ".csv")
        try:
            os.makedirs(DEPLOY_BLUEPRINT_DIR, exist_ok=True)
            shutil.copy2(path, shipped)
        except OSError as e:
            deploy_ok = False
            return {"success": False, "blueprint": path,
                    "error": f"blueprint written in the repo but not deployed: {e}",
                    "hint": "run ./scripts/deploy.sh deploy (needs write access to the game dir), "
                            "or set DFHACK_BLUEPRINT_DIR to a writable path"}
    if not DEPLOY_BLUEPRINT_DIR:
        return {"success": False, "blueprint": path,
                "error": "no deploy target configured",
                "hint": "set DFHACK_BLUEPRINT_DIR=<gamedir>/dfhack-config/blueprints; "
                        "the file is already saved in the repo and deployable via ./scripts/deploy.sh"}
    drain_replies()
    try:
        await RunCommand(input = CoreRunCommandRequest(command = "quickfort",
            arguments = ["run", name + ".csv", "-c", f"{int(cur[0])},{int(cur[1])-3},{int(cur[2])}"]))
    except (DFHackError, DFHackTransportError) as e:
        # The blueprint IS written and deployed; only the apply failed. Say so with both
        # paths, otherwise a caller cannot tell authoring from deployment.
        return {"success": False, "blueprint": path, "deployed_to": shipped, "mode": mode,
                "target_top_left": cur, "error": str(e),
                "output": "\n".join(drain_replies()),
                "hint": "the file is in the repo and deployed; the quickfort run is what failed"}
    return {"blueprint": path, "deployed_to": shipped, "mode": mode,
            "target_top_left": cur, "success": True,
            "output": "\n".join(drain_replies()),
            "note": "quickfort reports 'Buildings designated: N', 'Tiles designated for "
                    "digging: N', and 'Invalid key sequences: N' here"}

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
    """Designate digging - the `m` menu over RPC, no blueprint and no instant-dig. designation: one of NO_DIG, DEFAULT_DIG, UP_DOWN_STAIR_DIG, CHANNEL_DIG, RAMP_DIG, DOWN_STAIR_DIG, UP_STAIR_DIG; locations: list of [x,y,z] where z = elevation + 128, sent AS GIVEN (measured: lands where you put it). Optional swap:true reproduces the old mirrored behaviour for other builds. Only DISCOVERED, diggable tiles accept designations - unexplored tiles are rejected SILENTLY, and there is no reply, so ALWAYS verify: read `world_objects`/`look` for the dig cluster and its tile count before assuming work was ordered. Use NO_DIG to clear a rectangle."""
    body = await request.json()
    designation = body["designation"].upper()
    if designation not in DIG_DESIGNATIONS:
        return {"error": f"unknown designation '{designation}'", "known": sorted(DIG_DESIGNATIONS)}
    # Measured on 53.17-r1 STEAM (single-tile calibration): a designation sent as
    # [x=63, y=118, z=167] lands at x63,y118 on elev 39 - AS GIVEN, no transposition.
    # The old x/y swap was inherited from an earlier observation and sent whole
    # tunnels to the mirrored coordinates, where unexplored tiles silently rejected
    # them (verified: 87 designated tiles, dig count stayed 0). Passing `swap` is
    # accepted for callers who need the old behaviour on some other build.
    swap = bool(body.get("swap", False))
    locs = [Coord(x=int(p[1]), y=int(p[0]), z=int(p[2])) for p in body["locations"]] if swap \
           else [Coord(x=int(p[0]), y=int(p[1]), z=int(p[2])) for p in body["locations"]]
    await SendDigCommand(input = DigCommand(designation = DIG_DESIGNATIONS[designation], locations = locs))
    return {"designation": designation, "tiles": len(locs), "success": True}

@app.post("/get-block-list", operation_id="get_block_list")
async def get_block_list(request: Request):
    """RAW tile arrays - the expensive escape hatch: ONE elevation is ~147,000 tokens, so prefer look / world_objects / world_map / world_minimap. Read map blocks (explored tiles only) in a z window. Full map x/y is always used (windowed x/y requests return nothing - plugin bug); z windows <= 12 levels (larger overflows the RPC reply limit and kills the socket). Parameters: min_z[int], max_z[int], census_top[int] (optional, default 8)"""
    body = await request.json()
    min_z = int(body["min_z"]); max_z = int(body["max_z"])
    census_top = int(body.get("census_top", 8))
    tiles_x, tiles_y, z_count = await _map_extent()
    min_z = max(0, min(min_z, z_count - 1)); max_z = max(0, min(max_z, z_count - 1))
    if max_z < min_z: min_z, max_z = max_z, min_z
    if max_z - min_z > 11: max_z = min_z + 11   # reply size guard (850 MB overflow kills the socket)
    tt = await GetTiletypeList()
    names = {i: t.name for i, t in enumerate(tt.tiletype_list)}
    # GetBlockList treats max_z as EXCLUSIVE and a span-1 request returns ZERO blocks.
    # Verified live: 165-165 -> 0; 164-165 -> only z=164; 163-165 -> z=163,164.
    # worldmodel.blocks() applies the +1 fix and caches full-x/y 12-z chunks.
    bl = await world.cache.blocks(min_z, max_z, await world._gen())
    from collections import Counter
    census = {}; designations = []; blocks_out = []
    for b in bl:
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
    est = sum(len(str(x)) for x in blocks_out) // 4
    return {
        "requestedZ": [min_z, max_z],
        "gameElevations": [min_z - GAME_Z_OFFSET, max_z - GAME_Z_OFFSET],
        "blockCount": len(bl),
        "censusPerZ": {z: c.most_common(census_top) for z, c in sorted(census.items())},
        "digDesignations": designations[:200],
        "estTokens": est,
        "warning": f"blocks[] carries raw tile names: ~{est:,} tokens in this reply. "
                   f"Prefer world_raw (writes to a file) or world_objects (same planning content, ~1k tokens).",
        "blocks": blocks_out
    }

# ---------------------------------------------------------------------------
# PREPROCESSED WORLD VIEWS (worldmodel.py). Start here; zoom only as needed.
#
# Measured on a 192x192x191 save with 22,127 discovered blocks:
#   get_block_list (raw names), ONE z-level : ~588 KB  ~147,000 tokens
#   the whole map, all z                    : ~120 MB  ~30,000,000 tokens
#   look / world_objects, whole fort band   : ~2-4 KB      ~500-1,000 tokens
#   world_minimap, ALL z at once            : ~37 KB       ~9,000 tokens
# Every tool here returns compact plain text under a token budget and says so when
# it truncates. Coordinates in and out are GAME ELEVATIONS (rpc_z - 128).
# ---------------------------------------------------------------------------

@app.get("/look", operation_id="look")
async def look(e0: int = Query(-999, description="start game elevation; omit for auto fort band"),
               e1: int = Query(-999, description="end game elevation; omit for auto fort band"),
               with_legend: bool = Query(False, description="include the map glyph legend"),
               max_tokens: int = Query(WM.DEFAULT_MAX_TOKENS, description="token budget, cap 30000")):
    """START HERE. The whole-fort briefing in one call: date, pause state, population rolled up by profession/species/soldiers/wounded/hostile, latest reports, a per-elevation strip (walls/floors/open/water/magma/digs/buildings/items), what CHANGED since your last look, pending dig clusters as rectangles, building and item rollups, and named walkable regions. Auto-targets the elevation band where units and dig activity actually are. ~500-1,000 tokens. Then zoom with world_zone/world_map/world_objects."""
    e0 = None if e0 == -999 else e0
    e1 = None if e1 == -999 else e1
    return await world.look(max_tokens=max_tokens, with_legend=with_legend, e0=e0, e1=e1)

@app.get("/world-zone", operation_id="world_zone")
async def world_zone(e0: int = Query(..., description="start in-game Elevation"),
                     e1: int = Query(..., description="end game elevation (max 12 levels apart)"),
                     max_tokens: int = Query(WM.DEFAULT_MAX_TOKENS)):
    """One compact line per elevation: block count, wall/floor/open/tree/water/magma tile counts, pending dig count, building count, item count, region count. ~60 tokens per elevation. Use to pick where to look closer."""
    return await world.zone(e0, e1, max_tokens=max_tokens)

@app.get("/world-map", operation_id="world_map")
async def world_map(e: int = Query(..., description="in-game Elevation"),
                    x0: int = Query(...), x1: int = Query(...),
                    y0: int = Query(...), y1: int = Query(...),
                    rle: bool = Query(True, description="run-length encode flat rows"),
                    with_legend: bool = Query(False, description="include the glyph legend"),
                    max_tokens: int = Query(WM.DEFAULT_MAX_TOKENS)):
    """A single elevation as a 1-char-per-tile glyph grid for an explicit x/y window, with walkable-region letters annotated alongside. Keep windows ~40x40 (~400 tokens). Tiletype names are NEVER sent; read the legend once. Water/magma/hidden override the base glyph."""
    return await world.view(e, x0, x1, y0, y1, max_tokens=max_tokens, rle=rle,
                            with_legend=with_legend)

@app.get("/world-objects", operation_id="world_objects")
async def world_objects(e0: int = Query(..., description="start in-game Elevation"),
                        e1: int = Query(..., description="end in-game Elevation"),
                        max_tokens: int = Query(WM.DEFAULT_MAX_TOKENS)):
    """Everything actionable and nothing else: dig designations clustered into rectangles with kinds, buildings with names and footprints, item wealth rolled up by type/material, and named walkable regions. This is the planning view - the whole active fort band fits in ~1,000 tokens."""
    return await world.objects(e0, e1, max_tokens=max_tokens)

@app.get("/fort-progress", operation_id="fort_progress")
async def fort_progress(e0: int = Query(-999, description="start game elevation; omit for auto fort band"),
                        e1: int = Query(-999, description="end game elevation; omit for auto fort band"),
                        max_tokens: int = Query(WM.DEFAULT_MAX_TOKENS)):
    """THE PLAY THE GAME TOOL. The Quickstart milestone ladder evaluated against your live fort: pause/labors, entry tunnel, stairwell, stockpile, meeting area, refuse pile, farm plot, wood, still, pasture, workshops, furniture, wheelbarrows, barrels, garbage dump, trade depot, bedrooms, dining, offices, smelter+forge, military - each marked [x]/[ ] with its evidence. Then NEXT gives the first unsatisfied rung with its exact key sequence (m/u/t/p/z/b-o-f-...), why it matters, a concrete coordinate box inside a real walkable region, and water/magma alerts. Play the ladder in order; that is how this game is learned."""
    e0 = None if e0 == -999 else e0
    e1 = None if e1 == -999 else e1
    return await world.progress(e0, e1, max_tokens=max_tokens)

@app.get("/world-minimap", operation_id="world_minimap")
async def world_minimap(all_levels: bool = Query(False, description="every z-level (heavy: ~120 MB of RPC replies)"),
                        span: int = Query(48, description="z-levels to render around the fort band"),
                        max_tokens: int = Query(WM.DEFAULT_MAX_TOKENS)):
    """The map as one char per 16x16 block: # solid, ' ' open, ~ water, ! magma, B building, x dig, . floor. Default = 48 levels around the fort. all_levels=true walks every z - that is ~120 MB of RPC replies and the plugin kills the socket at ~850 MB, so it is opt-in and failures are reported per window."""
    return await world.minimap(max_tokens=max_tokens, all_levels=all_levels, span=span)

@app.get("/world-delta", operation_id="world_delta")
async def world_delta(e0: int = Query(-999, description="start game elevation; omit for auto fort band"),
                      e1: int = Query(-999, description="end game elevation; omit for auto fort band"),
                      max_tokens: int = Query(WM.DEFAULT_MAX_TOKENS)):
    """Only what changed since the last world read: new/cleared designations, buildings added/removed, item count changes, water/magma changes. Call this between turns instead of re-reading the world - near-zero cost per turn over a long game."""
    e0 = None if e0 == -999 else e0
    e1 = None if e1 == -999 else e1
    return await world.delta(e0, e1, max_tokens=max_tokens)

@app.get("/tile-legend", operation_id="tile_legend")
async def tile_legend():
    """The stable map glyph alphabet (697 tiletypes -> 29 glyphs) with walk/solid semantics. Read it ONCE per session; it never changes."""
    return {"legend": WM.legend(), "glyphCount": len({WM.classify_tile(n).glyph
                                                     for n in world.dec.tile.values()} or set())}

@app.post("/world-raw", operation_id="world_raw")
async def world_raw(request: Request):
    """Dump RAW map blocks to a FILE instead of into the conversation - use this when you truly need tile-level detail, then grep the file with your file tools. Parameters: e0[int], e1[int], path[str] (optional). Returns the path and shape stats, never the tiles."""
    import json, os, time
    body = await request.json()
    e0 = int(body["e0"]); e1 = int(body["e1"])
    out = body.get("path") or os.path.join("dumps", f"blocks-{int(time.time())}.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    blocks = await world.cache.blocks(e0 + GAME_Z_OFFSET, e1 + GAME_Z_OFFSET, await world._gen())
    names = world.dec.tile
    payload = [{"x": b.map_x, "y": b.map_y, "z": b.map_z, "elev": b.map_z - GAME_Z_OFFSET,
                "tiles": [names.get(t, f"id{t}") for t in b.tiles],
                "water": list(b.water), "magma": list(b.magma), "hidden": list(b.hidden),
                "designation": list(b.tile_dig_designation),
                "buildings": len(b.buildings), "items": len(b.items)} for b in blocks]
    with open(out, "w") as f:
        json.dump(payload, f)
    return {"path": out, "blocks": len(payload), "elevations": [e0, e1],
            "fileBytes": os.path.getsize(out),
            "note": "raw tile arrays are ~147,000 tokens per elevation: read this file with grep, do not paste it here"}


# ---------------------------------------------------------------------------
# PLAYER ACTIONS - the things a player does with a menu, drivable over RPC.
#
# Measured on this build (53.17-r1) with injected SDL1 events:
#   * menu hotkeys WORK  (b, c, T, escape, y, z open menus; the UI reacts)
#   * the keyboard cursor can be toggled (`toggle-kbd-cursor`) and GetViewInfo then
#     reports a real position, BUT **no arrow/numpad/keypad event variant moves it**
#     (tested sym 273/274/275/276 and 258/262/271, which=0/1, mod shift). So menu
#     *dragging* - designating a rectangle, aiming a building - is NOT drivable.
#   * `zone` is a plugin with no CLI commands; `stockpile` is not installed; so the
#     z-menu and p-menu are UI-only in this build.
# What IS drivable is listed here: labors (the y menu), manager workorders (j-m-q),
# dig designations (m menu semantics), instant dig/build, and quickfort blueprints.
# ---------------------------------------------------------------------------

@app.post("/game-lua", operation_id="game_lua")
async def game_lua(request: Request):
    """Run a Lua EXPRESSION against the live game through DFHack's `lua` command and get its printed output back. This is the door the proto list does not cover: read anything (world.jobs.list, buildings, units, path info) and call DFHack's own APIs - reqscript("quickfort").apply_blueprint{mode="build", data="D(1x1)", pos={x=...,y=...,z=...}} places buildings, dfhack.zone / dfhack.stockpile reach zones and stockpiles. CAUTION: this writes live game state; prefer read-only prints, test on a throwaway save, and keep a save before mutating. Parameters: expression[str] (a Lua chunk; use print() to return data)"""
    body = await request.json()
    expr = str(body["expression"])
    drain_replies()
    try:
        await RunCommand(input = CoreRunCommandRequest(command = "lua", arguments = [expr]))
    except (DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e), "output": "\n".join(drain_replies())}
    await asyncio.sleep(0.6)                      # let the text frames arrive
    out = drain_replies()
    return {"success": True, "output": "\n".join(out).strip(),
            "chars": len("\n".join(out)), "note": "print() is the return channel"}

# ---------------------------------------------------------------------------
# TYPED PLAYER OPERATIONS (luaops.py). These call DFHack's Lua API *internally*.
#
# Raw Lua is deliberately NOT exposed as an MCP tool: a local model that writes one
# bad field to `df.global.world` corrupts the save, and it cannot enumerate the API.
# So each operation here takes typed parameters, composes a fixed Lua template,
# parses one printed status line, and verifies the result in the same channel.
#
# Why verification is done in Lua and not via the map read: measured on the live
# save, a depot created with dfhack.buildings.constructBuilding + completeBuild is
# present in game state (findAtTile returns it) while GetBlockList reports ZERO
# buildings on that level - and ResetMapHashes + force_reload do not change that.
# RemoteFortressReader is not a trustworthy verifier for fresh writes.
# ---------------------------------------------------------------------------

@app.post("/place-building", operation_id="place_building")
async def place_building(request: Request):
    """CREATE A BUILDING at an exact tile - no menu, no mouse, no dwarf. building: friendly name (Stockpile, Zone, Trade Depot, Bed, Table, Chair, Door, Chest, Well, Farm Plot, Still, Mechanic, Carpenter, Stoneworker, Jeweler, Smelter, Forge, Kitchen, Butcher, Weapon Rack, Axle, Gear Assembly, Water Wheel, Lever, Trap, Cage, Trackstop...); x/y int, elev GAME ELEVATION; w/h optional (a Trade Depot is 5x5, a workshop 3x3); complete:true (default) finishes it via completeBuild - pass false to queue it for a dwarf to build with real materials; material:"log"/"boulder"/"block" with count:N attaches REAL item objects found by description (measured: constructWithItems rejects an empty list, and RFR item ids do not resolve); abstract:true for material-free buildings (auto for Stockpile/Zone - the game REQUIRES one of items/filters/abstract); items:[itemId,...] for furniture and doors. Returns the game type + building id verified by findAtTile, and the game's own rejection text (e.g. 'one of items, filters or abstract is required') when it refuses. Check a site with tile_at first: placing into water fails."""
    body = await request.json()
    dec = await world._dec(await world._gen())
    try:
        res = await LO.place_building(dec, body["building"], int(body["x"]), int(body["y"]),
                                      int(body["elev"]), w=body.get("w"), h=body.get("h"),
                                      complete=bool(body.get("complete", True)),
                                      abstract=body.get("abstract"),
                                      items=body.get("items"),
                                      material=body.get("material"),
                                      count=int(body.get("count", 3)))
    except LO.LuaOpError as e:
        return {"success": False, "error": str(e)}
    world.cache.reset()
    world.forget_band()
    return {"success": True, **res}

@app.post("/remove-building", operation_id="remove_building")
async def remove_building(request: Request):
    """Deconstruct the building at a tile (x, y, elev). Verifies through dfhack.buildings."""
    body = await request.json()
    try:
        res = await LO.remove_building(int(body["x"]), int(body["y"]), int(body["elev"]))
    except LO.LuaOpError as e:
        return {"success": False, "error": str(e)}
    world.cache.reset()
    return {"success": True, **res}

@app.get("/tile-at", operation_id="tile_at")
async def tile_at(x: int = Query(...), y: int = Query(...),
                  elev: int = Query(..., description="in-game Elevation (the map panel number)")):
    """Everything that matters about ONE tile, read from the block cache: tiletype + decoded name + glyph class, water/magma, hidden, dig designation, construction material, items on it, and any building whose footprint covers it. Use it to validate a placement site before spending a build call (water and walls reject buildings)."""
    res = await world.tile_at(x, y, elev)
    # RFR is not a reliable source for freshly created buildings (measured), so the
    # authoritative check for "is a building here" is the game's own findAtTile.
    try:
        res["at_tile"] = await LO.building_at(x, y, elev)
    except LO.LuaOpError as e:
        res["at_tile"] = {"error": str(e)}
    return res

@app.get("/building-at", operation_id="building_at")
async def building_at(x: int = Query(...), y: int = Query(...),
                      elev: int = Query(..., description="in-game Elevation (the map panel number)")):
    """What building (if any) is on one tile, read straight from game state via findAtTile - use this to confirm a placement, not the map tools."""
    try:
        return await LO.building_at(x, y, elev)
    except LO.LuaOpError as e:
        return {"error": str(e)}

@app.get("/walkable-between", operation_id="walkable_between")
async def walkable_between(x0: int = Query(...), y0: int = Query(...), e0: int = Query(...),
                           x1: int = Query(...), y1: int = Query(...), e1: int = Query(...)):
    """ASK THE GAME if a creature can walk between two tiles (dfhack.maps.canWalkBetween). This is the wagon-path / accessibility question the map views cannot answer - e.g. can a caravan reach a trade depot, is a job unreachable, is a room cut off."""
    try:
        return await LO.walkable_between(x0, y0, e0, x1, y1, e1)
    except LO.LuaOpError as e:
        return {"error": str(e)}

@app.get("/job-queue", operation_id="job_queue")
async def job_queue(limit: int = Query(40)):
    """The jobs queue - RemoteFortressReader exposes NONE of it. Returns total job count and job descriptions with the acting unit. This is how you find out why nothing is happening."""
    try:
        return await LO.list_jobs(limit=limit)
    except LO.LuaOpError as e:
        return {"error": str(e)}

@app.get("/unit-counts", operation_id="unit_counts")
async def unit_counts():
    """Live unit census from game state: dwarves, citizens, hostile, pets (dead excluded). Cheaper and more accurate than reading the unit list."""
    try:
        return await LO.unit_counts()
    except LO.LuaOpError as e:
        return {"error": str(e)}

# ---------------------------------------------------------------------------
# VERTICAL MODEL - because a tunnel is defined by what is ABOVE it.
# The first play test dug a "tunnel" with overburden 0 (a surface cut) while the
# player's line at the same elevation had 2 levels of rock over it. Per-level maps
# cannot express that; columns, overburden and cross-sections can.
# ---------------------------------------------------------------------------

@app.get("/column", operation_id="column")
async def column(x: int = Query(...), y: int = Query(...),
                 e0: int = Query(None), e1: int = Query(None)):
    """The tile column at (x,y), top elevation to bottom, as compact glyphs (# wall, . floor, ' ' open, ~ water, ? hidden). This is how you read a mountain vertically: if the tiles above your dig level are '#', you are tunnelling; if they are ' ', you are trenching the surface."""
    return await world.column(x, y, e0, e1)

@app.get("/tunnel-sites", operation_id="tunnel_sites")
async def tunnel_sites(length: int = Query(12, ge=4, le=40), width: int = Query(3, ge=1, le=8),
                       min_depth: int = Query(2, ge=1, le=12),
                       near_x: int = Query(None), near_y: int = Query(None),
                       near_elev: int = Query(None),
                       dry: bool = Query(True, description="reject runs whose columns contain aquifer at/above the dig level (the game's DAMP warning)"),
                       max_tokens: int = Query(1500)):
    """Find tunnel lines that obey the player's rule: solid rock, `width` rows, `length` tiles, on one elevation, each tile at least `min_depth` BELOW its local surface (mountain stays on top), rock-sealed at the far end, with explored walkable ground at the entrance. Ranked by depth then distance to near_x/near_y/near_elev (pass the wagon or fort centre). THIS is the tool that prevents a 'tunnel' dug on the surface with nothing above it."""
    near = (near_x, near_y, near_elev) if None not in (near_x, near_y, near_elev) else None
    sites = await world.tunnel_sites(length=length, width=width, min_depth=min_depth,
                                     near=near, require_dry=dry)
    b = WM.Budget(max_tokens)
    lines = [f"tunnel sites: length>={length} width={width} min_depth={min_depth}"
             + (f" near=({near_x},{near_y},{near_elev})" if near else "")]
    for i, r in enumerate(sites, 1):
        lines.append(f"  {i}. elev{r['elev']} x{r['x0']}-{r['x1']} y{r['y0']}-{r['y1']} "
                     f"depth>={r['min_depth']} tiles={r['tiles']} entrance={r['entrance']}"
                     + (f" dist={r['dist_to_near']}" if r['dist_to_near'] is not None else ""))
    if not sites:
        lines.append("  none - try a shorter length, a smaller min_depth, or explore first "
                     "(designations cannot land on unexplored tiles)")
    for l in lines:
        b.spend(len(l) + 1)
    return WM.finish(lines, b)

@app.get("/warnings", operation_id="warnings")
async def warnings(e0: int = Query(None), e1: int = Query(None),
                   max_tokens: int = Query(1200)):
    """The game's designation-time warnings, computed for the whole current dig: DAMP (aquifer or standing water in the tile's column at/above the dig level - mining it floods the works) and HOT (magma below). Reports flagged tile counts, the bounding boxes per elevation and samples, so a dangerous dig line can be cleared with NO_DIG before any dwarf breaches it."""
    w = await world.warnings(e0, e1)
    b = WM.Budget(max_tokens)
    lines = [f"designations scanned: {w['designated_tiles']} | DAMP {w['damp_tiles']} | HOT {w['hot_tiles']}"]
    for bx in w["damp_boxes"]:
        lines.append(f"  DAMP x{bx['x0']}-{bx['x1']} y{bx['y0']}-{bx['y1']} elev{bx['elev0']}-{bx['elev1']} ({bx['tiles']} tiles) - clear with NO_DIG")
    for bx in w["hot_boxes"]:
        lines.append(f"  HOT x{bx['x0']}-{bx['x1']} y{bx['y0']}-{bx['y1']} elev{bx['elev0']}-{bx['elev1']} ({bx['tiles']} tiles) - magma below")
    for x, y, e, aq, wa in w["samples"]:
        lines.append(f"    x{x},y{y},elev{e}  aquifer{aq} water{wa}")
    if not w["damp_tiles"] and not w["hot_tiles"]:
        lines.append("  no damp/hot designations - the current dig is safe")
    for l in lines:
        b.spend(len(l) + 1)
    return WM.finish(lines, b)

# ---------------------------------------------------------------------------
# /observe - ONE composite read for the df-observer plugin (plugin/df-observer).
#
# Why it exists: the observer polls every 5-15 s and needs 6-9 signals per tick.
# Polling the human-oriented views costs the plugin (and through it, nobody's)
# context, and parsing prose into state is a trap. So this endpoint answers
# machine-shaped facts, in one round trip, with every field explicitly present
# (a `notes` list labels each "no data": absence is never a fact) and every
# elevation an IN-GAME panel number (z - 129).
#
# Read-only by construction: it touches the RFR block cache, the Lua READ
# templates, and the crash-log directory listing. No designations, no writes.
# ---------------------------------------------------------------------------

OBSERVE_SCHEMA = "df-observer/v1"

# Read-side counters: the observer plugin's poll cadence must be MEASURABLE for
# acceptance (adaptive interval: paused 60 s, running 5-15 s). Pure in-memory.
OBSERVE_STATS: dict = {"count": 0, "last_at": None, "last_paused": None, "recent": []}
OBSERVE_LUA_SETTLE = 0.8

# <gamedir> for the crash-log watch. Server-side config (env), not the plugin's.
OBSERVE_GAME_DIR = os.getenv("DFHACK_GAME_DIR", "")


OBSERVE_PILE_LUA_TEMPLATE = r"""
local want = string.split("__IDS__", " ")
local CATS = {"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods",
  "food","furniture","gems","leather","refuse","sheet","stone","weapons","wood"}
local CATCOUNTS = {"stone","wood","food","refuse","furniture","corpses","cloth","bars_blocks","gems","leather","metal"}
local piles = {}
for _, id in ipairs(want) do
  local b = nil
  for _, sp in ipairs(df.global.world.buildings.other.STOCKPILE) do
    if sp.id == tonumber(id) then b = sp break end
  end
  local rec = {id = tonumber(id), found = 0}
  if b ~= nil then
    rec.found = 1; rec.x0 = b.x1; rec.x1 = b.x2; rec.y0 = b.y1; rec.y1 = b.y2
    rec.elev = b.z - 129
    local on = {}
    for _, c in ipairs(CATS) do
      if b.settings.flags[c] == true then on[#on + 1] = c end
    end
    rec.accepts = table.concat(on, ",")
    rec.wheelbarrows = b.storage.max_wheelbarrows
    rec.barrels = b.storage.max_barrels
    rec.bins = b.storage.max_bins
    rec.counts = {}
  end
  piles[#piles + 1] = rec
end
for _, it in ipairs(df.global.world.items.other.IN_PLAY) do
  local okp, pos = pcall(function() return it.pos end)
  if okp and pos then
    local tn = "?"
    pcall(function() for k, v in pairs(df) do if v == it._type then tn = k end end end)
    for _, rec in ipairs(piles) do
      if rec.found == 1 and pos.z - 129 == rec.elev
         and pos.x >= rec.x0 and pos.x <= rec.x1
         and pos.y >= rec.y0 and pos.y <= rec.y1 then
        rec.counts.total = (rec.counts.total or 0) + 1
        for _, c in ipairs(CATCOUNTS) do
          if tn == c then rec.counts[c] = (rec.counts[c] or 0) + 1 end
        end
      end
    end
  end
end
for _, rec in ipairs(piles) do
  local parts = { {"id", rec.id}, {"found", rec.found} }
  if rec.found == 1 then
    table.insert(parts, {"x0", rec.x0}); table.insert(parts, {"x1", rec.x1})
    table.insert(parts, {"y0", rec.y0}); table.insert(parts, {"y1", rec.y1})
    table.insert(parts, {"elev", rec.elev})
    table.insert(parts, {"wheelbarrows", rec.wheelbarrows})
    table.insert(parts, {"barrels", rec.barrels}); table.insert(parts, {"bins", rec.bins})
    table.insert(parts, {"accepts", rec.accepts})
    for k, v in pairs(rec.counts) do table.insert(parts, {k, v}) end
  end
  table.sort(parts, function(a, b) return a[1] < b[1] end)
  local kv = {}
  for _, p in ipairs(parts) do kv[#kv + 1] = p[1] .. "=" .. p[2] end
  print("PILE " .. rec.id .. " " .. table.concat(kv, " "))
end
"""

def observe_pile_lua(pile_ids: list[int]) -> str:
    """The pile-health probe as ONE Lua chunk (the watch_pile.py idioms, batched).

    Prints one `PILE <id> id=.. found=0|1 x0=.. x1=.. y0=.. y1=.. elev=.. active=..
    wheelbarrows=.. barrels=.. bins=.. accepts=.. items=.. stone=.. wood=.. food=..
    refuse=.. furniture=..` line per requested id, in the requested order. Categories
    are aggregated by item-type name - the honest granularity from items.other.IN_PLAY.
    """
    ids = " ".join(str(int(i)) for i in pile_ids)
    return OBSERVE_PILE_LUA_TEMPLATE.replace("__IDS__", ids).strip()


OBSERVE_PICKS_LUA = """
-- Picks are WEAPON items with subtype id ITEM_WEAPON_PICK (measured 53.17-r1:
-- the TOOL group holds wheelbarrows/scrolls, NOT picks).
local in_play = 0
local total = 0
for _, it in ipairs(df.global.world.items.other.ANY_WEAPON) do
  local ok, key = pcall(function() return tostring(it.subtype.id) end)
  if ok and key == "ITEM_WEAPON_PICK" then
    total = total + 1
    local okp, pos = pcall(function() return it.pos end)
    if okp and pos ~= nil then in_play = in_play + 1 end
  end
end
print("PICKS " .. in_play .. " TOTAL " .. total)
"""


def observe_parse_picks(out: str):
    for line in out.splitlines():
        if line.strip().startswith("PICKS "):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def observe_parse_piles(out: str) -> dict:
    """`PILE 12 id=12 found=1 x0=.. k=v ...` lines -> {id: {k: v}} (ints where numeric)."""
    piles: dict = {}
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("PILE "):
            continue
        fields = line.split()
        rec: dict = {}
        for f in fields[2:]:
            if "=" not in f:
                continue
            k, v = f.split("=", 1)
            try:
                rec[k] = int(v)
            except ValueError:
                rec[k] = v
        try:
            piles[int(fields[1])] = rec
        except ValueError:
            continue
    return piles


def observe_simplify_survey(sv: dict) -> dict:
    """Survey counts -> positive progress state.

    to_dig = rock + hidden (never designated); dug = floor + stairs + ramp; pending =
    already_designated. Completion requires POSITIVE state (to_dig == 0 AND dug > 0):
    absence in a box of fog is NOT completion - the trap that burned us twice.
    """
    c = sv.get("counts", {})
    g = lambda k: int(c.get(k, 0))
    return {
        "elev": sv.get("elev"), "box": sv.get("box"),
        "to_dig": g("rock") + g("hidden"), "dug": g("floor") + g("stairs") + g("ramp"),
        "pending": g("already_designated"), "water": g("water"), "open": g("open"),
        "building": g("building"), "other": g("other"),
        "total_tiles": sum(g(k) for k in ("rock", "hidden", "floor", "stairs", "ramp",
                                          "water", "open", "building", "other",
                                          "already_designated")),
    }


def observe_shafts(haz: dict, shafts: list, e0: int, e1: int) -> list:
    """Per monitored shaft column: aquifer / water / magma elevations inside the window."""
    out = []
    for s in shafts:
        x, y = int(s["x"]), int(s["y"])
        rec = haz.get((x, y), {})
        water = {int(e): int(lv) for e, lv in (rec.get("water") or {}).items()
                 if e0 <= int(e) <= e1 and int(lv) > 0}
        out.append({
            "x": x, "y": y,
            "aquifer_elevs": sorted(int(e) for e in rec.get("aquifer", []) if e0 <= int(e) <= e1),
            "water_elevs": sorted(water),
            "water_levels": water,
            "magma_elevs": sorted(int(e) for e in rec.get("magma", []) if e0 <= int(e) <= e1),
        })
    return out


def observe_crashlog(dir_path: str, limit: int) -> dict:
    """Names, sizes, mtimes of crash-log files - file signals ONLY (contents are out
    of scope for the observer; deep forensics belong to a separate plugin)."""
    if not dir_path:
        return {"dir": None, "notes": ["no crash-log directory configured (DFHACK_GAME_DIR)"]}
    if not os.path.isdir(dir_path):
        return {"dir": dir_path, "notes": ["crash-log directory does not exist on this host"]}
    cand = os.path.join(dir_path, "crashlog")
    if not os.path.isdir(cand):
        cand = dir_path
    files = []
    try:
        for name in os.listdir(cand):
            p = os.path.join(cand, name)
            if not os.path.isfile(p):
                continue
            low = name.lower()
            if not (low.startswith("crash") or low.endswith(".log")):
                continue
            st = os.stat(p)
            files.append({"name": name, "bytes": int(st.st_size), "mtime": int(st.st_mtime)})
    except OSError as e:
        return {"dir": cand, "notes": [f"listing failed: {e}"], "files": []}
    files.sort(key=lambda f: -f["mtime"])
    return {"dir": cand, "files": files[:max(0, int(limit))]}


@app.get("/observe", operation_id="observe")
async def observe(q: str = Query("{}", description="JSON observe request, see docstring")):
    """One machine-shaped composite read for the df-observer plugin (schema df-observer/v1).

    q is a JSON object (all keys optional): {elev0, elev1, boxes:[[x0,y0,x1,y1,elev],...],
    shafts:[[x,y],...], pile_ids:[...], warnings:bool, facts:bool, rungs:bool, next:bool,
    reports:N, pause:bool, crashlog_files:N, survey:bool}. Everything answers IN-GAME
    ELEVATION (z - 129). Survey-derived counts are tile categories: to_dig = rock+hidden,
    dug = floor+stairs+ramp, pending = already_designated - positive state only.
    Pile rows are one batched read-Lua probe (extents, accepted categories, container
    limits, wheelbarrows, on-pile item counts by category). Shafts answer aquifer/water/
    magma elevations per column from the cached hazard map. Crash log answers file
    NAME/SIZE/MTIME only. A `notes` list labels every source that produced no data.

    This is a debug/operator endpoint (not an MCP tool): read-only, no designations,
    no builds, no commands, no game writes.
    """
    notes: list = []
    OBSERVE_STATS["count"] += 1
    OBSERVE_STATS["last_at"] = time.time()
    OBSERVE_STATS["recent"].append({"at": time.time(), "deep": bool(json.loads(q).get("rungs")) if q.strip() and q.strip().startswith("{") else False})
    OBSERVE_STATS["recent"] = OBSERVE_STATS["recent"][-60:]
    try:
        req = json.loads(q) if q.strip() else {}
    except json.JSONDecodeError as e:
        return {"schema": OBSERVE_SCHEMA, "error": f"bad q JSON: {e}"}
    if not isinstance(req, dict):
        return {"schema": OBSERVE_SCHEMA, "error": "q must be a JSON object"}

    out: dict = {"schema": OBSERVE_SCHEMA}
    try:
        await ensure_connection()
    except DFHackTransportError as e:
        return {"schema": OBSERVE_SCHEMA, "connected": False, "error": str(e),
                "notes": ["game offline: RPC connect failed"]}
    out["connected"] = True
    try:
        st = connection_state()
        out["generation"] = int(st.get("generation", -1))
    except Exception as e:                      # pragma: no cover - defensive
        notes.append(f"generation unavailable: {e}")

    if req.get("pause", True):
        try:
            out["paused"] = bool((await GetPauseState()).Value)
            OBSERVE_STATS["last_paused"] = out["paused"]
        except (DFHackError, DFHackTransportError) as e:
            out["paused"] = None
            notes.append(f"pause state unavailable: {e}")

    e0 = req.get("elev0"); e1 = req.get("elev1")
    if e0 is None or e1 is None:
        z_lo, z_hi = await world.fort_band()
        e0 = z_lo - GAME_Z_OFFSET if e0 is None else int(e0)
        e1 = z_hi - GAME_Z_OFFSET if e1 is None else int(e1)
    out["elev0"], out["elev1"] = int(e0), int(e1)

    levels = facts = None
    want_facts = bool(req.get("facts", True)) or bool(req.get("rungs", True))
    if want_facts:
        try:
            gen = await world._gen()
            dec = await world._dec(gen)
            levels = await world.levels(int(e0), int(e1))
            facts = WM.FortFacts(levels, await WM.load_units(dec, gen, world._state), dec)
            await world.annotate_depth(facts)
        except Exception as e:                  # cache/RFR/lua failures label, not crash
            notes.append(f"facts unavailable: {e}")
    if facts is not None:
        out["facts"] = {
            "pop": facts.pop, "soldiers": facts.soldiers, "hostile": facts.hostile,
            "miners": facts.miners, "hunters": facts.hunters,
            "food": facts.food, "drink": facts.drink, "wood": facts.wood,
            "stone": facts.stone, "metal": facts.metal, "barrels": facts.barrels,
            "pending_dig": facts.designations, "underground_floors": getattr(facts, "underground_floors", 0),
            "water_tiles": facts.water_tiles, "magma_tiles": facts.magma_tiles,
            "buildings_total": len(facts.buildings),
        }
        if req.get("buildings", True):
            roll = Counter(b["name"] for b in facts.buildings)
            out["buildings"] = [{"name": k, "count": v} for k, v in sorted(roll.items(),
                                                                           key=lambda kv: -kv[1])[:40]]
    if facts is not None and req.get("rungs", True):
        rungs = [{"id": rid, "title": rung, "done": bool(done(facts)), "keys": keys, "why": why}
                 for (rid, rung, done, keys, why) in WM._LADDER]
        out["rungs"] = rungs
        missing = [r for r in rungs if not r["done"]]
        if missing:
            nxt = missing[0]
            entry = {"id": nxt["id"], "title": nxt["title"], "keys": nxt["keys"], "why": nxt["why"]}
            want = {"farm": (5, 5), "meeting": (7, 7), "stockpile": (11, 11), "bedrooms": (9, 9),
                    "workshops": (9, 9), "depot": (5, 5), "drinks": (5, 5)}.get(nxt["id"], (7, 7))
            room = WM.suggest_room(facts, want[0], want[1],
                                   prefer_elev=facts.levels[len(facts.levels) // 2].elev
                                   if facts.levels else None)
            if room:
                entry["at"] = room
            out["next"] = entry
        else:
            out["next"] = None
    if facts is None and req.get("rungs", True):
        notes.append("rungs not computed (facts unavailable)")

    if req.get("survey", True):
        boxes = []
        for b in req.get("boxes", [])[:16]:
            (x0, y0, x1, y1) = (int(v) for v in b[:4])
            e = int(b[4]) if len(b) > 4 and b[4] is not None else None
            if e is None:
                e = (int(e0) + int(e1)) // 2
            try:
                sv = await world.survey(e, x0, y0, x1, y1)
                boxes.append(observe_simplify_survey(sv))
            except Exception as e:              # label per-box failure, keep the rest
                boxes.append({"elev": e, "box": [x0, y0, x1, y1], "error": str(e)})
                notes.append(f"survey x{x0}-{x1} y{y0}-{y1} elev{e} unavailable: {e}")
        out["boxes"] = boxes
        if not req.get("boxes"):
            notes.append("no boxes requested: per-level dig progress unknown")

    if req.get("warnings", True):
        try:
            w = await world.warnings(int(e0), int(e1))
            out["warnings"] = {"designated_tiles": w["designated_tiles"],
                               "damp_tiles": w["damp_tiles"], "hot_tiles": w["hot_tiles"],
                               "damp_boxes": w["damp_boxes"][:6], "hot_boxes": w["hot_boxes"][:6],
                               "samples": w["samples"][:6]}
        except Exception as e:
            out["warnings"] = None
            notes.append(f"warnings unavailable: {e}")

    shafts = req.get("shafts", [])[:16]
    if shafts:
        try:
            haz = await world.hazard_map(int(e0), int(e1))
            out["shafts"] = observe_shafts(haz, [{"x": s[0], "y": s[1]} for s in shafts],
                                           int(e0), int(e1))
        except Exception as e:
            out["shafts"] = None
            notes.append(f"shaft hazard map unavailable: {e}")
    else:
        out["shafts"] = []
        notes.append("no shafts requested: monitored columns not checked")

    picks = None
    if req.get("picks", True):
        try:
            picks = observe_parse_picks(await LO._lua(OBSERVE_PICKS_LUA, settle=OBSERVE_LUA_SETTLE))
        except LO.LuaOpError as e:
            notes.append(f"picks probe unavailable: {e}")
    out["picks"] = picks
    if picks is None:
        notes.append("picks probe produced no number: pick count UNKNOWN (not 0)")

    pile_ids = [int(i) for i in req.get("pile_ids", [])[:16]]
    if pile_ids:
        try:
            parsed = observe_parse_piles(await LO._lua(observe_pile_lua(pile_ids),
                                                        settle=OBSERVE_LUA_SETTLE))
            out["piles"] = parsed
            missing = [i for i in pile_ids if i not in parsed]
            if missing:
                notes.append(f"pile probe printed no row for ids {missing}: pile health unknown")
        except LO.LuaOpError as e:
            out["piles"] = None
            notes.append(f"pile probe unavailable: {e}")
    else:
        out["piles"] = {}
        notes.append("no pile_ids requested: pile health unknown")

    n_rep = int(req.get("reports", 0) or 0)
    if n_rep > 0:
        try:
            status = await GetReports()
            reps = [{"year": int(r.year), "time": int(r.time), "text": str(r.text)[:160],
                     "z": int(r.pos.z) - GAME_Z_OFFSET} for r in list(status.reports)[-n_rep:]]
            out["reports"] = reps[-12:]
        except (DFHackError, DFHackTransportError) as e:
            out["reports"] = None
            notes.append(f"reports unavailable: {e}")
    else:
        out["reports"] = []

    n_cr = int(req.get("crashlog_files", 0) or 0)
    if n_cr > 0:
        out["crashlog"] = observe_crashlog(req.get("crashlog_dir") or OBSERVE_GAME_DIR, n_cr)
    else:
        out["crashlog"] = None

    out["notes"] = notes
    return out


@app.get("/observe-stats", operation_id="observe_stats")
async def observe_stats():
    """Read-side poll counters for the df-observer plugin: total /observe calls, the last call time,
    the last observed pause state, and a rolling window of recent call timestamps (deep calls
    flagged). Acceptance measurement only: no game data, no writes."""
    return {"count": OBSERVE_STATS["count"], "last_at": OBSERVE_STATS["last_at"],
            "last_paused": OBSERVE_STATS["last_paused"], "recent": OBSERVE_STATS["recent"][-40:],
            "window_seconds": 60 if OBSERVE_STATS["recent"] else 0}


@app.get("/wagon-path", operation_id="wagon_path")
async def wagon_path(x: int = Query(...), y: int = Query(...), elev: int = Query(...)):
    """Can a wagon get from this tile to the map edge? BFS over floors and ramps with diagonals, EXCLUDING stairs (wagons refuse them). Reports reachable tile count and the edge tiles reached - this is how the trade depot rung gets verified as a road, not declared from a building check."""
    return await world.wagon_path(x, y, elev)

@app.get("/dig-reachable", operation_id="dig_reachable")
async def dig_reachable(x: int = Query(...), y: int = Query(...), elev: int = Query(...),
                        from_x: int = Query(..., description="a dwarf-occupied tile, e.g. the wagon"),
                        from_y: int = Query(...),
                        from_elev: int = Query(..., description="an in-game Elevation, e.g. the wagon's")):
    """Can a dwarf actually work this tile? There is NO connectivity oracle on this RPC surface (measured: getWalkableGroup is uncallable, canWalkBetween throws for adjacent stair tiles), so the layer computes a 3D walk graph over explored tiles with stair/ramp z-links. A designation with no walkable neighbour in the dwarves' component is CANCELLED SILENTLY by the game - run this before ordering a dig, and order a band middle-first so each dug tile becomes the access point for the next."""
    return await world.dig_reachable(x, y, elev, from_x, from_y, from_elev)

@app.get("/reach-map", operation_id="reach_map")
async def reach_map(e0: int = Query(None), e1: int = Query(None),
                    max_tokens: int = Query(900)):
    """Summary of our computed 3D walk components: sizes and which elevations they touch. Use it to see which pockets of the fort are cut off - a stockpile in a component the dwarves cannot reach is decoration."""
    r = await world.reach_map(e0, e1)
    comp = r["components"]
    sizes: dict = {}
    for (x, y, z), c in comp.items():
        sizes[c] = sizes.get(c, 0) + 1
    b = WM.Budget(max_tokens)
    lines = [f"walk components: {r['count']} over {len(comp)} explored walkable tiles, elev {r['elev_range'][0]}-{r['elev_range'][1]}"]
    for c, n in sorted(sizes.items(), key=lambda kv: -kv[1])[:12]:
        pts = [(x, y, z) for (x, y, z), cc in comp.items() if cc == c]
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; zs = [p[2] for p in pts]
        lines.append(f"  comp {c}: {n} tiles x{min(xs)}-{max(xs)} y{min(ys)}-{max(ys)} elev{min(zs)}-{max(zs)}")
    for l in lines:
        b.spend(len(l) + 1)
    return WM.finish(lines, b)

@app.get("/probe-plan", operation_id="probe_plan")
async def probe_plan(x: int = Query(...), y: int = Query(...),
                     elev0: int = Query(..., description="lowest elevation to consider"),
                     elev1: int = Query(..., description="highest elevation to consider"),
                     min_depth: int = Query(2, ge=1, le=12)):
    """Rank the elevations in ONE column for a probe dig - the way a player beats fog. Plans change mid-game: dig a short 1-wide adit, read the strata, then go deeper / shallower / elsewhere. Per elevation it reports glyph, depth under the local surface, and a verdict: probe here (solid, deep enough), avoid (damp: aquifer or water in the column at/above - digging floods the works), shallow (surface cut), fog (designation rejected), or open air."""
    rows = await world.probe_plan(x, y, elev0, elev1, min_depth=min_depth)
    b = WM.Budget(1500)
    lines = [f"probe plan at x{x},y{y}, elevations {elev1} down to {elev0} "
             f"(all elevations are in-game panel numbers)"]
    for r in rows:
        lines.append(f"  elev{r['elev']}  [{r['glyph']}] depth={r['depth']} - {r['verdict']}")
    for l in lines:
        b.spend(len(l) + 1)
    return WM.finish(lines, b)

@app.get("/hazards", operation_id="hazards")
async def hazards(x0: int = Query(...), y0: int = Query(...),
                  x1: int = Query(...), y1: int = Query(...),
                  elev: int = Query(..., description="in-game Elevation you plan to dig at"),
                  max_tokens: int = Query(2000)):
    """DAMP and HOT for a rectangle at a dig elevation, the way the game warns a player. damp = aquifer at/below the dig level in that column, or standing water; hot = magma within range below. Digging into an aquifer floods the fort; digging into the magma crust brings lava. Reports counts, the worst tiles, and the aquifer/magma elevations so you can route around, under, or over it."""
    z0, z1 = await world.fort_band()
    hz = await world.hazard_map(z0 - WM.GAME_Z_OFFSET, z1 - WM.GAME_Z_OFFSET)
    b = WM.Budget(max_tokens)
    lines = [f"hazards at elev {elev} x{x0}-{x1} y{y0}-{y1}"]
    damp = hot = wet = clean = 0
    worst = []
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            rec = hz.get((x, y), {"aquifer": [], "water": {}, "magma": [], "salt": []})
            aq = [e for e in rec["aquifer"] if e >= elev]
            wa = {e: v for e, v in rec["water"].items() if e >= elev}
            mg = [e for e in rec["magma"] if e <= elev]
            d = bool(aq) or bool(wa)
            if d:
                damp += 1
            if mg:
                hot += 1
            if not d and not mg:
                clean += 1
            if d and len(worst) < 6:
                worst.append(f"  DAMP x{x},y{y}: aquifer@{aq[:4]} water@{sorted(wa.items())[:3]}")
    lines.append(f"  damp tiles: {damp} | magma-below tiles: {hot} | clean: {clean}")
    lines.extend(worst)
    for l in lines:
        b.spend(len(l) + 1)
    return WM.finish(lines, b)

@app.get("/magma-depth", operation_id="magma_depth")
async def magma_depth(x: int = Query(...), y: int = Query(...),
                      elev: int = Query(..., description="dig elevation"),
                      e_min: int = Query(-120), step: int = Query(2)):
    """Distance down to magma from a dig point - the HOT warning as a number. The magma crust sits far below the fort band, so this scans downward in coarse windows and returns the first magma elevation and how many levels below your dig it is."""
    return await world.magma_depth_below(x, y, elev, e_min=e_min, step=step)

@app.post("/make-stockpile", operation_id="make_stockpile")
async def make_stockpile(body: dict):
    """Create a multi-tile stockpile and set it up correctly in ONE call: size + category flags + the material arrays the game's custom-settings UI walks + container limits. Every new pile should come through here, so no pile is ever left in the state that crashes the Custom button."""
    try:
        dec = await world._dec(await world._gen())
        return await LO.make_stockpile(
            dec, int(body["x"]), int(body["y"]), int(body["elev"]), int(body["w"]), int(body["h"]),
            allow=body.get("allow"), deny=body.get("deny"),
            all_categories=bool(body.get("all_categories", False)),
            max_wheelbarrows=body.get("max_wheelbarrows"),
            max_barrels=body.get("max_barrels"), max_bins=body.get("max_bins"))
    except LO.LuaOpError as e:
        return {"success": False, "error": str(e)}


@app.post("/create-stockpile", operation_id="create_stockpile")
async def create_stockpile(body: dict):
    """Create a multi-tile stockpile (abstract -> setSize -> completeBuild). Use this, not place_building, for any pile bigger than 1 tile; then set its categories with /configure-stockpile."""
    try:
        dec = await world._dec(await world._gen())
        return await LO.create_stockpile(dec, int(body["x"]), int(body["y"]), int(body["elev"]),
                                         int(body["w"]), int(body["h"]))
    except LO.LuaOpError as e:
        return {"success": False, "error": str(e)}


@app.post("/resize-building", operation_id="resize_building")
async def resize_building(body: dict):
    """Resize a building - stockpiles included - with the game's own setSize. constructBuilding with nominal=true makes a 1x1 stockpile no matter what w/h you pass (measured), so big piles need this call afterwards."""
    try:
        dec = await world._dec(await world._gen())
        return await LO.resize_building(dec, int(body["building_id"]), int(body["w"]), int(body["h"]))
    except LO.LuaOpError as e:
        return {"success": False, "error": str(e)}


@app.post("/repair-stockpile-settings", operation_id="repair_stockpile_settings")
async def repair_stockpile_settings(body: dict):
    """Allocate the per-category material arrays the game's stockpile custom-settings UI walks. Writing only settings.flags leaves them size 0 while the category flag is true, and the UI then dereferences an empty vector - that is the crash (SIGSEGV at 0xa in render_things with overlay on the stack) the player hit by clicking Custom on a Lua-configured pile. Call this after configure-stockpile; the acceptance test is the custom button opening."""
    try:
        dec = await world._dec(await world._gen())
        return await LO.repair_stockpile_settings(dec, int(body["building_id"]))
    except LO.LuaOpError as e:
        return {"success": False, "error": str(e)}


@app.post("/configure-stockpile", operation_id="configure_stockpile")
async def configure_stockpile(body: dict):
    """Set a stockpile's accepted categories, container limits and wheelbarrows.

    Body: {"building_id": 12, "allow": ["wood"], "deny": ["stone"], "all_categories": false,
    "max_wheelbarrows": 3, "max_barrels": 0, "max_bins": 0}. The game's own category names
    are: ammo, animals, armor, bars_blocks, cloth, coins, corpses, finished_goods, food,
    furniture, gems, leather, misc, refuse, sheet, stone, weapons, wood. all_categories=true
    with a deny list is the general pile (take everything except stone and wood, per the
    quickstart). A wood pile near the entrance is allow=["wood"].
    """
    try:
        dec = await world._dec(await world._gen())
        return await LO.configure_stockpile(
            dec, int(body["building_id"]), allow=body.get("allow"), deny=body.get("deny"),
            all_categories=bool(body.get("all_categories", False)),
            max_wheelbarrows=body.get("max_wheelbarrows"),
            max_barrels=body.get("max_barrels"), max_bins=body.get("max_bins"))
    except LO.LuaOpError as e:
        return {"success": False, "error": str(e)}


@app.get("/survey", operation_id="survey")
async def survey(elev: int = Query(...), x0: int = Query(...), y0: int = Query(...),
                 x1: int = Query(...), y1: int = Query(...)):
    """Pre-flight check before designating a box: what is on every tile. NEVER designate over stuff - floors, stairs, ramps, buildings, items and existing designations are not rock. Returns counts per category, safe_to_designate, the blocked set, and samples, so a plan gets filtered before a single designation is sent."""
    return await world.survey(elev, x0, y0, x1, y1)

@app.get("/depth-profile", operation_id="depth_profile")
async def depth_profile(elev: int = Query(..., description="in-game Elevation you plan to dig at"),
                        x0: int = Query(...), x1: int = Query(...),
                        y0: int = Query(...), y1: int = Query(...),
                        min_tokens: int = Query(0), max_tokens: int = Query(2000)):
    """Depth under the local surface for every tile in a rectangle: the mountain-on-top check, tile by tile. depth 0 = you would be trenching the surface; depth >= 2 = a tunnel. Reports each tile's glyph and depth so a plan can be judged before it is designated, and the ceiling verified after digging."""
    z0, z1 = await world.fort_band()
    surf = await world.surface_map(z0 - WM.GAME_Z_OFFSET, z1 - WM.GAME_Z_OFFSET)
    b = WM.Budget(max_tokens)
    # Count FIRST, print the verdict FIRST. A 131x21 box at 400 tokens used to emit the
    # per-tile samples, hit the cap, and the caller never saw the deep/shallow/unexplored
    # summary - the verdict is the answer, the samples are evidence.
    shallow = 0; deep = 0; unknown = 0
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            top = surf.get((x, y))
            d = (top - elev) if top is not None else None
            if d is None:
                unknown += 1
            elif d < 2:
                shallow += 1
            else:
                deep += 1
    lines = [f"depth under surface at elev {elev} "
             f"x{x0}-{x1} y{y0}-{y1}",
             f"  tiles: deep(>=2)={deep} shallow(<2)={shallow} unexplored={unknown}"]
    xstep = max(1, (x1 - x0 + 1) // 6)
    ystep = max(1, (y1 - y0 + 1) // 8)
    for y in range(y0, y1 + 1, ystep):
        for x in range(x0, x1 + 1, xstep):
            top = surf.get((x, y))
            lines.append(f"  x{x},y{y}: surface={top} "
                         f"depth={(top - elev) if top is not None else None}")
    for l in lines:
        b.spend(len(l) + 1)
    return WM.finish(lines, b)

@app.get("/overburden", operation_id="overburden")
async def overburden(x: int = Query(...), y: int = Query(...),
                     elev: int = Query(..., description="game elevation to dig at")):
    """Count of NON-AIR levels above (x,y,elev): the tunnel-vs-trench test. overburden 0 = surface cut, 1 = shallow, >=2 = a real tunnel with mountain on top. overburden_unknown counts hidden tiles above, which you must not plan against."""
    return await world.overburden(x, y, elev)

@app.get("/cross-section", operation_id="cross_section")
async def cross_section(axis: str = Query("y", description="'y' fixes y=at and walks x; 'x' fixes x=at and walks y"),
                        at: int = Query(...), a0: int = Query(...), a1: int = Query(...),
                        e0: int = Query(None), e1: int = Query(None),
                        max_tokens: int = Query(2000)):
    """A vertical slice through the map - the only compact way to SEE the mountain. Rows are elevations top-down, so rock over a candidate dig line is visible as a column of '#'. Use it before designating a tunnel, and after digging, to prove the ceiling is intact."""
    res = await world.cross_section(axis, at, a0, a1, e0, e1)
    b = WM.Budget(max_tokens)
    lines = [res["head"]] + res["rows"]
    kept = []
    for l in lines:
        if b.allow(len(l) + 1):
            b.spend(len(l) + 1)
            kept.append(l)
        else:
            b.note("lines", 1)
    return WM.finish(kept, b)

@app.get("/labor-list", operation_id="labor_list")
async def labor_list():
    """Every labor name you can pass to set_unit_labors (the DFHack unit_labor enum, decoded from the game). Use these exact strings - MINING, MASON, CARPENTER, PLANT, IRRIGATE, FARMER, CRAFTSMAN, TRANSPORT, ..."""
    dec = await world._dec(await world._gen())
    labors = dec.flags.get("unit_labor", {})
    return {"count": len(labors), "labors": sorted(labors.values())}

@app.post("/set-unit-labors", operation_id="set_unit_labors")
async def set_unit_labors(request: Request):
    """THE LABOR MENU (`y`), driven. changes: [{unit_id, labor, value}] where labor is a name from labor_list (MINE, MASON, ...) or a labor id, value true/false. This is how the fort gets miners: SetUnitLabors is RPC, so no cursor is involved. Verify with world_objects/look: the `miners` count in the ladder header is read from the same labors."""
    body = await request.json()
    dec = await world._dec(await world._gen())
    by_name = {v: k for k, v in dec.flags.get("unit_labor", {}).items()}
    changes, bad = [], []
    for ch in body["changes"]:
        lab = ch["labor"]
        lid = int(lab) if isinstance(lab, int) or (isinstance(lab, str) and lab.isdigit()) \
             else by_name.get(str(lab).upper())
        if lid is None:
            bad.append(lab)
            continue
        changes.append(UnitLaborState(unit_id=int(ch["unit_id"]), labor=lid,
                                      value=bool(ch["value"])))
    if not changes:
        return {"error": "no valid labors", "unknown": bad,
                "hint": "see labor_list for the exact names"}
    try:
        await SetUnitLabors(input=SetUnitLaborsIn(change=changes))
    except (DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e), "applied": 0,
                "hint": "labor ids come from labor_list; unit ids from get_unit_list"}
    world._state.pop("units_gen", None)          # labors changed: drop the cached rollup
    return {"applied": len(changes), "unknown": bad, "success": True}

@app.post("/work-order", operation_id="work_order")
async def work_order(request: Request):
    """THE MANAGER'S WORK-ORDER SCREEN (`j-m-q`), driven. Body: {"job":"MakeBarrel","amount":5} (translated to the JSON form this build requires) or raw {"json":"{\"job\":\"MilkCreature\"}"}. Jobs are df.job_type names (MakeCharcoal, MakeTable, MakeChair, MakeBarrel, MakeRockBag, SmeltOre, FurnaceSmelt, CraftBoneCarving, ...). Use this to keep blocks/barrels/beds in production - the usual reason a planned building never gets built. Verify with the server log: it prints "Queuing <job> xN"."""
    body = await request.json()
    # Verified live: this build's workorder.lua parses its FIRST argument as JSON, so the
    # documented `workorder <jobtype> <amount>` positional form fails with
    # "can't parse JSON at byte 1 of: CollectRock 10". Build the JSON form ourselves.
    if "json" in body:
        spec = body["json"] if isinstance(body["json"], str) else json.dumps(body["json"])
    else:
        if "job" not in body:
            return {"error": "give job (a df.job_type name) or json"}
        spec_obj = {"job": str(body["job"])}
        if "amount" in body:
            spec_obj["amount_total"] = int(body["amount"])
        for k in ("material", "item_type", "quantity"):
            if k in body:
                spec_obj[k] = body[k]
        spec = json.dumps(spec_obj)
    args = [spec]
    try:
        await RunCommand(input=CoreRunCommandRequest(command="workorder", arguments=args))
    except DFHackError as e:
        return {"success": False, "error": str(e),
                "hint": "job name must be a df.job_type; 'workorder -l <filter>' lists types (its output streams to the server log)"}
    return {"success": True, "args": args,
            "note": "order enqueued; workorder's console reply streams to this server's log"}

@app.get("/world-cache-reset", operation_id="world_cache_reset")
async def world_cache_reset():
    """Drop the world-model caches (block cache, decode tables, fort band, delta snapshot). Call after a big in-game change such as `reveal`, a save reload, or a fort redesign."""
    world.cache.reset()
    world.forget_band()
    world._state.clear()
    world._prev.clear()
    # DFHack caches map data server-side; ask it to drop its hashes too, or the next
    # read can be served from stale block data after a big in-game change.
    try:
        await ResetMapHashes()
        reset = "cache + DFHack map hashes"
    except Exception as e:
        reset = f"cache only (ResetMapHashes failed: {e})"
    return {"reset": True, "scope": reset}


mcp = FastApiMCP(
    app,
    name="Dwarf Fortress MCP Server",
    description="A MCP server that uses the dfhack_client_python package to connect to a locally running Dwarf Fortress game through DFHack's RPC connection.",
    # game_lua stays an HTTP debug endpoint only. Raw Lua in the tool list invites a
    # local model to write arbitrary game state; the typed operations in luaops.py are
    # the supported way in.
    # observe is the df-observer plugin's composite read (machine-shaped JSON, ~2-4k
    # tokens): a plugin transport, not a model tool - it stays HTTP-only too.
    exclude_operations=["game_lua", "observe", "observe_stats"]
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
