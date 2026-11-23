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
import shutil
import time
from collections import Counter
from contextlib import asynccontextmanager
from typing import Any
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi_mcp import FastApiMCP
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
    action: str = Field("list", description="One of 'list' (inspect all noble positions), 'assign' (assign a specific position to unit_id), or 'auto_assign' (fill vacant MANAGER, BOOKKEEPER, BROKER, CHIEF_MEDICAL_DWARF positions across living citizens and set max bookkeeper precision)")
    position: str | None = Field(None, description="Noble position code for action='assign' (e.g. 'MANAGER', 'BOOKKEEPER', 'BROKER', 'CHIEF_MEDICAL_DWARF', 'MILITIA_COMMANDER', 'SHERIFF')")
    unit_id: int | None = Field(None, description="Citizen unit ID for action='assign' (pass -1 to vacate)")
    bookkeeper_settings: int | None = Field(None, ge=0, le=4, description="Optional bookkeeper precision level (0..4, where 4 is maximum precision)")


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
    action: str = Field("status", description="One of 'status' (inspect depot, caravans, goods at depot, pending haul queue, and top tradeable fortress items/bins), 'request_broker' (summon/dismiss Broker at depot), 'mark_goods' (queue items/bins to be hauled to depot via dfhack.items.markForTrade), 'unmark_goods' (cancel pending haul jobs or unload goods from depot), 'barter_status' (inspect open barter screen), or 'barter_select' (select/deselect items on open barter screen)")
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
    job: str | None = Field(None, description="df.job_type name (e.g. 'MakeBarrel', 'MakeBed', 'MakeTable', 'MakeChair', 'BrewDrink', 'MakeCharcoal', 'SmeltOre')")
    amount: int | None = Field(None, ge=1, description="Total amount to produce")
    material: str | None = Field(None, description="Optional material filter (e.g. 'rock', 'wood')")
    item_type: str | None = Field(None, description="Optional item_type filter")
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
    "autochop", "autobutcher", "autofarm", "autolabor", "autonestbox", "autoslab",
    "buildingplan", "suspendmanager", "prioritize", "orders", "workorder",
    "quickfort", "dreamfort",
    "warn-stranded", "locate-ore", "list-waves", "caravan", "liquids",
    "clean", "cleanowned", "seedwatch", "tailor", "ban-cooking",
    "toggle-kbd-cursor", "position", "prospect",
}

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
                "hint": "use typed tools instead: send_dig_command, place_building, create_stockpile, work_order, set_unit_labors"}
    if cmd_norm not in RUN_COMMAND_ALLOWLIST:
        return {"command": command, "arguments": list(arguments), "success": False,
                "blocked": True,
                "error": f"command '{command}' is not in the run_command allowlist",
                "allowed": sorted(RUN_COMMAND_ALLOWLIST)}
    # DFHack answers CR_NOT_IMPLEMENTED for an unknown command and CR_LINK_FAILURE for a
    # dead socket; both arrived as unhandled exceptions (HTTP 500 + a stack trace in the
    # log). Report them as data so the caller can read the failure and move on.
    drain_replies()                                   # drop anything from an earlier call
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
    """Fell the trees in a rectangle - the player's own action, typed.

    How a human does it (the player's words, 2026-11-21): *"as a player i select the tree
    trunk with the chopping order"*. So the target is the TRUNK tile: the chopping order
    lands on `tree_trunk` (TreeTrunk). A sapling is NOT a tree to fell - clearing a sapling
    is the "clear vegetation" order, a different job with a different reward. This tool
    designates trunks only and reports what it found, so a request to "fell 3-5 trees"
    (the quickstart rung) cannot silently become 5 sapling clearings.

    Body: {x0, y0, x1, y1, elev, limit?}  -> designates DEFAULT_DIG on every trunk tile in
    the box (the game turns that into Fell Tree jobs), verifies the designations landed,
    and reports the wood count before/after - hauling and cutting take in-game time, so
    `wood_after` is a fact about the moment of reading, not a promise.
    """
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
                     cell: int = Query(16), min_depth: int = Query(3), max_levels: int = Query(8)):
    """Where the water IS, per elevation - the census a well plan needs before any digging.

    The wiki's rule is exact: a well is 1x1 on a floor with a CLEAR line to water >=3/7
    deep, and it does not work through a staircase. So the questions are: which tiles hold
    water, how deep, and is there a floor above them. This reads the map blocks and reports
    clusters per `cell` with the depth range, per elevation - the answer to "the fort keeps
    cancelling Give water: No water source" is a coordinate, not a guess.
    """
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
        out.append(c)
    out.sort(key=lambda c: (-c["water_tiles"], -c["max_depth"]))
    return {"success": True, "levels_scanned": list(levels), "water_tiles_found": total,
            "clusters": out[:40], "min_depth": mind,
            "note": "a well needs a floor tile with a clear line to water >=3/7 below it - "
                    "use a cluster sample, and gate with /hazards before digging toward it"}


