-- dfhack_mcp.lua - Server-side RPC Lua module for dfhack-mcp
-- Deployed to <gamedir>/hack/scripts/dfhack_mcp.lua via ./scripts/deploy.sh deploy
-- and auto-bootstrapped into _G.__dfhack_mcp by luaops.py when needed.
--@ module = true

local M = {}
M.VERSION = "2026.11.22-v1"
M.GAME_Z_OFFSET = 129

local ok_json, df_json = pcall(require, "json")

local function escape_str(s)
  -- Escape EVERY control character. DF strings (trader/group names, formatted text)
  -- contain raw control bytes, and a raw newline inside MCP_JSON splits the printed
  -- payload across text frames: the python parser reads only the first line and reports
  -- "malformed MCP_JSON payload: Unterminated string starting at line 1 column 307" for
  -- /trade-depot status (measured 2026-11-22) - the envelope was broken, not the tool.
  local out = tostring(s)
    :gsub("\\", "\\\\")
    :gsub('"', '\\"')
    :gsub("\n", "\\n")
    :gsub("\r", "\\r")
    :gsub("\t", "\\t")
  out = out:gsub("[\1-\31]", function(c)
    return string.format("\\u%04x", string.byte(c))
  end)
  return out
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

local function unit_readable_name(u)
  if u == nil then return nil end
  local nm = nil
  pcall(function() nm = dfhack.units.getReadableName(u) end)
  if not nm or nm == "" then
    pcall(function() nm = tostring(dfhack.units.getRaceName(u)) .. "#" .. tostring(u.id) end)
  end
  return nm or ("unit#" .. tostring(u.id))
end

-- 5. assign_zone_owner: set or read the owner of a room/activity zone (Office, Bedroom, DiningHall, Tomb)
function OPS.assign_zone_owner(args)
  local bid = args and tonumber(args.building_id or args.zone_id) or nil
  if bid == nil then error("building_id is required") end
  local z = nil
  for _, zz in ipairs(df.global.world.buildings.other.ANY_ZONE) do
    if zz.id == bid then z = zz; break end
  end
  if z == nil then error("activity zone not found with building_id " .. tostring(bid)) end

  if args and args.unit_id ~= nil then
    local uid = tonumber(args.unit_id)
    if uid == -1 then
      pcall(function() dfhack.buildings.setOwner(z, nil) end)
    else
      local u = df.unit.find(uid)
      if u == nil then error("unit not found with unit_id " .. tostring(uid)) end
      local ok_set, err_set = pcall(function() dfhack.buildings.setOwner(z, u) end)
      if not ok_set then error("setOwner failed: " .. tostring(err_set)) end
    end
  end

  local owner = nil
  pcall(function() owner = dfhack.buildings.getOwner(z) end)
  local room_desc = nil
  pcall(function() room_desc = dfhack.buildings.getRoomDescription(z, owner) end)
  local tname = "UNKNOWN"
  pcall(function() tname = tostring(df.civzone_type[z.type] or z.type) end)
  return {
    building_id = tonumber(z.id),
    zone_type_id = tonumber(z.type),
    zone_type = tname,
    x0 = tonumber(z.x1),
    x1 = tonumber(z.x2),
    y0 = tonumber(z.y1),
    y1 = tonumber(z.y2),
    elev = tonumber(z.z) - M.GAME_Z_OFFSET,
    owner_id = owner and tonumber(owner.id) or nil,
    owner_name = owner and unit_readable_name(owner) or nil,
    room_description = room_desc,
  }
end

