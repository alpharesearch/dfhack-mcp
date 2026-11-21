# `df-observer` — DSH plugin plan (high level)

**One-sentence job:** watch the game through the DF MCP server and *tell the model what
matters, unprompted* — status, alerts, and the live to-do list with proof — so a local model
can play Dwarf Fortress without burning its context polling tools.

Status: **plan only.** Nothing in `plugin/df-observer/` is implemented or installed yet.

---

## 1. Why a plugin and not more tools

Playing the game is a long, asynchronous, feedback-driven task. Tools are *pull*: the model
must remember to ask, and every answer costs context. The last week of this project proved
that costs are what kill play:

- dig completion, designations drained by unreachability, water inflow, pick shortage,
  cave-in discipline, save reloads, game crashes — all detected by *repeated reads*,
- verification traps (painted map glyphs, stale caches, designation counts) that only matter
  because the model has to re-derive state from raw views every turn,
- watchers as background *bash jobs* with ad-hoc retry/backoff (`scripts/dfw.py`).

The plugin turns that into *push*: a standing observer that computes the fort's state and
injects a bounded briefing into the conversation, wakes the agent on real events, and keeps a
to-do ledger whose items carry executable acceptance tests. The know-how stays in the skills
(`df-dig`, `df-stockpile`); the plugin carries the *live state*.

## 2. DSH mechanisms this plan uses (verified against the installed docs)

