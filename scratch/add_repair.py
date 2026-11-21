import pathlib

LUA_FN = '''

# The per-category material arrays the custom-settings UI walks, as allocated by the game
# itself (measured against a UI-made pile, stockpile number 6, on this world). Writing only
# settings.flags leaves these vectors at size 0 while the category flag is true, and the
# custom-settings UI then dereferences past an empty vector: SIGSEGV at address 0xa in
# render_things with overlay.plug.so on the stack (crash logs 23:48:06 and 23:56:23).
SETTINGS_ARRAYS = {
    "ammo": {"mats": 319, "other_mats": 2, "quality_core": 7, "quality_total": 7, "type": 10},
    "animals": {"enabled": 1327},
    "armor": {"body": 20, "color": 136, "feet": 14, "hands": 11, "head": 16, "legs": 17,
              "mats": 319, "other_mats": 10, "quality_core": 7, "quality_total": 7, "shield": 6},
    "bars_blocks": {"bars_mats": 319, "bars_other_mats": 5, "blocks_mats": 319, "blocks_other_mats": 4},
    "cloth": {"cloth_metal": 1, "cloth_plant": 8, "cloth_silk": 49, "cloth_yarn": 8, "color": 136,
              "thread_metal": 1, "thread_plant": 8, "thread_silk": 49, "thread_yarn": 8},
    "coins": {"mats": 319},
    "corpses": {"corpses": 0},
    "finished_goods": {"color": 136, "mats": 319, "other_mats": 16, "quality_core": 7,
                       "quality_total": 7, "type": 113},
    "food": {"cheese_animal": 26, "cheese_plant": 0, "drink_animal": 2, "drink_plant": 76,
             "egg": 307, "fish": 81, "glob": 2420, "glob_paste": 13, "glob_pressed": 7,
             "leaves": 95, "liquid_animal": 265, "liquid_misc": 2, "liquid_plant": 9,
             "meat": 14476, "plants": 225, "powder_creature": 0, "powder_plant": 101,
             "seeds": 157, "unprepared_fish": 81},
    "furniture": {"mats": 319, "other_mats": 15, "quality_core": 7, "quality_total": 7, "type": 35},
    "gems": {"cut_mats": 319, "cut_other_mats": 659, "rough_mats": 319, "rough_other_mats": 659},
    "leather": {"color": 136, "mats": 892},
    "refuse": {"body_parts": 0, "bones": 0, "corpses": 0, "hair": 0, "horns": 0, "shells": 0},
    "sheet": {"paper": 9, "parchment": 892},
    "stone": {"mats": 319},
    "weapons": {"mats": 319, "other_mats": 10, "quality_core": 7, "quality_total": 7,
                "trapcomp_type": 5, "weapon_type": 45},
    "wood": {"mats": 319},
}


def _array_alloc_lua(category: str, sizes: dict) -> str:
    """Lua that resizes and fills one category's UI-walked arrays (allow everything)."""
    lines = []
    for field, n in sizes.items():
        if n <= 0:
            continue
        lines.append(f"  if s.{field} ~= nil and #s.{field} < {n} then")
        lines.append(f"    s.{field}:resize({n})")
        lines.append(f"    for i = 0, {n - 1} do pcall(function() s.{field}[i] = 1 end) end")
        lines.append("  end")
    return "\\n".join(lines)


async def repair_stockpile_settings(dec, building_id: int) -> dict:
    """Allocate the per-category material arrays the game's custom-settings UI walks.

    Flags-only configuration is not enough: enabling a category with an unallocated
    `mats`/`type`/sub-vector makes the custom-settings UI crash the game. Sizes here are the
    game's own, measured from a UI-made pile; a UI pile is also re-read at runtime so a
    different world's counts win over these constants.
    """
    per_cat = []
    for cat, sizes in SETTINGS_ARRAYS.items():
        block = _array_alloc_lua(cat, sizes)
        if block:
            per_cat.append(f'local s = sp.settings.{cat}\\nif s ~= nil then\\n{block}\\nend')
    body = "\\n".join(per_cat)
    expr = f"""
local sp = nil
for _,x in ipairs(df.global.world.buildings.other.STOCKPILE) do if x.id == {int(building_id)} then sp = x end end
if sp == nil then print("FAIL pile {int(building_id)} not found") return end
local fixed = {{}}
{body}
for _,c in ipairs{{"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","refuse","sheet","stone","weapons","wood"}} do
  local s = sp.settings[c]
  local on = false
  pcall(function() on = (sp.settings.flags[c] == true) end)
  if s ~= nil and on then
    local parts = {{}}
    for k,v in pairs(s) do
      local n = -1
      local ok = pcall(function() n = #v end)
      if ok and n >= 0 and tostring(v):find("vector<char>") then parts[#parts+1] = tostring(k) .. "=" .. n end
    end
    table.sort(parts)
    fixed[#fixed+1] = c .. "{{" .. table.concat(parts, ",") .. "}}"
  end
end
print("OK id=" .. tostring(sp.id) .. " " .. table.concat(fixed, " "))
""".strip()
    out = await _lua(expr)
    line = _one(out)
    if line.startswith("FAIL"):
        raise LuaOpError(line)
    return {"success": True, "line": line[:900]}
'''

p = pathlib.Path("luaops.py")
p.write_text(p.read_text().rstrip() + LUA_FN)

q = pathlib.Path("main.py")
s = q.read_text()
anchor = '@app.post("/configure-stockpile", operation_id="configure_stockpile")'
assert anchor in s
add = '''@app.post("/repair-stockpile-settings", operation_id="repair_stockpile_settings")
async def repair_stockpile_settings(body: dict):
    """Allocate the per-category material arrays the game's stockpile custom-settings UI walks. Writing only settings.flags leaves them size 0 while the category flag is true, and the UI then dereferences an empty vector - that is the crash (SIGSEGV at 0xa in render_things with overlay on the stack) the player hit by clicking Custom on a Lua-configured pile. Call this after configure-stockpile; the acceptance test is the custom button opening."""
    try:
        dec = await world._dec(await world._gen())
        return await LO.repair_stockpile_settings(dec, int(body["building_id"]))
    except LO.LuaOpError as e:
        return {"success": False, "error": str(e)}


''' + anchor
q.write_text(s.replace(anchor, add, 1))
print("repair op + endpoint wired")