-- 6. assign_pasture: assign, unassign, or clear tame grazing animals on a Pen/Pasture zone
function OPS.assign_pasture(args)
  local action = (args and args.action and tostring(args.action):lower()) or "assign"
  local bid = args and tonumber(args.building_id or args.zone_id) or nil
  local pen_type = df.civzone_type.Pen
  local z = nil
  for _, zz in ipairs(df.global.world.buildings.other.ANY_ZONE) do
    if (bid ~= nil and zz.id == bid) or (bid == nil and zz.type == pen_type) then
      z = zz
      break
    end
  end
  if z == nil then
    error(bid and ("zone not found with building_id " .. tostring(bid)) or "no Pen/Pasture zone found; create one first with create_zone(kind='pen')")
  end
  if z.type ~= pen_type then
    error("zone " .. tostring(z.id) .. " is not a Pen/Pasture (type=" .. tostring(z.type) .. ")")
  end

  local want_ids = {}
  local has_explicit = false
  if args and type(args.unit_ids) == "table" and #args.unit_ids > 0 then
    has_explicit = true
    for _, id in ipairs(args.unit_ids) do
      if tonumber(id) then want_ids[tonumber(id)] = true end
    end
  end
  local all_grazers = true
  if args and args.all_grazers ~= nil then all_grazers = (args.all_grazers == true) end
  local species_filter = args and args.species and tostring(args.species):upper() or nil

  local newly_assigned = {}
  local unassigned_now = {}
  if action == "clear" or action == "unassign" then
    for idx = #z.assigned_units - 1, 0, -1 do
      local uid = tonumber(z.assigned_units[idx])
      local u = uid and df.unit.find(uid) or nil
      local race = u and tostring(dfhack.units.getRaceName(u)):upper() or "?"
      local should_remove = (action == "clear")
      if not should_remove then
        if has_explicit then
          should_remove = (want_ids[uid] == true)
        elseif species_filter ~= nil then
          should_remove = (race:find(species_filter, 1, true) ~= nil)
        else
          should_remove = true
        end
      end
      if should_remove then
        z.assigned_units:erase(idx)
        if u ~= nil then
          pcall(function()
            for ridx = #u.general_refs - 1, 0, -1 do
              local r = u.general_refs[ridx]
              if df.general_ref_building_civzone_assignedst:is_instance(r) and r.building_id == z.id then
                u.general_refs:erase(ridx)
                pcall(function() r:delete() end)
              end
            end
          end)
        end
        unassigned_now[#unassigned_now + 1] = { unit_id = uid, species = race }
      end
    end
  elseif action == "assign" then
    if has_explicit or all_grazers or species_filter ~= nil then
      for _, u in ipairs(df.global.world.units.active) do
        local alive, tame, cit, grazer = false, false, false, false
        pcall(function() alive = dfhack.units.isAlive(u) end)
        pcall(function() tame = dfhack.units.isTame(u) end)
        pcall(function() cit = dfhack.units.isCitizen(u) end)
        pcall(function() grazer = dfhack.units.isGrazer(u) end)
        local race = "?"
        pcall(function() race = tostring(dfhack.units.getRaceName(u)):upper() end)
        local match = false
        if has_explicit then
          match = (want_ids[tonumber(u.id)] == true)
        elseif alive and tame and (not cit) then
          if species_filter ~= nil then
            match = (race:find(species_filter, 1, true) ~= nil)
          elseif all_grazers then
            match = grazer
          end
        end
        if match and alive then
          local uid = tonumber(u.id)
          -- Remove from any other pen/pit zone's assigned_units first
          for _, oz in ipairs(df.global.world.buildings.other.ANY_ZONE) do
            pcall(function()
              for idx = #oz.assigned_units - 1, 0, -1 do
                if oz.assigned_units[idx] == uid then
                  oz.assigned_units:erase(idx)
                end
              end
            end)
          end
          -- Remove any existing general_ref_building_civzone_assignedst on the unit
          pcall(function()
            for idx = #u.general_refs - 1, 0, -1 do
              local r = u.general_refs[idx]
              if df.general_ref_building_civzone_assignedst:is_instance(r) then
                u.general_refs:erase(idx)
                pcall(function() r:delete() end)
              end
            end
          end)
          -- Link unit <-> pen zone
          z.assigned_units:insert("#", uid)
          pcall(function()
            local ref = df.general_ref_building_civzone_assignedst:new()
            ref.building_id = z.id
            u.general_refs:insert("#", ref)
          end)
          newly_assigned[#newly_assigned + 1] = {
            unit_id = uid,
            species = race,
            grazer = grazer,
          }
        end
      end
    end
  else
    error("unknown assign_pasture action '" .. tostring(action) .. "' (expected assign, unassign, or clear)")
  end

  local assigned_list = {}
  local assigned_set = {}
  pcall(function()
    for i = 0, #z.assigned_units - 1 do
      local uid = tonumber(z.assigned_units[i])
      if uid then
        assigned_set[uid] = true
        local u = df.unit.find(uid)
        local race = u and tostring(dfhack.units.getRaceName(u)):upper() or "?"
        local grazer = false
        if u then pcall(function() grazer = dfhack.units.isGrazer(u) end) end
        assigned_list[#assigned_list + 1] = {
          unit_id = uid,
          species = race,
          grazer = grazer,
        }
      end
    end
  end)

  local unassigned_grazers = {}
  for _, u in ipairs(df.global.world.units.active) do
    local alive, tame, cit, grazer = false, false, false, false
    pcall(function() alive = dfhack.units.isAlive(u) end)
    pcall(function() tame = dfhack.units.isTame(u) end)
    pcall(function() cit = dfhack.units.isCitizen(u) end)
    pcall(function() grazer = dfhack.units.isGrazer(u) end)
    if alive and tame and (not cit) and grazer then
      local has_zone = assigned_set[tonumber(u.id)] == true
      if not has_zone then
        pcall(function()
          for i = 0, #u.general_refs - 1 do
            if df.general_ref_building_civzone_assignedst:is_instance(u.general_refs[i]) then
              has_zone = true
              break
            end
          end
        end)
      end
      if not has_zone then
        unassigned_grazers[#unassigned_grazers + 1] = {
          unit_id = tonumber(u.id),
          species = tostring(dfhack.units.getRaceName(u)):upper(),
        }
      end
    end
  end

  return {
    action = action,
    building_id = tonumber(z.id),
    x0 = tonumber(z.x1),
    x1 = tonumber(z.x2),
    y0 = tonumber(z.y1),
    y1 = tonumber(z.y2),
    elev = tonumber(z.z) - M.GAME_Z_OFFSET,
    newly_assigned_count = #newly_assigned,
    newly_assigned = newly_assigned,
    unassigned_now_count = #unassigned_now,
    unassigned_now = unassigned_now,
    total_assigned = #assigned_list,
    assigned_animals = assigned_list,
    unassigned_grazers_remaining = #unassigned_grazers,
    unassigned_grazers = unassigned_grazers,
  }
end

-- 7. manage_nobles: list, assign, or auto_assign fortress noble positions + bookkeeper precision
function OPS.manage_nobles(args)
  local action = (args and args.action and tostring(args.action):lower()) or "list"
  local ent = df.global.plotinfo.main.fortress_entity
  if ent == nil then error("no fortress_entity available") end

  local function assign_one_noble(pos_code_upper, u)
    local pos_obj = nil
    for _, p in ipairs(ent.positions.own) do
      if tostring(p.code):upper() == pos_code_upper then
        pos_obj = p
        break
      end
    end
    if pos_obj == nil then
      error("noble position '" .. tostring(pos_code_upper) .. "' not found in fortress_entity.positions.own")
    end
    local asn_obj = nil
    for _, a in ipairs(ent.positions.assignments) do
      if a.position_id == pos_obj.id then
        asn_obj = a
        break
      end
    end
    if asn_obj == nil then
      error("no assignment slot found for position '" .. tostring(pos_code_upper) .. "'")
    end
    local new_hf_id = u and tonumber(u.hist_figure_id) or -1
    if u ~= nil and (new_hf_id == nil or new_hf_id < 0) then
      error("unit " .. tostring(u.id) .. " has no historical figure id")
    end
    local old_hf_id = tonumber(asn_obj.histfig) or -1
    if old_hf_id >= 0 and old_hf_id ~= new_hf_id then
      local old_hf = df.historical_figure.find(old_hf_id)
      if old_hf ~= nil then
        pcall(function()
          for idx = #old_hf.entity_links - 1, 0, -1 do
            local el = old_hf.entity_links[idx]
            if df.histfig_entity_link_positionst:is_instance(el) and el.entity_id == ent.id and el.assignment_id == asn_obj.id then
              old_hf.entity_links:erase(idx)
              pcall(function() el:delete() end)
            end
          end
        end)
      end
    end
    asn_obj.histfig = new_hf_id
    asn_obj.histfig2 = new_hf_id
    if new_hf_id >= 0 then
      local hf = df.historical_figure.find(new_hf_id)
      if hf ~= nil then
        local has_link = false
        pcall(function()
          for i = 0, #hf.entity_links - 1 do
            local el = hf.entity_links[i]
            if df.histfig_entity_link_positionst:is_instance(el) and el.entity_id == ent.id and el.assignment_id == asn_obj.id then
              has_link = true
              break
            end
          end
        end)
        if not has_link then
          pcall(function()
            local link = df.histfig_entity_link_positionst:new()
            link.entity_id = ent.id
            link.link_strength = 100
            link.assignment_id = asn_obj.id
            link.start_year = df.global.cur_year
            hf.entity_links:insert("#", link)
          end)
        end
      end
    end
    return {
      position = pos_code_upper,
      assignment_id = tonumber(asn_obj.id),
      unit_id = u and tonumber(u.id) or nil,
      unit_name = u and unit_readable_name(u) or nil,
      histfig_id = new_hf_id,
    }
  end

  local changes = {}
  if action == "assign" then
    local pos_code = args and args.position and tostring(args.position):upper():gsub("[ %-]", "_") or nil
    if not pos_code or pos_code == "" then error("position is required for action='assign' (e.g. MANAGER, BOOKKEEPER, BROKER, CHIEF_MEDICAL_DWARF)") end
    if args.unit_id == nil then error("unit_id is required for action='assign'") end
    local uid = tonumber(args.unit_id)
    local u = nil
    if uid ~= -1 then
      u = df.unit.find(uid)
      if u == nil then error("unit not found with unit_id " .. tostring(uid)) end
      if not dfhack.units.isCitizen(u) then error("unit " .. tostring(uid) .. " is not a fortress citizen") end
    end
    changes[#changes + 1] = assign_one_noble(pos_code, u)
    if pos_code == "BOOKKEEPER" and (args.bookkeeper_settings == nil) then
      pcall(function() df.global.plotinfo.nobles.bookkeeper_settings = 4 end)
    end
  -- NOBLE LEGALITY: aptitudes. Measured 2026-11-22: the player's depot said "You do not have
  -- a broker" while the nobles screen showed one appointed, because auto_assign picked citizens
  -- round-robin by index with NO eligibility check. `positions.own[BROKER].apt` is EMPTY on this
  -- build (measured apt_count=0) and histfig flags expose no `merchanted` field, so the game's
  -- broker eligibility is not readable from those two sources - a position written onto an
  -- unqualified dwarf is a UI-visible lie, and the fortress's answer is the depot flag
  -- `anyone_can_trade`. The loop below therefore reports the aptitudes it CAN read, and warns
  -- when a position has none (i.e. eligibility cannot be verified here).
  local function apt_report(p)
    local res = {}
    if p == nil then return res end
    local n = 0
    pcall(function() n = #p.apt end)
    for i = 0, n - 1 do
      local a = p.apt[i]
      local keyname = "?"
      pcall(function() keyname = tostring(df.UnitAbility[a.key]) end)
      res[#res + 1] = { key = keyname, value = tonumber(a.value) }
    end
    return res
  end

  elseif action == "auto_assign" then
    local citizens = {}
    for _, u in ipairs(df.global.world.units.active) do
      local cit, alive = false, false
      pcall(function() cit = dfhack.units.isCitizen(u) end)
      pcall(function() alive = dfhack.units.isAlive(u) end)
      if cit and alive and tonumber(u.hist_figure_id) and tonumber(u.hist_figure_id) >= 0 then
        citizens[#citizens + 1] = u
      end
    end
    if #citizens == 0 then error("no living citizens found to appoint as nobles") end
    local essential = { "MANAGER", "BOOKKEEPER", "BROKER", "CHIEF_MEDICAL_DWARF" }
    local c_idx = 1
    for _, code in ipairs(essential) do
      local pos_id = nil
      for _, p in ipairs(ent.positions.own) do
        if tostring(p.code):upper() == code then pos_id = p.id; break end
      end
      if pos_id ~= nil then
        local cur_hf = -1
        for _, a in ipairs(ent.positions.assignments) do
          if a.position_id == pos_id then cur_hf = tonumber(a.histfig) or -1; break end
        end
        if cur_hf < 0 then
          local u = citizens[((c_idx - 1) % #citizens) + 1]
          c_idx = c_idx + 1
          local ap = apt_report(pos_obj)
          local ch = assign_one_noble(code, u)
          ch.aptitudes = ap
          if #ap == 0 then
            ch.legibility = "UNVERIFIED: this position exposes no aptitude list on this build, so the tool cannot confirm the dwarf qualifies (measured for BROKER: apt_count 0, no histfig 'merchanted' flag). The game may still refuse the dwarf in its own screens - for a Trade Depot, trade_flags.anyone_can_trade=true is the fortress's answer."
          end
          changes[#changes + 1] = ch
        end
      end
    end
    pcall(function() df.global.plotinfo.nobles.bookkeeper_settings = 4 end)
  elseif action ~= "list" then
    error("unknown manage_nobles action '" .. tostring(action) .. "' (expected list, assign, or auto_assign)")
  end

  if args and args.bookkeeper_settings ~= nil then
    local bset = math.max(0, math.min(4, tonumber(args.bookkeeper_settings) or 4))
    pcall(function() df.global.plotinfo.nobles.bookkeeper_settings = bset end)
  end

  -- Map histfig_id -> citizen unit
  local hf_to_unit = {}
  for _, u in ipairs(df.global.world.units.active) do
    local hfid = tonumber(u.hist_figure_id)
    if hfid and hfid >= 0 then hf_to_unit[hfid] = u end
  end

  local pos_by_id = {}
  for _, p in ipairs(ent.positions.own) do
    pos_by_id[tonumber(p.id)] = p
  end

  local positions = {}
  for _, a in ipairs(ent.positions.assignments) do
    local p = pos_by_id[tonumber(a.position_id)]
    if p ~= nil then
      local hfid = tonumber(a.histfig) or -1
      local u = (hfid >= 0) and hf_to_unit[hfid] or nil
      local title = ""
      pcall(function() title = tostring(p.name[0] or "") end)
      positions[#positions + 1] = {
        code = tostring(p.code),
        title = title,
        position_id = tonumber(p.id),
        assignment_id = tonumber(a.id),
        vacant = (hfid < 0),
        histfig_id = (hfid >= 0) and hfid or nil,
        unit_id = u and tonumber(u.id) or nil,
        unit_name = u and unit_readable_name(u) or nil,
      }
    end
  end

  local bk_settings = nil
  pcall(function() bk_settings = tonumber(df.global.plotinfo.nobles.bookkeeper_settings) end)
  return {
    action = action,
    changes = changes,
    bookkeeper_settings = bk_settings,
    positions = positions,
  }
end

-- 8. manage_work_orders: list or cancel manager work orders + check manager appointment status
function OPS.manage_work_orders(args)
  local action = (args and args.action and tostring(args.action):lower()) or "list"
  local cancelled = {}
  if action == "cancel" then
    local want_id = args and args.order_id and tonumber(args.order_id) or nil
    local want_job = args and args.job and tostring(args.job):lower() or nil
    if want_id == nil and want_job == nil then
      error("order_id or job is required for action='cancel'")
    end
    local vec = df.global.world.manager_orders.all
    for idx = #vec - 1, 0, -1 do
      local mo = vec[idx]
      local jtype_name = tostring(df.job_type[mo.job_type] or mo.job_type)
      local match = false
      if want_id ~= nil and tonumber(mo.id) == want_id then
        match = true
      elseif want_job ~= nil and jtype_name:lower() == want_job then
        match = true
      end
      if match then
        cancelled[#cancelled + 1] = {
          order_id = tonumber(mo.id),
          job = jtype_name,
          amount_left = tonumber(mo.amount_left),
          amount_total = tonumber(mo.amount_total),
        }
        vec:erase(idx)
        pcall(function() mo:delete() end)
      end
    end
  end

  local orders = {}
  pcall(function()
    for _, mo in ipairs(df.global.world.manager_orders.all) do
      local jtype_name = tostring(df.job_type[mo.job_type] or mo.job_type)
      local desc = jtype_name
      pcall(function() desc = dfhack.job.getManagerOrderName(mo) or jtype_name end)
      local validated, active = false, false
      pcall(function() validated = (mo.status.validated == true) end)
      pcall(function() active = (mo.status.active == true) end)
      orders[#orders + 1] = {
        order_id = tonumber(mo.id),
        job = jtype_name,
        description = desc,
        amount_left = tonumber(mo.amount_left),
        amount_total = tonumber(mo.amount_total),
        validated = validated,
        active = active,
        frequency = tostring(mo.frequency),
        workshop_id = tonumber(mo.workshop_id),
      }
    end
  end)

  local has_manager = false
  pcall(function()
    local ent = df.global.plotinfo.main.fortress_entity
    local mgr_pos_id = nil
    for _, p in ipairs(ent.positions.own) do
      if tostring(p.code):upper() == "MANAGER" then mgr_pos_id = p.id; break end
    end
    if mgr_pos_id ~= nil then
      for _, a in ipairs(ent.positions.assignments) do
        if a.position_id == mgr_pos_id and tonumber(a.histfig) and tonumber(a.histfig) >= 0 then
          has_manager = true
          break
        end
      end
    end
  end)

  return {
    action = action,
    count = #orders,
    has_manager = has_manager,
    cancelled = cancelled,
    orders = orders,
  }
end

-- 9. alerts_and_wellbeing: structured threat/opportunity radar + citizen wellbeing metrics
function OPS.alerts_and_wellbeing(_args)
  local alerts = {}
  local popup_count = 0
  pcall(function() popup_count = #df.global.world.status.popups end)
  if popup_count > 0 then
    alerts[#alerts + 1] = {
      kind = "popup_open",
      severity = "blocking",
      detail = tostring(popup_count) .. " modal popup dialog(s) currently open in the game UI",
      suggested_action = "inspect with copy_screen and dismiss via pass_keyboard_event",
    }
  end

  local caravan_count = 0
  pcall(function()
    for _, c in ipairs(df.global.plotinfo.caravans) do
      local tstate = tonumber(c.trade_state) or 0
      local time_rem = tonumber(c.time_remaining) or 0
      if tstate > 0 or time_rem > 0 then
        caravan_count = caravan_count + 1
        alerts[#alerts + 1] = {
          kind = "caravan_present",
          severity = "opportunity",
          detail = "Caravan present (trade_state=" .. tostring(tstate) .. ", time_remaining=" .. tostring(time_rem) .. ")",
          suggested_action = "ensure a Trade Depot is built and a Broker is appointed via manage_nobles",
        }
      end
    end
  end)

  local citizens = 0
  local thirsty, hungry, drowsy, stressed, wounded = 0, 0, 0, 0, 0
  local strange_moods = {}
  local citizen_issues = {}
  local hostiles = {}
  local unassigned_grazers = 0

  for _, u in ipairs(df.global.world.units.active) do
    local alive = false
    pcall(function() alive = dfhack.units.isAlive(u) end)
    if alive then
      local cit = false
      pcall(function() cit = dfhack.units.isCitizen(u) end)
      if cit then
        citizens = citizens + 1
        local th, hu, sl, st, md, wnd = 0, 0, 0, 0, -1, 0
        pcall(function() th = tonumber(u.counters2.thirst_timer) or 0 end)
        pcall(function() hu = tonumber(u.counters2.hunger_timer) or 0 end)
        pcall(function() sl = tonumber(u.counters2.sleepiness_timer) or 0 end)
        pcall(function() st = tonumber(u.status.current_soul.personality.stress) or 0 end)
        pcall(function() md = tonumber(u.mood) or -1 end)
        pcall(function() wnd = #u.body.wounds end)
        local flags = {}
        if th > 25000 then thirsty = thirsty + 1; flags[#flags + 1] = "thirsty(" .. th .. ")" end
        if hu > 50000 then hungry = hungry + 1; flags[#flags + 1] = "hungry(" .. hu .. ")" end
        if sl > 50000 then drowsy = drowsy + 1; flags[#flags + 1] = "drowsy(" .. sl .. ")" end
        if st >= 25000 then stressed = stressed + 1; flags[#flags + 1] = "stressed(" .. st .. ")" end
        if wnd > 0 then wounded = wounded + 1; flags[#flags + 1] = "wounds(" .. wnd .. ")" end
        if md ~= -1 then
          local mname = tostring(df.mood_type[md] or md)
          strange_moods[#strange_moods + 1] = {
            unit_id = tonumber(u.id),
            name = unit_readable_name(u),
            mood = mname,
          }
          flags[#flags + 1] = "mood(" .. mname .. ")"
        end
        if #flags > 0 then
          citizen_issues[#citizen_issues + 1] = {
            unit_id = tonumber(u.id),
            name = unit_readable_name(u),
            issues = flags,
          }
        end
      else
        local tame = false
        pcall(function() tame = dfhack.units.isTame(u) end)
        if tame then
          local grazer = false
          pcall(function() grazer = dfhack.units.isGrazer(u) end)
          if grazer then
            local has_pen = false
            pcall(function()
              for i = 0, #u.general_refs - 1 do
                if df.general_ref_building_civzone_assignedst:is_instance(u.general_refs[i]) then
                  has_pen = true
                  break
                end
              end
            end)
            if not has_pen then unassigned_grazers = unassigned_grazers + 1 end
          end
        else
          local inv = false
          pcall(function()
            inv = (u.flags1.active_invader == true) or (u.flags1.marauder == true)
               or (u.flags1.invader_origin == true) or (u.flags2.visitor_uninvited == true)
          end)
          if inv and #hostiles < 15 then
            hostiles[#hostiles + 1] = {
              unit_id = tonumber(u.id),
              species = tostring(dfhack.units.getRaceName(u)),
              x = tonumber(u.pos.x),
              y = tonumber(u.pos.y),
              elev = tonumber(u.pos.z) - M.GAME_Z_OFFSET,
            }
          end
        end
      end
    end
  end

  if #hostiles > 0 then
    alerts[#alerts + 1] = {
      kind = "hostile_units",
      severity = "critical",
      detail = tostring(#hostiles) .. " hostile/invader unit(s) detected on the map",
      suggested_action = "lock entrance doors or retreat citizens indoors",
    }
  end
  if #strange_moods > 0 then
    alerts[#alerts + 1] = {
      kind = "strange_mood_or_tantrum",
      severity = "critical",
      detail = tostring(#strange_moods) .. " citizen(s) in special mood state",
      suggested_action = "ensure appropriate craft workshops and raw materials are available",
    }
  end
  if thirsty > 0 or hungry > 0 then
    alerts[#alerts + 1] = {
      kind = "citizen_sustenance",
      severity = "warning",
      detail = tostring(thirsty) .. " thirsty and " .. tostring(hungry) .. " hungry citizen(s)",
      suggested_action = "build a Still, gather_plants or configure_farmplot, and queue BrewDrink via work_order",
    }
  end
  if unassigned_grazers > 0 then
    alerts[#alerts + 1] = {
      kind = "unpastured_grazers",
      severity = "warning",
      detail = tostring(unassigned_grazers) .. " tame grazing animal(s) are not assigned to any Pen/Pasture zone and risk starvation",
      suggested_action = "create a surface Pen zone with create_zone(kind='pen') and call assign_pasture",
    }
  end

  return {
    alerts_count = #alerts,
    alerts = alerts,
    popups_open = popup_count,
    caravans_present = caravan_count,
    hostiles = hostiles,
    unassigned_grazers = unassigned_grazers,
    wellbeing = {
      citizens = citizens,
      thirsty = thirsty,
      hungry = hungry,
      drowsy = drowsy,
      stressed = stressed,
      wounded = wounded,
      strange_moods = strange_moods,
      citizen_issues = citizen_issues,
    },
  }
end

-- 10. gather_and_items: gather surface plants, toggle dump/forbid on items in a box, or protect seeds from cooking
function OPS.gather_and_items(args)
  local action = (args and args.action and tostring(args.action):lower()) or "gather_plants"
  if action == "protect_seeds" then
    local out1, out2 = "", ""
    pcall(function() out1 = dfhack.run_command_silent("ban-cooking", "seeds") or "" end)
    if args and args.ban_brew ~= false then
      pcall(function() out2 = dfhack.run_command_silent("ban-cooking", "brew") or "" end)
    end
    return {
      action = "protect_seeds",
      ban_seeds_output = out1:gsub("%s+$", ""),
      ban_brew_output = out2:gsub("%s+$", ""),
    }
  end

  if args == nil or args.x0 == nil or args.y0 == nil or args.x1 == nil or args.y1 == nil or args.elev == nil then
    error("x0, y0, x1, y1, and elev are required for action='" .. tostring(action) .. "'")
  end
  local x0, x1 = math.min(tonumber(args.x0), tonumber(args.x1)), math.max(tonumber(args.x0), tonumber(args.x1))
  local y0, y1 = math.min(tonumber(args.y0), tonumber(args.y1)), math.max(tonumber(args.y0), tonumber(args.y1))
  local elev = tonumber(args.elev)
  local z = elev + M.GAME_Z_OFFSET

  if action == "gather_plants" then
    local shrubs_found, designated = 0, 0
    local shrub_shape = df.tiletype_shape.SHRUB
    local plant_mat = df.tiletype_material.PLANT
    for y = y0, y1 do
      for x = x0, x1 do
        local tt = dfhack.maps.getTileType(x, y, z)
        local fl = dfhack.maps.getTileFlags(x, y, z)
        if tt ~= nil and fl ~= nil and (not fl.hidden) then
          local is_shrub = false
          pcall(function()
            local attrs = df.tiletype.attrs[tt]
            is_shrub = (attrs.shape == shrub_shape) or (attrs.material == plant_mat and attrs.shape ~= df.tiletype_shape.TREE)
          end)
          if is_shrub then
            shrubs_found = shrubs_found + 1
            if fl.dig ~= df.tile_dig_designation.Default then
              fl.dig = df.tile_dig_designation.Default
              designated = designated + 1
            end
          end
        end
      end
    end
    if designated > 0 then
      pcall(function()
        local blk = dfhack.maps.getTileBlock(x0, y0, z)
        if blk then blk.flags.designated = true end
      end)
      pcall(dfhack.job.checkDesignationsNow)
    end
    return {
      action = "gather_plants",
      elev = elev,
      box = { x0, y0, x1, y1 },
      shrubs_found = shrubs_found,
      designated = designated,
    }
  elseif action == "dump" or action == "undump" or action == "forbid" or action == "reclaim" then
    local filter = args.item_filter and tostring(args.item_filter):lower() or nil
    local matched, updated = 0, 0
    local samples = {}
    for _, it in ipairs(df.global.world.items.other.IN_PLAY) do
      local okp, pos = pcall(function() return it.pos end)
      if okp and pos and pos.z == z and pos.x >= x0 and pos.x <= x1 and pos.y >= y0 and pos.y <= y1 then
        local on_ground = false
        pcall(function()
          on_ground = (it.flags.on_ground == true) and (not it.flags.in_building) and (not it.flags.construction)
        end)
        if on_ground then
          local desc = ""
          pcall(function() desc = tostring(dfhack.items.getDescription(it, 0) or "") end)
          if filter == nil or desc:lower():find(filter, 1, true) ~= nil then
            matched = matched + 1
            local chg = false
            pcall(function()
              if action == "dump" and (not it.flags.dump) then
                it.flags.dump = true; chg = true
              elseif action == "undump" and it.flags.dump then
                it.flags.dump = false; chg = true
              elseif action == "forbid" and (not it.flags.forbid) then
                it.flags.forbid = true; chg = true
              elseif action == "reclaim" and it.flags.forbid then
                it.flags.forbid = false; chg = true
              end
            end)
            if chg then updated = updated + 1 end
            if #samples < 15 then
              samples[#samples + 1] = {
                item_id = tonumber(it.id),
                description = desc,
                x = tonumber(pos.x),
                y = tonumber(pos.y),
              }
            end
          end
        end
      end
    end
    return {
      action = action,
      elev = elev,
      box = { x0, y0, x1, y1 },
      item_filter = filter,
      matched = matched,
      updated = updated,
      samples = samples,
    }
  else
    error("unknown gather_and_items action '" .. tostring(action) .. "' (expected gather_plants, dump, undump, forbid, reclaim, or protect_seeds)")
  end
end

-- 11. trade_depot: inspect depot, list tradeable goods, mark/unmark goods for hauling to depot,
-- request Broker, and inspect/select items on the barter screen when open.
function OPS.trade_depot(args)
  local action = (args and args.action and tostring(args.action):lower()) or "status"
  local bid = args and tonumber(args.building_id) or nil
  local depot = nil
  for _, td in ipairs(df.global.world.buildings.other.TRADE_DEPOT) do
    if bid == nil or td.id == bid then
      depot = td
      break
    end
  end
  if depot == nil then
    error("no Trade Depot found" .. (bid and (" with building_id " .. tostring(bid)) or "; place one first with place_building(building='TradeDepot', w=5, h=5)"))
  end

  local perm_items = {}
  local depot_trade_goods = {}
  pcall(function()
    for _, ci in ipairs(depot.contained_items) do
      if ci.item ~= nil then
        local iid = tonumber(ci.item.id)
        local mode = tonumber(ci.use_mode) or 0
        if mode == 2 then
          perm_items[iid] = true
        else
          local val = 0
          pcall(function() val = tonumber(dfhack.items.getValue(ci.item)) or 0 end)
          local desc = ""
          pcall(function() desc = tostring(dfhack.items.getDescription(ci.item, 0) or "") end)
          depot_trade_goods[#depot_trade_goods + 1] = {
            item_id = iid,
            type = tostring(df.item_type[ci.item:getType()] or ci.item:getType()),
            description = desc,
            value = val,
          }
        end
      end
    end
  end)

  local function is_pending_depot_haul(it)
    local pending, job_obj = false, nil
    pcall(function()
      if it.flags.in_job then
        local sref = dfhack.items.getSpecificRef(it, df.specific_ref_type.JOB)
        if sref and sref.data and sref.data.job and sref.data.job.job_type == df.job_type.BringItemToDepot then
          pending = true
          job_obj = sref.data.job
        end
      end
    end)
    return pending, job_obj
  end

  local function is_candidate_trade_good(it)
    if perm_items[tonumber(it.id)] then return false end
    if it.flags.in_building then return false end
    if it.flags.in_inventory then return false end
    local ok_tr, can_tr = pcall(function() return dfhack.items.canTradeWithContents(it) end)
    if not (ok_tr and can_tr) then return false end
    return true
  end

  local want_ids = {}
  local has_explicit_ids = false
  if args and type(args.item_ids) == "table" and #args.item_ids > 0 then
    has_explicit_ids = true
    for _, id in ipairs(args.item_ids) do
      if tonumber(id) then want_ids[tonumber(id)] = true end
    end
  end
  local filter = args and args.item_filter and tostring(args.item_filter):lower() or nil
  local min_value = (args and args.min_value and tonumber(args.min_value)) or 0
  local max_items = (args and args.max_items and tonumber(args.max_items)) or 25

  local marked_now = {}
  local unmarked_now = {}

  if action == "request_broker" then
    local req_val = true
    if args and args.trader_requested ~= nil then req_val = (args.trader_requested == true) end
    depot.trade_flags.trader_requested = req_val
    if args and args.anyone_can_trade ~= nil then
      depot.trade_flags.anyone_can_trade = (args.anyone_can_trade == true)
    end
  elseif action == "mark_goods" then
    for _, it in ipairs(df.global.world.items.other.IN_PLAY) do
      if #marked_now >= max_items then break end
      local iid = tonumber(it.id)
      local pend, _ = is_pending_depot_haul(it)
      if (not pend) and is_candidate_trade_good(it) then
        local desc = ""
        pcall(function() desc = tostring(dfhack.items.getDescription(it, 0) or "") end)
        local itype = tostring(df.item_type[it:getType()] or it:getType())
        local val = 0
        pcall(function() val = tonumber(dfhack.items.getValue(it)) or 0 end)
        local match = false
        if has_explicit_ids then
          match = (want_ids[iid] == true)
        elseif val >= min_value then
          if filter == nil then
            match = true
          else
            match = (desc:lower():find(filter, 1, true) ~= nil) or (itype:lower():find(filter, 1, true) ~= nil)
          end
        end
        if match then
          local ok_m, res_m = pcall(function() return dfhack.items.markForTrade(it, depot) end)
          if ok_m and res_m ~= false then
            marked_now[#marked_now + 1] = {
              item_id = iid,
              type = itype,
              description = desc,
              value = val,
            }
          end
        end
      end
    end
  elseif action == "unmark_goods" then
    for _, it in ipairs(df.global.world.items.other.IN_PLAY) do
      local iid = tonumber(it.id)
      local desc = ""
      pcall(function() desc = tostring(dfhack.items.getDescription(it, 0) or "") end)
      local itype = tostring(df.item_type[it:getType()] or it:getType())
      local match = true
      if has_explicit_ids then
        match = (want_ids[iid] == true)
      elseif filter ~= nil then
        match = (desc:lower():find(filter, 1, true) ~= nil) or (itype:lower():find(filter, 1, true) ~= nil)
      end
      if match then
        local pend, job_obj = is_pending_depot_haul(it)
        if pend and job_obj ~= nil then
          local ok_rj = pcall(function() dfhack.job.removeJob(job_obj) end)
          if ok_rj then
            unmarked_now[#unmarked_now + 1] = { item_id = iid, description = desc, removed_from = "haul_queue" }
          end
        elseif (not perm_items[iid]) and it.flags.in_building then
          local holder = nil
          pcall(function() holder = dfhack.items.getHolderBuilding(it) end)
          if holder == depot then
            it.flags.in_building = false
            unmarked_now[#unmarked_now + 1] = { item_id = iid, description = desc, removed_from = "depot" }
          end
        end
      end
    end
  elseif action == "barter_status" or action == "barter_select" then
    local tr = df.global.game.main_interface.trade
    local is_open = (tr ~= nil and tr.open == true)
    if not is_open then
      return {
        action = action,
        building_id = tonumber(depot.id),
        trade_screen_open = false,
        note = "The barter screen (df.global.game.main_interface.trade) is not currently open. A merchant caravan must be at the Trade Depot and the Broker must open the Trade screen first.",
      }
    end
    local side_idx = (args and args.side == "merchant") and 0 or 1
    local select_val = true
    if args and args.selected ~= nil then select_val = (args.selected == true) end
    local toggled = {}
    if action == "barter_select" then
      local goods = tr.good[side_idx]
      local flags = tr.goodflag[side_idx]
      for i = 0, #goods - 1 do
        local it = goods[i]
        local gf = flags[i]
        local iid = tonumber(it.id)
        local desc = ""
        pcall(function() desc = tostring(dfhack.items.getDescription(it, 0) or "") end)
        local match = false
        if has_explicit_ids then
          match = (want_ids[iid] == true)
        elseif filter ~= nil then
          match = (desc:lower():find(filter, 1, true) ~= nil)
        end
        if match then
          gf.selected = select_val
          toggled[#toggled + 1] = { item_id = iid, description = desc, selected = select_val }
        end
      end
    end
    local function summarize_side(s_idx)
      local list = {}
      local total_selected_val = 0
      pcall(function()
        local goods = tr.good[s_idx]
        local flags = tr.goodflag[s_idx]
        for i = 0, #goods - 1 do
          local it = goods[i]
          local gf = flags[i]
          local val = 0
          pcall(function() val = tonumber(dfhack.items.getValue(it)) or 0 end)
          local desc = ""
          pcall(function() desc = tostring(dfhack.items.getDescription(it, 0) or "") end)
          if gf.selected then total_selected_val = total_selected_val + val end
          if #list < 40 or gf.selected then
            list[#list + 1] = {
              index = i,
              item_id = tonumber(it.id),
              description = desc,
              value = val,
              selected = (gf.selected == true),
              contained = (gf.contained == true),
            }
          end
        end
      end)
      return { items = list, selected_value = total_selected_val }
    end
    return {
      action = action,
      building_id = tonumber(depot.id),
      trade_screen_open = true,
      still_unloading = (tonumber(tr.stillunloading) or 0) ~= 0,
      have_talker = (tonumber(tr.havetalker) or 0) ~= 0,
      toggled = toggled,
      merchant_side = summarize_side(0),
      fortress_side = summarize_side(1),
    }
  elseif action ~= "status" then
    error("unknown trade_depot action '" .. tostring(action) .. "' (expected status, request_broker, mark_goods, unmark_goods, barter_status, or barter_select)")
  end

  -- Collect pending BringItemToDepot jobs and top tradeable candidate items
  local pending_haul = {}
  local candidates = {}
  for _, it in ipairs(df.global.world.items.other.IN_PLAY) do
    local pend, _ = is_pending_depot_haul(it)
    if pend then
      local val = 0
      pcall(function() val = tonumber(dfhack.items.getValue(it)) or 0 end)
      local desc = ""
      pcall(function() desc = tostring(dfhack.items.getDescription(it, 0) or "") end)
      pending_haul[#pending_haul + 1] = {
        item_id = tonumber(it.id),
        type = tostring(df.item_type[it:getType()] or it:getType()),
        description = desc,
        value = val,
      }
    elseif is_candidate_trade_good(it) then
      local desc = ""
      pcall(function() desc = tostring(dfhack.items.getDescription(it, 0) or "") end)
      local itype = tostring(df.item_type[it:getType()] or it:getType())
      local val = 0
      pcall(function() val = tonumber(dfhack.items.getValue(it)) or 0 end)
      if val >= min_value and (filter == nil or desc:lower():find(filter, 1, true) ~= nil or itype:lower():find(filter, 1, true) ~= nil) then
        candidates[#candidates + 1] = {
          item_id = tonumber(it.id),
          type = itype,
          description = desc,
          value = val,
          is_bin = df.item_binst:is_instance(it),
        }
      end
    end
  end
  table.sort(candidates, function(a, b)
    if a.value == b.value then return a.item_id < b.item_id end
    return a.value > b.value
  end)
  local top_candidates = {}
  for i = 1, math.min(#candidates, max_items) do
    top_candidates[i] = candidates[i]
  end

  local caravans = {}
  pcall(function()
    for idx, c in ipairs(df.global.plotinfo.caravans) do
      local tstate = tonumber(c.trade_state) or 0
      local time_rem = tonumber(c.time_remaining) or 0
      if tstate > 0 or time_rem > 0 then
        caravans[#caravans + 1] = {
          index = idx,
          trade_state = tstate,
          trade_state_name = tostring(df.caravan_state.T_trade_state[tstate] or tstate),
          time_remaining = time_rem,
        }
      end
    end
  end)

  local stage, max_stage = 0, 0
  pcall(function() stage = depot:getBuildStage() end)
  pcall(function() max_stage = depot:getMaxBuildStage() end)

  return {
    action = action,
    building_id = tonumber(depot.id),
    x0 = tonumber(depot.x1),
    x1 = tonumber(depot.x2),
    y0 = tonumber(depot.y1),
    y1 = tonumber(depot.y2),
    elev = tonumber(depot.z) - M.GAME_Z_OFFSET,
    build_stage = stage,
    max_build_stage = max_stage,
    complete = (max_stage == 0 or stage >= max_stage),
    accessible = (depot.accessible == true),
    trader_requested = (depot.trade_flags.trader_requested == true),
    anyone_can_trade = (depot.trade_flags.anyone_can_trade == true),
    caravans_present = #caravans,
    caravans = caravans,
    marked_now_count = #marked_now,
    marked_now = marked_now,
    unmarked_now_count = #unmarked_now,
    unmarked_now = unmarked_now,
    goods_at_depot_count = #depot_trade_goods,
    goods_at_depot = depot_trade_goods,
    pending_haul_count = #pending_haul,
    pending_haul = pending_haul,
    tradeable_candidates_total = #candidates,
    tradeable_candidates = top_candidates,
  }
end

-- ---------------------------------------------------------------------------
-- Dispatcher: prints a single MCP_JSON|{...} line
-- ---------------------------------------------------------------------------

-- 12. pile_arrays: the crash the player hits by CLICKING Custom on a tool-made pile
-- crash_2026-11-22-20-02-29.txt: SIGSEGV at 0x5, render_things -> overlay.plug.so, no Lua
-- involvement. Cause: category flags ON over per-category material arrays that the UI allocates
-- when *it* switches a category on. A tool-made pile has flags without arrays, and the settings
-- screen walks a nil pointer. This op measures (action="check") and allocates (action="fix").
function OPS.pile_arrays(args)
  local action = (args and args.action and tostring(args.action):lower()) or "check"
  local only_id = args and tonumber(args.building_id) or nil
  -- The reference sizes come from a pile the GAME built (the UI allocates real arrays), so
  -- nothing here is a guess from the wiki.
  local reference = {}
  for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do
    for _,c in ipairs({"ammo","animals","armor","bars_blocks","cloth","coins","corpses",
                       "finished_goods","food","furniture","gems","leather","refuse","sheet",
                       "stone","weapons","wood"}) do
      local ok = pcall(function()
        for k,v in pairs(sp.settings[c]) do
          if type(v) == "table" or type(v) == "userdata" then
            local n = 0
            pcall(function() n = #v end)
            if n > (reference[c] and reference[c][k] or 0) then
              reference[c] = reference[c] or {}
              reference[c][k] = n
            end
          end
        end
      end)
    end
  end
  local report = {}
  for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do
    if only_id == nil or sp.id == only_id then
      local entry = { building_id = tonumber(sp.id), empty_fields = {}, fixed = {} }
      for _,c in ipairs({"ammo","animals","armor","bars_blocks","cloth","coins","corpses",
                         "finished_goods","food","furniture","gems","leather","refuse","sheet",
                         "stone","weapons","wood"}) do
        local on = false
        pcall(function() on = (sp.settings.flags[c] == true) end)
        if on then
          pcall(function()
            for k,v in pairs(sp.settings[c]) do
              if (type(v) == "table" or type(v) == "userdata") and tostring(v):find("vector<char>") then
                local want = (reference[c] and reference[c][k]) or 1
                if want < 1 then want = 1 end
                local ref = reference[c] and reference[c][k] or 0
                -- 0 is a LEGAL size when the game's own pile has 0 there (wood.mats measures 0 on
                -- a UI pile and opens fine). Only an array that the game fills and this pile does
                -- not is the crash condition.
                if ref > 0 and #v < ref then
                  entry.empty_fields[#entry.empty_fields+1] = c.."."..k.." size="..tostring(#v).."/"..tostring(ref)
                  if action == "fix" then
                    v:resize(ref)
                    want = ref
                    for i = 0, want - 1 do pcall(function() v[i] = 1 end) end
                    entry.fixed[#entry.fixed+1] = c.."."..k.."->"..tostring(want)
                  end
                end
              end
            end
          end)
        end
      end
      report[#report+1] = entry
    end
  end
  return { action = action, piles = report, reference_categories = (function()
    local n = 0 for _,_ in pairs(reference) do n = n + 1 end return n end)() }
end

-- (13 was find_soil. REMOVED: the Lua route to the tile grids is closed on this build - measured
-- `world.T_map.all: not found`, `map:find` nil, and every block grid (`matgrid`, `typegrid`,
-- `tileptr`, `regiongrid`, `water`) is unreadable; `pairs(block)` itself raises. It answered
-- `blocks_scanned: 0, soil_tiles: 0`, which is a read failure wearing a census. The census lives in
-- the server now (`GET /find-soil`), on the RPC block cache, reading the game's tiletype list.)

-- 14. unit_labors: the read-back for labor writes. `/set-unit-labors` validates names against
-- the RPC labor list and reports `applied` - that is validation, not the game's state. Measured
-- 2026-11-22: the player said "you only have one miner" while my write claimed applied=2; the
-- game's own fields (`u.status.labors`, field names like MINE, not MINING - `labors.MINING`
-- does not exist and a pcall probe reads it as a silent false) are the only authority.
function OPS.unit_labors(args)
  want = {}
  local ids = args and args.unit_ids
  if type(ids) == "table" then
    for _,id in ipairs(ids) do want[tonumber(id)] = true end
  end
  local filter = args and args.labor and tostring(args.labor):upper() or nil
  local units = {}
  for _,u in ipairs(df.global.world.units.active) do
    local cit, alive = false, false
    pcall(function() cit = dfhack.units.isCitizen(u) end)
    pcall(function() alive = dfhack.units.isAlive(u) end)
    if cit and alive and (next(want) == nil or want[tonumber(u.id)] == true) then
      local on = {}
      pcall(function()
        for k,v in pairs(u.status.labors) do
          if v == true then
            if filter == nil or tostring(k):upper() == filter then on[#on+1] = tostring(k) end
          end
        end
      end)
      table.sort(on)
      local nm = "?"
      pcall(function() nm = tostring(u.name.first_name) end)
      units[#units+1] = { unit_id = tonumber(u.id), name = nm, labors_on = on,
                          labor_count = #on, has_filter = (filter ~= nil and want[tonumber(u.id)] == true) }
    end
  end
  table.sort(units, function(a,b) return a.unit_id < b.unit_id end)
  -- Count citizens holding the filtered labor. `labor_count` is a NUMBER (it is the length
  -- already); `#x.labor_count` was my slip - the game answered:
  -- "attempt to get length of a number value (field 'labor_count')" (measured 2026-11-22).
  local with = 0
  if filter ~= nil then
    for _,x in ipairs(units) do if (x.labor_count or 0) > 0 then with = with + 1 end end
  end
  return { filter = filter, citizens = #units, with_labor = with, units = units }
end

-- 15. unit_profession: WHO IS A MINER, in the game's own terms.
-- Measured 2026-11-22 from the player's Labor screen: the Miners page ticks ONE dwarf (Aban,
-- "Proficient Miner") while my labor census reported 2 then 4 (`u.status.labors.MINE`). On this
-- build labors are driven by PROFESSION assignment (DF 50 work details): a labor write lands,
-- the census reads it, and the player's screen still shows the profession's roster. So asking
-- "how many miners do I have" has two answers on two layers, and the profession layer is the one
-- the game digs with. `dfhack.units.setProfession` is the game's own call.
function OPS.unit_profession(args)
  local action = (args and args.action and tostring(args.action):lower()) or "list"
  -- Measured 2026-11-22: `fortress_entity.professions` does not exist
  -- ("Cannot read field historical_entity.professions: not found"). The profession table is the
  -- RACE raws' list, and the name helper is dfhack.units.getProfessionName.
  -- `world.raws.dwarves` is not a field on this build either (measured: "Cannot read field
  -- world.T_raws.dwarves: not found"). The idiom that works is dfhack.units.getProfessionName,
  -- and for a profession NAME -> ID lookup the most honest source is the fort itself: take the
  -- id from a dwarf who already carries that profession (Aban shows as "Miner" in the player's
  -- Labor screen, so the id exists in the game's data - no raws guessing).
  local function prof_name(u)
    local n = "?"
    pcall(function() n = tostring(dfhack.units.getProfessionName(u)) end)
    if n == "?" or n == "" then
      pcall(function()
        local pid = tonumber(u.profession)
        if pid ~= nil and pid >= 0 and races and races.profession[pid] ~= nil then
          n = tostring(races.profession[pid].code)
        end
      end)
    end
    return n
  end
  local function prof_id_by_name(want)
    local w = tostring(want):upper():gsub(" ", "")
    for _,u in ipairs(df.global.world.units.active) do
      local pn = ""
      pcall(function() pn = tostring(dfhack.units.getProfessionName(u)) end)
      if pn ~= "" and pn:upper():gsub(" ", "") == w then
        local pid = tonumber(u.profession)
        if pid ~= nil then return pid end
      end
    end
    return nil
  end
  if action == "list" then
    local out = {}
    for _,u in ipairs(df.global.world.units.active) do
      local cit, alive = false, false
      pcall(function() cit = dfhack.units.isCitizen(u) end)
      pcall(function() alive = dfhack.units.isAlive(u) end)
      if cit and alive then
        local nm = "?"
        pcall(function() nm = tostring(u.name.first_name).." "..tostring(u.name.last_name) end)
        local pid = nil
        pcall(function() pid = tonumber(u.profession) end)
        out[#out+1] = { unit_id = tonumber(u.id), name = nm, profession = prof_name(u), profession_id = pid,
                        has_mine_labor = (function() local r=false; pcall(function() r=(u.status.labors.MINE==true) end); return r end)() }
      end
    end
    table.sort(out, function(a,b) return a.unit_id < b.unit_id end)
    local names, seen = {}, {}
    for _,u in ipairs(df.global.world.units.active) do
      local pn = ""
      pcall(function() pn = tostring(dfhack.units.getProfessionName(u)) end)
      local pid = tonumber(u.profession)
      if pn ~= "" and pid ~= nil and not seen[pid] then
        seen[pid] = true
        names[#names+1] = { id = pid, name = pn }
      end
    end
    table.sort(names, function(a,b) return tostring(a.name) < tostring(b.name) end)
    return { action = "list", citizens = out, professions = names }
  end
  if action == "set" then
    local uid = tonumber(args and args.unit_id)
    local u = (uid ~= nil) and df.unit.find(uid) or nil
    if u == nil then error("unit_id required and must name a living unit") end
    local pname = args and args.profession and tostring(args.profession) or nil
    if pname == nil and tonumber(args and args.profession_id) == nil then
      error("profession (name) or profession_id required; action='list' shows both, per citizen")
    end
    -- The name->id route can only see professions SOMEONE IN THE FORT carries: every lookup here
    -- reads the live roster, because the profession TABLE is unreachable from Lua on this build
    -- (measured: raws.professions, raws.dwarves, entity_raw.profession, world.professions,
    -- world.proficiencies, dfhack.units.getProfessionId, dfhack.units.setProfession and the RPC
    -- MapInfo are all absent). So restoring a profession that NO unit holds now - my own case:
    -- I made the fort's only Metalcrafter a Miner - has no tool route, and an explicit
    -- profession_id is accepted for exactly that case.
    local pid = tonumber(args and args.profession_id)
    if pid == nil and pname ~= nil then pid = prof_id_by_name(pname) end
    if pid == nil then
      local avail = {}
      for _,x in ipairs(names) do avail[#avail+1] = tostring(x.name) end
      avail[#avail+1] = "(only professions a living citizen currently carries are nameable here - pass profession_id for the rest)"
      error("profession '"..pname.."' not found for the fortress entity; available: "..table.concat(avail, ", "))
    end
    -- `dfhack.units.setProfession` is NOT on this build (measured: "attempt to call a nil value
    -- (field 'setProfession')"), and the RPC surface has no profession method (grep: no
    -- 'profession' in dfhack.py). The profession is a plain int field, and the id is taken from a
    -- unit that already carries the profession - so the write and the id are both measured data.
    local okp, errp = pcall(function() u.profession = pid end)
    if not okp then error("writing u.profession failed: "..tostring(errp)) end
    return { action = "set", unit_id = tonumber(u.id), profession = pname, profession_id = pid,
             read_back = prof_name(u), read_back_id = (function() local r; pcall(function() r = tonumber(u.profession) end); return r end)() }
  end
  error("unknown unit_profession action '"..tostring(action).."' (expected list or set)")
end

-- 16. work_details: THE LABOR MENU ITSELF - the green tick marks.
-- Found in the game's own scripts (autofish.lua, gui/manipulator.lua, gui/settings-manager.lua):
--   df.global.plotinfo.labor_info.work_details  ->  one entry per detail
--   entry fields (measured live): name, icon, flags, allowed_labors, assigned_units
--   assigned_units is a vector of UNIT IDS - that list IS the tick column, and Miners answered
--   2 entries the moment the player ticked two dwarves. Built-in details carry flags
--   `cannot_be_everybody` and `no_modify`, which is why the mode buttons behave per-detail.
-- This replaces the story my `/set-unit-labors` used to tell: it writes `unit.status.labors`,
-- the EFFECTIVE flags, and the detail recomputes them (measured: a census of 5 dropped to the 2
-- ticked units right after a tick).
function OPS.work_details(args)
  local action = (args and args.action and tostring(args.action):lower()) or "list"
  local li = df.global.plotinfo.labor_info
  local wd = li.work_details

  local function unit_name(uid)
    local u = df.unit.find(uid)
    if u == nil then return nil end
    local nm = "?"
    pcall(function() nm = tostring(u.name.first_name) end)
    return nm
  end
  local function detail_pair(sel)
    if sel == nil then return nil, nil end
    local n = tonumber(sel)
    if n ~= nil and wd[n] ~= nil then return wd[n], tonumber(n) end
    local want = tostring(sel):lower()
    for i = 0, #wd - 1 do
      local nm = ""
      pcall(function() nm = tostring(wd[i].name):lower() end)
      if nm == want then return wd[i], i end
    end
    return nil, nil
  end
  local function read_detail(d)
    local nm, icon = "?", nil
    pcall(function() nm = tostring(d.name) end)
    pcall(function() icon = tonumber(d.icon) end)
    local flags, labors, units = {}, {}, {}
    pcall(function() for k,v in pairs(d.flags) do if v == true then flags[#flags+1] = tostring(k) end end end)
    pcall(function() for k,v in pairs(d.allowed_labors) do if v == true then labors[#labors+1] = tostring(k) end end end)
    table.sort(flags); table.sort(labors)
    pcall(function()
      for i = 0, #d.assigned_units - 1 do
        local uid = tonumber(d.assigned_units[i])
        if uid ~= nil then units[#units+1] = { unit_id = uid, name = unit_name(uid) } end
      end
    end)
    return { name = nm, icon = icon, flags = flags, allowed_labors = labors,
             assigned_units = units, assigned_count = #units }
  end

  if action == "list" then
    local rows = {}
    for i = 0, #wd - 1 do
      local r = read_detail(wd[i])
      r.index = i
      rows[#rows+1] = r
    end
    return { action = "list", details = rows, count = #rows }
  end

  if action == "assign" then
    local d, idx = detail_pair(args.detail)
    if d == nil then
      local names = {}
      for i = 0, #wd - 1 do names[#names+1] = tostring(wd[i].name) end
      error("detail not found; pass `detail` as an index or a name. Available: "..table.concat(names, ", "))
    end
    local uid = tonumber(args.unit_id)
    if uid == nil then error("unit_id required") end
    local u = df.unit.find(uid)
    if u == nil then error("unit_id "..tostring(uid).." does not name a unit in this fort") end
    local mode = (args.mode and tostring(args.mode):lower()) or "add"
    local list = d.assigned_units
    local pos = nil
    for i = 0, #list - 1 do
      if tonumber(list[i]) == uid then pos = i end
    end
    local changed = "none"
    if mode == "add" and pos == nil then
      local okc, errc = pcall(function() list:insert(uid) end)
      if not okc then error("insert into assigned_units failed: "..tostring(errc)) end
      changed = "added"
    elseif mode == "remove" and pos ~= nil then
      local okc, errc = pcall(function() list:remove(pos) end)
      if not okc then error("remove from assigned_units failed: "..tostring(errc)) end
      changed = "removed"
    elseif mode == "add" and pos ~= nil then
      changed = "already-assigned"
    elseif mode == "remove" and pos == nil then
      changed = "not-assigned"
    end
    local r = read_detail(d)
    return { action = "assign", index = idx, detail = r.name, unit_id = uid, unit = unit_name(uid),
             change = changed, flags = r.flags, assigned_after = r.assigned_units,
             assigned_count_after = r.assigned_count,
             note = "assigned_units IS the tick column; the game recomputes unit.status.labors from the details, so read /unit-labors after this to see the flags land" }
  end

  if action == "set-labor" then
    local d, idx = detail_pair(args.detail)
    if d == nil then error("detail not found (index or name)") end
    local lab = args.labor and tostring(args.labor):upper() or nil
    if lab == nil then error("labor required (the RPC labor_list name, e.g. MINE, CUTWOOD)") end
    local val = (args.value == nil) and true or (args.value == true or args.value == "true")
    local okc, errc = pcall(function() d.allowed_labors[lab] = val end)
    if not okc then error("writing allowed_labors."..lab.." failed: "..tostring(errc)) end
    local r = read_detail(d)
    return { action = "set-labor", index = idx, detail = r.name, labor = lab, value = val,
             allowed_labors_after = r.allowed_labors, flags = r.flags }
  end

  error("unknown work_details action '"..tostring(action).."' (expected list, assign, set-labor)")
end

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
