# DF observer - detail

## What it does

The observer watches the live Dwarf Fortress game through the DF MCP server
(`http://127.0.0.1:8000`, read-only) and **pushes** state into your agent
sessions instead of making the agent poll tools:

- **Briefing** - injected (never waking) at the next admitted step when
  meaningful state changes: ladder rungs, the NEXT rung with keys/why and a
  coordinate suggestion, per-box dig progress by tile categories, to-dos with
  acceptance tests, alerts with evidence, pause state + attribution, and game
  health. Bounded by `briefing_max_tokens`.
- **Adaptive poll** - game running: `poll_running_ms` (5-15 s Config range);
  game paused: `poll_paused_ms` (60 s default). Transport mirrors
  `scripts/dfw.py`: exponential backoff, "game offline" announcements, an hour
  of tolerated game-off time, 4xx treated as the plugin's own bug.
- **To-do ledger** - seeded from the quickstart ladder **and** auto-created
  from ladder gaps (general pile excluding stone/wood, stone pile, wood pile,
  outdoor refuse pile, meeting area, farm plot, well, doors). Each to-do carries
  an acceptance test the plugin runs against live MCP reads; a status flip
  quotes the evidence. The model adds/closes tasks via `df_todo`.
- **Alerts** - water in monitored shaft columns, damp/hot designations, dig
  complete/stalled, pile health, crew/pick limits, game offline, new crash-log
  files (names/sizes only), save reload. Only `Config.alert_wakes` kinds wake
  the agent (`agent.followup()`); everything else rides the briefing. `df_alerts
  (action: ack)` silences repeats until the condition clears.
- **Pause attribution, not authority** - transitions of `get-pause-state` are
  reported with the source: plugin (a pause this plugin issued),
  game-auto-or-player, player-resume. Plugin-initiated pausing is
  Config-gated and **default OFF** (`pause_authority: off`); with
  `auto_resume: false` (default) the plugin never unpauses, and a plugin-made
  pause is never auto-resumed over a player-made pause. Hard guard: never
  pauses an idle fort (nothing designated, nothing moving).
- **Tools** - `df_status(scope)`, `df_todo(list|add|done|why)`,
  `df_alerts(list|ack|history)`.
- **Filter subagent (opt-in, `filter_enabled: false` default)** - a plugin-owned
  analyst session woken on significant ticks: it reads the FULL state payload in
  its own context (private `df_observe_state` tool) and answers one verdict line;
  play sessions then get a verdict-composed briefing (`filter_briefing_max_tokens`,
  default 260). The plugin keeps enforcing the wake whitelist, the evidence rules,
  and the read-only boundary over whatever the filter proposes; a silent filter
  falls back to the rule gate (recorded in the notes).
- **Panel** - the full GUI lives in the **right Sidebar** as its own tab
  (**Fort observer**), opened from the guide page or by the **⤢** button on the
  compact composer chip (the chip keeps `health · gen · tick · poll · pauseAuthority`
  and a `▸` quick expand). Sections: ladder bar + NEXT, monitored boxes, piles,
  to-dos **with titles and evidence**, alert feed with **Ack** buttons (the
  `/df-ack <id>` session command - the same words a human can type), the game's own
  reports quoted verbatim, the designation scan, and notes. Theme tokens only;
  light/dark come from the host. The page prints its **data provenance** -
  `panel data: ...` - so an empty section is never a mystery. Alert wakes also
  render as chat rows.

Everything the model sees is reconstructable from the session log: briefings and
alerts carry their complete post-change state as a payload line, and a session
projection folds that log (pure, synchronous) - plugin memory is a derived
cache. The standing reading rules live in one system-prompt section.

## Reading rules the payload enforces

- Progress is **positive tile state**: `to_dig = rock + hidden`,
  `dug = floor + stairs + ramp`, from `GET /survey` boxes; complete requires
  `to_dig == 0 AND dug > 0`. Painted glyphs, truncation, windows, filters, and
  designation counts are never treated as facts.
- Every number names its box and IN-GAME elevation (panel numbers; `z = elev + 129`).
- `NO DATA` is labelled as such; absence is never a fact.
- Know-how stays in the skills `df-dig` / `df-stockpile`; the briefing cites
  sections and carries state only.

## Configuration

