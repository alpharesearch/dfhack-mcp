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
* Game elevation = RPC z - 129 on this save (WM.GAME_Z_OFFSET; measured on the live map:
  the wagon reads z=167 in DFHack `position` and the panel says Elevation 38, and a stair
  sent at z=166 registered on Elevation 37. get_map_info publishes the offset as gameZOffset.
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
    a '#build' apply passed -c 108,9,165 produced a building at x106,y7 z167 (ELEV 38):
    z lands +2 from what is sent. To target elevation E, send z = E + 129 - 2.
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
import os
import shlex
import shutil
import time
from collections import Counter
from contextlib import asynccontextmanager
from typing import Any
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi_mcp import FastApiMCP
from fastapi_mcp.transport.http import FastApiHttpSessionManager
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import BaseModel, ConfigDict, Field
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
import fortplan as FP

MCP_STATELESS_HTTP = os.getenv("DFHACK_MCP_STATELESS_HTTP", "1").strip().lower() not in ("0", "false", "no")

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


# ---------------------------------------------------------------------------
# PYDANTIC REQUEST SCHEMAS (give FastApiMCP exact JSON Schemas for every POST tool)
# ---------------------------------------------------------------------------

async def _as_dict(payload: Any) -> dict:
    """Normalize a Pydantic request model, raw dict, or test Request stub into a dict."""
    if isinstance(payload, BaseModel):
        return payload.model_dump(by_alias=True, exclude_none=True)
    if isinstance(payload, dict):
        return payload
    if hasattr(payload, "json") and callable(payload.json):
        res = payload.json()
        return (await res) if asyncio.iscoroutine(res) else res
    return dict(payload)


class ListUnitsRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    unit_ids: list[int] = Field(..., description="Unit IDs to fetch details for (from look or get_unit_list)")


class RunCommandRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    command: str = Field(..., description="Allowlisted DFHack command name (e.g. 'autochop', 'buildingplan', 'suspendmanager', 'warn-stranded', 'locate-ore', 'list-waves', 'help'); raw lua/script and destructive commands are denied")
    arguments: list[str] = Field(default_factory=list, description="Arguments to pass to the DFHack command")


class PassKeyboardEventRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    key: str = Field(..., description="Key name: up/down/left/right/enter/escape/pageup/pagedown/home/end/tab/space/backspace/a-z/0-9")
    repeat: int = Field(1, ge=1, le=500, description="Number of times to send the key press")
    hold: int | None = Field(None, description="Optional: send N presses without releases")


class ApplyBlueprintRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = Field(..., description="Blueprint file name (written to deployables/blueprints/<name>.csv)")
    grid: list[list[str]] = Field(..., description="2D grid of rows of quickfort cell keys (e.g. [['d','d'],['d','d']])")
    cursor: list[int] = Field(..., description="[x, y, z] where the top-left cell should land (z = elev + 129)")
    mode: str = Field("dig", description="'dig' (#dig modeline) or 'build' (#build modeline)")


class SetPauseStateRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    paused: bool = Field(..., description="True to pause the game, False to unpause")


class CutTreesRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    x0: int = Field(..., description="Left X coordinate of rectangle")
    y0: int = Field(..., description="Top Y coordinate of rectangle")
    x1: int = Field(..., description="Right X coordinate of rectangle")
    y1: int = Field(..., description="Bottom Y coordinate of rectangle")
    elev: int = Field(..., description="In-game Elevation (panel number)")
    limit: int = Field(0, ge=0, description="Optional max number of tree trunks to designate (0 = all in box)")


class DigBox(BaseModel):
    model_config = ConfigDict(extra="allow")
    x0: int = Field(..., description="Start X tile coordinate (inclusive)")
    y0: int = Field(..., description="Start Y tile coordinate (inclusive)")
    x1: int = Field(..., description="End X tile coordinate (inclusive)")
    y1: int = Field(..., description="End Y tile coordinate (inclusive)")
    elev: int = Field(..., description="In-game Elevation (map panel number; converted internally via z = elev + 129)")
    elev1: int | None = Field(None, description="Optional end in-game Elevation (inclusive) to span multiple levels")
    designation: str | None = Field(None, description="Optional per-box designation override (DEFAULT_DIG, UP_DOWN_STAIR_DIG, CHANNEL_DIG, RAMP_DIG, DOWN_STAIR_DIG, UP_STAIR_DIG, NO_DIG)")


class StairShaftSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    x0: int = Field(..., description="Shaft start X coordinate (inclusive)")
    y0: int = Field(..., description="Shaft start Y coordinate (inclusive)")
    x1: int | None = Field(None, description="Shaft end X coordinate (inclusive; defaults to x0, or x0+1 for a 2x2 shaft)")
    y1: int | None = Field(None, description="Shaft end Y coordinate (inclusive; defaults to y0, or y0+1 for a 2x2 shaft)")
    from_elev: int = Field(..., description="Starting in-game Elevation (top or bottom of shaft)")
    to_elev: int = Field(..., description="Target in-game Elevation (bottom or top of shaft)")
    connect_above: bool = Field(False, description="If True, the top elevation uses UP_DOWN_STAIR_DIG instead of DOWN_STAIR_DIG (use when extending an existing shaft downward)")
    continue_below: bool = Field(False, description="If True, the bottom elevation uses UP_DOWN_STAIR_DIG instead of UP_STAIR_DIG (use when you plan to extend deeper later)")


class SendDigCommandRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    designation: str = Field("DEFAULT_DIG", description="One of DEFAULT_DIG, UP_DOWN_STAIR_DIG, CHANNEL_DIG, RAMP_DIG, DOWN_STAIR_DIG, UP_STAIR_DIG, NO_DIG (or STAIR_SHAFT with box)")
    locations: list[list[int]] | None = Field(None, description="Explicit list of [x, y, z] triples where z = elevation + 129 (GAME_Z_OFFSET)")
    box: DigBox | None = Field(None, description="Single rectangle in in-game Elevation coordinates {x0, y0, x1, y1, elev, elev1?}")
    boxes: list[DigBox] | None = Field(None, description="List of rectangles in in-game Elevation coordinates; middle-first ordering is applied automatically per box")
    stair_shaft: StairShaftSpec | None = Field(None, description="Multi-z stairwell helper: automatically designates DOWN_STAIR_DIG at the top elevation, UP_DOWN_STAIR_DIG on every intermediate elevation, and UP_STAIR_DIG at the bottom elevation")
    middle_first: bool = Field(True, description="For box/boxes: order the center spine of the rectangle before the flanks so miners carve an access path into fog")
    safe: bool = Field(True, description="Filter out already-dug floors, stairs, ramps, buildings, water, and aquifer tiles before designating DEFAULT_DIG (default True; set False to override)")
    swap: bool = Field(False, description="Legacy x/y swap flag (default False)")


class GetBlockListRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    min_z: int
    max_z: int
    census_top: int = 8


class WorldRawRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    e0: int
    e1: int
    path: str | None = None


class GameLuaRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    expression: str


class PlaceBuildingRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    building: str = Field(..., description="Friendly building name (Still, Carpenter, Mason, Mechanic, Jeweler, Craftsdwarf's Workshop, Smelter, Forge, Farm Plot, Trade Depot, Bed, Table, Chair, Door, Chest, Well, Lever, Bridge, Floodgate...)")
    x: int = Field(..., description="Top-left X tile coordinate")
    y: int = Field(..., description="Top-left Y tile coordinate")
    elev: int = Field(..., description="In-game Elevation (map panel number)")
    w: int = Field(1, ge=1, le=31, description="Footprint width in tiles (e.g. 3 for a 3x3 workshop, 5 for a Trade Depot)")
    h: int = Field(1, ge=1, le=31, description="Footprint height in tiles (e.g. 3 for a 3x3 workshop, 4 for a 3x4 Still)")
    complete: bool = Field(True, description="For 1x1 buildings: complete immediately via completeBuild (default True)")
    abstract: bool | None = Field(None, description="Material-free abstract building flag (auto-detected for Stockpile/Zone)")
    items: list[int] | None = Field(None, description="Specific item IDs to attach for furniture/doors")
    material: str | None = Field(None, description="Material description substring to attach closest in-play items (e.g. 'log', 'boulder', 'block')")
    count: int = Field(3, ge=1, le=20, description="Number of matching material items to attach when material is set")
    nominal: bool = Field(True, description="Use the canonical footprint (measured: a 1x1 request with nominal=true SNAPS to 3x3 for workshops). Set FALSE to get the exact w x h footprint - farm plots do not snap, so a 3x3 plot needs nominal=false (tool_missing #57)")


class RemoveBuildingRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    building_id: int | None = Field(None, description="Building ID from list_stockpiles, list_zones, or place_building")
    x: int | None = Field(None, description="Tile X coordinate (alternative to building_id)")
    y: int | None = Field(None, description="Tile Y coordinate (alternative to building_id)")
    elev: int | None = Field(None, description="In-game Elevation (alternative to building_id)")


class CreateStockpileRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    x: int = Field(..., description="Top-left X tile coordinate")
    y: int = Field(..., description="Top-left Y tile coordinate")
    elev: int = Field(..., description="In-game Elevation (map panel number)")
    w: int = Field(..., ge=1, le=31, description="Width in tiles")
    h: int = Field(..., ge=1, le=31, description="Height in tiles")
    allow: list[str] | None = Field(None, description="Optional categories to enable immediately: ammo, animals, armor, bars_blocks, cloth, coins, corpses, finished_goods, food, furniture, gems, leather, refuse, sheet, stone, weapons, wood")
    deny: list[str] | None = Field(None, description="Optional categories to disable (useful with all_categories=True for a general pile)")
    all_categories: bool = Field(False, description="Enable all 17 categories (except those in deny)")
    max_wheelbarrows: int | None = Field(None, ge=0, description="Optional wheelbarrow limit (e.g. 3 for a stone pile)")
    max_barrels: int | None = Field(None, ge=0, description="Optional barrel limit")
    max_bins: int | None = Field(None, ge=0, description="Optional bin limit")


class ResizeBuildingRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    building_id: int
    w: int
    h: int


class RemoveZoneRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    building_id: int = Field(..., description="Target zone ID")
    acknowledge_risk: bool = Field(False, description="Must be True: zone removal mutates unit refs and has crashed this fort once")


class BuildingIdRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    building_id: int = Field(..., description="Target building ID")


class ConfigureStockpileRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    building_id: int = Field(..., description="Stockpile building ID from list_stockpiles or create_stockpile")
    allow: list[str] | None = Field(None, description="Categories to enable: ammo, animals, armor, bars_blocks, cloth, coins, corpses, finished_goods, food, furniture, gems, leather, refuse, sheet, stone, weapons, wood")
    deny: list[str] | None = Field(None, description="Categories to disable (combined with all_categories=True)")
    all_categories: bool = Field(False, description="Enable all 17 categories except those listed in deny")
    max_wheelbarrows: int | None = Field(None, ge=0, description="Max wheelbarrows assigned to this stockpile")
    max_barrels: int | None = Field(None, ge=0, description="Max barrels assigned to this stockpile")
    max_bins: int | None = Field(None, ge=0, description="Max bins assigned to this stockpile")


class CreateZoneRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    kind: str = Field(..., description="Zone kind: meeting, pasture, pen, pit, pond, dump, garbage, refuse, watersource, office, bedroom, dining, barracks, dormitory, fishing, sand, archery, tomb, training, dungeon")
    x: int = Field(..., description="Top-left X tile coordinate")
    y: int = Field(..., description="Top-left Y tile coordinate")
    elev: int = Field(..., description="In-game Elevation (map panel number)")
    w: int = Field(5, ge=1, le=31, description="Zone width in tiles")
    h: int = Field(5, ge=1, le=31, description="Zone height in tiles")
    owner_unit_id: int | None = Field(None, description="Optional citizen unit ID to assign as owner immediately (for office, bedroom, dining, or tomb zones)")
    acknowledge_risk: bool = Field(False, description="Must be True to execute (zone creation via RPC is opt-in)")


class AssignZoneOwnerRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    building_id: int = Field(..., description="Zone building ID from list_zones or create_zone")
    unit_id: int | None = Field(None, description="Citizen unit ID to assign as owner (pass -1 to unassign, or omit to read current owner and room description)")


class AssignPastureRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    action: str = Field("assign", description="One of 'assign' (default: assign animals to pasture), 'unassign' (remove matching unit_ids or species from pasture), or 'clear' (remove all animals from pasture without deleting the zone)")
    building_id: int | None = Field(None, description="Pen/Pasture zone building ID from list_zones or create_zone (omit to target the first Pen zone)")
    all_grazers: bool = Field(True, description="Automatically assign all live tame grazing animals (yaks, sheep, goats, horses, cavies, rabbits) to this pasture")
    species: str | None = Field(None, description="Optional species substring filter (e.g. 'YAK', 'SHEEP', 'DOG')")
    unit_ids: list[int] | None = Field(None, description="Optional explicit list of animal unit IDs to assign or unassign")


class ManageNoblesRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    action: str = Field("list", description="One of 'list' (inspect all noble positions), 'candidates' (rank living citizens by current_soul.skills for noble roles), 'assign' (assign a specific position to unit_id), or 'auto_assign' (fill vacant MANAGER, BOOKKEEPER, BROKER, CHIEF_MEDICAL_DWARF positions with highest-skill living citizens)")
    position: str | None = Field(None, description="Noble position code for action='assign' or 'candidates' (e.g. 'MANAGER', 'BOOKKEEPER', 'BROKER', 'CHIEF_MEDICAL_DWARF', 'MILITIA_COMMANDER', 'SHERIFF')")
    unit_id: int | None = Field(None, description="Citizen unit ID for action='assign' (pass -1 to vacate)")
    bookkeeper_settings: int | None = Field(None, ge=0, le=4, description="Optional bookkeeper precision level (0..4, where 4 is maximum precision)")
    reassign_unskilled: bool = Field(False, description="For action='auto_assign': if True, replace an existing noble who has 0 relevant skills when a higher-skill citizen is available")


class WorkshopJobRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    action: str = Field("list", description="One of 'list' (list workshops, their active jobs, and crafted stock), 'stock' (count free crafted furniture/barrels/pots/raws), 'queue' (queue a crafting/brewing job directly at a workshop), or 'cancel' (remove a workshop job)")
    task: str | None = Field(None, description="Task to queue when action='queue': chair, barrel, bed, table, door, bin, bucket, pot, wheelbarrow, mechanisms, crafts, cabinet, chest, coffin, blocks, brew, meal")
    building_id: int | None = Field(None, description="Target workshop building ID (omit to auto-pick a compatible completed workshop)")
    material: str | None = Field(None, description="Optional material ('wood' or 'stone'; inferred from workshop when omitted)")
    count: int = Field(1, ge=1, le=10, description="Number of jobs to queue at the workshop (1..10)")
    repeat_job: bool = Field(False, description="Set the repeat flag on queued workshop jobs")
    job_id: int | None = Field(None, description="Specific job ID to cancel when action='cancel' (omit to cancel all jobs on building_id)")


SAFE_AUTOMATION_PLUGINS = {
    "autofarm", "autochop", "autobutcher", "seedwatch", "buildingplan",
    "suspendmanager", "tailor", "autonestbox", "autoslab", "prioritize",
    "logistics", "preserve-rooms", "preserve-tombs", "ban-cooking", "orders",
}


class AutomationRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    action: str = Field("status", description="One of 'status' (check safe automation plugins), 'enable', 'disable', 'configure' (run allowlisted plugin with args), or 'apply_preset' ('early_fort' or 'standard_orders')")
    plugin: str | None = Field(None, description="Allowlisted DFHack automation plugin name: autofarm, autochop, autobutcher, seedwatch, buildingplan, suspendmanager, tailor, autonestbox, autoslab, prioritize, logistics, preserve-rooms, preserve-tombs, ban-cooking, orders")
    args: list[str] = Field(default_factory=list, description="Arguments for action='configure' (e.g. ['default', '50'] for autofarm, ['all', '30'] for seedwatch, ['import', 'library/basic'] for orders)")
    preset: str | None = Field(None, description="For action='apply_preset': 'early_fort' (autofarm+seedwatch+ban-cooking+suspendmanager+buildingplan+autonestbox+autoslab+prioritize+tailor) or 'standard_orders' (orders import library/basic)")
    include_rockstock: bool = Field(False, description="For preset='standard_orders': also import library/rockstock")
    include_autochop: bool = Field(False, description="For preset='early_fort': also configure autochop target 30 60 and enable autochop")


class GatherAndItemsRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    action: str = Field("gather_plants", description="One of 'gather_plants' (designate surface shrubs in box for herbalism/plant gathering), 'dump' (flag ground items in box for dumping), 'undump', 'forbid', 'reclaim', or 'protect_seeds' (run ban-cooking seeds & brew so cooks never destroy seed stock)")
    x0: int | None = Field(None, description="Box start X tile coordinate (required for gather_plants, dump, undump, forbid, reclaim)")
    y0: int | None = Field(None, description="Box start Y tile coordinate")
    x1: int | None = Field(None, description="Box end X tile coordinate")
    y1: int | None = Field(None, description="Box end Y tile coordinate")
    elev: int | None = Field(None, description="In-game Elevation (map panel number)")
    item_filter: str | None = Field(None, description="Optional item description substring filter for dump/undump/forbid/reclaim (e.g. 'boulder', 'stone', 'corpse', 'log')")
    ban_brew: bool = Field(True, description="For action='protect_seeds': also ban cooking brewable plants in addition to seeds (default True)")


class TradeDepotRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    action: str = Field("status", description="One of 'status', 'request_broker', 'mark_goods', 'unmark_goods', 'open_trade', 'barter_status', 'barter_select', 'barter_confirm', or 'close_trade'")
    building_id: int | None = Field(None, description="Trade Depot building ID (omit to target the first Trade Depot)")
    item_ids: list[int] | None = Field(None, description="Optional explicit list of item/bin IDs to mark, unmark, or select in barter")
    item_filter: str | None = Field(None, description="Optional item description or type substring filter (e.g. 'bin', 'mug', 'craft', 'amulet', 'crown')")
    min_value: int = Field(0, ge=0, description="Minimum item/bin value filter when listing or marking trade goods")
    max_items: int = Field(25, ge=1, le=200, description="Maximum number of items to list or mark in one call")
    trader_requested: bool | None = Field(None, description="For action='request_broker': True to summon Broker to depot, False to dismiss")
    anyone_can_trade: bool | None = Field(None, description="For action='request_broker': True to allow any dwarf to trade at the depot, False for Broker only")
    side: str = Field("fortress", description="For action='barter_select': 'fortress' (your depot goods) or 'merchant' (caravan goods)")
    selected: bool = Field(True, description="For action='barter_select': True to select matching items for trade, False to deselect")


class LaborChange(BaseModel):
    model_config = ConfigDict(extra="allow")
    unit_id: int = Field(..., description="Unit ID of the citizen")
    labor: str | int = Field(..., description="Labor name from labor_list (e.g. 'MINE', 'CUTWOOD', 'CARPENTER', 'MASON', 'PLANT') or integer ID")
    value: bool = Field(..., description="True to enable the labor, False to disable")


class WorkOrderRequest(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    action: str | None = Field(None, description="One of 'create' (default when job or json is given), 'list' (default when job is omitted: inspect active manager orders & manager status), or 'cancel' (remove matching order_id or job)")
    order_id: int | None = Field(None, description="Manager order ID to cancel when action='cancel'")
    job: str | None = Field(None, description="df.job_type name or friendly alias (e.g. 'ConstructThrone'/'MakeChair', 'ConstructBed', 'ConstructDoor', 'ConstructTable', 'MakeBarrel', 'MakeCharcoal', 'SmeltOre', 'MakePot', 'BrewDrink').")
    amount: int | None = Field(None, ge=1, description="Total amount to produce")
    material: str | None = Field(None, description="Optional material filter ('wood' -> material_category:['wood'], 'rock'/'stone' -> 'INORGANIC', or a DF material string)")
    material_category: list[str] | None = Field(None, description="Optional DFHack material_category list (e.g. ['wood'])")
    item_type: str | None = Field(None, description="Optional item_type filter")
    item_subtype: str | None = Field(None, description="Optional item_subtype filter (e.g. 'ITEM_TOOL_LARGE_POT')")
    reaction: str | None = Field(None, description="Optional custom reaction name (e.g. 'BREW_DRINK_FROM_PLANT')")
    workshop_id: int | None = Field(None, description="Optional target workshop ID")
    quantity: int | None = Field(None, description="Optional quantity field")
    json_spec: str | dict | None = Field(None, alias="json", description="Optional raw workorder JSON string or object")


class ConfigureFarmplotRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    building_id: int | None = Field(None, description="Farm Plot building ID (omit to target the first farm plot)")
    crop: str | None = Field("MUSHROOM_HELMET_PLUMP", description="Plant raw ID or substring (e.g. 'MUSHROOM_HELMET_PLUMP', 'PLUMP', 'PIG_TAIL'); omit to read current seasonal crops without modifying")
    all_seasons: bool = Field(True, description="Set this crop for all four seasons (Spring, Summer, Autumn, Winter)")
    spring: bool = Field(False, description="Set for Spring (when all_seasons=False)")
    summer: bool = Field(False, description="Set for Summer (when all_seasons=False)")
    autumn: bool = Field(False, description="Set for Autumn (when all_seasons=False)")
    winter: bool = Field(False, description="Set for Winter (when all_seasons=False)")


# Allowlist and denylist for /run-command (PLAN-TOOLS.md principle #2 and #6):
# raw code execution (lua/script), destructive commands, and instant-cheat commands are denied.
RUN_COMMAND_DENY_EXACT = {
    "lua", "luap", "script", ":lua",
    "die", "kill-lua", "teleport", "exterminate", "importlegends",
    "dig-now", "build-now", "reveal", "unreveal",
}
RUN_COMMAND_DENY_PREFIXES = (
    "strip", "remove-", "force", "resurrect", "export", "modtools/", "devel/",
)
RUN_COMMAND_ALLOWLIST = {
    "help", "ls", "tags", "version",
    "autochop", "autobutcher", "autofarm", "autolabor", "autonestbox", "autoslab", "autocheese",
    "buildingplan", "suspendmanager", "prioritize", "orders", "workorder",
    "logistics", "preserve-rooms", "preserve-tombs",
    "quickfort", "dreamfort",
    "warn-stranded", "locate-ore", "list-waves", "caravan", "liquids",
    "clean", "cleanowned", "seedwatch", "tailor", "ban-cooking",
    "toggle-kbd-cursor", "position", "prospect",
}

# The preprocessing layer (see worldmodel.py). One instance per process: it owns the
# decode tables (tiletype/item/material/building/profession names), the z-chunk block
# cache, and the previous-snapshot used for delta reporting.
world = WM.WorldModel()
plan_store = FP.FortPlanStore()

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

@app.exception_handler(DFHackTransportError)
async def _offline_handler(request, exc):
    """The game is not reachable: say so, in the answer, with the recovery step.

    Measured 2026-11-21 (after my zone-removal write crashed the fort at 22:53:41): every
    endpoint that did not wrap its call answered HTTP 500 "Internal Server Error", so the
    observer and the agent read a server fault where the truth is one sentence - the game is
    down. Transport failures are not a bug in this server, so they are reported as 503 with
    a success flag and the next step, and the read-only tools keep answering.
    """
    return JSONResponse(status_code=503, content={
        "success": False,
        "game_offline": True,
        "error": f"cannot reach the DFHack RPC: {exc}",
        "next_step": "start Dwarf Fortress with DFHack (remote-server on 127.0.0.1:5000); "
                     "reads resume on their own - the dig and designation state lives in the save",
        "path": str(request.url.path),
    })



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
async def get_status(detail: str = Query("brief", description="'brief' or 'full' (adds map dimensions, view/cursor info, and world info)")):
    """Report the DFHack RPC connection state, the game versions, and the identity of the
    loaded save (save name + in-game date). `reloads` counts the save swaps this server has
    detected and cleared caches for; `reloaded` is the pending swap, reported once. Pass
    detail='full' to include mapInfo, viewInfo, and worldInfo in a single call."""
    try:
        await ensure_connection()
        out = dict(connection_state())          # read state AFTER connecting
        out["dfhackVersion"] = (await GetVersion()).value
        out["dwarfFortressVersion"] = (await GetDFVersion()).value
        sig = await world.signature()
        if sig:
            out["worldSignature"] = {k: sig.get(k) for k in
                                     ("save", "world", "mode", "tiles", "year", "tick")}
        world_loaded = bool(sig and sig.get("tiles") and sig.get("tiles") != "0x0x0")
        out["world_loaded"] = world_loaded
        out["ready"] = bool(out.get("connected") and world_loaded)
        out["mcpStatelessHttp"] = MCP_STATELESS_HTTP
        out["reloads"] = world.reloads
        pending = world.take_reload()
        if pending:
            out["reloaded"] = pending
        if str(detail).strip().lower() == "full":
            try:
                out["mapInfo"] = await get_map_info()
            except Exception as e:
                out["mapInfoError"] = str(e)
            try:
                out["viewInfo"] = await get_view_info()
            except Exception as e:
                out["viewInfoError"] = str(e)
            try:
                out["worldInfo"] = await get_world_info()
            except Exception as e:
                out["worldInfoError"] = str(e)
    except DFHackTransportError as e:
        out = dict(connection_state())
        out["connected"] = False
        out["world_loaded"] = False
        out["ready"] = False
        out["mcpStatelessHttp"] = MCP_STATELESS_HTTP
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
async def list_units(request: ListUnitsRequest):
    """Get details for the units with the given ids. Parameters: unit_ids[list[int]]"""
    body = await _as_dict(request)
    unit_ids = body["unit_ids"]
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
async def run_command(request: RunCommandRequest):
    """Run an allowlisted DFHack command in the currently running game and return its console output. Raw Lua/script execution, cheats (dig-now/build-now/reveal), and destructive commands are denied - use the typed tools instead."""
    body = await _as_dict(request)
    command = str(body["command"]).strip()
    arguments = [str(a) for a in body.get("arguments", [])]
    cmd_norm = command.lstrip(":").lower()
    if cmd_norm in RUN_COMMAND_DENY_EXACT or cmd_norm.startswith(RUN_COMMAND_DENY_PREFIXES):
        return {"command": command, "arguments": list(arguments), "success": False,
                "blocked": True,
                "error": f"command '{command}' is denied on the MCP surface (no raw Lua, cheats, or destructive commands)",
                "hint": "use typed tools instead: send_dig_command, place_building, create_stockpile, work_order, work_details"}
    if cmd_norm not in RUN_COMMAND_ALLOWLIST:
        return {"command": command, "arguments": list(arguments), "success": False,
                "blocked": True,
                "error": f"command '{command}' is not in the run_command allowlist",
                "allowed": sorted(RUN_COMMAND_ALLOWLIST)}
    # DFHack answers CR_NOT_IMPLEMENTED for an unknown command and CR_LINK_FAILURE for a
    # dead socket; both arrived as unhandled exceptions (HTTP 500 + a stack trace in the
    # log). Report them as data so the caller can read the failure and move on.
    drain_replies()                                   # drop anything from an earlier call
    if cmd_norm == "prioritize":
        expanded: list[str] = []
        for a in arguments:
            expanded.extend(shlex.split(a) if any(ch.isspace() for ch in a) else [a])
        arguments = expanded
    try:
        await RunCommand(input = CoreRunCommandRequest(command = command, arguments = arguments))
    except DFHackError as e:
        out = getattr(e, "replies", None) or drain_replies()
        return {"command": command, "arguments": list(arguments), "success": False,
                "error": str(e),
                "output": "\n".join(out),
                "hint": "console text (including the reason for a failure) is returned in "
                        "'output' - see 'help <command>' for usage"}
    except DFHackTransportError as e:
        return {"command": command, "arguments": list(arguments), "success": False,
                "error": f"transport: {e}", "hint": "the game died mid-command; the next "
                                                     "call reconnects by itself"}
    output_text = "\n".join(drain_replies())
    if cmd_norm == "prioritize" and "ignoring unknown job type" in output_text.lower():
        return {"command": command, "arguments": list(arguments), "success": False,
                "error": "prioritize ignored unknown job type(s)",
                "output": output_text,
                "hint": "Valid examples: Dig, CarveUpDownStaircase, ConstructBuilding, CustomReaction --reaction-name BREW_DRINK_FROM_PLANT (prioritize is on/off, no numeric level)"}
    return {"command": command, "arguments": list(arguments), "success": True,
            "output": output_text,
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
async def copy_screen(max_chars: int = Query(4000, ge=200, le=20000, description="maximum characters of screen text to return")):
    """Capture the current game screen as ASCII text (rows joined, bounded by max_chars) - shows menus, designation UI, announcements"""
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
    used = 0
    truncated = False
    cap = max(200, min(int(max_chars), 20000))
    for y in range(h):
        out = []
        last = None
        for x in range(w):
            c = tiles[x*h+y].character
            ch = cp437(c) if c else ' '
            if ch != last:
                out.append(ch)
            last = ch
        row_str = ''.join(out).rstrip()
        if used + len(row_str) + 1 > cap:
            truncated = True
            break
        rows.append(row_str)
        used += len(row_str) + 1
    return {"width": w, "height": h, "rows": rows, "chars": used, "max_chars": cap, "truncated": truncated}

@app.post("/pass-keyboard-event", operation_id="pass_keyboard_event")
async def pass_keyboard_event(request: PassKeyboardEventRequest):
    """Inject a key press into the game - drives the real UI hotkeys (menus, dialogs). Arrow keys do not move the keyboard cursor in DF 53. Parameters: key[str], repeat[int] (optional, default 1)"""
    body = await _as_dict(request)
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
async def apply_blueprint(request: ApplyBlueprintRequest):
    """Write a quickfort blueprint INTO THE REPO (deployables/blueprints, git-controlled) and apply it: the reliable bulk path for BOTH digging and building. mode: "dig" (default) writes a #dig modeline; "build" writes #build - build keys are only valid there (D=trade depot, b=stairs, h=hatch, d=door, f=forge, w=well...). quickfort modelines set the mode, so a build key in a dig blueprint answers "invalid key sequence". grid: list of rows, each a list of single dig keys (d=default, c=channel, i=up-down stairs, x=up, u=down...); cursor: [x,y,z] where the TOP-LEFT cell should land - this tool pre-compensates the known +3 y offset. Set DFHACK_BLUEPRINT_DIR to the game's dfhack-config/blueprints path to enable. Returns quickfort stats; verify with world_objects / look."""
    body = await _as_dict(request)
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
        out = getattr(e, "replies", None) or drain_replies()
        return {"success": False, "blueprint": path, "deployed_to": shipped, "mode": mode,
                "target_top_left": cur, "error": str(e),
                "output": "\n".join(out),
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
async def set_pause_state(request: SetPauseStateRequest):
    """Pause (true) or unpause (false) the game. Parameters: paused[bool]"""
    body = await _as_dict(request)
    want = bool(body["paused"])
    await SetPauseState(input = SingleBool(Value = want))
    state = await GetPauseState()
    out = {"paused": state.Value, "requested": want}
    if state.Value != want:
        # Measured 2026-11-22: an unpause request answered paused=true twice in a row while the
        # player sat in the Nobles screen. The game holds its own pause in UI screens; the write
        # is issued, the state is the game's. Say so, so "paused: true" is never read as the
        # tool refusing to cooperate.
        out["note"] = ("the game kept its own pause state; a UI screen (menus, popups) holds the "
                       "pause and the player resumes it - do not re-issue this write as a fix")
    return out

@app.post("/cut-trees", operation_id="cut_trees")
async def cut_trees(request: CutTreesRequest):
    """Fell trees in `[x0..x1, y0..y1]` at `elev` by designating `DEFAULT_DIG` on `tree_trunk` tiles (saplings are ignored). Reports designated trunks and wood count."""
    body = await _as_dict(request)
    x0, y0, x1, y1, e = (int(body[k]) for k in ("x0", "y0", "x1", "y1", "elev"))
    limit = int(body.get("limit") or 0) or 0
    TRUNK = "tree_trunk"
    async def _wood() -> int | None:
        """The wood the fort HOLDS, built the way /observe builds facts.

        `(await world.progress()).facts` was my invention - progress() returns the rendered
        ladder dict, so the attribute error was swallowed and the tool answered
        wood_before/wood_after: null (measured 2026-11-21, twice)."""
        try:
            gen = await world._gen()
            dec = await world._dec(gen)
            lo, hi = await world.fort_band()
            lv = await world.levels(lo - WM.GAME_Z_OFFSET, hi - WM.GAME_Z_OFFSET)
            facts = WM.FortFacts(lv, await WM.load_units(dec, gen, world._state), dec)
            return int(facts.wood)
        except Exception:
            return None

    wood0 = await _wood()
    trunks, saplings, scanned = [], 0, 0
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            scanned += 1
            try:
                t = await world.tile_at(x, y, e)
            except Exception:
                continue
            cls = str(t.get("class") or "")
            if cls == TRUNK:
                trunks.append((x, y))
            elif cls in ("sapling", "tree_dead"):
                saplings += 1
    if limit:
        trunks = trunks[:limit]
    if not trunks:
        return {"success": True, "trees_designated": 0, "trunks_found": 0,
                "saplings_seen": saplings, "tiles_scanned": scanned,
                "at": [x0, y0, x1, y1, e],
                "note": "no tree trunk in this rectangle - saplings are cleared with the "
                        "vegetation order, not the chopping order, so they are not counted"}
    z = WM.GAME_Z_OFFSET
    locs = [[x, y, e + z] for (x, y) in trunks]
    designated = await _designate("DEFAULT_DIG", locs)
    wood1 = await _wood()
    return {"success": designated > 0, "designation": "DEFAULT_DIG (the game makes these Fell Tree jobs)",
            "trees_designated": designated, "trunks_found": len(trunks),
            "saplings_seen_not_felled": saplings, "tiles_scanned": scanned,
            "trunks": trunks[:24], "wood_before": wood0, "wood_after": wood1,
            "note": "felling and hauling take in-game time; wood_after is a reading, not a promise"}


async def _designate(designation: str, locations: list[list[int]]) -> int:
    """The ONE sanctioned designation path: SendDigCommand with the game's own coordinates
    (z = elev + WM.GAME_Z_OFFSET, passed as given). Both /send-dig-command and /cut-trees go
    through here, so the stair/shaft rules live in one place."""
    await SendDigCommand(input=DigCommand(designation=DIG_DESIGNATIONS[designation],
                                          locations=[Coord(x=int(p0[0]), y=int(p0[1]), z=int(p0[2]))
                                                    for p0 in locations]))
    return len(locations)


@app.get("/find-water", operation_id="find_water")
async def find_water(elev0: int = Query(...), elev1: int = Query(...),
                     cell: int = Query(16), min_depth: int = Query(3), max_levels: int = Query(8),
                     near_x: int | None = Query(None, description="Optional x to sort closest clusters first"),
                     near_y: int | None = Query(None, description="Optional y to sort closest clusters first")):
    """Scan map blocks across `elev0..elev1` for water tiles (`>= min_depth`, default 3/7 for wells) and return clustered coordinates and depth ranges."""
    from worldmodel import GAME_Z_OFFSET
    gen = await world._gen()
    dec = await world._dec(gen)
    step = max(4, int(cell))
    mind = int(min_depth)
    levels = range(int(elev0), int(elev0) + min(int(max_levels), 12))
    cells: dict[tuple[int, int, int], dict] = {}
    total = 0
    for e in levels:
        z = e + GAME_Z_OFFSET
        blocks = await world.cache.blocks(z, z + 1, gen)
        for b in blocks:
            if int(b.map_z) != z:
                continue
            # map_x/map_y are TILE coordinates (multiples of 16)
            base_x, base_y = int(b.map_x), int(b.map_y)
            for idx, tile in enumerate(b.tiles):
                x, y = base_x + (idx % 16), base_y + (idx // 16)
                hid = bool(b.hidden[idx]) if idx < len(b.hidden) else False
                if hid:
                    continue
                w = int(b.water[idx]) if idx < len(b.water) else 0
                if w < mind:
                    continue
                total += 1
                cls = str(dec.tile_class(tile).key)
                key = (e, x // step, y // step)
                c = cells.setdefault(key, {"elev": e, "x0": (x // step) * step, "y0": (y // step) * step,
                                           "x1": (x // step) * step + step - 1, "y1": (y // step) * step + step - 1,
                                           "water_tiles": 0, "max_depth": 0, "sample": None,
                                           "classes": set()})
                c["water_tiles"] += 1
                c["max_depth"] = max(c["max_depth"], w)
                c["classes"].add(cls)
                if c["sample"] is None:
                    c["sample"] = [x, y, w]
    out = []
    for c in cells.values():
        c["classes"] = sorted(c["classes"])
        if near_x is not None and near_y is not None:
            cx_c = (c["x0"] + c["x1"]) / 2.0
            cy_c = (c["y0"] + c["y1"]) / 2.0
            c["dist"] = round(((cx_c - near_x) ** 2 + (cy_c - near_y) ** 2) ** 0.5, 1)
        out.append(c)
    if near_x is not None and near_y is not None:
        out.sort(key=lambda c: (c["dist"], -c["water_tiles"]))
    else:
        out.sort(key=lambda c: (-c["water_tiles"], -c["max_depth"]))
    return {"success": True, "levels_scanned": list(levels), "water_tiles_found": total,
            "clusters": out[:40], "min_depth": mind,
            "note": "a well needs a floor tile with a clear line to water >=3/7 below it - "
                    "use a cluster sample, and gate with /hazards before digging toward it"}


@app.get("/find-trees", operation_id="find_trees")
async def find_trees(elev0: int = Query(...), elev1: int = Query(...),
                     cell: int = Query(16), max_levels: int = Query(8),
                     near_x: int | None = Query(None, description="Optional x to sort closest clusters first"),
                     near_y: int | None = Query(None, description="Optional y to sort closest clusters first")):
    """Scan map blocks across `elev0..elev1` and return clustered `tree_trunk` coordinates (with separate sapling counts) for `cut_trees`."""
    from worldmodel import GAME_Z_OFFSET
    gen = await world._gen()
    dec = await world._dec(gen)
    step = max(4, int(cell))
    levels = range(int(elev0), int(elev0) + min(int(max_levels), 12))
    cells: dict[tuple[int, int, int], dict] = {}
    trunk_total = sap_total = 0
    for e in levels:
        z = e + GAME_Z_OFFSET
        blocks = await world.cache.blocks(z, z + 1, gen)
        for b in blocks:
            if int(b.map_z) != z:
                continue
            # map_x/map_y are TILE coordinates (multiples of 16) - the worldmodel header
            # says so, and multiplying by 16 again produced "trees" at x256,y1792 on a
            # 192x192 map (measured 2026-11-21: 1034 trunks found, none of them real).
            base_x, base_y = int(b.map_x), int(b.map_y)
            for idx, tile in enumerate(b.tiles):
                x, y = base_x + (idx % 16), base_y + (idx // 16)
                hid = bool(b.hidden[idx]) if idx < len(b.hidden) else False
                if hid:
                    continue
                cls = str(dec.tile_class(tile).key)
                if cls not in ("tree_trunk", "sapling", "tree_dead"):
                    continue
                key = (e, x // step, y // step)
                c = cells.setdefault(key, {"elev": e, "x0": (x // step) * step, "y0": (y // step) * step,
                                           "x1": (x // step) * step + step - 1, "y1": (y // step) * step + step - 1,
                                           "trunks": 0, "saplings": 0, "sample": None})
                if cls == "tree_trunk":
                    c["trunks"] += 1
                    trunk_total += 1
                    if c["sample"] is None:
                        c["sample"] = [x, y]
                else:
                    c["saplings"] += 1
                    sap_total += 1
    if near_x is not None and near_y is not None:
        for c in cells.values():
            cx_c = (c["x0"] + c["x1"]) / 2.0
            cy_c = (c["y0"] + c["y1"]) / 2.0
            c["dist"] = round(((cx_c - near_x) ** 2 + (cy_c - near_y) ** 2) ** 0.5, 1)
        clusters = sorted(cells.values(), key=lambda c: (c["dist"], -c["trunks"]))[:40]
    else:
        clusters = sorted(cells.values(), key=lambda c: -c["trunks"])[:40]
    return {"success": True, "levels_scanned": list(levels), "cells_with_trees": len(cells),
            "trunks_found": trunk_total, "saplings_found": sap_total, "clusters": clusters,
            "note": "fell trunks with /cut-trees using a cluster box; saplings are the vegetation order"}


def _expand_dig_box(x0: int, y0: int, x1: int, y1: int, e0: int, e1: int | None = None,
                    middle_first: bool = True) -> list[list[int]]:
    """Expand an in-game Elevation rectangle [x0..x1, y0..y1, e0..e1] into RPC [x, y, z]
    triples (z = elev + GAME_Z_OFFSET), ordering the center spine before the flanks so
    miners carve an access corridor into fog without stranding flank designations."""
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    e_end = e0 if e1 is None else int(e1)
    e_lo, e_hi = min(int(e0), e_end), max(int(e0), e_end)
    xs = list(range(int(x0), int(x1) + 1))
    ys = list(range(int(y0), int(y1) + 1))
    if middle_first:
        if len(xs) >= len(ys):
            cy = (y0 + y1) / 2.0
            ys = sorted(ys, key=lambda y: (abs(y - cy), y))
        else:
            cx = (x0 + x1) / 2.0
            xs = sorted(xs, key=lambda x: (abs(x - cx), x))
    out: list[list[int]] = []
    for e in range(e_hi, e_lo - 1, -1):
        z = e + GAME_Z_OFFSET
        if len(xs) >= len(ys):
            for y in ys:
                for x in xs:
                    out.append([x, y, z])
        else:
            for x in xs:
                for y in ys:
                    out.append([x, y, z])
    return out


_SAFE_DIG_GLYPHS = {"#", "?", "B", "p"}
_PROTECTED_DIG_DESIGNATIONS = {2, 3, 4, 5, 6}  # UP_DOWN_STAIR, CHANNEL, RAMP, DOWN_STAIR, UP_STAIR


async def _filter_safe_dig_points(pts: list[list[int]]) -> tuple[list[list[int]], dict]:
    """Filter out tiles that are not rock/fog (floors, stairs, ramps, buildings), have water/aquifer, or already hold stair/channel/ramp designations."""
    if not pts:
        return [], {}
    by_z: dict[int, list[list[int]]] = {}
    for p in pts:
        by_z.setdefault(int(p[2]), []).append(p)
    kept: list[list[int]] = []
    skipped = {"non_rock": 0, "building": 0, "water_or_aquifer": 0, "existing_stair_or_channel": 0}
    try:
        gen = await world._gen()
        dec = await world._dec(gen)
    except Exception:
        return pts, {}
    for z, z_pts in by_z.items():
        elev = z - GAME_Z_OFFSET
        try:
            blocks = await world.cache.blocks(z, z + 1, gen)
            hz = await world.hazard_map(elev, elev)
        except Exception:
            kept.extend(z_pts)
            continue
        bld: set[tuple[int, int]] = set()
        live: dict[tuple[int, int], str] = {}
        fog: set[tuple[int, int]] = set()
        des_map: dict[tuple[int, int], int] = {}
        for b in blocks:
            if int(b.map_z) != z:
                continue
            for bb in b.buildings:
                for xx in range(bb.pos_x_min, bb.pos_x_max + 1):
                    for yy in range(bb.pos_y_min, bb.pos_y_max + 1):
                        bld.add((xx, yy))
            for i, hid in enumerate(b.hidden):
                if int(hid):
                    fog.add((b.map_x + (i % 16), b.map_y + (i // 16)))
            for i, tid in enumerate(b.tiles):
                live[(b.map_x + (i % 16), b.map_y + (i // 16))] = dec.tile_class(int(tid)).glyph
            for i, dval in enumerate(b.tile_dig_designation):
                if int(dval):
                    des_map[(b.map_x + (i % 16), b.map_y + (i // 16))] = int(dval)
        for p in z_pts:
            x, y = int(p[0]), int(p[1])
            if (x, y) in bld:
                skipped["building"] += 1
                continue
            if des_map.get((x, y), 0) in _PROTECTED_DIG_DESIGNATIONS:
                skipped["existing_stair_or_channel"] += 1
                continue
            rec = hz.get((x, y))
            if rec and (any(e >= elev for e in rec.get("aquifer", [])) or any(e >= elev for e in rec.get("water", {}))):
                skipped["water_or_aquifer"] += 1
                continue
            if (x, y) not in fog:
                g = live.get((x, y))
                if g is not None and g not in _SAFE_DIG_GLYPHS:
                    skipped["non_rock"] += 1
                    continue
            kept.append(p)
    return kept, {k: v for k, v in skipped.items() if v > 0}


@app.post("/send-dig-command", operation_id="send_dig_command")
async def send_dig_command(request: SendDigCommandRequest):
    """Designate digging (`NO_DIG`, `DEFAULT_DIG`, `UP_DOWN_STAIR_DIG`, `CHANNEL_DIG`, `RAMP_DIG`, `DOWN_STAIR_DIG`, `UP_STAIR_DIG`, or `STAIR_SHAFT`).
    Accepts `box`/`boxes` (`{x0,y0,x1,y1,elev,elev1?}` using in-game elevation, converted via `z = elevation + 129` / `WM.GAME_Z_OFFSET`, ordered middle-first), `stair_shaft` (`{x0,y0,from_elev,to_elev,...}`), or raw `locations` `[x,y,z]`.
    FOG WORKFLOW: designating into unexplored fog (`hidden=True`) WORKS and is the intended workflow; gate with `fort_strata`/`survey` and verify completion via `survey` (`dug > 0 and to_dig == 0`)."""
    body = await _as_dict(request)
    swap = bool(body.get("swap", False))
    middle_first = bool(body.get("middle_first", True))
    safe = bool(body.get("safe", True))

    def _apply_swap(pts: list[list[int]]) -> list[list[int]]:
        return [[int(p[1]), int(p[0]), int(p[2])] for p in pts] if swap \
               else [[int(p[0]), int(p[1]), int(p[2])] for p in pts]

    # Mode 1: explicit stair_shaft helper (or designation="STAIR_SHAFT" with box)
    shaft = body.get("stair_shaft")
    raw_des = str(body.get("designation") or "DEFAULT_DIG").upper()
    if shaft is None and raw_des == "STAIR_SHAFT" and body.get("box"):
        bx = body["box"]
        shaft = {
            "x0": bx["x0"], "y0": bx["y0"],
            "x1": bx.get("x1", bx["x0"]), "y1": bx.get("y1", bx["y0"]),
            "from_elev": bx["elev"],
            "to_elev": bx.get("elev1") if bx.get("elev1") is not None else bx["elev"],
        }
    if shaft is not None:
        x0, y0 = int(shaft["x0"]), int(shaft["y0"])
        x1 = int(shaft["x1"]) if shaft.get("x1") is not None else x0
        y1 = int(shaft["y1"]) if shaft.get("y1") is not None else y0
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        hi = max(int(shaft["from_elev"]), int(shaft["to_elev"]))
        lo = min(int(shaft["from_elev"]), int(shaft["to_elev"]))
        connect_above = bool(shaft.get("connect_above", False))
        continue_below = bool(shaft.get("continue_below", False))
        shaft_levels = []
        total_tiles = 0
        for e in range(hi, lo - 1, -1):
            if hi == lo:
                kind = "UP_DOWN_STAIR_DIG"
            elif e == hi:
                kind = "UP_DOWN_STAIR_DIG" if connect_above else "DOWN_STAIR_DIG"
            elif e == lo:
                kind = "UP_DOWN_STAIR_DIG" if continue_below else "UP_STAIR_DIG"
            else:
                kind = "UP_DOWN_STAIR_DIG"
            lvl_pts = _apply_swap(_expand_dig_box(x0, y0, x1, y1, e, e, middle_first=False))
            n_lvl = await _designate(kind, lvl_pts)
            total_tiles += n_lvl
            shaft_levels.append({"elev": e, "z": e + GAME_Z_OFFSET, "designation": kind, "tiles": n_lvl})
        return {
            "success": True,
            "mode": "stair_shaft",
            "designation": "STAIR_SHAFT",
            "footprint": [x0, y0, x1, y1],
            "elevations": [hi, lo],
            "tiles": total_tiles,
            "shaft_levels": shaft_levels,
        }

    designation = raw_des
    if designation not in DIG_DESIGNATIONS:
        return {"error": f"unknown designation '{designation}'", "known": sorted(DIG_DESIGNATIONS)}

    # Mode 2: box / boxes (or top-level x0, y0, x1, y1, elev convenience fields)
    boxes_in = []
    if body.get("box"):
        boxes_in.append(body["box"])
    if body.get("boxes"):
        boxes_in.extend(body["boxes"])
    if not boxes_in and all(k in body for k in ("x0", "y0", "x1", "y1", "elev")):
        boxes_in.append({k: body[k] for k in ("x0", "y0", "x1", "y1", "elev", "elev1") if k in body})

    total_sent = 0
    boxes_applied = []
    skipped_summary: dict[str, int] = {}
    sent_pts_for_verify: list[list[int]] = []
    for bx in boxes_in:
        b_des = str(bx.get("designation") or designation).upper()
        if b_des not in DIG_DESIGNATIONS:
            return {"error": f"unknown box designation '{b_des}'", "known": sorted(DIG_DESIGNATIONS)}
        pts = _apply_swap(_expand_dig_box(
            int(bx["x0"]), int(bx["y0"]), int(bx["x1"]), int(bx["y1"]),
            int(bx["elev"]), bx.get("elev1"), middle_first=middle_first
        ))
        if safe and b_des == "DEFAULT_DIG":
            pts, sk = await _filter_safe_dig_points(pts)
            for k, v in sk.items():
                skipped_summary[k] = skipped_summary.get(k, 0) + v
        n_box = await _designate(b_des, pts) if pts else 0
        total_sent += n_box
        if b_des != "NO_DIG" and n_box > 0:
            sent_pts_for_verify.extend(pts)
        boxes_applied.append({
            "x0": int(bx["x0"]), "y0": int(bx["y0"]),
            "x1": int(bx["x1"]), "y1": int(bx["y1"]),
            "elev": int(bx["elev"]),
            "elev1": int(bx["elev1"]) if bx.get("elev1") is not None else int(bx["elev"]),
            "designation": b_des,
            "tiles": n_box,
        })

    # Mode 3: explicit locations [[x, y, z], ...]
    if body.get("locations"):
        locs = [Coord(x=int(p[1]), y=int(p[0]), z=int(p[2])) for p in body["locations"]] if swap \
               else [Coord(x=int(p[0]), y=int(p[1]), z=int(p[2])) for p in body["locations"]]
        loc_pts = [[c.x, c.y, c.z] for c in locs]
        if safe and designation == "DEFAULT_DIG":
            loc_pts, sk = await _filter_safe_dig_points(loc_pts)
            for k, v in sk.items():
                skipped_summary[k] = skipped_summary.get(k, 0) + v
        n_loc = await _designate(designation, loc_pts) if loc_pts else 0
        total_sent += n_loc
        if designation != "NO_DIG" and n_loc > 0:
            sent_pts_for_verify.extend(loc_pts)

    if not boxes_in and not body.get("locations"):
        return {"success": False,
                "error": "provide 'box' ({x0,y0,x1,y1,elev}), 'boxes', 'stair_shaft', or 'locations' ([[x,y,z],...])"}

    out_res: dict[str, Any] = {"designation": designation, "tiles": total_sent, "success": True}
    if boxes_applied:
        out_res["boxes_applied"] = boxes_applied
    if skipped_summary:
        out_res["skipped_tiles"] = skipped_summary
        if skipped_summary.get("non_rock", 0) > 0:
            out_res["warning"] = f"skipped {skipped_summary['non_rock']} already-dug/non-rock tile(s) in partially dug box (safe=True)"
    if sent_pts_for_verify:
        try:
            world.cache.reset()
            gen = await world._gen()
            by_z_v: dict[int, set[tuple[int, int]]] = {}
            for p in sent_pts_for_verify:
                by_z_v.setdefault(int(p[2]), set()).add((int(p[0]), int(p[1])))
            kept_after = 0
            for zv, coords in by_z_v.items():
                blks = await world.cache.blocks(zv, zv + 1, gen)
                for blk in blks:
                    if int(blk.map_z) != zv:
                        continue
                    for i, dval in enumerate(blk.tile_dig_designation):
                        if int(dval) != 0 and (blk.map_x + (i % 16), blk.map_y + (i // 16)) in coords:
                            kept_after += 1
            cancelled_now = max(0, len(sent_pts_for_verify) - kept_after)
            out_res["designated_after_write"] = kept_after
            if cancelled_now > 0:
                out_res["cancelled_immediately"] = cancelled_now
                cancel_msg = (
                    f"{cancelled_now} of {len(sent_pts_for_verify)} designation(s) were cancelled immediately "
                    "(likely damp/aquifer tile or unreachable from fort)"
                )
                out_res["warning"] = (out_res["warning"] + "; " + cancel_msg) if "warning" in out_res else cancel_msg
        except Exception:
            pass
    return out_res

@app.post("/get-block-list", operation_id="get_block_list")
async def get_block_list(request: GetBlockListRequest):
    """RAW tile arrays - the expensive escape hatch: ONE elevation is ~147,000 tokens, so prefer look / world_objects / world_map / world_minimap. Read map blocks (explored tiles only) in a z window. Full map x/y is always used (windowed x/y requests return nothing - plugin bug); z windows <= 12 levels (larger overflows the RPC reply limit and kills the socket). Parameters: min_z[int], max_z[int], census_top[int] (optional, default 8)"""
    body = await _as_dict(request)
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
# it truncates. Coordinates in and out are GAME ELEVATIONS (rpc_z - 129).
# ---------------------------------------------------------------------------

@app.get("/look", operation_id="look")
async def look(e0: int = Query(-999, description="start game elevation; omit for auto fort band"),
               e1: int = Query(-999, description="end game elevation; omit for auto fort band"),
               with_legend: bool = Query(False, description="include the map glyph legend"),
               max_tokens: int = Query(WM.DEFAULT_MAX_TOKENS, description="token budget, cap 30000")):
    """Start here: whole-fort briefing with date, pause state, population rollup, recent reports, per-elevation strip, delta since last read, pending dig clusters, buildings, items, and walkable regions. Zoom in with `world_map`."""
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
                    rle: bool = Query(False, description="run-length encode flat rows (default False so the top X-coordinate ruler aligns character-for-character; set True for wide >60-col scans)"),
                    with_legend: bool = Query(False, description="include the glyph legend"),
                    max_tokens: int = Query(WM.DEFAULT_MAX_TOKENS)):
    """Render a single elevation `e` as an aligned 1-char-per-tile glyph grid over `[x0..x1, y0..y1]` with X/Y coordinate rulers and walkable-region labels (keep windows ~40x40)."""
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
    """Evaluate the Quickstart milestone ladder against the live fort (tunnel, stairs, stockpiles, farms, workshops, nobles, military) and return the next unsatisfied rung with actionable guidance."""
    e0 = None if e0 == -999 else e0
    e1 = None if e1 == -999 else e1
    bk_info = None
    try:
        nb = await LO.manage_nobles(action="list")
        for pos_row in nb.get("positions", []):
            if str(pos_row.get("code", "")).upper() == "BOOKKEEPER":
                bk_info = pos_row
                break
        if bk_info is not None:
            bk_info = dict(bk_info)
            bk_info["bookkeeper_settings"] = nb.get("bookkeeper_settings")
    except Exception:
        bk_info = None
    return await world.progress(e0, e1, max_tokens=max_tokens, bookkeeper=bk_info)

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
async def world_raw(request: WorldRawRequest):
    """Dump RAW map blocks to a FILE instead of into the conversation - use this when you truly need tile-level detail, then grep the file with your file tools. Parameters: e0[int], e1[int], path[str] (optional). Returns the path and shape stats, never the tiles."""
    import json, os, time
    body = await _as_dict(request)
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
async def game_lua(request: GameLuaRequest):
    """Run a Lua EXPRESSION against the live game through DFHack's `lua` command and get its printed output back. This is the door the proto list does not cover: read anything (world.jobs.list, buildings, units, path info) and call DFHack's own APIs - reqscript("quickfort").apply_blueprint{mode="build", data="D(1x1)", pos={x=...,y=...,z=...}} places buildings, dfhack.zone / dfhack.stockpile reach zones and stockpiles. CAUTION: this writes live game state; prefer read-only prints, test on a throwaway save, and keep a save before mutating. Parameters: expression[str] (a Lua chunk; use print() to return data)"""
    body = await _as_dict(request)
    expr = str(body["expression"])
    drain_replies()
    try:
        await RunCommand(input = CoreRunCommandRequest(command = "lua", arguments = [expr]))
    except (DFHackError, DFHackTransportError) as e:
        out = getattr(e, "replies", None) or drain_replies()
        return {"success": False, "error": str(e), "output": "\n".join(out)}
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

async def _verify_footprint_ground(e: int, x0: int, y0: int, w: int, h: int,
                                   label: str = "building") -> dict | None:
    """Shared pre-flight site gate: verify that [x0..x0+w-1, y0..y0+h-1] at elevation e
    is walkable ground (no unexcavated rock, wall, hidden fog, open cavern air, or standing
    water). Returns None when the footprint is valid ground, or a refusal dict."""
    x1, y1 = int(x0) + max(1, int(w)) - 1, int(y0) + max(1, int(h)) - 1
    try:
        st = await world.box_state(int(e), int(x0), int(y0), x1, y1)
        sv = await world.survey(int(e), int(x0), int(y0), x1, y1)
    except Exception as ex:
        return {"success": False, "error": f"site gate could not read the footprint: {ex}",
                "hint": "the read failed; nothing was placed"}
    counts = sv.get("counts") or {}
    nonground = {k: v for k, v in counts.items()
                 if k in ("rock", "wall", "hidden", "open", "wood_wall", "water")}
    dug, total = int(st.get("dug", 0)), int(st.get("total_tiles", max(1, int(w)) * max(1, int(h))))
    to_dig = int(st.get("to_dig", 0))
    if sum(nonground.values()) > 0:
        return {"success": False,
                "error": f"site x{x0}-{x1} y{y0}-{y1} elev{e} is not valid {label} ground: {nonground}",
                "hint": f"a {label} needs walkable, dry ground under every tile - rock, fog, water and cavern air are not ground",
                "gate": "no rock/wall/hidden/open/water in the footprint", "measured": counts}
    if to_dig > 0:
        return {"success": False,
                "error": f"site x{x0}-{x1} y{y0}-{y1} elev{e} is not valid {label} ground: "
                         f"{to_dig} tiles still to dig (dug {dug}/{total})",
                "hint": "dig it out first before placing a building or zone",
                "gate": "no rock/wall/hidden/open, to_dig == 0", "measured": st}
    return None


@app.post("/place-building", operation_id="place_building")
async def place_building(request: PlaceBuildingRequest):
    """Place a building or furniture (`Trade Depot`, `Well`, `Farm Plot`, `Still`, `Carpenter`, `Stoneworker`, `Bed`, `Table`, `Chair`, `Door`, etc.) at `(x, y, elev)` with optional `w`/`h`, `material` (`log`/`boulder`/`block`), `items`, or `complete=False`. Pre-flight site gate rejects rock, fog, open air, or water (use `create_stockpile`/`create_zone` for piles/zones)."""
    body = await _as_dict(request)
    _x0, _y0, _e = int(body["x"]), int(body["y"]), int(body["elev"])
    _w, _h = int(body.get("w") or 1), int(body.get("h") or 1)
    if body.get("check_site", True) is not False:
        _bname = str(body.get("building") or "").strip().lower()
        # Wells and bridges legitimately span open space/water, so exempt them from the dry-floor gate
        if _bname not in ("well", "bridge", "water wheel", "screw pump"):
            gate_err = await _verify_footprint_ground(_e, _x0, _y0, _w, _h, label="building")
            if gate_err is not None:
                return gate_err
        if _bname in ("farm plot", "plot", "farmplot"):
            try:
                soil_info = await LO.check_farm_soil(_e, _x0, _y0, _x0 + _w - 1, _y0 + _h - 1)
                if int(soil_info.get("plantable_tiles", 0)) < int(soil_info.get("total_tiles", 1)):
                    return {
                        "success": False,
                        "error": f"farm plot at x{_x0}..{_x0+_w-1} y{_y0}..{_y0+_h-1} @elev{_e} has only {soil_info.get('plantable_tiles', 0)}/{soil_info.get('total_tiles', 1)} soil/muddy tiles (stone_tiles={soil_info.get('stone_tiles')})",
                        "hint": "use fort_plan(action='find_site', kind='farm') to locate soil or dig-to-soil tiles, or pass check_site=false to override",
                        "gate": "farm plot requires soil or muddy floor",
                        "measured": soil_info,
                    }
            except Exception:
                pass
    dec = await world._dec(await world._gen())
    try:
        # ANY multi-tile request goes through the game's own setSize path. Not just
        # piles: a 3x2 Farm Plot came back 1x1 the same way (measured 2026-11-21, and
        # the ladder rung still read satisfied - a 1-tile farm quietly starves a fort).
        # 2026-11-22 (tool_missing #57): the caller may ask for the EXACT footprint with nominal=false.
        # Farm plots do not snap to a canonical size (measured: my nominal 1x1 plot reads rect 86,108-86,108),
        # and the plan route refused 3x3 plots - so an exact-footprint request goes straight to constructBuilding.
        if _w > 1 or _h > 1:
            # Only a stockpile (and other material-free abstracts) is a legal argument for
            # constructAbstract. A WORKSHOP is not: measured placing a 3x4 Still, the abstract
            # route died with "constructAbstract(): expected !bld->isActual()". Workshops go
            # through quickfort's plan call (constructBuilding with width/height + room
            # extents, no abstract flag) - see luaops.place_building_plan.
            _name = str(body["building"]).strip().lower()
            _abstract_family = _name in ("stockpile", "stock_pile", "pile")
            if not _abstract_family:
                try:
                    _pl = await LO.place_building_plan(dec, body["building"], int(body["x"]),
                                                       int(body["y"]), int(body["elev"]), _w, _h)
                except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
                    return {"success": False, "error": str(e),
                            "hint": "workshops are placed as plans; check the site with tile_at "
                                    "and pass a 1x1 origin that is a dug floor"}
                world.cache.reset()
                world.forget_band()
                return {"success": True, "placed": str(body["building"]),
                        "at": [int(body["x"]), int(body["y"]), int(body["elev"])],
                        "route": "place_building_plan (constructBuilding width/height + room.extents, "
                                 "no abstract) - quickfort's workshop path",
                        "next": "a builder finishes the plan with real materials; probe the far corner tile",
                        **_pl}
            _sp = await LO.create_building_multi(dec, body["building"], int(body["x"]),
                                                 int(body["y"]), int(body["elev"]), _w, _h)
            world.cache.reset()
            world.forget_band()
            _is_pile = str(body["building"]).strip().lower() in ("stockpile", "stock_pile", "pile")
            return {"success": True, "placed": str(body["building"]),
                    "at": [int(body["x"]), int(body["y"]), int(body["elev"])],
                    "route": "create_building_multi (allocInstance -> setSize -> commit)",
                    "next": ("configure_stockpile for the categories, then probe the pile"
                             if _is_pile else "probe the building at its far corner tile"),
                    **_sp}
        res = await LO.place_building(dec, body["building"], int(body["x"]), int(body["y"]),
                                      int(body["elev"]), w=body.get("w"), h=body.get("h"),
                                      complete=bool(body.get("complete", True)),
                                      abstract=body.get("abstract"),
                                      items=body.get("items"),
                                      material=body.get("material"),
                                      count=int(body.get("count", 3)),
                                      nominal=bool(body.get("nominal", True)))
    except LO.LuaOpError as e:
        return {"success": False, "error": str(e)}
    world.cache.reset()
    world.forget_band()
    return {"success": True, **res}

async def _building_present(building_id: int) -> bool | None:
    """Is this id still in the live pile/zone lists? None = the reads produced nothing.

    A WRITE that prints nothing is not a failed write: the RPC reply stream flakes on
    write calls constantly ("lua produced no output; the chunk printed nothing" - measured
    on /remove-building x3 and /place-building), which left the player with an unverified
    removal and me reporting a pile as leftover. The lists are the authority, so every
    write gets a read-back that does not depend on the write's own reply.
    """
    seen_any = False
    for _ in range(4):
        dec = await world._dec(await world._gen())
        rows = []
        try:
            rows += await LO.list_stockpiles(dec)
        except LO.LuaOpError:
            pass
        try:
            rows += await LO.list_zones(dec)
        except LO.LuaOpError:
            pass
        if rows:
            seen_any = True
            if not any(int(r["id"]) == int(building_id) for r in rows):
                return False
        await asyncio.sleep(2.0)
    return True if seen_any else None


@app.post("/remove-building", operation_id="remove_building")
async def remove_building(request: RemoveBuildingRequest):
    """Deconstruct a building by `(x, y, elev)` or `building_id` and verify removal (refuses activity zones; use `remove_zone` for zones)."""
    body = await _as_dict(request)
    x = y = elev = None
    bid_req = int(body["building_id"]) if body.get("building_id") is not None else None
    if bid_req is not None:
        dec = await world._dec(await world._gen())
        try:
            zones = await LO.list_zones(dec)
        except LO.LuaOpError as e:
            zones = []
        for row in zones:
            if int(row["id"]) == bid_req:
                return {"success": False, "verdict": "refused_zone", "building_id": bid_req,
                        "error": f"building_id {bid_req} is an activity zone ({row.get('type_name', 'Zone')}); findAtTile cannot see zones so remove_building cannot remove it",
                        "hint": "call remove_zone(building_id=..., acknowledge_risk=True) instead"}
        for row in await LO.list_stockpiles(dec):
            if int(row["id"]) == bid_req:
                x, y, elev = int(row["x0"]), int(row["y0"]), int(row["elev"])
                break
        if x is None:
            try:
                live = await LO.buildings_live()
                for row in live.get("buildings", []):
                    if int(row.get("building_id", -1)) == bid_req:
                        x, y, elev = int(row["x"]), int(row["y"]), int(row["elev"])
                        break
            except (LO.LuaOpError, DFHackError, DFHackTransportError):
                pass
        if x is None:
            return {"success": False,
                    "error": f"no building or stockpile with building_id {bid_req} in live lists"}
    else:
        try:
            x, y, elev = int(body["x"]), int(body["y"]), int(body["elev"])
        except (KeyError, TypeError, ValueError):
            return {"success": False,
                    "error": 'send {"building_id": N} or {"x": X, "y": Y, "elev": E}; '
                             "nothing was removed"}
    try:
        res = await LO.remove_building(x, y, elev)
        write_note = None
    except LO.LuaOpError as e:
        res, write_note = {"error": str(e)}, str(e)
    world.cache.reset()
    bid = res.get("building_id") if isinstance(res, dict) else None
    if bid is None:
        bid = bid_req
    if bid is None:
        try:
            at = await LO.building_at(x, y, elev)
            bid = at.get("building_id") if isinstance(at, dict) else None
        except (LO.LuaOpError, DFHackError, DFHackTransportError):
            pass
    if isinstance(res, dict) and res.get("verdict") == "marked":
        return {"success": True, "verdict": "marked", "removed": False,
                "marked_for_removal": True, "building_id": bid,
                "resolved_from_tile": [x, y, elev],
                "note": res.get("note"), "write_reply": write_note}

    # Verify absence against buildings_live (and fallback to building_at)
    present: bool | None = None
    verified_by = "buildings_live"
    try:
        live = await LO.buildings_live()
        blds = live.get("buildings")
        if isinstance(blds, list):
            if bid is not None:
                present = any(int(r.get("building_id", -1)) == int(bid) for r in blds)
            else:
                present = any(int(r.get("x", -1)) == x and int(r.get("y", -1)) == y and int(r.get("elev", -999)) == elev for r in blds)
    except (LO.LuaOpError, DFHackError, DFHackTransportError):
        pass
    if present is None:
        try:
            at = await LO.building_at(x, y, elev)
            if isinstance(at, dict):
                present = (at.get("building") is not None)
                verified_by = "findAtTile"
        except (LO.LuaOpError, DFHackError, DFHackTransportError):
            present = None

    if present is False:
        return {"success": True, "verdict": "removed", "building_id": bid,
                "resolved_from_tile": [x, y, elev],
                "verified_by": f"the building is absent from {verified_by}",
                "write_reply": write_note}
    if present is None:
        return {"success": False, "verdict": "UNVERIFIED", "building_id": bid,
                "resolved_from_tile": [x, y, elev],
                "error": "buildings_live and findAtTile produced NO DATA: removal not proven",
                "write_reply": write_note}
    return {"success": False, "verdict": "still present", "building_id": bid,
            "resolved_from_tile": [x, y, elev],
            "error": (res.get("error") if isinstance(res, dict) else None) or f"the building is still present in {verified_by}",
            "write_reply": write_note}

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

@app.get("/unit-activity", operation_id="unit_activity")
async def unit_activity(unit_ids: str = Query(None, description="comma-separated unit ids to inspect (default: all active units)"), limit: int = Query(20, ge=1, le=40)):
    """What each dwarf is actually doing: position, move target, mining labour, and its job list.

    Why this exists: `df.global.world.jobs.list` is EMPTY on DF 50, so `/job-queue` answered
    `total_jobs: 0` while 56 designations sat pending (tool_missing #46) - a zero that was a read
    failure, not a fact. Jobs live on the units, so the stall question ("are the miners mining?")
    is answered per unit here.
    """
    ids = [int(x) for x in str(unit_ids).split(",") if x.strip().isdigit()] if unit_ids else None
    try:
        res = await LO.unit_activity(unit_ids=ids, limit=int(limit))
        return {"success": True, **res}
    except LO.LuaOpError as e:
        return {"success": False, "error": str(e)}


@app.get("/job-queue", operation_id="job_queue")
async def job_queue(limit: int = Query(40)):
    """The jobs queue - RemoteFortressReader exposes NONE of it. Returns total job count and job descriptions with the acting unit. This is how you find out why nothing is happening."""
    try:
        # Measured 2026-11-22: the rebuilt queue answered with real counts (total_jobs 7: unit 5,
        # building 2) and `success: null` - a reply must state its own outcome (tool_missing #46).
        res = await LO.list_jobs(limit=limit)
        return {"success": True, **res}
    except LO.LuaOpError as e:
        return {"success": False, "error": str(e)}

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
        lines.append("  none - try a shorter length, a smaller min_depth, or start from an explored entrance "
                     "and extend into fog via send_dig_command (gate with hazards and depth_profile first)")
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
OBSERVE_LUA_SETTLE = 0.0

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
  local rec = {id = tonumber(id), found = 0, counts = {}, accepts = "", wheelbarrows = -1, barrels = -1, bins = -1}
  if b ~= nil then
    rec.found = 1
    -- One pile with an unreadable field must not kill the whole probe (measured:
    -- the pile probe printed NO rows, and every pile then read NO DATA).
    pcall(function() rec.x0 = b.x1; rec.x1 = b.x2; rec.y0 = b.y1; rec.y1 = b.y2; rec.elev = b.z - __Z_OFF__ end)
    local on = {}
    for _, c in ipairs(CATS) do
      local ok, v = pcall(function() return b.settings.flags[c] end)
      if ok and v == true then on[#on + 1] = c end
    end
    rec.accepts = table.concat(on, ",")
    pcall(function() rec.wheelbarrows = b.storage.max_wheelbarrows end)
    pcall(function() rec.barrels = b.storage.max_barrels end)
    pcall(function() rec.bins = b.storage.max_bins end)
  end
  piles[#piles + 1] = rec
end
for _, it in ipairs(df.global.world.items.other.IN_PLAY) do
  local okp, pos = pcall(function() return it.pos end)
  if okp and pos then
    local tn = "?"
    pcall(function() for k, v in pairs(df) do if v == it._type then tn = k end end end)
    for _, rec in ipairs(piles) do
      if rec.found == 1 and pos.z - __Z_OFF__ == rec.elev
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
    return OBSERVE_PILE_LUA_TEMPLATE.replace("__IDS__", ids).replace("__Z_OFF__", str(GAME_Z_OFFSET)).strip()


OBSERVE_PICKS_LUA = """
-- One probe for the status-bar facts (a Lua round-trip costs ~0.6 s, so the
-- observer asks for everything it can get in a single call, deep ticks only).
local picks = 0
for _, it in ipairs(df.global.world.items.other.ANY_WEAPON) do
  local ok, key = pcall(function() return tostring(it.subtype.id) end)
  if ok and key == "ITEM_WEAPON_PICK" then
    picks = picks + 1
    local okp, pos = pcall(function() return it.pos end)
  end
end
local ok_site, site = pcall(function()
  local s = dfhack.world.getCurrentSite()
  return s == nil and "none" or tostring(s.id)
end)
local ok_date, date = pcall(function()
  return dfhack.world.ReadCurrentYear() .. "-" .. dfhack.world.ReadCurrentMonth() .. "-" .. dfhack.world.ReadCurrentDay()
end)
local ok_seeds, seeds = pcall(function() return #df.global.world.items.other.SEEDS end)
-- Supply as the player ranks it: alcohol first, then prepared meals, then raw food.
local drink, meals, raw = 0, 0, 0
for _, d in ipairs(df.global.world.items.other.DRINK) do drink = drink + 1 end
for _, it in ipairs(df.global.world.items.other.FOOD) do
  local ok, sid = pcall(function() return tostring(it.subtype.id) end)
  if ok and sid:find("MEAL") then meals = meals + 1 else raw = raw + 1 end
end
print("OBS2 PICKS " .. picks .. " SITE " .. (ok_site and site or "ERR") .. " DATE " .. (ok_date and date or "ERR")
  .. " SEEDS " .. (ok_seeds and seeds or "ERR") .. " DRINK " .. drink .. " MEALS " .. meals .. " RAW " .. raw)
"""


def observe_parse_obs2(out: str) -> dict:
    """Parse the combined status-bar probe line. Missing fields stay None (labelled)."""
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("OBS2 "):
            continue
        parts = line.split()[1:]
        kv: dict = {}
        i = 0
        while i + 1 < len(parts):
            kv[parts[i]] = parts[i + 1]
            i += 2
        def num_of(key: str):
            v = kv.get(key)
            if v is None or v == "ERR":
                return None
            try:
                return int(v)
            except ValueError:
                return None
        date = kv.get("DATE")
        parsed = {"picks": num_of("PICKS"), "site_id": num_of("SITE"), "seeds": num_of("SEEDS"),
                  "alcohol": num_of("DRINK"), "meals": num_of("MEALS"), "raw_food": num_of("RAW")}
        if date and date != "ERR" and len(date.split("-")) == 3:
            y, m, d = date.split("-")
            parsed["date"] = {"year": int(y), "month": int(m), "day": int(d)}
        else:
            parsed["date"] = None
        if kv.get("SITE") == "none":
            parsed["site_id"] = None
        return parsed
    return {"picks": None, "site_id": None, "seeds": None, "alcohol": None, "meals": None,
            "raw_food": None, "date": None}


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
        "to_dig": g("rock") + g("hidden"), "rock": g("rock"), "hidden": g("hidden"),
        "dug": g("floor") + g("stairs") + g("ramp"),
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

    # The identity of the LOADED GAME, and the save-swap event. A save reload does
    # not change `generation` (the socket survives), so the observer needs the
    # world's own signature to know the fort it is measuring is a different fort.
    # `reloaded` is reported ONCE, on the tick the swap is detected.
    try:
        reload_evt = await world.check_reload()
        sig = await world.signature()
        if sig:
            out["worldSignature"] = {k: sig.get(k) for k in
                                     ("save", "world", "mode", "tiles", "year", "tick")}
        if reload_evt:
            out["reloaded"] = reload_evt
    except Exception as e:                      # pragma: no cover - defensive
        notes.append(f"game signature unavailable: {e}")

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
            # Zone kinds and pile categories come from the injected readers. `progress()`
            # calls them and `observe` did not, so the SAME rung answered true from
            # /fort-progress and false from /observe (measured: meeting area + refuse pile
            # both standing, the plugin reporting both open). One rung, one answer.
            for attr, field in (("zone_provider", "zones"), ("pile_provider", "piles")):
                fn = getattr(world, attr, None)
                if fn is not None:
                    try:
                        setattr(facts, field, await fn(dec))
                    except Exception as e:
                        notes.append(f"{field} unavailable: {e} (rung reads NO DATA, not false)")
        except Exception as e:                  # cache/RFR/lua failures label, not crash
            notes.append(f"facts unavailable: {e}")
    if facts is not None:
        out["facts"] = {
            "pop": facts.pop, "dwarves": facts.dwarves, "soldiers": facts.soldiers, "hostile": facts.hostile,
            "miners": facts.miners, "hunters": facts.hunters,
            "food": facts.food, "drink": facts.drink, "wood": facts.wood,
            "stone": facts.stone, "metal": facts.metal, "barrels": facts.barrels,
            "pending_dig": facts.designations, "underground_floors": getattr(facts, "underground_floors", 0),
            "water_tiles": facts.water_tiles, "magma_tiles": facts.magma_tiles,
            "buildings_total": len(facts.buildings),
        }
        if req.get("buildings", True):
            # Buildings are a fort-wide fact: scan the whole map so a plot outside
            # the watched band is never reported as missing (measured: Farm Plot
            # invisible at band 22-45, found at 0-128). The ladder reads the same
            # map-wide list, so rung labels stop lying about unbuilt buildings.
            band_names = Counter(b["name"] for b in facts.buildings)
            mapwide: list[dict] = []
            try:
                mapwide = await world.buildings_mapwide()
            except Exception as e:
                notes.append(f"map-wide building scan unavailable: {e}")
            pool = mapwide or facts.buildings
            # FortFacts derives `built_names` in __init__, so replacing the list
            # without it leaves every rung predicate reading the STALE band names
            # (measured: Farm Plot found map-wide, `farm` rung still done=false).
            facts.buildings = pool
            facts.built_names = [b["name"].lower() for b in pool]
            roll = Counter(b["name"] for b in pool)
            out["buildings"] = [{"name": k, "count": v} for k, v in sorted(roll.items(),
                                                                           key=lambda kv: -kv[1])[:40]]
            out["buildings_scope"] = "map-wide" if mapwide else "watched band only"
            out["buildings_band_only"] = [{"name": k, "count": v} for k, v in sorted(band_names.items(),
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
                # Tile state comes from the block cache: `dug` is positive tile
                # class, independent of any designation flag left on a floor (the
                # /survey RPC classifies a designated floor as already_designated,
                # which under-counts dug - measured dug 2 where 13 were floor).
                st = await world.box_state(e, x0, y0, x1, y1)
                if st.get("tiles_known", 0) == 0:
                    st = observe_simplify_survey(await world.survey(e, x0, y0, x1, y1))
                boxes.append(st)
                if st.get("notes"):
                    notes.append(f"box x{x0}-{x1} y{y0}-{y1} elev{e}: {st['notes']}")
            except Exception as err:            # label per-box failure, keep the rest
                boxes.append({"elev": e, "box": [x0, y0, x1, y1], "error": str(err)})
                notes.append(f"survey x{x0}-{x1} y{y0}-{y1} elev{e} unavailable: {err}")
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

    # Designation clusters (deep ticks): where the game's dig designations physically
    # are, as merged 16x16-block rectangles per in-game elevation. The plugin asks
    # for `designations: N` and labels the field NO DATA when this route is absent.
    if req.get("designations"):
        try:
            limit = max(1, min(int(req["designations"]), 40))
            out["designations"] = await world.designation_clusters(int(e0), int(e1), limit=limit)
        except Exception as e:
            out["designations"] = None
            notes.append(f"designation scan unavailable: {e}")


    obs2 = {"picks": None, "site_id": None, "seeds": None, "date": None}
    if req.get("status_probe", False):
        try:
            obs2 = observe_parse_obs2(await LO._lua(OBSERVE_PICKS_LUA, settle=OBSERVE_LUA_SETTLE))
        except LO.LuaOpError as e:
            notes.append(f"status probe unavailable: {e}")
    out["status"] = obs2
    if obs2["picks"] is None:
        notes.append("status probe produced no pick count: picks UNKNOWN (not 0)")

    _want_piles = req.get("pile_ids", [])
    pile_ids = []
    if _want_piles == "auto" or _want_piles == ["auto"]:
        # Pile ids change with every save (measured: a profile row carried [18,19,20,24]
        # from a fort that no longer existed, so every pile alert read "not found" and the
        # pile acceptance tests could never run). Discover them from the game.
        try:
            _ps = await LO.list_stockpiles(await world._dec(gen))
            pile_ids = [int(p["id"]) for p in _ps][:16]
            notes.append(f"pile ids discovered from the game: {pile_ids or 'none (no piles)'}")
        except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
            notes.append(f"pile id discovery failed: {e}; pile health unknown")
    elif isinstance(_want_piles, list):
        pile_ids = [int(i) for i in _want_piles[:16]]
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
    """Rank the elevations in ONE column for a probe dig - the way a player beats fog. Plans change mid-game: dig a short 1-wide adit, read the strata, then go deeper / shallower / elsewhere. Per elevation it reports glyph, depth under the local surface, and a verdict: probe here (solid, deep enough), avoid (damp: aquifer or water in the column at/above - digging floods the works), shallow (surface cut), fog (unexplored rock - designations ARE accepted in fog; gate with hazards/depth_profile as miners reveal it), or open air."""
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

class PileArraysRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    action: str = Field("check", description="'check' lists piles whose enabled categories have empty material arrays; 'fix' allocates them to a reference pile's measured sizes")
    building_id: int | None = Field(None, description="Limit to one pile id")


@app.get("/unit-professions", operation_id="unit_professions")
async def unit_professions(action: str = Query("list", description="'list' shows each citizen's profession and the fortress's profession names; 'set' assigns one"),
                           unit_id: int = Query(None, description="set: the citizen to reassign"),
                           profession: str = Query(None, description="set: the profession name, e.g. Miner (case-insensitive; 'list' shows what exists)"),
                           profession_id: int = Query(None, description="set: the numeric profession id (from action=list). Use this to restore a profession NO citizen currently carries - the name route can only see professions a living unit holds, because the profession table is unreachable from Lua on this build"),
                           acknowledge_risk: bool = Query(False, description="set: REQUIRED. A profession write takes a craft away from a dwarf; the game has no undo for it")):
    """The profession layer - what the Labor screen's tick marks mean on this build.

    Measured 2026-11-22: the player's Miners page showed ONE tick (Aban, Proficient Miner) while
    `/unit-labors` reported 4 citizens with `MINE`. DF 50 drives labors from profession
    assignment, so a labor write and a profession roster are different facts, and the roster is
    what the game digs with. Use this to make dwarves Miners, then `/unit-labors` to read the
    flags back.
    """
    if str(action).strip().lower() == "set":
        if not acknowledge_risk:
            return {"success": False,
                    "error": ("a profession write removes a craft from a dwarf. Measured cost: `set 6491 "
                              "Miner` took the fort's ONLY Metalcrafter (tool_missing #32, df-dig §21). "
                              "Staffing is a work-detail job - use GET /work-details action=assign. "
                              "Check /unit-professions for uniqueness first, then pass acknowledge_risk=true"),
                    "uniqueness_check": ("run action=list; do not convert a craft with exactly one holder")}
    try:
        res = await LO.unit_profession(action=action, unit_id=unit_id, profession=profession,
                                       profession_id=profession_id)
        return {"success": True, **res}
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e)}


@app.get("/work-details", operation_id="work_details")
async def work_details(action: str = Query("list", description="'list' (the whole Labor menu), 'assign' (tick/untick a dwarf on a detail), 'set-mode' (switch between 'everybody', 'nobody', or 'selected'), 'set-labor' (change what labors a detail grants), 'create' (create a custom work detail), or 'delete' (remove a custom work detail)"),
                       detail: str = Query(None, description="assign/set-mode/set-labor/create/delete: the detail index (from list), detail name (e.g. Miners, Miner, Planters), or labor name (e.g. MINE, CUTWOOD)"),
                       unit_id: int = Query(None, description="assign/create: the citizen to tick/untick (unit id from /unit-professions)"),
                       mode: str = Query(None, description="assign: 'add' (tick) or 'remove' (untick); set-mode/create: 'everybody' (1), 'nobody' (2), or 'selected' (3)"),
                       labor: str = Query(None, description="set-labor/create: labor name(s) from /labor-list, e.g. MINE, CARPENTER, MASON, BREWER"),
                       value: bool = Query(None, description="set-labor: true to grant, false to revoke (default true)")):
    """Inspect or edit work details (`list`, `assign`, `set-mode`, `set-labor`, `create`, `delete`). Updates `assigned_units`, switches mode to `OnlySelectedDoesThis` on assign, and refreshes citizen professions/labors immediately."""
    try:
        res = await LO.work_details(action=action, detail=detail, unit_id=unit_id,
                                    mode=mode, labor=labor, value=value)
        if str(action).strip().lower() != "list":
            world._state.pop("units_gen", None)
        return {"success": True, **res}
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e)}


@app.get("/unit-labors", operation_id="unit_labors")
async def unit_labors(unit_ids: str = Query("", description="comma-separated unit ids to restrict to (default: all citizens)"),
                      labor: str = Query(None, description="count only this labor, e.g. MINE, CUTWOOD, BREWER")):
    """Which labors the citizens ACTUALLY hold - the read-back labor writes need.

    `MINE` is the field name on this build; `MINING` is not (`u.status.labors.MINING` does not
    exist, and a pcall probe reads it as a silent false - my own probe did exactly that and
    reported 0 miners). The plugin's `miners` metric is profession-based, so it can legitimately
    disagree with the labor census: professions label the labor screen, labors are what dig.
    """
    ids = [int(x) for x in str(unit_ids).split(",") if x.strip().isdigit()]
    try:
        res = await LO.unit_labors(ids or None, labor)
        return {"success": True, **res}
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e)}


@app.get("/buildings-live", operation_id="buildings_live")
async def buildings_live(elev0: int = Query(None), elev1: int = Query(None),
                         type: str = Query(None, description="substring filter on the building type name, e.g. artery, chain, depot, bed")):
    """The game's FULL building list (`df.global.world.buildings.all`) - type, tile, elevation, flags.

    Why a second census: `GET /list-buildings` reads the RPC block payload, and RemoteFortressReader
    publishes only its own building enum. Measured 2026-11-22: with the game announcing "An artery
    has been opened by the attack!", the RPC census over 762 blocks found the wagon and a chair and
    **no artery**. The fort's own list is the authority for what is standing in it.
    """
    try:
        res = await LO.buildings_live(elev0=elev0, elev1=elev1, type=type)
        # Lua has no building defname tables on this build (measured), so the type NAME comes from
        # the RPC decoder - the same source that named "Wagon" in the block census.
        try:
            dec = await world._dec(await world._gen())
            for row in res.get("buildings", []):
                tid = row.get("type_id")
                resolved = str(row.get("type", ""))
                # The Lua census names buildings with dfhack.buildings.getName (the game's own
                # helper). Only when that gave nothing ("type 32") do we try the RPC decoder -
                # and the RPC enum ids do NOT match the game's, so its answer is labelled.
                if tid is not None and resolved.startswith("type "):
                    try:
                        row["type_name"] = str(dec.building_name(int(tid)))
                    except Exception:
                        row["type_name"] = f"type {tid} (id not in the RPC decoder's enum)"
        except (DFHackError, DFHackTransportError) as e:
            res["names_note"] = f"type names unavailable: {e}"
        if type:
            needle = str(type).lower()
            res["buildings"] = [r for r in res.get("buildings", [])
                                if needle in str(r.get("type_name", "")).lower()
                                or needle in str(r.get("type", "")).lower()]
            res["count"] = len(res["buildings"])
        return {"success": True, **res}
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e), "hint": "a Lua read failure is a read failure - retry once the game answers"}


@app.get("/list-buildings", operation_id="list_buildings")
async def list_buildings(elev0: int = Query(...), elev1: int = Query(...),
                         max_levels: int = Query(12, ge=1, le=12), type: str = Query(None, description="filter by building type name substring (case-insensitive), e.g. artery, depot, workshop, bed")):
    """CENSUS of the fort's buildings - type, footprint, active flag, attached items - from the RPC
    block data, because the server had no way to list buildings at all.

    Measured gap (2026-11-22): the game reported **"An artery has been opened by the attack!"** and
    I could not tell the player WHERE. `/building-status` needs a building id you already have;
    `/list-buildings` and `/buildings` did not exist (404), and `/world-objects` is a 422 without
    params. The data was always in the blocks I already read: `b.buildings` carries
    `pos_x_min..pos_x_max`, `pos_y_min..pos_y_max`, `pos_z_min`, `building_type`, `items`, `active`
    (the layer builder uses exactly these), so the census is a block walk, not new plumbing.
    """
    from worldmodel import GAME_Z_OFFSET
    # Guard like the other census tools (measured 2026-11-22: the first call landed while the
    # game was re-handshaking after coming back online - GetMapInfo raised CR_FAILURE and the
    # endpoint answered HTTP 500. A failed read must answer success:false with the error text.)
    try:
        gen = await world._gen()
        dec = await world._dec(gen)
    except (DFHackError, DFHackTransportError, Exception) as e:
        return {"success": False, "error": f"map read failed: {e}", "blocks_scanned": 0,
                "hint": "the game answers CR_FAILURE while it is re-handshaking after a reload; retry once it is steady"}
    levels = range(int(elev0), int(elev0) + min(int(max_levels), 12))
    found: dict[tuple, dict] = {}
    blocks_scanned = 0
    for e in levels:
        z = e + GAME_Z_OFFSET
        try:
            blocks = await world.cache.blocks(z, z + 1, gen)  # max_z is EXCLUSIVE (measured)
        except (DFHackError, DFHackTransportError) as e:
            return {"success": False, "error": f"block read failed at elev {e}: {e}",
                    "elev0": int(elev0), "blocks_scanned": blocks_scanned,
                    "hint": "partial scan - the census reports what it read, never a fake empty fort"}
        for b in blocks:
            if int(b.map_z) != z:
                continue
            blocks_scanned += 1
            for bd in b.buildings:
                try:
                    x0, x1 = int(bd.pos_x_min), int(bd.pos_x_max)
                    y0, y1 = int(bd.pos_y_min), int(bd.pos_y_max)
                    zmin = int(getattr(bd, "pos_z_min", z))
                    name = str(dec.building_name(bd.building_type))
                except Exception:
                    continue
                key = (name, x0, y0, zmin - GAME_Z_OFFSET)
                row = found.get(key)
                if row is None:
                    found[key] = {
                        "type": name,
                        "x0": x0, "x1": x1, "y0": y0, "y1": y1,
                        "elev": zmin - GAME_Z_OFFSET,
                        "w": x1 - x0 + 1, "h": y1 - y0 + 1,
                        "items": int(len(getattr(bd, "items", []) or [])),
                        "active": bool(getattr(bd, "active", False)),
                    }
                else:
                    row["items"] = max(row["items"], int(len(getattr(bd, "items", []) or [])))
    rows = list(found.values())
    if type:
        needle = str(type).lower()
        rows = [r for r in rows if needle in r["type"].lower()]
    rows.sort(key=lambda r: (r["type"], r["elev"], r["x0"], r["y0"]))
    by_type: dict[str, int] = {}
    for r in rows:
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1
    return {"success": True, "elev0": int(elev0), "levels_scanned": list(levels),
            "blocks_scanned": blocks_scanned, "count": len(rows),
            "by_type": by_type, "buildings": rows[:300],
            "note": "the census reads the blocks the game sent: blocks_scanned 0 is a read failure, not an empty fort"}


@app.get("/find-soil", operation_id="find_soil")
async def find_soil(elev0: int = Query(...), elev1: int = Query(...),
                    cell: int = Query(16, ge=4, le=64), max_levels: int = Query(8, ge=1, le=12)):
    """Where the FARM can legally go: soil-floor tiles, clustered, read through the RPC.

    The farm rung is "3x3 plot in a 5x5 soil room" and `/survey` answers tile CLASSES only -
    soil is a tiletype property. Two attempts failed before this one, both measured:
    (1) a Lua module op - dead on this build: `world.T_map.all: not found`, `map:find` returns nil
    for real tile coordinates, and every block grid is unreadable from Lua (`matgrid`, `typegrid`,
    `tileptr`, `regiongrid`, `water` all fail; `pairs(block)` itself raises). It answered
    `blocks_scanned: 0, soil_tiles: 0` - a read failure wearing a census, tool_missing #28/#30.
    (2) a material-flag census (MUDPYROCLASTIC/PYROPLASTIC) - unreachable for the same reason.
    This uses the RPC the rest of the server already relies on: `GetTiletypeList` carries
    `material` per tiletype and the enum is `SOIL = 1` (measured from the proto:
    NO_MATERIAL=-1, AIR=0, SOIL=1, STONE=2, ... GRASS_LIGHT=8..GRASS_DEAD=11). Grass tiletypes are
    included because a grass floor is soil ground, which is what a farm plot wants.
    """
    from worldmodel import GAME_Z_OFFSET
    gen = await world._gen()
    dec = await world._dec(gen)      # the tile-class decoder (measured: NameError 'dec' is not defined)
    tt = await GetTiletypeList()
    soil_ids, soil_names = set(), {}
    # Measured 2026-11-22: `Tiletype.material` is an ENUM INTEGER, not a name - my first version
    # stringified it ("1") and matched nothing, answering soil_tiles: 0 over 762 scanned blocks.
    # The enum (from the proto): SOIL=1, GRASS_LIGHT=8, GRASS_DARK=9, GRASS_DRY=10, GRASS_DEAD=11.
    SOIL_MATS = {1, 8, 9, 10, 11}
    for t in tt.tiletype_list:
        tid = int(t.id)
        try:
            mat = int(getattr(t, "material", -1))
        except Exception:
            mat = -1
        nm = str(getattr(t, "name", "") or "")
        if mat in SOIL_MATS:
            soil_ids.add(tid)
            soil_names[tid] = nm
    step = max(4, int(cell))
    levels = range(int(elev0), int(elev0) + min(int(max_levels), 12))
    cells: dict[tuple[int, int, int], dict] = {}
    total, blocks_scanned = 0, 0
    for e in levels:
        z = e + GAME_Z_OFFSET
        blocks = await world.cache.blocks(z, z + 1, gen)     # max_z is EXCLUSIVE (measured)
        for b in blocks:
            if int(b.map_z) != z:
                continue
            blocks_scanned += 1
            base_x, base_y = int(b.map_x), int(b.map_y)      # map_x/map_y ARE tile coords
            for idx, tile in enumerate(b.tiles):
                x, y = base_x + (idx % 16), base_y + (idx // 16)
                hid = bool(b.hidden[idx]) if idx < len(b.hidden) else False
                if hid:
                    continue
                tid = int(tile)
                if tid not in soil_ids:
                    continue
                total += 1
                key = (e, x // step, y // step)
                c = cells.setdefault(key, {"elev": e, "x0": (x // step) * step, "y0": (y // step) * step,
                                           "x1": (x // step) * step + step - 1,
                                           "y1": (y // step) * step + step - 1,
                                           "soil_tiles": 0, "sample": None, "types": {}})
                c["soil_tiles"] += 1
                if c["sample"] is None:
                    c["sample"] = [x, y]
                nm = soil_names.get(tid, str(tid))
                c["types"][nm] = c["types"].get(nm, 0) + 1
    # The player's rule (2026-11-22): a DUG FLOOR TAKES THE MATERIAL OF THE LAYER ABOVE, so a room
    # dug one level under the soil surface has a SOIL FLOOR - that is the farmable set, and my
    # census answering only existing soil tiles said "no underground soil on this world", which was
    # the wrong question. Potential soil = a diggable tile at `e` with soil at `e+1` above it.
    cover_above: dict[tuple[int, int, int], bool] = {}
    for e in levels:
        z = e + GAME_Z_OFFSET
        blocks = await world.cache.blocks(z, z + 1, gen)
        for b in blocks:
            if int(b.map_z) != z:
                continue
            base_x, base_y = int(b.map_x), int(b.map_y)
            for idx, tile in enumerate(b.tiles):
                x, y = base_x + (idx % 16), base_y + (idx // 16)
                if int(tile) in soil_ids:
                    cover_above[(x, y, e)] = True
    pot: dict[tuple[int, int, int], dict] = {}
    pot_total = 0
    for e in levels:
        z = e + GAME_Z_OFFSET
        blocks = await world.cache.blocks(z, z + 1, gen)
        for b in blocks:
            if int(b.map_z) != z:
                continue
            base_x, base_y = int(b.map_x), int(b.map_y)
            for idx, tile in enumerate(b.tiles):
                x, y = base_x + (idx % 16), base_y + (idx // 16)
                if int(tile) in soil_ids:
                    continue
                hid = bool(b.hidden[idx]) if idx < len(b.hidden) else False
                if hid:
                    continue
                if not cover_above.get((x, y, e + 1)):
                    continue
                cls = str(dec.tile_class(tile).key) if hasattr(dec, "tile_class") else "?"
                if cls not in ("rock", "wall"):
                    continue
                pot_total += 1
                key = (e, x // step, y // step)
                c = pot.setdefault(key, {"elev": e, "x0": (x // step) * step, "y0": (y // step) * step,
                                         "x1": (x // step) * step + step - 1,
                                         "y1": (y // step) * step + step - 1,
                                         "potential_soil_tiles": 0, "sample": None})
                c["potential_soil_tiles"] += 1
                if c["sample"] is None:
                    c["sample"] = [x, y]
    pot_clusters = sorted(pot.values(), key=lambda c: -c["potential_soil_tiles"])[:24]
    clusters = sorted(cells.values(), key=lambda c: -c["soil_tiles"])[:24]
    for c in clusters:
        c["types"] = [k for k, _ in sorted(c["types"].items(), key=lambda kv: -kv[1])[:3]]
    return {"success": True, "elev0": int(elev0), "elev1": int(elev1), "cell": step,
            "levels_scanned": list(levels), "blocks_scanned": blocks_scanned,
            "soil_tiles": total, "soil_tiletypes_seen": sorted(set(soil_names.values()))[:24],
            "potential_soil_tiles": pot_total,
            "potential_soil_note": "a dug floor takes the material of the layer above, so rock under soil becomes a SOIL FLOOR - this is the farmable set (the player's rule, df-dig §23). `soil_tiles` alone is only existing soil, and a map with no underground soil is NOT a map with no farm site",
            "clusters": clusters, "potential_soil_clusters": pot_clusters,
            "note": "a farm plot needs a FLOOR on soil inside mined space: check /survey for the "
                    "class (floor, not rock) at the sample, and soil_tiles is only meaningful when "
                    "blocks_scanned > 0 - a scan of 0 blocks answering 0 is a read failure, not a map fact"}


@app.post("/pile-arrays", operation_id="pile_arrays")
async def pile_arrays(request: PileArraysRequest):
    """Pile health for the crash you get by clicking Custom: enabled category flags over EMPTY material arrays.

    Measured 2026-11-22: `crash_2026-11-22-20-02-29.txt` - SIGSEGV at address 0x5, stack
    `render_things` -> `overlay.plug.so`, "no Lua involvement" - the player clicked Custom on an
    outdoor pile this tool created. The game's UI allocates the per-category arrays when *it*
    switches a category on; a tool-made pile has the flags without them. Run action='check' before
    opening Custom on any agent-made pile, and action='fix' to allocate them from a UI pile's
    measured sizes.
    """
    body = await _as_dict(request)
    try:
        await world._gen()
        res = await LO.pile_arrays(action=str(body.get("action", "check")),
                                   building_id=body.get("building_id"))
        return {"success": True, **res}
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e)}


@app.get("/magma-depth", operation_id="magma_depth")
async def magma_depth(x: int = Query(...), y: int = Query(...),
                      elev: int = Query(..., description="dig elevation"),
                      e_min: int = Query(-120), step: int = Query(2)):
    """Distance down to magma from a dig point - the HOT warning as a number. The magma crust sits far below the fort band, so this scans downward in coarse windows and returns the first magma elevation and how many levels below your dig it is."""
    return await world.magma_depth_below(x, y, elev, e_min=e_min, step=step)

@app.post("/make-stockpile", operation_id="make_stockpile")
async def make_stockpile(request: CreateStockpileRequest):
    """Create a multi-tile stockpile and set it up correctly in ONE call: site gate + size + category flags + the material arrays the game's custom-settings UI walks + container limits. Delegates to create_stockpile so the mid-air site gate is always enforced."""
    return await create_stockpile(request)


@app.post("/create-stockpile", operation_id="create_stockpile")
async def create_stockpile(request: CreateStockpileRequest):
    """Create a multi-tile stockpile (`x, y, elev, w, h`) and optionally set `allow`/`deny`/`all_categories` and `max_wheelbarrows`/`max_barrels`/`max_bins`. Rejects footprints containing rock, fog, or open space."""
    body = await _as_dict(request)
    x0, y0, e = int(body["x"]), int(body["y"]), int(body["elev"])
    w, h = max(1, int(body["w"])), max(1, int(body["h"]))
    x1, y1 = x0 + w - 1, y0 + h - 1
    try:
        st = await world.box_state(e, x0, y0, x1, y1)
        sv = await world.survey(int(e), int(x0), int(y0), int(x1), int(y1))
    except Exception as ex:
        return {"success": False, "error": f"site gate could not read the footprint: {ex}",
                "hint": "the read failed; nothing was placed"}
    counts = sv.get("counts") or {}
    # Ground = anything walkable that is NOT unexcavated rock, fog, cavern air or a wall.
    # Measured 2026-11-21: a legal outdoor spot on the open-sky plateau at 42 was refused
    # because two of its tiles were SAPLINGS and one was PEBBLES - all of them floors with
    # something growing on them, exactly where the wiki puts a wood/refuse pile.
    nonground = {k: v for k, v in counts.items() if k in ("rock", "wall", "hidden", "open", "wood_wall")}
    dug, total = int(st.get("dug", 0)), int(st.get("total_tiles", w * h))
    to_dig, hidden = int(st.get("to_dig", 0)), int(st.get("hidden", 0))
    if sum(nonground.values()) > 0:
        return {"success": False,
                "error": f"site x{x0}-{x1} y{y0}-{y1} elev{e} is not pile ground: {nonground}",
                "hint": "a pile needs walkable ground under every tile - rock, fog and cavern air are not ground",
                "gate": "no rock/wall/hidden/open in the footprint", "measured": counts}
    if to_dig > 0:
        return {"success": False,
                "error": f"site x{x0}-{x1} y{y0}-{y1} elev{e} is not pile ground: "
                         f"{to_dig} tiles still to dig (dug {dug}/{total})",
                "hint": "dig it out first - the game accepts piles on cavern air and fog, and "
                        "the pile then floats (measured: 3 piles built in mid air on this fort)",
                "gate": "no rock/wall/hidden/open, to_dig == 0", "measured": st}
    try:
        dec = await world._dec(await world._gen())
        # Idempotency gate: if an active stockpile already occupies this exact footprint,
        # reuse it instead of creating a duplicate overlapping pile.
        existing = None
        try:
            for sp in await LO.list_stockpiles(dec):
                if (not sp.get("marked_for_removal")
                        and int(sp["x0"]) == x0 and int(sp["x1"]) == x1
                        and int(sp["y0"]) == y0 and int(sp["y1"]) == y1
                        and int(sp["elev"]) == e):
                    existing = sp
                    break
        except Exception:
            existing = None
        if existing is not None:
            res = {"building_id": int(existing["id"]), "extent": existing["extent"],
                   "already_exists": True}
        else:
            res = await LO.create_stockpile(dec, x0, y0, e, w, h)
        has_cfg = any(body.get(k) is not None for k in (
            "allow", "deny", "max_wheelbarrows", "max_barrels", "max_bins"
        )) or bool(body.get("all_categories", False))
        if has_cfg and res.get("building_id") is not None:
            cfg = await LO.configure_stockpile(
                dec, int(res["building_id"]),
                allow=body.get("allow"), deny=body.get("deny"),
                all_categories=bool(body.get("all_categories", False)),
                max_wheelbarrows=body.get("max_wheelbarrows"),
                max_barrels=body.get("max_barrels"),
                max_bins=body.get("max_bins"),
            )
            res["configured"] = cfg.get("line")
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e2:
        return {"success": False, "error": str(e2)}
    world.cache.reset()
    world.forget_band()
    return {"success": True, "site_gate": f"dug {dug}/{total}", **res}


@app.post("/resize-building", operation_id="resize_building")
async def resize_building(request: ResizeBuildingRequest):
    """Resize a building - stockpiles included - with the game's own setSize. constructBuilding with nominal=true makes a 1x1 stockpile no matter what w/h you pass (measured), so big piles need this call afterwards."""
    body = await _as_dict(request)
    try:
        dec = await world._dec(await world._gen())
        return await LO.resize_building(dec, int(body["building_id"]), int(body["w"]), int(body["h"]))
    except LO.LuaOpError as e:
        return {"success": False, "error": str(e)}


@app.post("/repair-stockpile-settings", operation_id="repair_stockpile_settings")
async def repair_stockpile_settings(request: BuildingIdRequest):
    """Allocate the per-category material arrays the game's stockpile custom-settings UI walks. Writing only settings.flags leaves them size 0 while the category flag is true, and the UI then dereferences an empty vector - that is the crash (SIGSEGV at 0xa in render_things with overlay on the stack) the player hit by clicking Custom on a Lua-configured pile. Call this after configure-stockpile; the acceptance test is the custom button opening."""
    body = await _as_dict(request)
    try:
        dec = await world._dec(await world._gen())
        return await LO.repair_stockpile_settings(dec, int(body["building_id"]))
    except LO.LuaOpError as e:
        return {"success": False, "error": str(e)}


@app.get("/stockpiles", operation_id="list_stockpiles")
async def list_stockpiles():
    """Every stockpile with its BUILDING ID, extents, accepted categories and container limits. The id is what configure_stockpile / resize / remove need, and no other read gives it."""
    dec = await world._dec(await world._gen())
    try:
        rows = await LO.list_stockpiles(dec)
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e), "stockpiles": []}
    return {"success": True, "count": len(rows), "stockpiles": rows}


@app.post("/configure-stockpile", operation_id="configure_stockpile")
async def configure_stockpile(request: ConfigureStockpileRequest, raw_request: Request):
    """Set a stockpile's accepted categories (`allow`/`deny`/`all_categories`: `ammo`, `animals`, `armor`, `bars_blocks`, `cloth`, `coins`, `corpses`, `finished_goods`, `food`, `furniture`, `gems`, `leather`, `misc`, `refuse`, `sheet`, `stone`, `weapons`, `wood`) and container limits (`max_wheelbarrows`, `max_barrels`, `max_bins`), verified via `list_stockpiles`."""
    body = await _as_dict(request)
    # The gate reads the RAW body, not the validated model: `_as_dict` uses
    # exclude_none=True, so absent allow/deny disappear from `body` entirely and a check on
    # `body` passes while the call still means TAKE NOTHING (measured twice on 2026-11-22 -
    # my own {"building_id":3} probe emptied pile 3 the second time).
    try:
        raw_body = await raw_request.json()
    except Exception:
        raw_body = {}
    has_cat_keys = isinstance(raw_body, dict) and any(k in raw_body for k in ("allow", "deny", "all_categories"))
    has_container_keys = isinstance(raw_body, dict) and any(
        k in raw_body for k in ("max_wheelbarrows", "max_barrels", "max_bins")
    )
    if isinstance(raw_body, dict) and not has_cat_keys and not has_container_keys:
        return {"success": False,
                "error": "say what to accept: allow=[...], deny=[...] (with all_categories=true), "
                         "all_categories=true, or container limits (max_bins/max_barrels/max_wheelbarrows). "
                         "An empty request means TAKE NOTHING and is not sent.",
                "received_keys": sorted(raw_body.keys()) if isinstance(raw_body, dict) else [],
                "known_keys": ["allow", "deny", "all_categories", "max_wheelbarrows",
                               "max_barrels", "max_bins", "building_id"],
                "categories": LO.CATEGORIES}
    # The request model is extra="allow", so a wrong key is NOT an error: sending
    # {"categories": [...]} (my own call, 2026-11-22) silently became allow=None/deny=None,
    # which means TAKE NOTHING - the write landed and pile 3 was emptied, answered
    # "success". Refuse a body that does not say what the pile should accept.
    try:
        dec = await world._dec(await world._gen())
        bid = int(body["building_id"])
        res = await LO.configure_stockpile(
            dec, bid, allow=body.get("allow"), deny=body.get("deny"),
            all_categories=bool(body.get("all_categories", False)),
            max_wheelbarrows=body.get("max_wheelbarrows"),
            max_barrels=body.get("max_barrels"), max_bins=body.get("max_bins"),
            allow_crash_categories=bool(body.get("allow_crash_categories", False)),
            update_categories=has_cat_keys)
    except LO.LuaOpError as e:
        return {"success": False, "error": str(e)}
    # VERDICT BY READING THE GAME: the write's own reply is not evidence.
    write_note = None
    try:
        row = next((r for r in await LO.list_stockpiles(dec) if int(r["id"]) == bid), None)
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        row, write_note = None, str(e)
    if row is None:
        return {"success": False, "verdict": "UNVERIFIED", "building_id": bid,
                "error": "the pile reads produced NO DATA: the configuration is not proven",
                "write_reply": write_note}
    return {"success": True, "verdict": "configured (verified against the live pile read)",
            "building_id": bid, "accepts": row.get("accepts"),
            "max_bins": row.get("max_bins"), "max_barrels": row.get("max_barrels"),
            "max_wheelbarrows": row.get("max_wheelbarrows"), "write_reply": write_note}


@app.get("/survey", operation_id="survey")
async def survey(elev: int = Query(...), x0: int = Query(...), y0: int = Query(...),
                 x1: int = Query(...), y1: int = Query(...)):
    """Pre-flight check for `[x0..x1, y0..y1]` at `elev`: returns tile category counts (`rock`, `floor`, `hidden`, `open`, etc.), `dug`, `to_dig`, `safe_to_designate`, and blocked samples."""
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

@app.get("/building-def-list", operation_id="get_building_def_list")
async def get_building_def_list(query: str = Query("", description="substring filter, e.g. 'meet' or 'zone'")):
    """THE BUILDABLE NAMES OF THIS BUILD, straight from the game's own definition table.

    place_building resolves a friendly name through this same table, and its rejection
    text has always said "get one from get_building_def_list" - while no such endpoint
    existed, so an agent hit a dead end with a pointer to a tool it could not call.
    RFR gives each activity zone its OWN building type (the lists are named
    ZONE_MEETING_HALL, ZONE_PASTURE, ...), which is why a request for "Meeting Area"
    resolved to nothing: the name is not "Meeting Area" in this build.
    """
    dec = await world._dec(await world._gen())
    q = str(query).strip().lower()
    rows = []
    for (bt, sub, cus), nm in sorted(dec.building.items(), key=lambda kv: str(kv[1])):
        if not q or q in str(nm).lower():
            rows.append({"name": str(nm), "building_type": int(bt),
                         "subtype": int(sub), "custom": int(cus)})
    return {"count": len(rows), "query": query, "buildings": rows[:400]}


@app.get("/zones", operation_id="list_zones")
async def list_zones():
    """Every activity zone with its KIND (meeting/dump/pen/pond/water source/...) and size. The building census calls them all "Zone", so this is the only read that tells a meeting area from an empty marked rectangle."""
    dec = await world._dec(await world._gen())
    try:
        rows = await LO.list_zones(dec)
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e), "zones": []}
    return {"success": True, "count": len(rows), "zones": rows}


@app.post("/remove-zone", operation_id="remove_zone")
async def remove_zone(request: RemoveZoneRequest):
    """Remove an activity zone by `building_id` (requires `acknowledge_risk=True` due to DFHack pointer cleanup; see `docs/tool-notes.md`) and verify against `list_zones`."""
    body = await _as_dict(request)
    bid = int(body["building_id"])
    if body.get("acknowledge_risk") is not True:
        return {"success": False,
                "error": f"remove_zone is gated: this path crashed the fort once "
                         f"(crash_2026-11-21-22-53-41.txt) and still mutates unit refs before "
                         f"deconstruct; zone {bid} was NOT touched",
                "hint": "remove it in-game (z -> the zone -> remove), or pass acknowledge_risk: true",
                "requires": "acknowledge_risk"}
    dec = await world._dec(await world._gen())
    # Never remove blind: is the id even a zone right now?
    try:
        zones = await LO.list_zones(dec)
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "verdict": "UNVERIFIED",
                "error": f"the zone list produced NO DATA, so removal is refused: {e}"}
    mine = [z for z in zones if int(z["id"]) == bid]
    if not mine:
        return {"success": False, "verdict": "not a live zone",
                "error": f"no zone with building_id {bid} in the live list "
                         f"({', '.join(str(z['id']) for z in zones) or 'no zones'}); nothing removed"}
    try:
        res = await LO.remove_zone(dec, bid)
        write_note = None
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        res, write_note = {"error": str(e)}, str(e)
    world.cache.reset()
    present = await _building_present(bid)
    if present is False:
        return {"success": True, "verdict": "removed", "removed_zone_id": bid,
                "was": mine[0].get("type_name"),
                "verified_by": "the id is absent from the live zone list",
                "write_reply": write_note}
    if present is None:
        return {"success": False, "verdict": "UNVERIFIED", "removed_zone_id": bid,
                "error": "the zone reads produced NO DATA: removal not proven",
                "write_reply": write_note}
    return {"success": False, "verdict": "still present", "removed_zone_id": bid,
            "error": res.get("error") or "the zone is still in the live list",
            "write_reply": write_note}


@app.post("/create-zone", operation_id="create_zone")
async def create_zone(request: CreateZoneRequest):
    """Create an activity zone (`meeting`, `pasture`, `pit`, `pond`, `dump`, `watersource`, `office`, `bedroom`, `dining`, `tomb`, etc.) at `(x, y, elev, w, h)` on dug ground (requires `acknowledge_risk=True`)."""
    body = await _as_dict(request)
    # ZONE WRITES ARE OPT-IN. Measured twice on this fort (2026-11-21): the game crashed
    # after `create_zone` calls - allocInstance -> setSize -> constructAbstract -> write
    # z.type -> notifyCivzoneModified. A zone made that way is not created through the
    # game's own zone-creation path, and we have no evidence it is safe on a live save.
    # A tool that can destroy a fort must not fire because a caller asked for a rung.
    if body.get("acknowledge_risk") is not True:
        return {"success": False,
                "error": "create_zone is gated: the last two zone writes on this fort "
                         "crashed the game (allocInstance/constructAbstract is not the "
                         "game's own zone creation path)",
                "hint": "place it in-game (z -> Meeting Area -> draw rect -> Accept), or "
                        "pass acknowledge_risk: true after testing on a THROWAWAY save",
                "requires": "acknowledge_risk"}
    x0, y0, e = int(body["x"]), int(body["y"]), int(body["elev"])
    w, h = max(1, int(body.get("w", 5))), max(1, int(body.get("h", 5)))
    x1, y1 = x0 + w - 1, y0 + h - 1
    kind_str = str(body["kind"]).strip().lower()
    if body.get("check_site", True) is not False and kind_str not in ("watersource", "water", "fishing", "pond", "pit"):
        gate_err = await _verify_footprint_ground(e, x0, y0, w, h, label="zone")
        if gate_err is not None:
            return gate_err
        if kind_str in ("pen", "pasture"):
            try:
                gp = await LO.check_pasture_grass(e, x0, y0, x1, y1)
                if int(gp.get("grass_tiles", 0)) == 0:
                    return {"success": False,
                            "error": f"pasture site x{x0}..{x1} y{y0}..{y1} @elev{e} has 0 grass tiles "
                                     f"(bare_or_non_grass={gp.get('bare_or_non_grass_tiles')}); grazing animals "
                                     f"starve on bare stone or dug floor",
                            "hint": "place Pen/Pasture zones on surface grass tiles (tile_at material GRASS_*), "
                                    "or pass check_site: false to override",
                            "gate": "pasture requires grass_tiles > 0",
                            "measured": gp}
            except Exception:
                pass
    try:
        dec = await world._dec(await world._gen())
        existing_zones: list[dict] = []
        try:
            existing_zones = await LO.list_zones(dec)
        except Exception:
            existing_zones = []
        # Idempotency check (tool_missing.md #16): if an active zone already occupies
        # this exact footprint, return it instead of creating a duplicate zone.
        for zz in existing_zones:
            if (not zz.get("marked_for_removal")
                    and int(zz["x0"]) == x0 and int(zz["x1"]) == x1
                    and int(zz["y0"]) == y0 and int(zz["y1"]) == y1
                    and int(zz["elev"]) == e):
                return {"success": True, "already_exists": True,
                        "building_id": int(zz["id"]), "kind": kind_str,
                        "zone_type": zz.get("type_name"),
                        "extent": f"{x0}..{x1},{y0}..{y1}",
                        "rect_tiles": w * h}
        _ROOM_ZONE_KINDS = {
            "office", "study", "bedroom", "dining", "dininghall",
            "tomb", "meeting", "meetingarea", "tavern", "temple", "hospital", "guildhall",
        }
        _INDOOR_REQUIRED_KINDS = {
            "office", "study", "bedroom", "dining", "dininghall",
            "meeting", "meetingarea", "hospital", "temple", "tavern", "guildhall",
        }
        enclosure_info: dict[str, Any] | None = None
        if kind_str in _ROOM_ZONE_KINDS:
            # #85: refuse overlapping another active room zone (especially owned noble rooms)
            if body.get("allow_overlap") is not True:
                for zz in existing_zones:
                    if zz.get("marked_for_removal") or int(zz.get("elev", -999)) != e:
                        continue
                    ztype = str(zz.get("type_name") or "").lower()
                    if not any(rk in ztype for rk in ("office", "bedroom", "dining", "tomb", "meeting", "tavern", "temple", "hospital", "guild")):
                        continue
                    zx0, zx1 = int(zz["x0"]), int(zz["x1"])
                    zy0, zy1 = int(zz["y0"]), int(zz["y1"])
                    if not (x1 < zx0 or x0 > zx1 or y1 < zy0 or y0 > zy1):
                        return {
                            "success": False,
                            "gate": "overlapping room zone",
                            "error": (
                                f"zone x{x0}..{x1} y{y0}..{y1} @elev{e} overlaps existing zone #{zz['id']} "
                                f"({zz.get('type_name')} {zx0}..{zx1},{zy0}..{zy1})"
                            ),
                            "hint": "give each noble/room its own enclosed space separated by >=1 wall tile, or pass allow_overlap=true",
                            "overlapping_zone": zz,
                        }
            # #77 & #86: check roof above (elev+1) and perimeter walls/ramps
            open_above = 0
            indoors = True
            ramp_in_box = 0
            perim_non_wall = None
            try:
                sv_above = await world.survey(e + 1, x0, y0, x1, y1)
                open_above = int((sv_above.get("counts") or {}).get("open", 0))
                indoors = (open_above == 0)
            except Exception:
                pass
            if kind_str in _INDOOR_REQUIRED_KINDS and not indoors and body.get("check_site", True) is not False and body.get("allow_outdoors") is not True:
                return {
                    "success": False,
                    "gate": "indoor room requires solid roof above",
                    "error": f"{kind_str} zone x{x0}..{x1} y{y0}..{y1} @elev{e} has {open_above} open-air tiles above it (outdoors)",
                    "hint": "dig underground under solid rock (e.g. elev <= surface - 2) or pass allow_outdoors=true to override",
                    "open_above": open_above,
                }
            try:
                sv_in = await world.survey(e, x0, y0, x1, y1)
                ramp_in_box = int((sv_in.get("counts") or {}).get("ramp", 0))
                sv_perim = await world.survey(e, x0 - 1, y0 - 1, x1 + 1, y1 + 1)
                c_in = sv_in.get("counts") or {}
                c_all = sv_perim.get("counts") or {}
                wall_all = int(c_all.get("rock", 0)) + int(c_all.get("wall", 0)) + int(c_all.get("hidden", 0))
                wall_in = int(c_in.get("rock", 0)) + int(c_in.get("wall", 0)) + int(c_in.get("hidden", 0))
                perim_total = (w + 2) * (h + 2) - (w * h)
                perim_non_wall = max(0, perim_total - max(0, wall_all - wall_in))
            except Exception:
                pass
            enclosure_info = {
                "indoors": indoors,
                "open_above": open_above,
                "ramp_tiles_in_box": ramp_in_box,
                "perimeter_non_wall_tiles": perim_non_wall,
            }
        res = await LO.create_zone(dec, str(body["kind"]), x0, y0, e, w, h)
        if enclosure_info is not None:
            res["enclosure"] = enclosure_info
        if body.get("owner_unit_id") is not None and res.get("building_id") is not None:
            own = await LO.assign_zone_owner(int(res["building_id"]), int(body["owner_unit_id"]))
            res["owner"] = {
                "owner_id": own.get("owner_id"),
                "owner_name": own.get("owner_name"),
                "room_description": own.get("room_description"),
            }
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e)}
    world.cache.reset()
    world.forget_band()
    return {"success": True, **res}


@app.get("/labor-list", operation_id="labor_list")
async def labor_list():
    """Every labor name you can pass to work_details / unit_labors (the DFHack unit_labor enum, decoded from the game). Use these exact strings - MINE, MASON, CARPENTER, PLANT, IRRIGATE, FARMER, CRAFTSMAN, TRANSPORT, ..."""
    dec = await world._dec(await world._gen())
    labors = dec.flags.get("unit_labor", {})
    return {"count": len(labors), "labors": sorted(labors.values())}

# REMOVED 2026-11-22 (the player's call): `/set-unit-labors`.
# It wrote `unit.status.labors` - a layer the game RECOMPUTES from the work details (measured: a
# census of 5 MINE-holders fell to the 2 ticked dwarves the moment a tick was added), so its
# `applied: N` described validated names, never the fort. "We can not have a tool that is wrong"
# has two answers: make it honest, or take it away. This one is taken away.
# The real levers stay: `GET /work-details` (assign = the green tick; set-labor = what a detail
# grants), `GET /unit-labors` (the census - read-only, and read-only is right for a derived layer),
# `GET /unit-professions` (the roster; its `set` is gated behind acknowledge_risk).
# RPC `SetUnitLabors` is still imported: the plugin's labor census and labor_list use the decoders,
# and the method itself remains available to anyone who reads the game's data and knows the layer.


@app.get("/work-order-link", operation_id="work_order_link")
async def work_order_link(order_id: int = Query(...), workshop_id: int = Query(...)):
    """Attach a workshop to a manager work order so the game can run it.

    Why: `workorder`-created orders sit at `active: false, validated: false, workshop_id: -1`
    (tool_missing #50). Orders live at `df.global.world.manager_orders.all`; `workshop_id` is a real
    writable field, verified by reading the order back after the write.
    """
    try:
        res = await LO.order_set_workshop(order_id=order_id, workshop_id=workshop_id)
        return {"success": True, **res}
    except LO.LuaOpError as e:
        return {"success": False, "error": str(e)}


_WORKORDER_JOB_ALIASES = {
    "makechair": ("ConstructThrone", {}),
    "chair": ("ConstructThrone", {}),
    "throne": ("ConstructThrone", {}),
    "makebed": ("ConstructBed", {}),
    "bed": ("ConstructBed", {}),
    "makedoor": ("ConstructDoor", {}),
    "door": ("ConstructDoor", {}),
    "maketable": ("ConstructTable", {}),
    "table": ("ConstructTable", {}),
    "makebin": ("ConstructBin", {}),
    "bin": ("ConstructBin", {}),
    "makecabinet": ("ConstructCabinet", {}),
    "makebox": ("ConstructChest", {}),
    "makechest": ("ConstructChest", {}),
    "makecoffin": ("ConstructCoffin", {}),
    "makepot": ("MakeTool", {"item_subtype": "ITEM_TOOL_LARGE_POT", "material": "INORGANIC"}),
    "rockpot": ("MakeTool", {"item_subtype": "ITEM_TOOL_LARGE_POT", "material": "INORGANIC"}),
    "largepot": ("MakeTool", {"item_subtype": "ITEM_TOOL_LARGE_POT", "material": "INORGANIC"}),
    "brewdrink": ("CustomReaction", {"reaction": "BREW_DRINK_FROM_PLANT"}),
    "brew": ("CustomReaction", {"reaction": "BREW_DRINK_FROM_PLANT"}),
}


@app.post("/work-order", operation_id="work_order")
async def work_order(request: WorkOrderRequest):
    """Manage manager work orders (`action='create'|'list'|'cancel'`): e.g. `{"job":"MakeBarrel","amount":5}`, `{"action":"list"}`, or `{"action":"cancel","order_id":0}`."""
    body = await _as_dict(request)
    act = str(body.get("action") or ("create" if ("job" in body or "json" in body) else "list")).strip().lower()
    if act in ("list", "cancel"):
        try:
            res = await LO.manage_work_orders(action=act,
                                              order_id=body.get("order_id"),
                                              job=body.get("job"))
            return {"success": True, **res}
        except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
            return {"success": False, "error": str(e)}

    # Verified live: this build's workorder.lua parses its FIRST argument as JSON, so the
    # documented `workorder <jobtype> <amount>` positional form fails with
    # "can't parse JSON at byte 1 of: CollectRock 10". Build the JSON form ourselves.
    if "json" in body:
        spec = body["json"] if isinstance(body["json"], str) else json.dumps(body["json"])
    else:
        if "job" not in body:
            return {"error": "give job (a df.job_type name), json, or action='list'|'cancel'"}
        raw_job = str(body["job"]).strip()
        alias_norm = raw_job.lower().replace("_", "").replace(" ", "")
        spec_obj: dict[str, Any] = {}
        if alias_norm in _WORKORDER_JOB_ALIASES:
            mapped_job, defaults = _WORKORDER_JOB_ALIASES[alias_norm]
            spec_obj["job"] = mapped_job
            spec_obj.update(defaults)
        else:
            spec_obj["job"] = raw_job
        if "amount" in body:
            spec_obj["amount_total"] = int(body["amount"])
        for k in ("material", "material_category", "item_type", "item_subtype", "reaction", "workshop_id", "quantity"):
            if k in body and body[k] is not None:
                spec_obj[k] = body[k]
        if "material" in spec_obj:
            mat_l = str(spec_obj["material"]).strip().lower()
            if mat_l in ("wood", "log", "logs"):
                spec_obj.pop("material", None)
                spec_obj.setdefault("material_category", ["wood"])
            elif mat_l in ("rock", "stone", "boulder", "boulders"):
                spec_obj["material"] = "INORGANIC"
        spec = json.dumps(spec_obj)
    args = [spec]
    try:
        await RunCommand(input=CoreRunCommandRequest(command="workorder", arguments=args))
    except DFHackError as e:
        return {"success": False, "error": str(e),
                "hint": "job name must be a df.job_type; 'workorder -l <filter>' lists types (its output streams to the server log)"}
    readback = {}
    try:
        readback = await LO.manage_work_orders(action="list")
    except Exception:
        pass
    out = {"success": True, "args": args,
           "note": "order enqueued; workorder's console reply streams to this server's log"}
    if readback:
        out["has_manager"] = readback.get("has_manager")
        out["orders_count"] = readback.get("count")
        out["orders"] = readback.get("orders")
        if readback.get("has_manager") is False:
            out["warning"] = "No MANAGER noble is currently appointed; appoint one with manage_nobles(action='auto_assign') so work orders get validated."
    return out

@app.get("/world-cache-reset", operation_id="world_cache_reset")
async def world_cache_reset():
    """Drop the world-model caches (block cache, decode tables, fort band, delta snapshot,
    stack memo) and DFHack's map hashes. Call after a big in-game change such as `reveal`,
    a save reload, or a fort redesign - a save swap does this by itself (see `get_status`)."""
    scope = await world.drop_all_views()
    world._sig_prev = None          # re-baseline the identity check too
    sig = await world.signature(force=True)
    return {"reset": True, "scope": scope, "reloads": world.reloads,
            "worldSignature": {k: sig.get(k) for k in
                               ("save", "world", "mode", "tiles", "year", "tick")} if sig else None,
            "note": "every derived view is gone; the next read re-fetches the world from the game"}


@app.get("/game-paths", operation_id="game_paths")
async def game_paths():
    """Discover the running game's filesystem paths (df_path, save_path, crashlogs_dir,
    blueprints_dir, scripts_dir) and whether `deployables/scripts/dfhack_mcp.lua` is deployed
    to `<gamedir>/hack/scripts/`."""
    try:
        res = await LO.game_paths()
        return {"success": True, **res}
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e)}


@app.get("/building-status", operation_id="building_status")
async def building_status(building_id: int | None = Query(None, description="Specific building ID to inspect; omit to list unfinished/planned buildings"),
                          only_incomplete: bool = Query(True, description="When True (default), list only buildings still under construction / awaiting materials"),
                          limit: int = Query(25, ge=1, le=100, description="Max buildings to return")):
    """Diagnose unfinished buildings (`build_stage`, attached items, active jobs, suspended flag, missing materials, crew labor/pick bottlenecks, and recent cancellations)."""
    try:
        res = await LO.building_status(building_id=building_id,
                                       only_incomplete=only_incomplete,
                                       limit=limit)
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e)}
    # Enrich with crew labor/pick bottlenecks and recent cancellation announcements
    diagnostics: dict = {"crew": {}, "bottlenecks": [], "recent_cancellations": []}
    try:
        gen = await world._gen()
        dec = await world._dec(gen)
        units = await WM.load_units(dec, gen, world._state)
        citizens = [u for u in units if u.get("race") == "dwarf" and not u.get("hostile")]
        miners = sum(1 for u in citizens if "MINE" in u.get("labors", []))
        masons = sum(1 for u in citizens if "MASON" in u.get("labors", []))
        carpenters = sum(1 for u in citizens if "CARPENTER" in u.get("labors", []))
        mechanics = sum(1 for u in citizens if "DETAIL" in u.get("labors", []) or "SIEGE_OPERATE" in u.get("labors", []) or "MECHANIC" in " ".join(u.get("labors", [])))
        obs_st = {}
        try:
            obs_st = await LO.observe_status_module()
        except Exception:
            pass
        picks = obs_st.get("picks")
        crew_health = res.get("crew_health") or {}
        miners_wounded = int(crew_health.get("miners_wounded", 0))
        miners_avail = int(crew_health.get("miners_available", miners))
        cits_wounded = int(crew_health.get("citizens_wounded", 0))
        diagnostics["crew"] = {
            "dwarves": len(citizens),
            "miners": miners,
            "miners_available": miners_avail,
            "miners_wounded": miners_wounded,
            "citizens_wounded": cits_wounded,
            "picks": picks,
            "masons": masons,
            "carpenters": carpenters,
        }
        if crew_health.get("wounded_citizens"):
            diagnostics["crew"]["wounded_citizens"] = crew_health["wounded_citizens"]
        if miners == 0:
            diagnostics["bottlenecks"].append("no miners assigned (MINE labor = 0)")
        if miners > 0 and miners_wounded > 0 and miners_avail == 0:
            diagnostics["bottlenecks"].append(f"ALL miners are wounded ({miners_wounded}/{miners}) — no healthy miner can dig")
        elif miners_wounded > 0:
            diagnostics["bottlenecks"].append(f"{miners_wounded} of {miners} miners are wounded")
        if cits_wounded > 0:
            diagnostics["bottlenecks"].append(f"{cits_wounded} of {len(citizens)} citizens are wounded")
        if picks is not None and picks < miners:
            diagnostics["bottlenecks"].append(f"pick shortage: {picks} pick(s) for {miners} designated miner(s)")
        for b in res.get("buildings", []):
            if not b.get("complete"):
                if b.get("suspended"):
                    diagnostics["bottlenecks"].append(f"building #{b['id']} ({b['name']}) construction job is SUSPENDED")
                if b.get("missing_item_filters"):
                    need = ", ".join(f"{f['item_type']}x{f['quantity_needed']}" for f in b["missing_item_filters"])
                    diagnostics["bottlenecks"].append(f"building #{b['id']} ({b['name']}) awaiting materials: {need}")
        reps = await WM.load_reports(gen, world._state, limit=30)
        cancellations = [
            r for r in reps
            if any(k in r.lower() for k in ("cancels", "unable to complete", "needs ", "inaccessible", "damp", "warm"))
        ]
        diagnostics["recent_cancellations"] = cancellations[-10:]
    except Exception as ex:
        diagnostics["notes"] = f"partial diagnostics: {ex}"
    return {"success": True, **res, "diagnostics": diagnostics}


@app.get("/transitions", operation_id="transitions")
async def transitions(e0: int | None = Query(None, description="Optional lowest in-game Elevation to filter"),
                      e1: int | None = Query(None, description="Optional highest in-game Elevation to filter")):
    """Report transition-verified excavated tiles (`wall/fog -> floor/stair/ramp`) from the
    persistent SQLite TransitionStore (`PLAN-MODEL.md` Technique 1). Unlike a raw floor census,
    this counts only tiles observed transitioning from solid rock/fog to floor or stairs, so
    natural subterranean caverns are never mistaken for player-dug tunnels."""
    sig = await world.signature()
    skey = (sig or {}).get("key", "default")
    return {"success": True, **world.transitions.summary(skey, elev0=e0, elev1=e1)}


@app.post("/configure-farmplot", operation_id="configure_farmplot")
async def configure_farmplot(request: ConfigureFarmplotRequest):
    """Set or inspect a Farm Plot's seasonal crops (default: plump helmets in all seasons,
    `crop='MUSHROOM_HELMET_PLUMP'`, `all_seasons=True`), with full read-back of all four
    seasons and available plant raws. Powered by deployables/scripts/dfhack_mcp.lua."""
    body = await _as_dict(request)
    try:
        res = await LO.configure_farmplot(
            building_id=body.get("building_id"),
            crop=body.get("crop"),
            all_seasons=bool(body.get("all_seasons", True)),
            spring=bool(body.get("spring", False)),
            summer=bool(body.get("summer", False)),
            autumn=bool(body.get("autumn", False)),
            winter=bool(body.get("winter", False)),
        )
        return {"success": True, **res}
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e)}


@app.post("/assign-zone-owner", operation_id="assign_zone_owner")
async def assign_zone_owner(request: AssignZoneOwnerRequest):
    """Assign or inspect the citizen owner of an activity zone (`Office`, `Bedroom`, `DiningHall`, `Tomb`). Managers require an assigned `Office` zone with a chair once population > 20."""
    body = await _as_dict(request)
    try:
        res = await LO.assign_zone_owner(
            building_id=int(body["building_id"]),
            unit_id=body.get("unit_id"),
        )
        return {"success": True, **res}
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e)}


@app.post("/assign-pasture", operation_id="assign_pasture")
async def assign_pasture(request: AssignPastureRequest):
    """Assign, unassign, or clear tame grazing animals (`all_grazers=True`, `unit_ids`, or `species`) on a Pen/Pasture zone."""
    body = await _as_dict(request)
    try:
        res = await LO.assign_pasture(
            building_id=body.get("building_id"),
            unit_ids=body.get("unit_ids"),
            all_grazers=bool(body.get("all_grazers", True)),
            species=body.get("species"),
            action=str(body.get("action", "assign")),
        )
        return {"success": True, **res}
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e)}


@app.post("/manage-nobles", operation_id="manage_nobles")
async def manage_nobles(request: ManageNoblesRequest):
    """List, rank candidates, assign, or auto-assign fortress noble positions (`MANAGER`, `BOOKKEEPER`,
    `BROKER`, `CHIEF_MEDICAL_DWARF`, `MILITIA_COMMANDER`, `SHERIFF`, etc.) and set bookkeeper
    precision (`bookkeeper_settings=4` = maximum precision)."""
    body = await _as_dict(request)
    try:
        res = await LO.manage_nobles(
            action=str(body.get("action", "list")),
            position=body.get("position"),
            unit_id=body.get("unit_id"),
            bookkeeper_settings=body.get("bookkeeper_settings"),
            reassign_unskilled=bool(body.get("reassign_unskilled", False)),
        )
        return {"success": True, **res}
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e)}


@app.post("/workshop-job", operation_id="workshop_job")
async def workshop_job(request: WorkshopJobRequest):
    """Manage direct workshop jobs and crafted stock (`list`, `stock`, `queue`, `cancel`). `queue` works before a Manager or Office exists (e.g. first Chair and Barrels)."""
    body = await _as_dict(request)
    try:
        res = await LO.workshop_job(
            action=str(body.get("action", "list")),
            task=body.get("task"),
            building_id=body.get("building_id"),
            material=body.get("material"),
            count=int(body.get("count", 1)),
            repeat_job=bool(body.get("repeat_job", False)),
            job_id=body.get("job_id"),
        )
        return {"success": True, **res}
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e)}


async def _run_dfhack_cmd(cmd: str, arguments: list[str] | None = None) -> dict:
    drain_replies()
    args = [str(a) for a in (arguments or [])]
    if str(cmd).strip().lower() == "prioritize":
        expanded: list[str] = []
        for a in args:
            expanded.extend(shlex.split(a) if any(ch.isspace() for ch in a) else [a])
        args = expanded
    try:
        await RunCommand(input=CoreRunCommandRequest(command=cmd, arguments=args))
        output_text = "\n".join(drain_replies()).strip()
        if str(cmd).strip().lower() == "prioritize" and "ignoring unknown job type" in output_text.lower():
            return {
                "command": f"{cmd} {' '.join(args)}".strip(),
                "ok": False,
                "error": "prioritize ignored unknown job type(s)",
                "output": output_text,
                "hint": "Valid examples: Dig, CarveUpDownStaircase, ConstructBuilding, CustomReaction --reaction-name BREW_DRINK_FROM_PLANT",
            }
        return {"command": f"{cmd} {' '.join(args)}".strip(), "ok": True, "output": output_text}
    except (DFHackError, DFHackTransportError) as e:
        out = getattr(e, "replies", None) or drain_replies()
        return {"command": f"{cmd} {' '.join(args)}".strip(), "ok": False,
                "error": str(e), "output": "\n".join(out).strip()}


@app.post("/automation", operation_id="dfhack_automation")
async def dfhack_automation(request: AutomationRequest):
    """Inspect, enable, disable, or configure safe DFHack automation helpers (`autofarm`, `seedwatch`, `autochop`, `autobutcher`, `buildingplan`, `suspendmanager`, `tailor`, `autonestbox`, `autoslab`, `prioritize`, `logistics`, `ban-cooking`, `orders`) or apply `early_fort`/`standard_orders` presets."""
    body = await _as_dict(request)
    act = str(body.get("action", "status")).strip().lower()
    plugin = str(body.get("plugin") or "").strip().lower()

    if act == "status":
        en = await _run_dfhack_cmd("enable", [])
        states = {}
        for line in (en.get("output") or "").splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                kn = k.strip().lower()
                if kn in SAFE_AUTOMATION_PLUGINS:
                    states[kn] = v.strip()
        af = await _run_dfhack_cmd("autofarm", ["status"])
        sw = await _run_dfhack_cmd("seedwatch", ["status"])
        ac = await _run_dfhack_cmd("autochop", ["status"])
        return {
            "success": True,
            "action": "status",
            "plugins": states,
            "autofarm": af.get("output"),
            "seedwatch": sw.get("output"),
            "autochop": ac.get("output"),
        }

    if act in ("enable", "disable", "configure"):
        if plugin not in SAFE_AUTOMATION_PLUGINS:
            return {
                "success": False,
                "blocked": True,
                "error": f"plugin '{plugin}' is not in SAFE_AUTOMATION_PLUGINS",
                "allowed": sorted(SAFE_AUTOMATION_PLUGINS),
            }
        if act in ("enable", "disable"):
            res = await _run_dfhack_cmd(act, [plugin])
            return {"success": res["ok"], "action": act, "plugin": plugin, **res}
        res = await _run_dfhack_cmd(plugin, [str(a) for a in body.get("args", [])])
        return {"success": res["ok"], "action": act, "plugin": plugin, **res}

    if act == "apply_preset":
        preset = str(body.get("preset") or "early_fort").strip().lower()
        steps_cmds: list[tuple[str, list[str]]] = []
        if preset == "early_fort":
            steps_cmds = [
                ("enable", ["autofarm"]),
                ("autofarm", ["default", "50"]),
                ("enable", ["seedwatch"]),
                ("seedwatch", ["all", "30"]),
                ("ban-cooking", ["seeds"]),
                ("ban-cooking", ["brew"]),
                ("enable", ["suspendmanager"]),
                ("enable", ["buildingplan"]),
                ("enable", ["autonestbox"]),
                ("enable", ["autoslab"]),
                ("enable", ["prioritize"]),
                ("enable", ["tailor"]),
            ]
            if body.get("include_autochop"):
                steps_cmds.extend([("autochop", ["target", "30", "60"]), ("enable", ["autochop"])])
        elif preset == "standard_orders":
            steps_cmds = [("orders", ["import", "library/basic"])]
            if body.get("include_rockstock"):
                steps_cmds.append(("orders", ["import", "library/rockstock"]))
        else:
            return {"success": False, "error": f"unknown preset '{preset}' (expected 'early_fort' or 'standard_orders')"}
        results = [await _run_dfhack_cmd(c, a) for c, a in steps_cmds]
        out: dict[str, Any] = {
            "success": all(r["ok"] for r in results),
            "action": "apply_preset",
            "preset": preset,
            "steps": results,
        }
        if preset == "standard_orders":
            out["note"] = "Manager orders require a skilled MANAGER noble and an assigned Office zone (with a crafted Chair) to validate."
        return out

    return {"success": False, "error": f"unknown automation action '{act}' (expected status, enable, disable, configure, or apply_preset)"}


@app.get("/alerts", operation_id="alerts_and_wellbeing")
async def alerts_and_wellbeing():
    """Check blocking UI popups, active caravans, invaders, unpastured grazers, strange moods/tantrums, and citizen thirst/hunger/drowsiness/stress/wounds."""
    try:
        res = await LO.alerts_and_wellbeing()
        return {"success": True, **res}
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e)}


@app.post("/gather-and-items", operation_id="gather_and_items")
async def gather_and_items(request: GatherAndItemsRequest):
    """Gather surface plants (`gather_plants`), flag ground items (`dump`/`undump`/`forbid`/`reclaim`), or protect seeds/brewables from cooking (`protect_seeds`)."""
    body = await _as_dict(request)
    try:
        res = await LO.gather_and_items(
            action=str(body.get("action", "gather_plants")),
            x0=body.get("x0"),
            y0=body.get("y0"),
            x1=body.get("x1"),
            y1=body.get("y1"),
            elev=body.get("elev"),
            item_filter=body.get("item_filter"),
            ban_brew=bool(body.get("ban_brew", True)),
        )
        return {"success": True, **res}
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e)}


@app.post("/trade-depot", operation_id="trade_depot")
async def trade_depot(request: TradeDepotRequest):
    """Manage the Trade Depot (`status`, `request_broker`, `mark_goods`, `unmark_goods`, `open_trade`, `barter_status`, `barter_select`, `barter_confirm`, `close_trade`) for caravan hauling, opening barter, selecting goods, and completing trade."""
    body = await _as_dict(request)
    act = str(body.get("action", "status")).lower()
    common_kw = dict(
        building_id=body.get("building_id"),
        item_ids=body.get("item_ids"),
        item_filter=body.get("item_filter"),
        min_value=int(body.get("min_value", 0)),
        max_items=int(body.get("max_items", 25)),
        trader_requested=body.get("trader_requested"),
        anyone_can_trade=body.get("anyone_can_trade"),
        side=str(body.get("side", "fortress")),
        selected=bool(body.get("selected", True)),
    )
    try:
        if act == "open_trade":
            s1 = await LO.trade_depot(action="_open_depot_sheet", **common_kw)
            if not s1.get("already_open"):
                await asyncio.sleep(0.25)
                s2 = await LO.trade_depot(action="_click_trade_button", **common_kw)
                if not s2.get("trade_screen_open"):
                    return {"success": False, "action": "open_trade", **s2}
                await asyncio.sleep(0.25)
            bs = await LO.trade_depot(action="barter_status", **{**common_kw, "max_items": max(80, int(body.get("max_items", 80)))})
            return {"success": bool(bs.get("trade_screen_open")), "action": "open_trade", **bs}
        if act == "barter_confirm":
            c1 = await LO.trade_depot(action="barter_confirm", **common_kw)
            if not c1.get("clicked_button"):
                return {"success": False, **c1}
            await asyncio.sleep(0.2)
            dlg = await LO.trade_depot(action="_accept_confirm_dialog", **common_kw)
            await asyncio.sleep(0.25)
            bs = await LO.trade_depot(action="barter_status", **{**common_kw, "max_items": max(80, int(body.get("max_items", 80)))})
            return {
                "success": True,
                "confirmed": True,
                **c1,
                **dlg,
                "after": bs,
            }
        if act == "close_trade":
            cl = await LO.trade_depot(action="close_trade", **common_kw)
            await asyncio.sleep(0.15)
            if cl.get("trade_screen_open") or cl.get("view_sheet_open"):
                cl = await LO.trade_depot(action="close_trade", **common_kw)
            return {"success": not cl.get("trade_screen_open", False), **cl}
        res = await LO.trade_depot(action=act, **common_kw)
        if act == "status" and res.get("accessible") is False and res.get("x0") is not None:
            try:
                cx = (int(res["x0"]) + int(res["x1"])) // 2
                cy = (int(res["y0"]) + int(res["y1"])) // 2
                ce = int(res["elev"])
                wp = await world.wagon_path(cx, cy, ce)
                res["wagon_access_check"] = wp
                if wp.get("passable"):
                    res["accessibility_note"] = "wagon_path to map edge is clear; DF updates depot.accessible when a caravan/map path check ticks or if depot is on surface"
                else:
                    res["accessibility_note"] = f"no 3-wide wagon path from ({cx},{cy},elev {ce}) to map edge: {wp.get('why') or wp.get('blocked_by') or 'blocked'}"
            except Exception:
                pass
        return {"success": True, **res}
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e)}


