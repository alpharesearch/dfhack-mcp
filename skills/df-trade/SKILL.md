---
name: df-trade
description: Autonomous caravan trading in Dwarf Fortress - Trade Depot placement with 3-wide wagon access, Broker appointment and summoning, marking/hauling trade goods, opening barter safely, filtering merchant stock, selecting goods, confirming trades, and closing the UI cleanly.
whenToUse: Use when a caravan arrives (`caravan_present`), when building or verifying a Trade Depot (`accessible: false`), when appointing or summoning a Broker, when hauling goods to the depot, or when executing a barter transaction end-to-end.
metadata:
  source: measured live on DF 53.17-r1 / dfhack-mcp (33-tool MCP surface)
  tools: trade_depot, place_building, manage_nobles, unit_profile, alerts_and_wellbeing, building_status, survey, fort_plan, set_pause_state
---

# Caravan Trading & Autonomous Barter

Trading fails when a depot has no 3-wide wagon path, when goods are not hauled before the merchants leave, or when the Broker never talks to the merchant. Follow this sequence and verify each state transition from live reads.

## 1. Depot Placement & 3-Wide Wagon Access

- **Size & placement**: Place `Trade Depot` as a `5x5` building (`place_building(building="TradeDepot", w=5, h=5)`) on the surface or a ramp-accessible terrace (`fort_plan(action="find_site", w=5, h=5)`).
- **Wagon path rule**: Wagons require a **3-wide clear path of floors and ramps** to the map edge. **Wagons cannot climb stairs** and cannot turn in 2-wide corridors.
- **Check accessibility**: Always run `trade_depot(action="status")`:
  - If `accessible: false`, inspect `wagon_access_check` (`reaches_map_edge`, `wagon_reachable_tiles`) and `accessibility_note` to locate the blockage.
  - Note: a single-tile or 2-wide adit still lets dwarves haul goods to a depot (`trade_state_name: "AtDepot"`), but wagons will not reach the depot itself without 3-wide access.

## 2. Broker Appointment & `anyone_can_trade`

- **Pick a skilled Broker**: Check `unit_profile(action="skills", skill="APPRAIS")` or `manage_nobles(action="candidates", position="BROKER")` (looks at `APPRAISAL`, `NEGOTIATION`, `PERSUASION`), then appoint via `manage_nobles`.
- **Summon when a caravan is present**: When `alerts_and_wellbeing` reports `caravan_present` (or `trade_depot(action="status")` shows `caravans_present > 0` / `AtDepot`):
  - Call `trade_depot(action="request_broker", trader_requested=True)`.
  - If the appointed Broker is wounded, busy, or rejected by the depot, pass `anyone_can_trade=True` so any citizen can act as trader.

## 3. Marking & Hauling Trade Goods

- Queue hauling with `trade_depot(action="mark_goods", min_value=15, max_items=25)` (or `item_filter=...`, `item_ids=[...]`).
- **Filter matching rule**: `item_filter` matches **item description or type substrings**:
  - `"rough"` matches rough gems (`"rough carnelians"`); `"gem"` matches nothing because rough gems have type `ROUGH`.
  - Bins of finished goods/crafts haul many items in one trip—prefer crafts, mugs, goblets, rough gems, and bins.
- **Verify hauling**: Never assume `mark_goods` moved items immediately. Confirm `goods_at_depot_count` rising and `pending_haul_count` draining on `trade_depot(action="status")` while unpaused.
- Cancel unwanted hauls or unload depot items with `trade_depot(action="unmark_goods", ...)`.

## 4. Autonomous Barter Cycle

Run the full barter cycle through `trade_depot`:

1. **Open the Trade screen**:
   - Call `trade_depot(action="open_trade", max_items=200)`.
   - Verify `trade_screen_open: true`, `have_talker: true`, and `still_unloading: false` before selecting goods.
   - **NEVER write `main_interface.trade.open = true` directly from raw Lua**—doing so on a populated trade state crashes Dwarf Fortress. `open_trade` safely clicks an unoccupied depot tile and clicks the in-game `Trade` button.
2. **Browse & filter merchant stock**:
   - Use `trade_depot(action="barter_status", item_filter="...", max_items=200)` to search for specific needs (e.g. `"barrel"`, `"plant"`, `"drink"`, `"cloth"`, `"leather"`, `"bar"`, `"anvil"`).
   - Check `shown_items` vs `total_items` on `merchant_side` and `fortress_side` so you never mistake a filtered/capped slice for empty stock.
3. **Select goods on both sides**:
   - Call `trade_depot(action="barter_select", side="merchant", item_ids=[...], selected=True)` for items to buy.
   - Call `trade_depot(action="barter_select", side="fortress", item_ids=[...], selected=True)` for goods to offer.
   - **Profit & ethics rules**:
     - Offer fortress goods worth **1.3x–1.5x** the merchant's selected value so the trader accepts on the first offer.
     - **Elven caravans**: never offer wooden items, barrels/bins made of wood, or animal-derived goods—elves will refuse trade and leave offended.
4. **Confirm & close cleanly**:
   - Call `trade_depot(action="barter_confirm")`. This verifies `fortress_selected > 0`, clicks `Trade`, accepts any DFHack confirmation dialog, and returns the post-trade `barter_status` (`after`).
   - Always finish with `trade_depot(action="close_trade")` so no trade modal or building sheet remains open on screen.