| Need | Mechanism (from `dsh-doc`) |
|---|---|
| Plugin entry, tunables | `export function apply(ctx, config)` + `export const Config` (validates the row's config) |
| Per-agent behaviour, removed on disposal | register on `agent.ctx` inside an `agent/created` listener, wrapped in `agent.ctx.effect()`, disposer also kept keyed by agent in the plugin's own effect |
| Feed status to the model, no wake | `agent.inject()` — logged as `agent/inbox/spliced`, enters the next admitted step |
| Wake the agent on an alert | a timer that calls `agent.followup()`; timer cleared in the owning effect |
| Standing rules for reading the briefing | `ctx.systemPrompt.section()` |
| Deeper queries (pull, on demand) | `ctx.tools.register(...)` — tools: `df_status`, `df_todo`, `df_alerts` |
| Derived state, reconstructable from the log | `ctx.sessionProjections` unit (`apply(state, event)` pure, `view()` reference-stable, `stateVersion` bumped on semantic change); plugin memory is a derived cache |
| Human-facing panel | `ctx.slots.inject(ownerKey, () => ctx.slots.register(...))` with theme tokens `--dsh-alias-*` and copied markup (no host-primitive imports) |
| Alerts as chat rows | `ctx.uiConversation.events.register()` + view under the definition's `kind` in `conversation.chat.node` |
| Config for users | the plugin's `Config` schema, edited in `cordis.patch.yml`; user layer survives upgrades |
| Distribution | bundle: `package.json` with `dsh.bundle.patch`, `cordis.patch.yml` inserting the plugin row, `locale/*.json` `meta.title/description/detail`, `icon.svg` in `files` |

**Deliberately not used:** `appendPluginRecord()` (reserved for experimental packages,
migration best-effort), `system-prompt/assemble` replacement (too strong), new session event
`type`s (readers reject them), direct DOM writes, iframe UI.

## 3. Data sources (all reads, no game writes)

Poll the DF MCP server's HTTP endpoints — the same JSON the tools use, so the plugin needs no
MCP tool calls and costs the model nothing:

| Signal | Source |
|---|---|
| ladder rungs + facts (pop, food, drink, wood, stone, metal, miners, pending dig) | `GET /fort-progress` |
| per-level dig progress by **tile categories** | `GET /survey?elev&x0&y0&x1&y1` (`rock+hidden` = to-dig, `floor+stairs+ramp` = dug) |
| deltas (what changed since last read) | `GET /world-delta` |
| hazards on live designations (damp/hot) | `GET /warnings` |
| stockpile health (holes, filters, wheelbarrows, contents) | `GET /game-lua` probe (the pile probe from `skills/df-stockpile`) |
| water in a shaft column | `GET /tile-at` down the column (the `sink_shaft` rule: water = stop) |
| labour/pick capacity, crew size | `GET /labor-list`, unit probe (`it.subtype.name` for picks) |
| game/session health | TCP `:5000` open? `GET /get-status`; `crashlog/` new files; generation change (save reload) |

Transport rules (already proven in `scripts/dfw.py`): retry with exponential backoff, announce
"game offline", tolerate long sessions, 4xx = our bug, 5xx while `:5000` is down = the game is
closed. Elevations are **in-game panel numbers** everywhere (`z = elev + 129`).

## 4. What the plugin gives the model

### 4.1 The briefing (injected, delta-gated, bounded)

Injected with `agent.inject()` **only when the projection's meaningful state changes**
(thresholds in `Config`), always compact and always with evidence and coordinates:

```
FORT BRIEFING (in-game elevations) | year 1 Spring 4 | pop 46 | miners 5 of 3 picks | paused
LADDER: [x] general stockpile  [x] trade depot + wagon road  [ ] meeting area  [ ] farm plot
NEXT:   meeting area 5x5 at in-game 36, landing x41-45 y119-123 is the space; see df-dig §16b
TODOS:
  #12 dig room wings in-game 37  to-dig 558 | dug 4 | pending 89      (accept: to_dig==0 && dug>0)
  #13 dig room wings in-game 36  to-dig 603 | dug 201 | pending 57    (accept: to_dig==0 && dug>0)
  #14 stone pile hauling         1 boulder of 5 left on general pile  (accept: stone pile >0, general=0)
ALERTS:
  ! pick shortage: 3 picks for 5 designated miners -> 2 idle at dig (#12/#13 slower than ordered)
  ! in-game 33 shaft column: no water yet; aquifer band is 32-33 (df-dig §12) - wet-stop armed
```

Rules baked into the payload (the lessons we paid for):
- progress is **positive tile state**, never "no walls seen"; painted-glyph views are not used;
- every number carries its box/elevation and the window it came from;
- "no data" is labelled as such — never read as a fact;
- each to-do carries its acceptance test so the model can verify instead of assume;
- each next-action points at the skill section that explains how.

### 4.2 Alerts (wake the agent)

`agent.followup()` only for alert classes in `Config.alert_wakes` (default set from real
events in this project):

| Alert | Trigger |
|---|---|
| `water` | water appears in the monitored shaft column |
| `damp_hot` | `warnings` reports damp/hot tiles among *live* designations |
| `dig_complete` / `dig_stalled` | per to-do acceptance flips; or designations drained with no dug change |
| `stair_incomplete` | stair column has a level with no stair and the descent is marked unfinished |
| `pile_full` / `pile_holes` / `pile_none_accepting` | stockpile health probes |
| `crew` | picks < designated miners; hauling backlog above threshold; food/drink at zero |
| `game_offline` / `game_crashed` | `:5000` closed; new file in `crashlog/` (with the stack signature summary) |
| `save_reloaded` | generation change with dug-state differences (what survived, what is missing) |

Every alert carries: what changed, the evidence, the coordinates, the suggested action, and a
`df_alerts(ack=…)` to silence repeats.

### 4.3 The to-do ledger

- Seed = the quickstart ladder from `/fort-progress`, in order, each with a rung predicate.
- The model adds tasks with `df_todo.add(title, accept, box)`; the plugin owns verification:
  each acceptance test is an MCP read the plugin runs on its poll cycle, and status flips are
  reported **with the evidence** in the next briefing.
- Ledger lives in the session log as tool results and injected briefings (reconstructable
  after fork/resume); the plugin's copy is a cache.

### 4.4 Tools (pull, for depth)

`df_status(scope)` — the current projection, bounded; `df_todo(list|add|done|why)` —
ledger + the evidence trail of a status flip; `df_alerts(list|ack|history)`. Tool
descriptions follow `tool-design.md`: behaviour not implementation, parameter rules on the
parameter, bounded output with ids to fetch more, no duplication of the briefing rules.

### 4.5 Human-facing UI (phase 2)

A slot panel: ladder progress bar, to-do list with evidence, alert feed, and the game/session
health line (offline/crash/reload). Registered through `ctx.slots.inject` +
`ctx.slots.register`, theme tokens only, own controls. Alert rows in Chat via
`ctx.uiConversation.events.register()`.

## 5. Boundaries

- **Read-only towards the game.** The one write the plan contemplates - pausing - is demoted to
  **optional and default OFF** (§5a), because the game self-pauses. The plugin
  never designates, builds, sets labours, runs commands, sends keys, digs, blueprints, or writes
  game-dir files. All in-game action stays agent-driven, so the player's rules (no dig-now, no
  blueprints for play, designate-in-fog, stop-when-wet) stay enforceable by the model's choices
  and the player's authority, not hidden plugin side effects.