class FortPlanRequest(BaseModel):
    action: str = Field(
        "view",
        description=(
            "Action: 'view' (render ASCII floorplan & legend), 'set_hub' (set central shaft), "
            "'add_room' (attach a room with collision check), 'add_template' (food_district | "
            "workshop_wing | bedroom_wing | civic_hall), 'remove_room', 'validate' (check hazards & soil), "
            "'commit' (access-first phased dig designations), or 'clear'."
        ),
    )
    elev: int | None = Field(None, description="Target elevation for view, add_room, add_template, or commit.")
    x: int | None = Field(None, description="Hub center x coordinate (set_hub).")
    y: int | None = Field(None, description="Hub center y coordinate (set_hub).")
    elev_top: int | None = Field(None, description="Hub shaft top elevation (set_hub).")
    elev_bottom: int | None = Field(None, description="Hub shaft bottom elevation (set_hub).")
    radius: int = Field(1, description="Hub landing radius around shaft center (default 1 -> 3x3).")
    name: str | None = Field(None, description="Room name (add_room / remove_room).")
    kind: str = Field("workshop", description="Room kind (workshop, stockpile, farm, still, kitchen, bedroom, dining, office, temple, barracks, hospital, depot, corridor).")
    parent: str = Field("hub", description="Anchor room name or 'hub' to attach to.")
    direction: str = Field("E", description="Attachment/template facing direction: N, S, E, W.")
    width: int | None = Field(None, description="Room width (defaults by kind).")
    height: int | None = Field(None, description="Room height (defaults by kind).")
    corridor_len: int = Field(2, description="Connecting corridor length in tiles (default 2).")
    corridor_width: int = Field(1, description="Connecting corridor width in tiles (default 1).")
    offset: int = Field(0, description="Perpendicular offset along parent face.")
    notes: str = Field("", description="Optional room notes.")
    template: str | None = Field(None, description="District template: bootstrap_camp | food_district | workshop_wing | bedroom_wing | civic_hall.")
    prefix: str = Field("", description="Optional name prefix for template rooms.")
    wall_thickness: int = Field(1, description="Minimum solid wall tiles between rooms (default 1).")
    phase: str = Field("auto", description="Commit phase: auto | shaft | hub_and_corridors | rooms | all.")
    dry_run: bool = Field(False, description="If true, compute designations or Quickfort stats without mutating DF.")
    blueprint: str | None = Field(None, description="Quickfort blueprint path or filter (e.g. 'library/dreamfort.csv' or 'dreamfort').")
    label: str | None = Field(None, description="Quickfort blueprint label (e.g. '/farming1', '/industry1', '/industry2').")
    force: bool = Field(False, description="Bypass Quickfort bootstrap/aquifer safety gates.")


