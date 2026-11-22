# deployables - files the MCP owns and ships into the game

Everything in this tree is **git-controlled and source-of-truth**. The game directory is a
copy target, never the authoring place. Nothing here requires host write access to create.

| Source (this repo)                    | Deploy target (game dir)             | What |
|---------------------------------------|--------------------------------------|------|
| `deployables/blueprints/*.csv`        | `<gamedir>/dfhack-config/blueprints/` | quickfort blueprints (the MCP writes these via `apply_blueprint`) |
| `deployables/scripts/**/*.lua`        | `<gamedir>/hack/scripts/`             | DFHack lua scripts, mirroring subdirectories (ships [`dfhack_mcp.lua`](scripts/dfhack_mcp.lua)) |

Deploy with:

```bash
./scripts/deploy.sh check     # report what differs (default)
./scripts/deploy.sh deploy    # copy repo -> game
DF_GAMEDIR=/path/to/df ./scripts/deploy.sh deploy
```

Why this direction: DFHack reads blueprints and scripts only from the game directory, so a
copy is unavoidable - but writing there needs host access. Keeping authoring in the repo
means the MCP server, its blueprint writes, and every generated file live under git, are
reviewable, and run with plain workspace permissions.