Every tunable is in the plugin's Config (see the comments in the bundle's
`cordis.patch.yml`): poll intervals, monitored boxes, pile ids, shaft columns,
briefing budget and gating, alert wake whitelist, overload thresholds, stall
threshold, quiet switches (`briefing_mode`, `wake_enabled`), agent targeting,
`pause_authority` / `pause_on` / `auto_resume`, `game_dir` for the crash-log
watch, `tool_docs` / `rules_mode` (compact tool descriptions and rules save
~225 tokens per request), `water_growth_min`, `unwatched_min_tiles` (0 disables),
and the panel's cost dial `panel_full_briefing`. Your patch layer survives upgrades; overriding the row replaces the whole
config object, so copy the keys you change.

## Boundaries and limitations (verified against this build)

- **Read-only towards the game.** The plugin never designates, builds, sets
  labours, runs commands, sends keys, digs, uses blueprints, or writes game-dir
  files. The single write it can issue is `POST /set-pause-state`, default OFF.
- Composite reads come from the server's `GET /observe` endpoint
  (schema `df-observer/v1`, HTTP-only, not an MCP tool). A server without it
  answers 404 and the plugin reports that as **its own bug** (4xx), loudly.
- Auto-pause flags themselves are not readable over this Lua proxy
  (`df.global.world.options` is not exposed), so pause sources are
  **observed transitions, not predicted**: a plugin pause is attributed by the
  plugin's own record within `plugin_pause_attribution_ticks`; every other
  transition is reported as game-auto-or-player.
- Crash logs: file **name/size/mtime only**; contents are out of scope (deep
  forensics belong to a separate plugin).
- Pile contents are aggregated by item-type name at the honest granularity the
  probe provides (`items.other.IN_PLAY`), not per material stack.
- Reachability/stall explanations are the skills' job (`df-dig`); the observer
  reports the state pattern (pending drained with no dug change), not the cause.
- Subagents are not briefed by default (`brief_subagents: false`): they inherit
  the parent's log; the parent session carries the live state.
- The plugin targets live root agents in its own Host process; game state is
  global, briefing bookkeeping is per-agent.
- Panel/chat rows need the Web client; with the page open the panel appears
  above the composer. The panel shows the plugin's live state plus
  log-folded ledger entries for the current session.


- **Ledger is durable** (`storageDomain` unit `df_observer`, per-record:
  `~/.dsh/storages/df_observer/todos/<id>.json`), restored at activation with
  ids that never collide with the seeds.