async def _plan_save_key() -> str:
    try:
        sig = await world.signature()
        if isinstance(sig, dict) and sig.get("key"):
            return str(sig["key"])
    except Exception:
        pass
    return "default"


def _find_anchor(plan: dict, parent: str, elev: int | None) -> dict | None:
    if parent == "hub":
        hub = plan.get("hub")
        if not hub:
            return None
        anchor = dict(hub)
        if elev is not None:
            anchor["elev"] = int(elev)
        return anchor
    for r in plan.get("rooms", []):
        if r.get("name") == parent:
            return r
    return None


@app.get("/fort-strata", operation_id="fort_strata")
async def fort_strata(
    x: int | None = None,
    y: int | None = None,
    elev: int | None = None,
    radius: int = 12,
    above: int = 3,
    below: int = 8,
):
    """Scan vertical geological strata around `(x, y, elev)` (defaults to fort center) to locate
    soil/potential-soil layers for farms, safe stone layers for workshops/bedrooms, aquifers,
    water, magma, and caverns before planning an underground fortress."""
    try:
        return await FP.fort_strata(
            world,
            x=x,
            y=y,
            elev=elev,
            radius=radius,
            above=above,
            below=below,
        )
    except (DFHackError, DFHackTransportError, ValueError) as e:
        return {"success": False, "error": str(e)}


