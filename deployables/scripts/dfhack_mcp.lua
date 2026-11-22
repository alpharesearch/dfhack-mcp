-- dfhack_mcp.lua - Server-side RPC Lua module for dfhack-mcp
-- Deployed to <gamedir>/hack/scripts/dfhack_mcp.lua via ./scripts/deploy.sh deploy
-- and auto-bootstrapped into _G.__dfhack_mcp by luaops.py when needed.
--@ module = true

local M = {}
M.VERSION = "2026.11.22-v1"
M.GAME_Z_OFFSET = 129

local ok_json, df_json = pcall(require, "json")

local function escape_str(s)
  return tostring(s)
    :gsub("\\", "\\\\")
    :gsub('"', '\\"')
    :gsub("\n", "\\n")
    :gsub("\r", "\\r")
    :gsub("\t", "\\t")
end

local function encode_json(val)
  local t = type(val)
  if val == nil then
    return "null"
  elseif t == "boolean" then
    return val and "true" or "false"
  elseif t == "number" then
    if val ~= val or val == math.huge or val == -math.huge then return "null" end
    return tostring(val)
  elseif t == "string" then
    return '"' .. escape_str(val) .. '"'
  elseif t == "table" then
    local is_arr = true
    local max_i = 0
    local count = 0
    for k, _ in pairs(val) do
      count = count + 1
      if type(k) ~= "number" or k < 1 or math.floor(k) ~= k then
        is_arr = false
        break
      end
      if k > max_i then max_i = k end
    end
    if count == 0 then
      return "[]"
    end
    if is_arr and max_i == count then
      local parts = {}
      for i = 1, count do
        parts[i] = encode_json(val[i])
      end
      return "[" .. table.concat(parts, ",") .. "]"
    else
      local parts = {}
      for k, v in pairs(val) do
        if v ~= nil then
          parts[#parts + 1] = '"' .. escape_str(k) .. '":' .. encode_json(v)
        end
      end
      table.sort(parts)
      return "{" .. table.concat(parts, ",") .. "}"
    end
  end
  return '"' .. escape_str(tostring(val)) .. '"'
end

local function decode_json(s)
  if type(s) == "table" then return s end
  if not s or s == "" then return {} end
  if ok_json and df_json and df_json.decode then
    local ok, res = pcall(df_json.decode, s)
    if ok and type(res) == "table" then return res end
  end
  return {}
end

M.encode_json = encode_json
M.decode_json = decode_json

-- ---------------------------------------------------------------------------
-- Operations
-- ---------------------------------------------------------------------------

local OPS = {}

-- 1. game_paths: filesystem discovery (solves tool_missing.md #5)
function OPS.game_paths(_args)
  local df_path, save_path, script_file = nil, nil, nil
  pcall(function() df_path = dfhack.getDFPath() end)
  pcall(function() save_path = dfhack.getSavePath() end)
  pcall(function() script_file = dfhack.findScript("dfhack_mcp") end)
  return {
    df_path = df_path,
    save_path = save_path,
    crashlogs_dir = df_path and (df_path .. "/crashlogs") or nil,
    blueprints_dir = df_path and (df_path .. "/dfhack-config/blueprints") or nil,
    scripts_dir = df_path and (df_path .. "/hack/scripts") or nil,
    script_deployed = (script_file ~= nil and script_file ~= ""),
    script_path = script_file,
    module_version = M.VERSION,
  }
end

-- 2. observe_status: combined status-bar probe (picks, site, date, seeds, alcohol, meals, raw_food)
function OPS.observe_status(_args)
  local picks = 0
  pcall(function()
    for _, it in ipairs(df.global.world.items.other.ANY_WEAPON) do
      local ok, key = pcall(function() return tostring(it.subtype.id) end)
      if ok and key == "ITEM_WEAPON_PICK" then
        picks = picks + 1
      end
    end
  end)
  local site_id = nil
  pcall(function()
    local s = dfhack.world.getCurrentSite()
    if s ~= nil then site_id = tonumber(s.id) end
  end)
  local date = nil
  pcall(function()
    date = {
      year = tonumber(dfhack.world.ReadCurrentYear()),
      month = tonumber(dfhack.world.ReadCurrentMonth()),
      day = tonumber(dfhack.world.ReadCurrentDay()),
    }
  end)
  local seeds = nil
  pcall(function() seeds = #df.global.world.items.other.SEEDS end)
  local drink, meals, raw = 0, 0, 0
  pcall(function()
    for _, _ in ipairs(df.global.world.items.other.DRINK) do drink = drink + 1 end
    for _, it in ipairs(df.global.world.items.other.FOOD) do
      local ok, sid = pcall(function() return tostring(it.subtype.id) end)
      if ok and sid:find("MEAL") then meals = meals + 1 else raw = raw + 1 end
    end
  end)
  return {
    picks = picks,
    site_id = site_id,
    date = date,
    seeds = seeds,
    alcohol = drink,
    meals = meals,
    raw_food = raw,
  }
end

-- 3. building_status: inspect unfinished/planned or active buildings & missing materials
-- (solves tool_missing.md #15: "why did the Well or Workshop construction stall?")
function OPS.building_status(args)
  local want_id = args and args.building_id and tonumber(args.building_id) or nil
  local only_incomplete = true
  if args and args.only_incomplete ~= nil then
    only_incomplete = (args.only_incomplete == true)
  end
  if want_id ~= nil then
    only_incomplete = false
  end
  local limit = (args and args.limit and tonumber(args.limit)) or 25
  local rows = {}
  for _, b in ipairs(df.global.world.buildings.all) do
    if want_id == nil or b.id == want_id then
      local stage, max_stage = 0, 0
      pcall(function() stage = b:getBuildStage() end)
      pcall(function() max_stage = b:getMaxBuildStage() end)
      local is_actual = true
      pcall(function() is_actual = b:isActual() end)
      local incomplete = (not is_actual) or (max_stage > 0 and stage < max_stage)
      if (not only_incomplete) or incomplete then
        local bname = "?"
        pcall(function() bname = dfhack.buildings.getName(b) or "?" end)
        local tname = tostring(b._type)
        tname = tname:match("<type: ([_%w]+)") or tname
        local jobs = {}
        local job_filters = {}
        local suspended = false
        pcall(function()
          for _, j in ipairs(b.jobs) do
            local jname = "?"
            pcall(function() jname = dfhack.job.getName(j) end)
            local jsusp = false
            pcall(function() jsusp = (j.flags.suspend == true) end)
            if jsusp then suspended = true end
            local worker = nil
            pcall(function()
              if j.unit ~= nil and j.unit.id ~= nil then worker = tonumber(j.unit.id) end
            end)
            jobs[#jobs + 1] = {
              job_type = tostring(j.job_type),
              description = jname,
              suspended = jsusp,
              worker_id = worker,
            }
            pcall(function()
              for _, ji in ipairs(j.job_items.elements) do
                local it_name = tostring(ji.item_type)
                local qty = tonumber(ji.quantity) or 0
                job_filters[#job_filters + 1] = {
                  item_type = it_name,
                  quantity_needed = qty,
                  mat_type = tonumber(ji.mat_type) or -1,
                }
              end
            end)
          end
        end)
        local items_attached = 0
        pcall(function() items_attached = #b.contained_items end)
        rows[#rows + 1] = {
          id = tonumber(b.id),
          name = bname,
          game_type = tname,
          x0 = tonumber(b.x1),
          x1 = tonumber(b.x2),
          y0 = tonumber(b.y1),
          y1 = tonumber(b.y2),
          elev = tonumber(b.z) - M.GAME_Z_OFFSET,
          build_stage = stage,
          max_build_stage = max_stage,
          complete = (not incomplete),
          suspended = suspended,
          items_attached = items_attached,
          jobs = jobs,
          missing_item_filters = job_filters,
        }
        if #rows >= limit then break end
      end
    end
  end
  return {
    count = #rows,
    only_incomplete = only_incomplete,
    buildings = rows,
  }
end

-- 4. configure_farmplot: read/set seasonal crops on a farm plot (solves tool_missing.md #6)
function OPS.configure_farmplot(args)
  local bid = args and tonumber(args.building_id) or nil
  local b = nil
  for _, fp in ipairs(df.global.world.buildings.other.FARM_PLOT) do
    if bid == nil or fp.id == bid then
      b = fp
      break
    end
  end
  if b == nil then
    error("farm plot not found" .. (bid and (" with building_id " .. tostring(bid)) or ""))
  end
  local want_crop = args and args.crop and tostring(args.crop):upper():gsub("[ %-]", "_") or nil
  local plant_idx = nil
  local plant_id_name = nil
  local available_crops = {}
  pcall(function()
    for idx, p in ipairs(df.global.world.raws.plants.all) do
      local pid = tostring(p.id)
      local is_crop = false
      pcall(function()
        is_crop = (p.flags.SPRING or p.flags.SUMMER or p.flags.AUTUMN or p.flags.WINTER)
      end)
      if is_crop and #available_crops < 25 then
        available_crops[#available_crops + 1] = { index = idx, id = pid }
      end
      if want_crop and (pid == want_crop or pid:find(want_crop, 1, true)) then
        if plant_idx == nil or pid == want_crop then
          plant_idx = idx
          plant_id_name = pid
        end
      end
    end
  end)
  if want_crop and plant_idx == nil then
    error("no plant raw matching '" .. tostring(args.crop) .. "'")
  end
  local seasons_changed = {}
  if plant_idx ~= nil then
    local set_all = (args.all_seasons ~= false)
    local map = {
      { idx = 0, name = "spring", on = set_all or (args.spring == true) },
      { idx = 1, name = "summer", on = set_all or (args.summer == true) },
      { idx = 2, name = "autumn", on = set_all or (args.autumn == true) },
      { idx = 3, name = "winter", on = set_all or (args.winter == true) },
    }
    for _, s in ipairs(map) do
      if s.on then
        local ok_w = pcall(function() b.plant_id[s.idx] = plant_idx end)
        if ok_w then seasons_changed[#seasons_changed + 1] = s.name end
      end
    end
  end
  local season_crops = {}
  local s_names = { [0] = "spring", [1] = "summer", [2] = "autumn", [3] = "winter" }
  for s_idx = 0, 3 do
    local pid_num = -1
    pcall(function() pid_num = tonumber(b.plant_id[s_idx]) or -1 end)
    local pname = nil
    if pid_num >= 0 then
      pcall(function() pname = tostring(df.global.world.raws.plants.all[pid_num].id) end)
    end
    season_crops[s_names[s_idx]] = { plant_index = pid_num, plant_id = pname }
  end
  return {
    building_id = tonumber(b.id),
    x0 = tonumber(b.x1),
    x1 = tonumber(b.x2),
    y0 = tonumber(b.y1),
    y1 = tonumber(b.y2),
    elev = tonumber(b.z) - M.GAME_Z_OFFSET,
    crop_set = plant_id_name,
    plant_index = plant_idx,
    seasons_updated = seasons_changed,
    seasons = season_crops,
    available_crops = available_crops,
  }
end

-- ---------------------------------------------------------------------------
-- Dispatcher: prints a single MCP_JSON|{...} line
-- ---------------------------------------------------------------------------

function M.dispatch(op, raw_args)
  local fn = OPS[tostring(op)]
  if not fn then
    local known = {}
    for k, _ in pairs(OPS) do known[#known + 1] = k end
    table.sort(known)
    print("MCP_JSON|" .. encode_json({
      ok = false,
      op = tostring(op),
      error = "unknown operation '" .. tostring(op) .. "'",
      known = known,
    }))
    return
  end
  local args = decode_json(raw_args)
  local ok, res = pcall(fn, args)
  if not ok then
    print("MCP_JSON|" .. encode_json({
      ok = false,
      op = tostring(op),
      error = tostring(res),
    }))
    return
  end
  print("MCP_JSON|" .. encode_json({
    ok = true,
    op = tostring(op),
    result = res,
  }))
end

_G.__dfhack_mcp = M

if not dfhack_flags or not dfhack_flags.module then
  local iargs = { ... }
  if #iargs >= 1 then
    M.dispatch(iargs[1], iargs[2] or "{}")
  else
    print("MCP_JSON|" .. encode_json({
      ok = true,
      module = "dfhack_mcp",
      version = M.VERSION,
    }))
  end
end

return M