@app.get("/find-trees", operation_id="find_trees")
async def find_trees(elev0: int = Query(...), elev1: int = Query(...),
                     cell: int = Query(16), max_levels: int = Query(8)):
    """Where the trees ARE - a whole-map census of trunk tiles, per elevation.

    Built first because felling needs a target: the chopping order lands on the TRUNK
    (the player: "as a player i select the tree trunk with the chopping order"). The survey
    endpoint lumps vegetation into `other`, so this reads the map blocks directly and
    decodes every tile's class - trunks counted per `cell`x`cell` bin with a sample
    coordinate, saplings counted separately (a sapling is the vegetation order, not a tree).
    """
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


@app.post("/send-dig-command", operation_id="send_dig_command")
async def send_dig_command(request: SendDigCommandRequest):
    """Designate digging - the `m` menu over RPC, no blueprint and no instant-dig.
    designation: one of NO_DIG, DEFAULT_DIG, UP_DOWN_STAIR_DIG, CHANNEL_DIG, RAMP_DIG,
    DOWN_STAIR_DIG, UP_STAIR_DIG (or STAIR_SHAFT with box).

    Three ways to pass coordinates:
    1. `box`: `{x0, y0, x1, y1, elev, elev1?}` (or `boxes`: list of boxes) using in-game
       Elevation numbers directly (`z = elevation + 129`, `WM.GAME_Z_OFFSET`, converted
       internally). Orders the middle spine before the flanks (`middle_first=True`) so
       miners carve access into fog cleanly.
    2. `stair_shaft`: `{x0, y0, x1?, y1?, from_elev, to_elev, connect_above?, continue_below?}`
       - automatically designates `DOWN_STAIR_DIG` at the top elevation, `UP_DOWN_STAIR_DIG`
       on every intermediate elevation, and `UP_STAIR_DIG` at the bottom elevation so a
       multi-z stairwell is never half-ordered.
    3. `locations`: list of `[x, y, z]` where `z = elevation + 129` (`WM.GAME_Z_OFFSET` - the
       SAME number every read tool uses), sent AS GIVEN.

    FOG WORKFLOW (verified live on 53.17-r1): designating into unexplored fog (`hidden=True`,
    `?` glyph) WORKS and is the intended workflow for extending tunnels, rooms, and stair
    shafts into the mountain. Gate before designating with `hazards` (damp/hot),
    `depth_profile` / `overburden` (depth >= 2), and `survey` (do not designate over existing
    floors/buildings). Note: `tile_at` does not show `dig_designation` on hidden tiles, so
    verify fog orders via `world_objects` / `look` dig clusters, and verify completion by
    positive tile state (`dug > 0 and to_dig == 0` in `survey`), never by designation count
    dropping to 0. Use `NO_DIG` to clear a rectangle."""
    body = await _as_dict(request)
    swap = bool(body.get("swap", False))
    middle_first = bool(body.get("middle_first", True))

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
    for bx in boxes_in:
        b_des = str(bx.get("designation") or designation).upper()
        if b_des not in DIG_DESIGNATIONS:
            return {"error": f"unknown box designation '{b_des}'", "known": sorted(DIG_DESIGNATIONS)}
        pts = _apply_swap(_expand_dig_box(
            int(bx["x0"]), int(bx["y0"]), int(bx["x1"]), int(bx["y1"]),
            int(bx["elev"]), bx.get("elev1"), middle_first=middle_first
        ))
        n_box = await _designate(b_des, pts)
        total_sent += n_box
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
        total_sent += await _designate(designation, [[c.x, c.y, c.z] for c in locs])

    if not boxes_in and not body.get("locations"):
        return {"success": False,
                "error": "provide 'box' ({x0,y0,x1,y1,elev}), 'boxes', 'stair_shaft', or 'locations' ([[x,y,z],...])"}

    out_res = {"designation": designation, "tiles": total_sent, "success": True}
    if boxes_applied:
        out_res["boxes_applied"] = boxes_applied
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
                    rle: bool = Query(False, description="run-length encode flat rows (default False so the top X-coordinate ruler aligns character-for-character; set True for wide >60-col scans)"),
                    with_legend: bool = Query(False, description="include the glyph legend"),
                    max_tokens: int = Query(WM.DEFAULT_MAX_TOKENS)):
    """A single elevation as a 1-char-per-tile glyph grid for an explicit x/y window, with a top X-coordinate ruler, left Y-coordinate labels, and walkable-region letters annotated alongside. Keep windows ~40x40 (~400 tokens). Tiletype names are NEVER sent; read the legend once. Water/magma/hidden override the base glyph."""
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
    """CREATE A BUILDING at an exact tile - no menu, no mouse, no dwarf. building: friendly name (Stockpile, Zone, Trade Depot, Bed, Table, Chair, Door, Chest, Well, Farm Plot, Still, Mechanic, Carpenter, Stoneworker, Jeweler, Smelter, Forge, Kitchen, Butcher, Weapon Rack, Axle, Gear Assembly, Water Wheel, Lever, Trap, Cage, Trackstop...); x/y int, elev GAME ELEVATION; w/h optional (a Trade Depot is 5x5, a workshop 3x3); complete:true (default) finishes it via completeBuild - pass false to queue it for a dwarf to build with real materials; material:"log"/"boulder"/"block" with count:N attaches REAL item objects found by description (measured: constructWithItems rejects an empty list, and RFR item ids do not resolve); abstract:true for material-free buildings (auto for Stockpile/Zone - the game REQUIRES one of items/filters/abstract); items:[itemId,...] for furniture and doors. Returns the game type + building id verified by findAtTile, and the game's own rejection text (e.g. 'one of items, filters or abstract is required') when it refuses. Pre-flight site gate rejects footprints containing rock, fog, open air, or water."""
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
    dec = await world._dec(await world._gen())
    try:
        # ANY multi-tile request goes through the game's own setSize path. Not just
        # piles: a 3x2 Farm Plot came back 1x1 the same way (measured 2026-11-21, and
        # the ladder rung still read satisfied - a 1-tile farm quietly starves a fort).
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
                                      count=int(body.get("count", 3)))
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
    """Deconstruct a building - by tile (x, y, elev) OR by building_id.

    Measured 2026-11-21: this endpoint read body["x"] unconditionally, so the natural call
    {"building_id": 28} answered HTTP 500 with no explanation - the tool the player asked
    to fix ("looks like the remove tool needs some work too"). An id is resolved to the
    footprint start from the live pile/zone lists; a call that names neither is answered
    with a usage message, not a traceback.
    """
    body = await _as_dict(request)
    x = y = elev = None
    if body.get("building_id") is not None:
        bid = int(body["building_id"])
        dec = await world._dec(await world._gen())
        for row in await LO.list_stockpiles(dec):
            if int(row["id"]) == bid:
                x, y, elev = int(row["x0"]), int(row["y0"]), int(row["elev"])
                break
        if x is None:
            try:
                zones = await LO.list_zones(await world._dec(await world._gen()))
            except LO.LuaOpError as e:
                return {"success": False, "error": f"zone scan failed while resolving id {bid}: {e}"}
            for row in zones:
                if int(row["id"]) == bid:
                    x, y, elev = int(row["x0"]), int(row["y0"]), int(row["elev"])
                    break
        if x is None:
            return {"success": False,
                    "error": f"no stockpile or zone with building_id {bid} - the live lists are the "
                             "source; a pile marked for removal may already be gone"}
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
        # The write's own reply is allowed to be silent; it is not evidence about the game.
        res, write_note = {"error": str(e)}, str(e)
    world.cache.reset()
    bid = res.get("building_id")
    if bid is None:
        # The reply never named the building; resolve it from the tile we were given.
        at = await LO.building_at(x, y, elev)
        bid = at.get("building_id") if isinstance(at, dict) else None
    present = await _building_present(int(bid)) if bid is not None else None
    if present is False:
        return {"success": True, "verdict": "removed", "building_id": bid,
                "resolved_from_tile": [x, y, elev],
                "verified_by": "the id is absent from the live pile/zone lists",
                "write_reply": write_note}
    if present is None:
        return {"success": False, "verdict": "UNVERIFIED", "building_id": bid,
                "resolved_from_tile": [x, y, elev],
                "error": "the pile/zone reads produced NO DATA: removal not proven",
                "write_reply": write_note}
    return {"success": False, "verdict": "still present", "building_id": bid,
            "resolved_from_tile": [x, y, elev],
            "error": res.get("error") or "the building is still in the live lists",
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
    pcall(function() rec.x0 = b.x1; rec.x1 = b.x2; rec.y0 = b.y1; rec.y1 = b.y2; rec.elev = b.z - 129 end)
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
async def work_details(action: str = Query("list", description="'list' (the whole Labor menu), 'assign' (tick/untick a dwarf on a detail), 'set-labor' (change what labors a detail grants)"),
                       detail: str = Query(None, description="assign/set-labor: the detail index (from list) or its name, e.g. Miners"),
                       unit_id: int = Query(None, description="assign: the citizen to tick/untick (unit id from /unit-professions)"),
                       mode: str = Query(None, description="assign: 'add' (tick) or 'remove' (untick)"),
                       labor: str = Query(None, description="set-labor: a labor name from /labor-list, e.g. MINE"),
                       value: bool = Query(None, description="set-labor: true to grant, false to revoke (default true)")):
    """THE LABOR MENU, at the layer the game actually uses - the green tick marks.

    Measured 2026-11-22: the details live at `df.global.plotinfo.labor_info.work_details`, each
    with `name`, `allowed_labors`, `flags` and **`assigned_units` (a vector of unit ids - the tick
    column itself)**. The built-in details carry `cannot_be_everybody`/`no_modify`. This is the
    authority over the REMOVED `/set-unit-labors` (2026-11-22): that tool wrote `unit.status.labors`, the EFFECTIVE flags,
    and the game recomputes them from the details - a census of 5 fell to the 2 ticked dwarves the
    instant a tick was added. So: change the detail here, then read `/unit-labors` to see the
    flags land.
    """
    try:
        res = await LO.work_details(action=action, detail=detail, unit_id=unit_id,
                                    mode=mode, labor=labor, value=value)
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
                if tid is not None:
                    try:
                        row["type_name"] = str(dec.building_name(int(tid)))
                    except Exception:
                        row["type_name"] = f"type {tid} (not in the decoder's list)"
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
    """Create a multi-tile stockpile (abstract -> setSize -> completeBuild), optionally configuring its categories (`allow`, `deny`, `all_categories`) and container limits (`max_wheelbarrows`, `max_barrels`, `max_bins`) in the same call. Use this, not place_building, for any pile bigger than 1 tile.

    SITE GATE (measured 2026-11-21, three times on one fort): a pile placed on tiles that
    are not dug floor - `open` cavern air, unexcavated rock, fog - is accepted by the game
    and lands IN MID AIR. The player's words: "you had build the three stockpiles in mid
    air". Furniture floating over a cavern is a bug factory, so this endpoint now reads the
    footprint first and refuses when any tile is not a dug, walkable floor."""
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
    """Set a stockpile's accepted categories, container limits and wheelbarrows.

    Body: {"building_id": 12, "allow": ["wood"], "deny": ["stone"], "all_categories": false,
    "max_wheelbarrows": 3, "max_barrels": 0, "max_bins": 0}. The game's own category names
    are: ammo, animals, armor, bars_blocks, cloth, coins, corpses, finished_goods, food,
    furniture, gems, leather, misc, refuse, sheet, stone, weapons, wood. all_categories=true
    with a deny list is the general pile (take everything except stone and wood, per the
    quickstart). A wood pile near the entrance is allow=["wood"].
    """
    body = await _as_dict(request)
    # The gate reads the RAW body, not the validated model: `_as_dict` uses
    # exclude_none=True, so absent allow/deny disappear from `body` entirely and a check on
    # `body` passes while the call still means TAKE NOTHING (measured twice on 2026-11-22 -
    # my own {"building_id":3} probe emptied pile 3 the second time).
    try:
        raw_body = await raw_request.json()
    except Exception:
        raw_body = {}
    if isinstance(raw_body, dict) and not any(k in raw_body for k in ("allow", "deny", "all_categories")):
        return {"success": False,
                "error": "say what to accept: allow=[...], deny=[...] (with all_categories=true), "
                         "or all_categories=true. An empty request means TAKE NOTHING and is not "
                         "sent.",
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
            allow_crash_categories=bool(body.get("allow_crash_categories", False)))
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
            "building_id": bid, "accepts": row.get("accepts"), "write_reply": write_note}


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
    """Remove an activity zone by id - **opt-in**, because this path has killed the fort once.

    Measured 2026-11-21 22:53:41 (`crash_2026-11-21-22-53-41.txt`): a zone removal via RPC died
    with SIGSEGV at address 0 inside `Buildings::notifyCivzoneModified`. The no-arg call is
    gone, but the chunk still performs pointer surgery (setOwner(b, nil), erasing
    `assigned_units`, erasing + `:delete()`-ing every `general_refs` civzone entry in
    `units.active`, then `deconstruct`). That is the riskiest shape of write this project has,
    so it is gated like zone creation: pass `acknowledge_risk: true`, and prefer the in-game
    zone removal (`z -> ... -> remove`) for a living fortress. The answer carries a read-back
    verdict: a zone is proven gone from the live zone list, not from the write's reply.
    """
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
    """CREATE AN ACTIVITY ZONE - the meeting area, pasture, pit/pond, dump - at an exact rectangle.

    Body: {"kind": "meeting" | "pasture" | "pit" | "pond" | "dump" | "watersource" | any
    name substring from get_building_def_list, "x": int, "y": int, "elev": GAME ELEVATION,
    "w": int, "h": int}. Zones are ABSTRACT buildings (no materials), so the pile recipe
    works: allocInstance -> setSize -> constructAbstract, verified by findAtTile.

    Why this tool exists: the ladder rungs for the meeting area (wiki step 5), the pasture,
    the garbage dump and the refuse zone all need a zone, and the `zone` plugin implements
    no commands (SETUP-LINUX notes), so until now zones were UI-only and uncreatable by
    an agent. Dig the room out FIRST: a zone on unmined rock marks nothing.
    """
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
    try:
        dec = await world._dec(await world._gen())
        # Idempotency check (tool_missing.md #16): if an active zone already occupies
        # this exact footprint, return it instead of creating a duplicate zone.
        try:
            for zz in await LO.list_zones(dec):
                if (not zz.get("marked_for_removal")
                        and int(zz["x0"]) == x0 and int(zz["x1"]) == x1
                        and int(zz["y0"]) == y0 and int(zz["y1"]) == y1
                        and int(zz["elev"]) == e):
                    return {"success": True, "already_exists": True,
                            "building_id": int(zz["id"]), "kind": kind_str,
                            "zone_type": zz.get("type_name"),
                            "extent": f"{x0}..{x1},{y0}..{y1}",
                            "rect_tiles": w * h}
        except Exception:
            pass
        res = await LO.create_zone(dec, str(body["kind"]), x0, y0, e, w, h)
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
    """Every labor name you can pass to set_unit_labors (the DFHack unit_labor enum, decoded from the game). Use these exact strings - MINING, MASON, CARPENTER, PLANT, IRRIGATE, FARMER, CRAFTSMAN, TRANSPORT, ..."""
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