- Supply in the header = **alcohol > meals > raw food** (the player's ranking),
  as fort-wide item counts from the deep probe; alerts fire the most important
  gap first, and stay silent (`supply ? (deep probe pending)`) before the probe.
- `food-on-piles` / `drink-on-piles` label what the numbers are (item counts on
  the watched piles). The game's own Food/Drink bars are fort-wide meal supply -
  `Food None` on the bar and `food-on-piles 3` are both true.
- **Header = the game's status bar, as far as this build allows**: dwarf count
  (`dwarves 21 (units 79)` - the unit count includes pets and livestock), date
  (`Y100 Win M9 d18`), seeds, picks. The site's *name* needs the language module
  (absent in the game's Lua env) and mood needs unit-thoughts; both are labelled
  as not probeable, never invented.

- Wakes steer (`agent.steer`): a busy agent consumes the alert at its next step,
  an idle one starts a turn. `followup` queues the next turn (the composer's
  pending item) - that is what the player had to press.
- `tile_state_lost` (whitelisted wake): a monitored box's `dug` count DECREASING
  raises it with both numbers, because positive tile state was removed.

## Verified platform facts (measured, this install)

- A third-party bundle CANNOT add its own `ctx.remote` namespace on the web
  client (the face is compiled from fixed imports). Panel data comes from the
  projection's `wire.view` via `useProjection`; UI actions run session commands
  through `remote.commands`.
- The static Client half has no stylesheet-insert builtin; styling is inline
  React styles over `--dsw-alias-*` tokens.
- `@Remote` markers (when a bundle does expose Remote endpoints) must be on the
  prototype before the service is constructed - the runtime has no decorator
  syntax, so they are applied at module load.
- Picks are `ITEM_WEAPON_PICK` **weapon** items (the TOOL group holds
  wheelbarrows/scrolls); the picks probe reads that.

- Reinstalling a linked bundle at the SAME version reuses the loaded module in a
  live profile: bump `version` when the code changes (measured 0.1.0 -> 0.1.1,
  and 0.1.21's new header labels arrived live after a bump + reinstall).

## Alert kinds and wake boundaries (current build)

| kind | raised when | wakes (steer) by default |
|---|---|---|
| `water` | a monitored shaft column shows water, **or** the monitored band's water tiles grow by `water_growth_min` (measured 1030 -> 1091 = the aquifer breach flooding) | yes |
| `damp_hot` | the game's designation warnings flag DAMP/HOT tiles (the elev22-24 case), or magma is under a monitored column | no |
| `dig_complete` | a watched box reaches `to_dig == 0 && dug > 0`, or a to-do's acceptance flips to done | yes |
| `dig_stalled` | a face is frozen for `stall_ticks` reads - drained (designations queued, drained, nothing dug) **or** idle (nothing queued, nothing dug) | yes |
| `tile_state_lost` | a box's `dug` count *decreases* (a dug floor/stair was destroyed) | yes |
| `crew` | supply by the player's ranking: alcohol 0 -> meals 0 -> raw food 0; or the game's own `cancels Eat/Drink`-style report | no |
| `game_says` | a noteworthy game report (attack, flood, out of food, caravan, migrant...) - evidence is the **verbatim quote** + elevation | no |
| `game_crashed` / `game_offline` | the crash log grows / the game stops answering | yes |
| `unwatched_designations` | a designation cluster of `>= unwatched_min_tiles` tiles sits outside every monitored box - work nothing measures - with a runnable `df_todo` watch recipe | no |

`alert_wakes` is Config; a wake fires once per alert lifetime (`wakeSent`) plus
`wake_min_spacing_ms`, and briefings always `inject` (never steer).

## What a tool answer tells you about freshness

`df_status` header: `tick N | running|PAUSED|GAME OFFLINE | gen G | data <age>s old`
(or `data NO READ YET`). A field with no reading is labelled `NO DATA`, never
inferred; an empty probe read cannot flip a to-do (`noData`).

## Diagnosing the game channel

`bash plugin/df-observer/deploy.sh channel` compares the plugin's own read path
(`GET /observe`, plain HTTP) with the MCP tools' path (`POST /mcp`): 404 there is
the measured dead session binding after a DFHack server restart (`-32600 Session
not found` in tool calls), which does **not** affect the observer's `df_*` tools
- they answer from the live model.

## What the briefing costs (measured on the live fort, 18-row ledger)

| knob / level | tokens | what the reader gets |
|---|---|---|
| `briefing_max_tokens: 700` (rule gate) | 674 | header, ladder, boxes, ledger with evidence, alerts + advice, GAME SAYS |
| same, cap 300 | 282 | header, ledger ids+states, lean alerts (contract preserved) |
| filtered wire cap 340/360 | 288 / 346 | **ids only** - titles (+50) do not fit; the filter's prose floor is ~275 |
| filtered wire cap **420** | 407 | **titles on every open row** (this profile's value) |
| filtered wire cap 460 | 437 | titles **and** the designation scan |
| `panel_full_briefing: true` | +622 | Ladder/Boxes/Piles/Notes/scan ride the wire for the panel |

Priorities the shrink ladders encode: the **ledger never drops below ids+states**
(the session log must reconstruct), **panel readability (titles) outranks filter
commentary**, sugar strips in reader order (scan first, titles last), and an
over-budget result is labelled `overBudget` rather than passed off as success.

## Verified platform facts (added by measurement, 0.1.66 - 0.1.77)

- `ctx.sidebarRightTabs.register` + the keyed seats `sidebar.right.pane.tab` and
  `sidebar.right.pane.tab.title` place a plugin page in the right Sidebar; optional
  Context faces must be reached through the **lazy** `ctx.inject([...], fn)` - a
  name in the static `inject` list that cannot resolve aborts the whole client row.
- `remote.commands.execute(sessionId, '/cmd')` appends the command row to the
  session, and **its promise does not settle for the client half**: plugin UI data
  must come from the projection wire, not from awaiting a command.
- The state field set lives in FOUR places (`stateSchema`, `init()`,
  `STATE_FIELDS`, `viewOf`); a field missing from any one of them is invisible to
  the panel. A test now walks all four for every field.
- Storage keys must match `/^[a-zA-Z0-9_-]+$/` - the first box key used `:`/`,`,
  so watched boxes never persisted (measured: `ledger restored: ... 0 watched
  boxes`).
- The ledger is ordered **open rows first**: a row-count slice on a done-first
  ledger silently dropped exactly the rows the panel lists.