- **No new session event types**, no `appendPluginRecord`; everything the model sees must be
  reconstructable from committed session events.
- **Config-driven, no hard-coded numbers**: poll interval, delta thresholds, alert whitelist,
  monitored shaft columns, pile watch list, briefing token budget, quiet modes
  (`briefing: on/off`, `wake: on/off`) for when the player wants the agent to stay quiet.
- **Skills stay the know-how**: the briefing cites `df-dig`/`df-stockpile` sections; the plugin
  carries state, not doctrine.

## 6. Package layout (this repo: `plugin/df-observer/`)

```
plugin/df-observer/
  package.json          # name @local/df-observer, dsh.bundle.patch, exports, files
  cordis.patch.yml      # insert: id df-observer, name @local/df-observer, config { ... }
  index.js              # apply(ctx, config) + Config; wires effects, timers, tools
  observer.js           # poller + transport (backoff, offline grace), endpoint map
  model.js              # FortState: rungs, todos, alerts; delta gates; stateVersion
  brief.js              # the bounded briefing text builder (in-game elevations)
  client.js             # phase 2: slot panel + chat rows
  locale/en.json        # meta.title/description/detail + UI labels
  locale/zh.json        # same fields (user languages)
  docs/DETAIL.md        # capability, usage, verified limitations
  icon.svg              # original icon, <=256 KiB
```

Install path: author files → syntax check → `plugin_manager install_bundle` with the absolute
directory (approval-gated) → confirm the row in **Plugins**, verify in a **new** session
(`application: applied`), then the play test. `list_plugins` first for exact ids. Replacing an
installed package needs a restart; new bundles can activate via HMR.

## 7. Phases

| Phase | Deliverable | Acceptance test |
|---|---|---|
| **P0** | poller + projection + `agent.inject()` briefing + `df_status` + `Config`; **adaptive interval** (paused 60 s, running 5-15 s) | briefing appears in the next admitted step, bounded to `briefing_max_tokens`, numbers match a manual `/survey` read; poll rate visibly follows the pause state |
| **P1** | to-do ledger (ladder seed + auto-created basics) with plugin-run acceptance tests, alerts + `agent.followup()` wakes, **pause attribution** (§5a; plugin-initiated pausing default OFF) + overload signalling | during a real dig the agent is woken by `dig_complete` and `water` without calling any tool; status flips quote evidence; a plugin pause names its cause and is never auto-resumed over a player pause |
| **P2** | slot panel (ladder/todos/alerts/health) + chat alert rows + `df_alerts(ack)` - **shipped in the first install** | human sees ladder/todos/alerts live; ack silences repeats; theme/light-dark consistent |
| **P3** | game-health watcher (offline, **crash-log name/size only**, pause-source attribution, save-reload diff) + hauling/pick monitoring + pile health | after a crash the briefing reports the new crash log and the offline span; after a reload it lists what survived and re-issues lost designations as to-dos. Stack forensics live in a separate plugin, later |
| **P4** | tuning: delta gating measured, token cost per turn measured (first-turn prompt tokens before/after), Config examples | a 1-hour play session with briefing-only context stays inside the budget; tool calls drop |

## 8. Success criterion (the game test, not the plugin test)