@app.post("/work-order", operation_id="work_order")
async def work_order(request: WorkOrderRequest):
    """THE MANAGER'S WORK-ORDER SCREEN (`j-m-q`), driven.
    - Create: `{"job":"MakeBarrel","amount":5}` (or raw `{"json":"{\"job\":\"MilkCreature\"}"}`).
    - List: `{"action":"list"}` (or `{}`) returns all active manager orders (`amount_left`, `amount_total`, `validated`, `active`) and whether a `MANAGER` noble is appointed (`has_manager`).
    - Cancel: `{"action":"cancel","order_id":0}` (or `{"action":"cancel","job":"MakeBarrel"}`).
    Jobs are df.job_type names (MakeCharcoal, MakeBed, MakeTable, MakeChair, MakeBarrel, ConstructMechanisms, BrewDrink, SmeltOre, ...)."""
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
    """Diagnose why a planned building (Well, Workshop, Furniture) or dig is not finishing:
    reports build_stage/max_build_stage, attached items, active construction jobs, whether
    suspended, missing item filters (e.g. 'needs mechanisms'), crew labor/pick bottlenecks,
    and recent cancellation announcements from the game report log. Powered by
    deployables/scripts/dfhack_mcp.lua."""
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
        diagnostics["crew"] = {
            "dwarves": len(citizens),
            "miners": miners,
            "picks": picks,
            "masons": masons,
            "carpenters": carpenters,
        }
        if miners == 0:
            diagnostics["bottlenecks"].append("no miners assigned (MINE labor = 0)")
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
    """Assign (or inspect) the citizen owner of an activity zone (`Office`, `Bedroom`,
    `DiningHall`, `Tomb`) via `dfhack.buildings.setOwner` and `getRoomDescription`. In DF v50+/v53,
    rooms are activity zones, and once population exceeds 20 the Manager noble requires an
    assigned `Office` zone (with a chair/throne) to validate manager work orders."""
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
    """Assign, unassign, or clear tame grazing animals (yaks, sheep, goats, horses, cavies,
    rabbits — or specific `unit_ids` / `species`) on a Pen/Pasture activity zone:
    - `action='assign'` (default): assigns matching animals (`all_grazers=True` by default).
    - `action='unassign'`: removes specific `unit_ids` or `species` from the pasture.
    - `action='clear'`: removes all animals from the pasture without deleting the Pen zone.
    To delete the Pen/Pasture zone itself, call `remove_zone` (`POST /remove-zone`)."""
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
    """List, assign, or auto-assign fortress noble positions (`MANAGER`, `BOOKKEEPER`,
    `BROKER`, `CHIEF_MEDICAL_DWARF`, `MILITIA_COMMANDER`, `SHERIFF`, etc.) and set bookkeeper
    precision (`bookkeeper_settings=4` = maximum precision). On a fresh embark, `MANAGER`,
    `BOOKKEEPER`, `BROKER`, and `CHIEF_MEDICAL_DWARF` are vacant (`histfig=-1`); calling
    `manage_nobles(action='auto_assign')` appoints living citizens to all vacant essential
    roles and sets highest bookkeeper accuracy."""
    body = await _as_dict(request)
    try:
        res = await LO.manage_nobles(
            action=str(body.get("action", "list")),
            position=body.get("position"),
            unit_id=body.get("unit_id"),
            bookkeeper_settings=body.get("bookkeeper_settings"),
        )
        return {"success": True, **res}
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e)}


