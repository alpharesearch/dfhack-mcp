# deployables/scripts - DFHack lua scripts, deployed by copy

Anything here (including subdirectories, e.g. `gui/`) is copied to
`<gamedir>/hack/scripts/` by `./scripts/deploy.sh`.

## Shipped scripts

* [`dfhack_mcp.lua`](dfhack_mcp.lua) — the server-side RPC Lua module for `dfhack-mcp`.
  Exposes structured JSON operations (`game_paths`, `observe_status`, `building_status`,
  `configure_farmplot`, `assign_zone_owner`, `assign_pasture`, `manage_nobles`,
  `manage_work_orders`, `alerts_and_wellbeing`, `gather_and_items`) returning `MCP_JSON|{...}`
  lines. Deployed to `<gamedir>/hack/scripts/dfhack_mcp.lua` via `./scripts/deploy.sh deploy`,
  and also auto-bootstrapped into `_G.__dfhack_mcp` per connection generation by `luaops.py`.
