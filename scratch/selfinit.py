import pathlib

p = pathlib.Path("luaops.py")
s = p.read_text()

# --- 1) configure_stockpile: allocate the UI's arrays in the SAME pass as the flags ---
a = '''    writes = "\\n".join(lines)'''
assert a in s, "writes anchor"
add_alloc = '''    writes = "\\n".join(lines)
    # Flags alone are NOT a stockpile: the game's custom-settings UI walks per-category
    # material arrays that the UI allocates when a category is switched on. Setting a flag on
    # top of a size-0 vector crashes the game when the player clicks Custom (measured:
    # SIGSEGV at 0xa in render_things with overlay on the stack). So every category we switch
    # on is initialised here, in the same pass - the repair tool is a rescue for piles built
    # before this existed, never part of normal use.
    alloc_lines = []
    for c in CATEGORIES:
        on = (c in wanted and c not in denied)
        sizes = SETTINGS_ARRAYS.get(c, {})
        if not on or not sizes:
            continue
        for field, n in sizes.items():
            if n <= 0:
                continue
            alloc_lines.append(f"  if s.{c}.{field} ~= nil and #s.{c}.{field} < {n} then")
            alloc_lines.append(f"    s.{c}.{field}:resize({n})")
            alloc_lines.append(f"    for i = 0, {n - 1} do pcall(function() s.{c}.{field}[i] = 1 end) end")
            alloc_lines.append("  end")
    writes = writes + "\\n" + "\\n".join(alloc_lines)'''
s = s.replace(a, add_alloc, 1)

# configure's read-back: report array sizes so a bad pile is visible in the response
a2 = '''print("OK id=" .. tostring(b.id) .. " | after: " .. table.concat(after, " "))'''
assert a2 in s
b2 = '''local arrays = {}
for _,c in ipairs{"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","refuse","sheet","stone","weapons","wood"} do
  if s.flags[c] == true then
    local cat = b.settings[c]
    if cat ~= nil then
      local parts = {}
      for k,v in pairs(cat) do
        local n = -1
        local ok = pcall(function() n = #v end)
        if ok and n >= 0 and tostring(v):find("vector<char>") then parts[#parts+1] = tostring(k) .. "=" .. n end
      end
      table.sort(parts)
      if #parts > 0 then arrays[#arrays+1] = c .. "{" .. table.concat(parts, ",") .. "}" end
    end
  end
end
print("OK id=" .. tostring(b.id) .. " | after: " .. table.concat(after, " ") .. " | arrays: " .. table.concat(arrays, " "))'''
s = s.replace(a2, b2, 1)

# --- 2) create_stockpile: take the filters/limits and initialise in the same call ---
tail = '''

async def make_stockpile(dec, x: int, y: int, elev: int, w: int, h: int,
                         allow: list[str] | None = None, deny: list[str] | None = None,
                         all_categories: bool = False, max_wheelbarrows: int | None = None,
                         max_barrels: int | None = None, max_bins: int | None = None) -> dict:
    """Create a multi-tile stockpile AND set it up correctly, in one call.

    Order: allocInstance -> setSize -> constructAbstract, then the category flags plus the
    material arrays the custom-settings UI walks, then container limits - all before we
    report. There is no follow-up step to forget: a pile made here is openable in the UI.
    """
    built = await create_stockpile(dec, x, y, elev, w, h)
    bid = int(str(built.get("line", "")).split("id=")[1].split()[0])
    cfg = await configure_stockpile(dec, bid, allow=allow, deny=deny,
                                   all_categories=all_categories,
                                   max_wheelbarrows=max_wheelbarrows,
                                   max_barrels=max_barrels, max_bins=max_bins)
    return {"success": True, "building_id": bid, "built": built.get("line"),
            "configured": cfg.get("line")}
'''
s = s.rstrip() + tail
p.write_text(s)
print("configure now initialises the UI arrays; make_stockpile is the one-call path")

# --- 3) endpoint for the one-call path ---
q = pathlib.Path("main.py")
t = q.read_text()
anchor = '@app.post("/create-stockpile", operation_id="create_stockpile")'
assert anchor in t
add = '''@app.post("/make-stockpile", operation_id="make_stockpile")
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


''' + anchor
q.write_text(t.replace(anchor, add, 1))
print("/make-stockpile wired")