@app.get("/alerts", operation_id="alerts_and_wellbeing")
async def alerts_and_wellbeing():
    """Time-critical fortress alerts and citizen wellbeing radar: checks for blocking modal UI
    popups (`world.status.popups`), active caravans (`plotinfo.caravans`), hostile invaders on
    the map, unpastured grazing animals at risk of starvation, strange moods/tantrums, and
    per-citizen thirst, hunger, drowsiness, stress, and wounds."""
    try:
        res = await LO.alerts_and_wellbeing()
        return {"success": True, **res}
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e)}


@app.post("/gather-and-items", operation_id="gather_and_items")
async def gather_and_items(request: GatherAndItemsRequest):
    """Perform Quickstart surface plant gathering, ground item dump/forbid flagging, or
    seed/brewable cooking protection:
    - `action='gather_plants'`: designate explored surface shrubs in `[x0..x1, y0..y1]` at `elev` for herbalism/gathering (`d -> p`).
    - `action='dump' | 'undump' | 'forbid' | 'reclaim'`: set or clear `dump` / `forbid` flags on loose ground items in `[x0..x1, y0..y1]` at `elev` (with optional `item_filter`, e.g. `'boulder'`).
    - `action='protect_seeds'`: run `ban-cooking seeds` (and `ban-cooking brew` when `ban_brew=True`) so kitchen jobs never destroy seed stock."""
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
    """Manage the Trade Depot, caravan goods hauling, Broker attendance, and barter selection:
    - `action='status'` (default): inspect Trade Depot state (`accessible`, `trader_requested`, `anyone_can_trade`), active caravans, goods already unloaded at the depot, pending `BringItemToDepot` haul jobs, and top tradeable fortress items/bins by value.
    - `action='request_broker'`: summon (`trader_requested=True`) or dismiss the Broker at the Trade Depot, and optionally toggle `anyone_can_trade`.
    - `action='mark_goods'`: mark specific `item_ids` or items matching `item_filter` / `min_value` (up to `max_items`) for hauling to the Trade Depot via `dfhack.items.markForTrade`. Works even before merchants arrive.
    - `action='unmark_goods'`: cancel pending depot haul jobs (`dfhack.job.removeJob`) or unload trade goods from the depot.
    - `action='barter_status' | 'barter_select'`: inspect or toggle `trade.goodflag[side][idx].selected` when the barter screen (`df.global.game.main_interface.trade`) is open."""
    body = await _as_dict(request)
    try:
        res = await LO.trade_depot(
            action=str(body.get("action", "status")),
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
        return {"success": True, **res}
    except (LO.LuaOpError, DFHackError, DFHackTransportError) as e:
        return {"success": False, "error": str(e)}


mcp = FastApiMCP(
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
    exclude_operations=["game_lua", "observe", "observe_stats", "game_paths", "get_block_list", "world_raw", "get_versions", "get_world_info", "get_map_info", "get_view_info", "get_unit_list", "world_zone", "building_at", "make_stockpile", "resize_building", "repair_stockpile_settings"]
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