async def _quickfort_readiness() -> dict:
    nobles_res: dict = {}
    workshops_res: dict = {}
    zones_res: list = []
    auto_res: dict = {}
    try:
        nobles_res = await LO.manage_nobles(action="list")
    except Exception:
        pass
    try:
        workshops_res = await LO.workshop_job(action="list")
    except Exception:
        pass
    try:
        dec = await world._dec(await world._gen())
        zones_res = await LO.list_zones(dec)
    except Exception:
        pass
    try:
        en = await _run_dfhack_cmd("enable", [])
        plugins = {}
        for line in (en.get("output") or "").splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                plugins[k.strip().lower()] = v.strip()
        auto_res = {"plugins": plugins}
    except Exception:
        pass
    return FP.check_quickfort_readiness(nobles_res, workshops_res, zones_res, auto_res)


@app.post("/fort-plan", operation_id="fort_plan")
async def fort_plan(request: FortPlanRequest):
    """Underground fortress CAD & Quickfort planner (`view`, `set_hub`, `add_room`, `add_template`, `remove_room`, `validate`, `commit`, `find_site`, plus `qf_status`, `qf_list`, `qf_orders`, `qf_run`)."""
    body = await _as_dict(request)
    action = str(body.get("action", "view")).lower()
    save_key = await _plan_save_key()
    plan = plan_store.load(save_key)

    try:
        if action == "clear":
            plan_store.clear(save_key)
            return {"success": True, "save_key": save_key, "cleared": True}

        if action == "set_hub":
            if body.get("x") is None or body.get("y") is None or body.get("elev_top") is None or body.get("elev_bottom") is None:
                return {"success": False, "error": "set_hub requires x, y, elev_top, and elev_bottom."}
            hub = FP.ensure_hub(
                plan,
                x=int(body["x"]),
                y=int(body["y"]),
                elev_top=int(body["elev_top"]),
                elev_bottom=int(body["elev_bottom"]),
                radius=int(body.get("radius", 1)),
            )
            plan_store.save(save_key, plan)
            return {"success": True, "save_key": save_key, "hub": hub}

        if action == "add_room":
            parent = str(body.get("parent", "hub"))
            anchor = _find_anchor(plan, parent, body.get("elev"))
            if anchor is None:
                return {"success": False, "error": f"Parent {parent!r} not found in plan (call set_hub first)."}
            kind = str(body.get("kind", "workshop"))
            defaults = FP.ROOM_DEFAULTS.get(kind, FP.ROOM_DEFAULTS["workshop"])
            w = int(body["width"]) if body.get("width") is not None else int(defaults["width"])
            h = int(body["height"]) if body.get("height") is not None else int(defaults["height"])
            room_rect, corr_rect = FP.compute_attached_room(
                anchor,
                direction=str(body.get("direction", "E")),
                width=w,
                height=h,
                corridor_len=int(body.get("corridor_len", 2)),
                corridor_width=int(body.get("corridor_width", 1)),
                offset=int(body.get("offset", 0)),
                elev=body.get("elev"),
            )
            name = str(body.get("name") or f"{kind}_{len(plan['rooms']) + 1}")
            new_room = {
                "name": name,
                "kind": kind,
                "elev": int(room_rect["elev"]),
                "x0": int(room_rect["x0"]),
                "y0": int(room_rect["y0"]),
                "x1": int(room_rect["x1"]),
                "y1": int(room_rect["y1"]),
                "corridor": corr_rect,
                "parent": parent,
                "direction": str(body.get("direction", "E")).upper(),
                "needs_soil": bool(defaults.get("needs_soil", False)),
                "notes": str(body.get("notes") or defaults.get("purpose", "")),
            }
            if any(r.get("name") == name for r in plan["rooms"]):
                return {"success": False, "error": f"Room named {name!r} already exists."}
            collisions = FP.check_plan_collisions(
                plan,
                new_room,
                corr_rect,
                wall_thickness=int(body.get("wall_thickness", 1)),
            )
            if collisions:
                return {"success": False, "error": "Room collides with existing plan.", "collisions": collisions}
            plan["rooms"].append(new_room)
            plan_store.save(save_key, plan)
            return {"success": True, "save_key": save_key, "room": new_room, "view": FP.render_plan_view(plan, elev=new_room["elev"])}

        if action == "add_template":
            template = body.get("template")
            if not template:
                return {"success": False, "error": f"add_template requires template in {FP.VALID_TEMPLATES}."}
            parent = str(body.get("parent", "hub"))
            anchor = _find_anchor(plan, parent, body.get("elev"))
            if anchor is None:
                return {"success": False, "error": f"Parent {parent!r} not found in plan (call set_hub first)."}
            new_rooms = FP.build_template_rooms(
                template=str(template),
                anchor=anchor,
                direction=str(body.get("direction", "E")),
                prefix=str(body.get("prefix", "")),
                elev=body.get("elev"),
            )
            wall_t = int(body.get("wall_thickness", 1))
            existing_names = {r.get("name") for r in plan["rooms"]}
            for nr in new_rooms:
                if nr["name"] in existing_names:
                    return {"success": False, "error": f"Room name {nr['name']!r} already exists; pass a unique prefix."}
                collisions = FP.check_plan_collisions(plan, nr, nr.get("corridor"), wall_thickness=wall_t)
                if collisions:
                    return {
                        "success": False,
                        "error": f"Template room {nr['name']!r} collides with existing plan.",
                        "collisions": collisions,
                    }
            plan["rooms"].extend(new_rooms)
            plan_store.save(save_key, plan)
            view_elev = new_rooms[0]["elev"] if new_rooms else body.get("elev")
            return {"success": True, "save_key": save_key, "added": [r["name"] for r in new_rooms], "view": FP.render_plan_view(plan, elev=view_elev)}

        if action == "remove_room":
            name = body.get("name")
            if not name:
                return {"success": False, "error": "remove_room requires name."}
            before = len(plan["rooms"])
            plan["rooms"] = [r for r in plan["rooms"] if r.get("name") != name]
            if len(plan["rooms"]) == before:
                return {"success": False, "error": f"Room {name!r} not found."}
            plan_store.save(save_key, plan)
            return {"success": True, "save_key": save_key, "removed": name}

        if action == "validate":
            checks = await FP.validate_rooms_terrain(world, plan.get("rooms", []))
            return {"success": True, "save_key": save_key, "all_ok": all(c["ok"] for c in checks), "rooms": checks}

        if action == "commit":
            res = await FP.commit_plan(
                world,
                plan,
                designate_fn=_designate,
                filter_safe_fn=_filter_safe_dig_points,
                expand_box_fn=_expand_dig_box,
                phase=str(body.get("phase", "auto")),
                dry_run=bool(body.get("dry_run", False)),
                elev=body.get("elev"),
            )
            if not body.get("dry_run", False) and res.get("total_designated", 0) > 0:
                world.cache.reset()
            return {"success": True, "save_key": save_key, **res}

        if action == "view":
            return {"success": True, **FP.render_plan_view(plan, elev=body.get("elev"))}

        if action == "find_site":
            hub = plan.get("hub") or {}
            ce = body.get("elev") if body.get("elev") is not None else hub.get("elev_bottom")
            cx = body.get("x") if body.get("x") is not None else hub.get("x")
            cy = body.get("y") if body.get("y") is not None else hub.get("y")
            if ce is None or cx is None or cy is None:
                rx, ry, re = await FP._resolve_center(world, cx, cy, ce)
                cx = rx if cx is None else int(cx)
                cy = ry if cy is None else int(cy)
                ce = re if ce is None else int(ce)
            w = int(body.get("width") or 3)
            h = int(body.get("height") or 3)
            raw_r = body.get("radius")
            scan_rad = int(raw_r) if raw_r is not None and int(raw_r) > 2 else 16
            zones: list = []
            try:
                dec = await world._dec(await world._gen())
                zones.extend(await LO.list_zones(dec))
            except Exception:
                pass
            try:
                dec = await world._dec(await world._gen())
                zones.extend(await LO.list_stockpiles(dec))
            except Exception:
                pass
            try:
                bl = await LO.buildings_live(elev0=int(ce), elev1=int(ce))
                for b in bl.get("buildings", []):
                    bx = int(b.get("x", 0))
                    by = int(b.get("y", 0))
                    zones.append({"elev": int(ce), "x0": bx - 1, "y0": by - 1, "x1": bx + 1, "y1": by + 1})
            except Exception:
                pass
            res = await FP.find_clean_sites(
                world, x=int(cx), y=int(cy), elev=int(ce),
                width=w, height=h, radius=scan_rad, zones=zones,
                require_soil=(str(body.get("kind") or "").strip().lower() == "farm"),
            )
            return {"success": True, "save_key": save_key, **res}

        if action == "qf_status":
            readiness = await _quickfort_readiness()
            return {"success": True, "save_key": save_key, "hub": plan.get("hub"), **readiness}

        if action == "qf_list":
            bp_filter = str(body.get("blueprint") or "").strip()
            args = ["list", bp_filter] if bp_filter else ["list"]
            res = await _run_dfhack_cmd("quickfort", args)
            bps = FP.parse_quickfort_list(res.get("output") or "")
            return {"success": res["ok"], "count": len(bps), "blueprints": bps, "error": res.get("error")}

        if action == "qf_orders":
            bp = str(body.get("blueprint") or "").strip()
            if not bp:
                return {"success": False, "error": "qf_orders requires blueprint."}
            lbl = body.get("label")
            dry = bool(body.get("dry_run", False))
            force = bool(body.get("force", False))
            if not dry and not force:
                readiness = await _quickfort_readiness()
                if not readiness["ready_for_meta_and_orders"]:
                    return {
                        "success": False,
                        "blocked": True,
                        "error": "Bootstrap fort not ready for Quickfort work orders.",
                        **readiness,
                    }
            args = FP.build_quickfort_args("orders", bp, label=lbl, dry_run=dry)
            res = await _run_dfhack_cmd("quickfort", args)
            return {
                "success": res["ok"],
                "save_key": save_key,
                "dry_run": dry,
                "command": res["command"],
                "output": res.get("output"),
                "error": res.get("error"),
            }

        if action == "qf_run":
            bp = str(body.get("blueprint") or "").strip()
            if not bp:
                return {"success": False, "error": "qf_run requires blueprint."}
            if body.get("elev") is None:
                return {"success": False, "error": "qf_run requires elev."}
            ce = int(body["elev"])
            hub = plan.get("hub") or {}
            cx = body.get("x") if body.get("x") is not None else hub.get("x")
            cy = body.get("y") if body.get("y") is not None else hub.get("y")
            if cx is None or cy is None:
                return {"success": False, "error": "qf_run requires x and y (or set_hub first)."}
            cx, cy = int(cx), int(cy)
            lbl = body.get("label")
            dry = bool(body.get("dry_run", False))
            force = bool(body.get("force", False))
            dig_only = FP.is_dig_only_blueprint(bp, lbl)

            if not dry and not force:
                if not dig_only:
                    readiness = await _quickfort_readiness()
                    if not readiness["ready_for_meta_and_orders"]:
                        return {
                            "success": False,
                            "blocked": True,
                            "error": "Bootstrap fort not ready for Quickfort build/meta blueprints; finish bootstrap workshops + Manager + buildingplan first.",
                            **readiness,
                        }
                strata = await FP.fort_strata(world, x=cx, y=cy, elev=ce, radius=10, above=0, below=0)
                bad_layers = [
                    l for l in strata.get("layers", [])
                    if l.get("aquifer_tiles", 0) > 0 or l.get("magma_tiles", 0) > 0
                ]
                if bad_layers:
                    return {
                        "success": False,
                        "blocked": True,
                        "error": f"Hazard detected at elev {ce}; pass force=true to override.",
                        "strata": strata,
                    }

            args = FP.build_quickfort_args("run", bp, label=lbl, x=cx, y=cy, elev=ce, dry_run=dry)
            res = await _run_dfhack_cmd("quickfort", args)
            if not dry and res["ok"]:
                world.cache.reset()
            return {
                "success": res["ok"],
                "save_key": save_key,
                "dig_only": dig_only,
                "cursor": {"x": cx, "y": cy, "elev": ce, "z": ce + WM.GAME_Z_OFFSET},
                "dry_run": dry,
                "command": res["command"],
                "output": res.get("output"),
                "error": res.get("error"),
            }

        return {"success": False, "error": f"Unknown action {action!r}."}
    except (ValueError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e)}


