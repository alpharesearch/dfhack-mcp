import pathlib

LUA = '''

CATEGORIES = ["ammo", "animals", "armor", "bars_blocks", "cloth", "coins", "corpses",
              "finished_goods", "food", "furniture", "gems", "leather", "misc", "refuse",
              "sheet", "stone", "weapons", "wood"]


async def configure_stockpile(dec, building_id: int, allow: list[str] | None = None,
                              deny: list[str] | None = None, all_categories: bool = False,
                              max_wheelbarrows: int | None = None,
                              max_barrels: int | None = None,
                              max_bins: int | None = None) -> dict:
    """Set a stockpile's accepted categories by writing the game's own flags.

    Measured on this build (53.17-r1): building_stockpilest exposes `settings.flags`
    (stockpile_group_set) with exactly the wiki's category names, and the per-category
    structs carry only `mats` - so the flags ARE the preset. All flags true = takes
    everything; one flag = that preset (a wood pile = wood only); all false = "None".
    """
    cats = [str(c).lower().strip() for c in (allow or [])]
    denied = [str(c).lower().strip() for c in (deny or [])]
    for c in cats + denied:
        if c not in CATEGORIES:
            raise LuaOpError("unknown category '%s'; the game's names are: %s"
                             % (c, ", ".join(CATEGORIES)))
    wanted = CATEGORIES if all_categories else cats
    lines = []
    for c in CATEGORIES:
        on = (c in wanted and c not in denied)
        lines.append("  s.flags.%s = %s" % (c, "true" if on else "false"))
    writes = "\\n".join(lines)
    extras = ""
    if max_wheelbarrows is not None:
        extras += "b.max_wheelbarrows = %d\\n" % int(max_wheelbarrows)
    if max_barrels is not None:
        extras += "b.max_barrels = %d\\n" % int(max_barrels)
    if max_bins is not None:
        extras += "b.max_bins = %d\\n" % int(max_bins)
    catlist = '", "'.join(CATEGORIES)
    expr = """
local b = nil
for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do
  if sp.id == __ID__ then b = sp end
end
if b == nil then print("FAIL stockpile __ID__ not found") return end
local s = b.settings
local before = {}
for _,c in ipairs{"__CATS__"} do before[#before+1] = c .. "=" .. tostring(s.flags[c]) end
__WRITES__
__EXTRAS__local after = {}
for _,c in ipairs{"__CATS__"} do after[#after+1] = c .. "=" .. tostring(s.flags[c]) end
print("OK id=" .. tostring(b.id) .. " | after: " .. table.concat(after, " "))
""".strip().replace("__ID__", str(int(building_id))).replace("__CATS__", catlist) \
        .replace("__WRITES__", writes).replace("__EXTRAS__", extras)
    out = await _lua(expr)
    line = _one(out)
    if line.startswith("FAIL"):
        raise LuaOpError(line)
    return {"success": True, "line": line, "allow": wanted, "deny": denied}
'''

p = pathlib.Path("luaops.py")
s = p.read_text().rstrip() + LUA
p.write_text(s)
print("configure_stockpile appended")