With the plugin active and **no watcher jobs**, on a fresh embark the agent gets:
*"dig a 3-wide tunnel 15 long with mountain on top, a 6×6 room, a 2×2 stairwell down, and
stockpiles"* → it orders designations, assigns labour, waits, and is **told** (not asked):
progress, stall, water, completion, pile fills, crash/reload — and finishes the job with
tile-proved evidence. Measured: tokens spent per hour of game time, tool calls avoided, and
zero false "complete" claims (the projection can only say complete from positive tile state).

## 9. Decisions (player, 2026-11-21)

1. **Wake policy:** start with `water`, `dig_complete`, `crash` (game offline/crash log). More
   wakes will be added only with evidence from play. **Plugin pausing is demoted to optional,
   default OFF** (§5a): the game self-pauses on the events that matter, so the plugin's job is
   pause **attribution** (game vs player vs plugin) plus the model-side cases the game will not
   cover (overload, mid-plan completion, save-reload).
2. **Interval: adaptive** - game **paused: 60 s**; game **running: 5-15 s** (Config range). If
   the alert/to-do backlog grows past `overload_*` thresholds, the plugin (a) tells the model to
   work the queue in order and (b) may pause the game so nothing changes underneath it while the
   model catches up. Auto-resume only after the queue is drained, and only if the plugin - not
   the player - did the pausing.
3. **To-dos:** the plugin seeds the quickstart ladder **and** auto-creates to-dos from ladder
   gaps, so the basics always exist as verifiable tasks (no stone pile, no meeting area, no
   refuse pile outdoors, no wood stockpile near the entrance, no well/still, no doors on ordered
   openings). Auto-created to-dos are labelled as such, carry their acceptance test, and are the
   model's to-do list - it can reorder, merge, or close them with evidence.
4. **UI panel from the first install** - P2 moves into the initial version (headless-only is not
   the plan).
5. **Crash-log contents are out of scope for this plugin.** The observer reports only "a new
   crash log appeared" from file name/size signals; deep crash forensics (reading `crashlog/`
   stacks, correlating with game state) belongs to a **separate plugin later**.

### 5a. Pause authority - **demoted to optional, default OFF** (the game self-pauses)

The player's note: **Dwarf Fortress 53 auto-pauses on the events that matter** (built-in
auto-pause settings; the DFHack `dwarfmonitor` plugin's alert layer is configured in
`dfhack-config/dwarfmonitor.json`, currently defaults). So a plugin pause is largely
redundant, and the game's own pause is the authority the player already knows.

What remains for the plugin:
- **Detect and attribute pauses.** Watch transitions of `GET /get-pause-state` and say who
  paused it: the game (auto-pause fired), the player, or the plugin. Attribution matters
  because the plugin must never auto-resume a pause it did not make.
  Measured limitation: the auto-pause flags themselves are not readable from this Lua proxy
  (`df.global.world.options` is not exposed), so transitions are observed, not predicted.
- **Pause for the model's benefit only** - the cases the game will not pause for: alert/to-do
  **overload** (so state stops changing while the model works the queue), a to-do flipping to
  `dig_complete` mid-plan, and save-reload detection. Default `pause_authority = off`;
  `events` is opt-in per event in `Config.pause_on`.
- Keep the hard guard: never pause when `pending_designations == 0` and nothing is moving,
  and `Config.auto_resume = false` (default) means the plugin pauses only and never unpauses.

- Allowed write: `POST /set-pause-state` only. **No** designations, buildings, labours,
  `run-command`, `pass-keyboard-event`, `dig-now`, blueprints, game-dir writes.
- Config-gated per event: `pause_on = ["water", "overload", "crash_risk", "cave_in_window"]`,
  `pause_authority = off|events|manual_suggestion` (default `events`).
- Every pause is announced in the briefing with the **evidence and coordinates** that caused it,
  and the plugin tracks "I paused it" so it never auto-resumes a pause the player made.
- Auto-resume rules: only after the to-do/alert queue is drained, only for plugin-made pauses,
  and `Config.auto_resume = false` disables it entirely (player decides).
- Hard guard: the plugin will not pause while `pending_designations == 0` and nothing is moving -
  pausing an idle fort is the player's business, not the plugin's.
