import pathlib

p = pathlib.Path("main.py")
s = p.read_text()
anchor = '@app.get("/survey", operation_id="survey")'
assert anchor in s

add = '''@app.post("/configure-stockpile", operation_id="configure_stockpile")
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
        return await LO.configure_stockpile(
            dec, int(body["building_id"]), allow=body.get("allow"), deny=body.get("deny"),
            all_categories=bool(body.get("all_categories", False)),
            max_wheelbarrows=body.get("max_wheelbarrows"),
            max_barrels=body.get("max_barrels"), max_bins=body.get("max_bins"))
    except LO.LuaOpError as e:
        return {"success": False, "error": str(e)}


''' + anchor
p.write_text(s.replace(anchor, add, 1))
print("endpoint wired")