class StatelessFastApiHttpSessionManager(FastApiHttpSessionManager):
    """StreamableHTTP session manager with stateless=True so server restarts do not invalidate client mcp-session-id headers."""

    async def _ensure_session_manager_started(self) -> None:
        if self._manager_started:
            return
        async with self._startup_lock:
            if self._manager_started:
                return
            self._session_manager = StreamableHTTPSessionManager(
                app=self.mcp_server,
                event_store=self.event_store,
                json_response=self.json_response,
                stateless=True,
                security_settings=self.security_settings,
            )

            async def run_session_manager():
                try:
                    async with self._session_manager.run():
                        await asyncio.Event().wait()
                except asyncio.CancelledError:
                    raise

            self._manager_task = asyncio.create_task(run_session_manager())
            self._manager_started = True
            await asyncio.sleep(0.1)


class ResilientFastApiMCP(FastApiMCP):
    """FastApiMCP subclass using StatelessFastApiHttpSessionManager when DFHACK_MCP_STATELESS_HTTP is enabled."""

    def mount_http(self, router=None, mount_path: str = "/mcp") -> None:
        if not MCP_STATELESS_HTTP:
            return super().mount_http(router=router, mount_path=mount_path)
        if not mount_path.startswith("/"):
            mount_path = f"/{mount_path}"
        if mount_path.endswith("/"):
            mount_path = mount_path[:-1]
        if not router:
            router = self.fastapi
        http_transport = StatelessFastApiHttpSessionManager(mcp_server=self.server)
        dependencies = self._auth_config.dependencies if self._auth_config else None
        self._register_mcp_endpoints_http(router, http_transport, mount_path, dependencies)
        self._setup_auth()
        self._http_transport = http_transport
        if hasattr(router, "prefix"):
            self.fastapi.include_router(router)


