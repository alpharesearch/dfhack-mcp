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
- **Panel** - above the composer. It renders the session's *folded projection view*
  (the same log-derived state the model reads, published by the projection's
  `wire.view`); its **Ack** button runs the `/df-ack <id>` session command, the same
  words a human can type in the composer: game/session health line, ladder bar, NEXT,
  monitored boxes, to-dos with evidence, alert feed with Ack buttons, notes.
  Theme tokens only, light/dark from the host. Alert wakes also render as chat
  rows.

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
watch. Your patch layer survives upgrades; overriding the row replaces the whole
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