mcp = ResilientFastApiMCP(
    app,
    name="Dwarf Fortress MCP Server",
    description="A MCP server that uses the dfhack_client_python package to connect to a locally running Dwarf Fortress game through DFHack's RPC connection.",
    # Pruned MCP tool surface (PLAN-TOOLS.md Phase 1):
    # - game_lua / observe / observe_stats / game_paths stay HTTP-only for debug and the df-observer plugin.
    # - get_block_list (~147k tokens/z) and world_raw (file dump) are operator HTTP endpoints.
    # - get_versions / get_world_info / get_map_info / get_view_info are folded into get_status.
    # - get_unit_list is a verbose raw dump; play uses unit_counts, list_units, and look.
    # - world_zone is subsumed by look's per-elevation strip; building_at is inside tile_at.
    # - make_stockpile is unified into create_stockpile (site gate + categories);
    #   resize_building and repair_stockpile_settings stay HTTP-only.
    exclude_operations=[
        # Previously excluded debug, observer, raw, and legacy endpoints (16)
        "game_lua", "observe", "observe_stats", "game_paths", "get_block_list",
        "world_raw", "get_versions", "get_world_info", "get_map_info",
        "get_view_info", "get_unit_list", "world_zone", "building_at",
        "make_stockpile", "resize_building", "repair_stockpile_settings",
        # Superseded vertical/geology/reachability micro-probes (13)
        "overburden", "column", "cross_section", "depth_profile", "magma_depth",
        "probe_plan", "tunnel_sites", "find_soil", "hazards", "warnings",
        "reach_map", "dig_reachable", "walkable_between",
        # Legacy / raw / debug / UI-hack endpoints (11)
        "apply_blueprint", "copy_screen", "pass_keyboard_event", "reconnect",
        "world_cache_reset", "world_delta", "world_minimap", "world_objects",
        "transitions", "pile_arrays", "wagon_path",
        # Redundant read / lookup endpoints (16)
        "get_status", "get_pause_state", "get_reports", "tile_at", "tile_legend",
        "list_buildings", "buildings_live", "get_building_def_list", "labor_list",
        "unit_counts", "list_units", "unit_labors", "unit_professions",
        "unit_activity", "job_queue", "work_order_link",
    ]
)
# Streamable HTTP (POST/GET /mcp) - the transport dsh-mcp-client speaks.
# mcp.mount() is deprecated and mounts the legacy SSE pair (GET /mcp + POST /mcp/messages/).
# Zone kinds (meeting / dump / pen) are readable only through the game's own structures,
# which live in this layer - the model layer cannot import luaops (luaops imports it).
world.zone_provider = lambda dec: LO.list_zones(dec)
world.pile_provider = lambda dec: LO.list_stockpiles(dec)

mcp.mount_http()

if __name__ == "__main__":
    import os
    import uvicorn
    # 127.0.0.1 only: DFHack's own RPC is localhost-bound (allow_remote: false),
    # and this server can drive the game. Bind 0.0.0.0 deliberately if you want LAN use.
    uvicorn.run(app, host=os.getenv("DFHACK_MCP_HOST", "127.0.0.1"),
                port=int(os.getenv("DFHACK_MCP_PORT", "8000")))
