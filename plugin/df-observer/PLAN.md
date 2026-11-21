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


---

## 10. Implementation notes (forced changes, v0.1.0)

Two plan-level additions were required to build P0+P1+panel in one install:

1. **New MCP read (player-authorized 2026-11-21): `GET /observe`** in `main.py`,
   schema `df-observer/v1`, HTTP-only (excluded from the MCP tool list with
   `game_lua`). One request per poll tick answers: pause, generation, facts,
   structured ladder rungs + NEXT, per-box survey simplified to
   `to_dig/dug/pending` (tile categories), shaft columns (aquifer/water/magma
   elevs from the cached hazard map), the batched pile probe, warnings, report
   tails, and crash-log **file name/size/mtime only**. Every "no data" is a
   labelled `notes` entry. Measured: full tick payload ~6.7 KB (~1,700 tokens)
   and never enters the model's context - the plugin parses it.
2. **Budget strategy for the injected state line** (PLAN §4.1 + §2 log-truth):
   the briefing body and the `[df-observer] {...}` payload are budgeted
   *together*: the builder prefers the richest payload level that fits
   (`briefing_max_tokens`), trimming the human body only when no payload level
   fits, with a hard floor (header + minimal payload, `overBudget` flagged).
   Payload shrink ladder level 0 = complete state; levels 1-7 drop decoration
   sections, then detail. The projection folds any payload (merge-on-present),
   so the log always carries the ledger state - todos, alerts, dig events - and
   briefings at Config 700 tokens measure ~600-950 in tests.

Measured pile-probe fields (53.17-r1): accepted categories live in
`b.settings.flags[<category name>]`; `b.flags` is `building_flags` (no
`active`, no numeric indices in range); container limits in
`b.storage.max_barrels/max_bins/max_wheelbarrows`; elevation `b.z - 129`.


## 11. Filter subagent (player proposal 2026-11-21, implemented as opt-in)

The token limits are starting points, not verdicts: the rule-gate (model.js)
decides significance from numeric deltas and cannot notice patterns (stalled
shafts + pick exhaustion + a flood arriving behind a quiet ladder) the way a
model can. So the plugin can own a **filter agent**: a persistent session
spawned by the plugin (`sessionController.create`, Config `filter_enabled`,
default OFF) that:

- is woken on significant ticks with a one-line instruction (its own context
  absorbs the cost - not the play sessions');
- reads the FULL observation payload through a private tool `df_observe_state`
  (registered only for that agent);
- answers with exactly one verdict line `[df-verdict] {tick, significance,
  headline, changes[], next, wake[], notes[]}`;
- the plugin then composes the play-session briefing FROM the verdict
  (`filter_briefing_max_tokens`, default 260) and appends the foldable state
  line (shrink ladder, ledger always present).

Boundaries stay plugin-enforced: `wake[].kind` outside `Config.alert_wakes` is
demoted to briefing text; the filter has no game tools; verdict older than
`filter_timeout_ms` -> the rule-gate briefing resumes (recorded in notes).
Measured A/B on the live fort (2026-11-21, `scratch/measure_brief_ab.mjs`, full
payload 2.7k tokens):

| path | tokens/injection | note |
|---|---|---|
| rule briefing @ `briefing_max_tokens=700` | **642** | payload folded at the leanest level |
| filtered @ 260 | **174** | body squeezed to headline only - cap too tight |
| filtered @ 340 (new default) | **315** | headline + changes + NEXT + DIG + fold line |
| rules section (per assembly) | full 316 -> **compact 116** (`rules_mode`) |

So the filter buys ~50% of the injected context at a cap that keeps its
judgment legible; below ~300 the verdict text is what gets sacrificed, not the
payload (the ledger fold is sacred). First-turn delta of the plugin, measured:
~958 tokens (rules 316 + first briefing 642), ~758 with `rules_mode: compact`.


## 12. Client data path (measured 2026-11-21 - forced change to §5's panel wiring)

The plan drew the panel as "slots + the package's own Remote". Measured against
the installed web profile, that path does not exist for third-party bundles:

- `ctx.remote` on the web client is a **compiled aggregate**
  (`dsh-api-remotes/src/index.ts` imports each shipped service's generated
  `/remote` face). A bundle cannot add its namespace: `ctx.remote.dfObserver`
  stays undefined, and a `dsh.client.inject` declaring the deep path
  `remote.dfObserver` aborts the whole client module row (the panel vanished).
- `dsh.client` accepts only `platform/inject/external/immediately` - no
  `remotes` field exists in this install.
- The static Client factory has **no `styles` builtin** (`styles.insert` threw,
  killing `apply` before the panel registered). Styling ships as React inline
  styles over `--dsw-alias-*` tokens - the pattern dsh-dox uses.
- No decorator syntax in the runtime (Node 24.14): `@Remote` markers must be
  applied to the prototype at module load, and the gateway reads them at
  construction; markers applied in `[Service.init]` are invisible.

**Adopted path (the one the docs sanction, practices.md + user-actions.md):**

- Panel data = the session projection's `wire.view`: the Host folds the log and
  publishes the value; the Client renders it with the dock's
  `useProjection('dfObserver')` prop. No polling, no Remote.
- Panel action (Ack) = a session command `/df-ack <id>` executed through the
  shipped `remote.commands` namespace (`ctx.remote.commands.execute(sessionId,
  line, [])`); the same words work in the composer, and the projection folds
  `command/run` so the ack is log-truth. The agent path (`df_alerts ack`)
  calls the same `model.ackAlert`.
- Consequence: the panel shows the session's folded view, which updates at
  briefing cadence (`inject_max_spacing_ms`), not per poll - by design, since
  the log is the truth the panel may show.

Staleness (measured, twice, in both directions):

- A linked reinstall at the SAME version reuses the loaded module (`application:
  applied`, plugin re-activates, code stays old). Bumping `version` fixed it
  0.1.0 -> 0.1.1 (new command registered, new payload fields appeared).
- Bumps DO load: 0.1.21's relabelled header (`food-on-piles`) appeared live
  after a remove+install. The 0.1.18 -> 0.1.20 "reuse" reading was MY error -
  the 0.1.19 fix never landed (a replace pattern missed a re-indented line), so
  the running code behaved identically and looked stale. Lesson recorded in
  0.1.22: verify an edit by its own marker (`grep` the changed string), not by
  behaviour alone.
- Therefore: for a linked bundle, a **Host restart** is the dependable way to
  load new code. `plugin_manager` (which records the bundle version) is the
  supported install route; the CLI `dsh plugin --profile web add <dir>` writes
  the dependency but carries no version metadata.
- Measured failure mode of the CLI + manager combo: `remove` then a failed
  install leaves `@local/df-observer` OUT of the profile's `package.json`
  dependencies - the plugin is then not mounted at all (no config entries, no
  row, no tools, poller stops), while a retry says `application: applied`.


## 13. The game's own status bar in the briefing (player request 2026-11-21, live-verified)

The player showed the status bar - `Pop 21 | mood 2,2,9,8 | Food None Drink None
Seeds ~70 | 20th Timber Late Autumn Year 100` - and asked for it in the update.
Measured what this build can actually give (Lua probes, 53.17-r1):

| field | source | status |
|---|---|---|
| dwarves | `FortFacts.units` race == dwarf, non-hostile | **in the header** as `dwarves 21 (units 79)` |
| date | `dfhack.world.ReadCurrentYear/Month/Day` | **in the header** as `Y100 Win M9 d18`; season = `floor(month/3)` (measured: month 9 -> Winter; a `season` key was a bug) |
| seeds | `#df.global.world.items.other.SEEDS` | **in the header** |
| picks | weapon items `ITEM_WEAPON_PICK` (the TOOL group holds wheelbarrows/scrolls) | **in the header**, `? (probe has no data)` before the first deep tick |
| site id | `dfhack.world.getCurrentSite().id` (677) | in facts; **name is NOT renderable** - `dfhack.language` is nil in this Lua env |
| mood | `u.mood` is -1 for every unit here; no `dfhack.units` mood helper exists | **not probeable in this build** - labelled, never guessed |

| food / drink | pile probe: item counts **on the watched piles** by category | relabelled `food-on-piles` / `drink-on-piles`: the game's status bar shows the fort-wide **meal/drink supply**, and "Food None" there is *not* contradicted by `food-on-piles 3` (measured: the piles drained 5 -> 3 as dwarves ate, while the game's bar said None) |

Mechanics: one combined Lua probe (`OBSERVE_PICKS_LUA` -> `OBS2 PICKS .. SITE ..
DATE .. SEEDS ..`) on **deep ticks only** - a Lua round-trip costs ~0.6 s and
must not be paid every 8 s. The probed fields live in `model.statusFacts` and
are re-attached on every fast tick, because `ingestFast` replaces `facts` with
the server's object (measured: the header lost date/seeds on the tick after the
probe).


## 14. Durable ledger (measured, proven 2026-11-21)

To-dos are plugin-owned state, so they must survive reloads (measured gap: a
reinstall wiped a registered to-do, twice). Route: `ctx.storageDomain` with the
unit name `df_observer` (`UNIT_NAME_RE = ^[a-z][a-z0-9_]*$` - camelCase is
rejected), `layout: 'per-record'`, tables `todos` + `boxes`.

Four measured failures on the way, all now fixed:

1. `ctx.effect(() => this.openLedger())` - an async body is not a disposer; the
   open promise was dropped and the domain never opened.
2. Unit name `dfObserver` rejected → `ledger open failed (labelled)`.
3. `ctx.effect(ledgerDispose)` - the callback IS the constructor, so passing the
   disposer ran it at activation → `domain 'df_observer' is closed` on every
   write.
4. The name fix itself did not land (a replace pattern missed a re-indented
   line) and looked like module staleness; `grep` the changed string to prove an
   edit.

Proof (live): `~/.dsh/storages/df_observer/todos/19.json` holds the full record;
after a remove+install the briefing shows `#19 Durability probe 5: tunnel dug to
>=66 x27-42 y121-126 elev36` with `accept: to_dig==0 && dug>=66 in box`, and the
seeded ledger re-keyed to ids 20+ so nothing collides.

Also proven live: an acceptance flip raises the whitelisted `dig_complete` alert
(`A1` in the panel/briefing, with the evidence quoted), and `df_status
scope=health` now renders notes - including the plugin's own diagnostics, which
is how every failure above became visible.


## 15. Supply ranking (player rule 2026-11-21: "drinks are more important than
meals and meals are more important than raw food")

The observer must not treat "food" as one number. Measured on this build:

| measure | probe | note |
|---|---|---|
| alcohol | `#items.other.DRINK` | fort-wide items; this fort: **0** |
| meals | `items.other.FOOD` whose `subtype.id` contains `MEAL` | this fort: **0** |
| raw food | the rest of `items.other.FOOD` | this fort: **0** |
| seeds | `#items.other.SEEDS` | 76 |

All four ride the **same deep-tick Lua probe** (`OBS2 ... DRINK MEALS RAW`),
so the cost stays one round-trip (~0.6 s).

Alerts now follow the ranking (one at a time, the most important gap first):
`crew-no-drink` (alcohol 0) → `crew-no-meals` (drinks stored, no meals) →
`crew-no-raw` (no raw food and no seeds). No alert before the first probe:
the header says `supply ? (deep probe pending)`.

Two labels that keep the numbers honest, both measured:
- The briefing's `drink/meals/raw` are **fort-wide item counts**. The game's
  status-bar Food/Drink are its own fort-resource numbers - "Food None" and a
  non-zero pile count are not a contradiction.
- `food-on-piles` / per-pile counts come from the pile probe (items physically
  on watched piles). They stay in the pile lines, never in the header.

Live A/B, measured 2026-11-21 with the filter session running in the profile
(`filter: live (verdict tick ?)` + note `filter session live: df-observer-filter-mwqoozgw`):

| mode | tokens per injection |
|---|---|
| rule mode (compact rules, 700 cap) | **649** |
| filtered @340 cap (current default) | **315** |
| filtered @260 cap | 174 (body squeezed - too lean) |

= **334 tokens saved per injection** with the filter live.

## 16. Cadence re-measured (0.1.26, live)

`scripts/watch_poll.py` over 27 /observe polls with the game running:
**avg 8.14 s (min 8.13, max 8.15)**, deep ticks flagged every 4th poll -
matches `poll_running_ms: 8000` (the earlier 8.0 s reading holds at 0.1.26).
Paused cadence measured earlier at 60.0 s (`poll_paused_ms`).


Live alert feed with plugin-run acceptance, measured 2026-11-21 (pile probe fixed):

```
A3 dig_complete: to-do #60 "Wake probe: a watched pile accepts stone and holds >=1 item"
     -> done: pile 19 x30-33 y126-128 elev36 accepts stone, 13 items on it (accept: >= 1)
A5 dig_complete: #72 stone pile for the dig face  -> pile 19, 13 items, 3 wheelbarrows
A6 dig_complete: #71 general pile near the wagon  -> pile 20 elev38 accepts …
A2 dig_complete: #20 felled wood reaches the wood pile -> pile 18 accepts wood, 2 items
A1 crew x8: alcohol 0 with 21 dwarves: drink outranks meals and raw food   (acked -> repeats stop)
```

Two alerts already carried `(acked)` with `x1` - the panel Ack silenced their
repeats; `df_alerts ack A1` did the same for the alcohol alert (repeats silenced
while the condition stays). Pile health became visible only after 0.1.28: the
server keys pile rows by id, and `Array.isArray(data.piles)` silently dropped
them, so every pile read NO DATA while a direct `curl` showed the rows.

### Measured: rung predicates must be as strict as the rung text (precision gap, live)
Alert A2 marked to-do #84 "General pile near the wagon, **excluding stone/wood/corpses/refuse/gems**"
as done with its own evidence showing the pile accepting **gems**:

```
pile 20 elev38 accepts ammo,animals,armor,bars_blocks,cloth,coins,finished_goods,food,
                      furniture,gems,leather,sheet,weapons   <- gems present
```

The predicate only checked "excludes stone/wood". Fixed in-game (`/configure-stockpile` on 20:
`gems=false`), and the rule for the plugin: **a rung's acceptance test must encode every
category the rung names** - `pile_categories` with an exact allowed set, not a subset check.
An alert that quotes evidence contradicting its own title is worse than no alert: it teaches the
model to accept a half-right state.

## 17. The game's own hints (player question 2026-11-21: "out of food, build a
farm plot" style announcements)

Yes - the game's announcements are the best hint channel we have, and they are
**quoted verbatim**, never paraphrased:

```
GAME SAYS [Y101]: Urdim Rerrasbomrek, expedition leader cancels Eat: Item inaccessible.
GAME SAYS [Y101]: A caravan from Ral Al has arrived.  Overall trade sentiments are lukewarm.
```

Implementation (0.1.34): `reports: N` already rode the deep read, and three
defects kept it from the player - `payload()` never carried `reports`/`crashlog`;
the section was inside the trimmable body (it vanished at the 700-token budget);
and the builder called a helper that does not exist in `brief.js`, so
`buildBriefing` threw. Now: the section sits **after** the trimmable body (our
prose gets cut before the game's words), it prints nothing when the read is
empty, and 1-4 lines survive every shrink level.

The game's supply hints outrank our derived math: a report matching
`cancels Eat | Out of food | Out of drink | get drunk` raises the `crew` alert
`game-says-supply` with the report's own words and `elevNN` from the report.
The filter prompt carries the same rule ("quote them verbatim ... attribute them
to the game").

### Measured: completion is a tile-state question, twice over
A box was announced complete as `to_dig 0, dug 2` (accept `dug>0`) while 21 of 21 tiles were
dug. Two independent bugs produced that:

1. **Designation class replaced tile class.** `survey` overwrote a tile's category with
   `already_designated`, so 19 dug floors carrying stale orders counted as neither `dug` nor
   `to_dig` - and the game cancels such orders (they were gone on the next read). Fixed: tile
   state is the category; designations are reported as `designated` + `designated_on` per
   category, with `to_dig = rock + hidden` and `dug = floor + stairs + ramp` independent of
   orders, plus `safe_to_designate` = rock/hidden **without** an order on it.
2. **Glyph alphabet gaps.** `>` (stairs_down) and `<` (stairs_up) were not in the decode map,
   `^` (ramp top) and `'` (smoothed floor) likewise - dug stairs read as `other`. Now included.

Plugin rules that follow:
- `tiles_progress` boxes need `min_dug` sized to the box (a 7x3 room is not "complete" at 2
  tiles); `dug > 0` alone is a weak test - use it only for probe tunnels.
- Stale designations on dug floors are noise: report them (`designated_on.floor > 0`) as a
  housekeeping alert, never as progress.

### Measured from the live feed: NO DATA reopened done to-dos (flap), plus labelling noise
Feed sample (`df_alerts list`):
```
A11-A14 todo_reopened: "... no pile accepts wood (probe covered ids none)"
A15-A22 dig_complete  : to-do #20/#71-#73/#84-#86 "pile ... accepts wood/stone, N items on it"
```
Three defects, in severity order:

1. **NO DATA was read as a fact.** `probe covered ids none` means the pile probe returned
   nothing (the `/game-lua` call answers empty while the game is mid-tick). The plugin treated
   that as *"no pile accepts wood"* and reopened completed to-dos - the exact violation of the
   project's gate rule ("unknown is never a veto"). Rule: a test read that returns no data must
   be recorded as **not checked** and must never change a to-do state. Only a positive read with
   evidence may flip a state, in either direction.
2. **Alert kinds are mislabelled.** Pile-acceptance and crew events all arrive as
   `dig_complete`, so the feed cannot be filtered, and the suggested action text
   ("verify the ceiling with cross-section") is wrong for a pile event. Kinds must match the
   test that fired: `pile_categories`, `crew`, `dig_complete`.
3. **Ledger duplicates.** #60/#71/#72/#73/#84/#85/#86 are the same three conditions (wood pile,
   stone pile, general pile), and the ladder re-seed on each save-reload adds them again.
   Seed must be idempotent: key to-dos by `(kind, params)` and merge, never append duplicates.

Why it matters for play: the flap makes the model re-do work the ledger says is undone (dig
orders, pile reconfiguration) while the fort is short of food and drink - and every reopen is
followed by a re-complete alert, which is pure noise on top of the briefing budget.

## 18. The wake boundary (player-measured 2026-11-21)

The player saw a `dig_complete` wake "sitting above my message with the little up
arrow - I had to press it". Cause: the alert used `agent.followup`, which the
Agent docs define as "queue an ordinary follow-up turn **and wake the driver**" -
on a *busy* agent that is a queued next turn, shown as the composer's pending
item.

Boundary rules (Agent runtime docs, measured):

| call | idle agent | busy agent |
|---|---|---|
| `agent.inject` | parks until something else wakes it | claimed at a later step boundary |
| `agent.followup` | starts a turn | queued as the NEXT turn (the up arrow) |
| `agent.steer` | starts a turn | consumed at its NEXT STEP |

So: **alerts steer** (`deliveryOf('alert', agent)` -> steer, followup only as a
fallback), **briefings inject** (state, not a request - the plan's rule). A wake
now lands mid-task, which is what a water/crew/tile-loss alert is for.

## 19. Per-request cost of the plugin, measured (0.1.38)

The plugin's standing cost in a target agent's request header (estimator:
bytes/4, the same one the briefings use):

| piece | tokens |
|---|---|
| tool schemas (3 tools), long how-to text | **962** |
| tool schemas with `tool_docs: compact` (default) | **737** (df_status 123+79, df_todo 104+~200, df_alerts 50+124) |
| `STANDING_RULES_COMPACT` (system prompt section) | 148 |
| `STANDING_RULES` full | 387 |
| first briefing (measured live at embark, rule mode) | 600-650 (cap 700) |
| filtered briefing (filter live) | 315 (cap 340) |

So the floor a session pays for the plugin is **~885 tokens** in compact
defaults (737 + 148), plus one briefing. `tool_docs: full` is the teaching
variant (+219 tokens per request); `rules_mode: full` adds 239.

Knobs, all Config: `tool_docs` (compact|full), `rules_mode` (compact|full|off),
`briefing_max_tokens` (700), `filter_briefing_max_tokens` (340),
`briefing_mode` (on|off), `wake_enabled`.

## 20. The filter, live end to end (0.1.40, measured)

Two defects kept the filter from ever being used: `assistant/message` is not a
bus event (the append feed is `session/event`), so the verdict listener never
fired; and `df_observe_state` output failed the lossless-JSON check (the filter's
own log shows "The tool is throwing an error", and it fell back to `df_status`).

Live injections from this session (verdict-driven, filter session
`df-observer-filter-…`):

```
DF-OBSERVER (filtered) tick 7
HEADLINE: Three game days passed, zero tiles dug: all boxes frozen, pending 0, elev35 adit fog-blocked, drink 0
DF-OBSERVER (filtered) tick 8
HEADLINE: Watched-pile food 43→40 falling with drink 0; dig boxes frozen, pending 0; crew alert A2 persists
```

Measured on the live payload (3,848 tokens): rule briefing **456**, filtered
**225** (caps 260 and 340 land at the same body) → **231 tokens saved per
injection**. A stale verdict is labelled by its tick ("No change at tick 11" on
tick 13), never presented as fresh.

Live proof of the idle-face rule (0.1.41, same session):

```
A3 dig_stalled [x45-66 y121-122 elev35] x4: to_dig 44, pending 0, dug 0 unchanged for 7 reads
   - nothing queued and nothing dug (the plan is idle at this face)
A2 dig_stalled [x27-42 y121-126 elev36] x4: to_dig 51, pending 0, dug 45 unchanged for 7 reads
```

The player then chose (2026-11-21): **`dig_stalled` joins the wake whitelist**, so a
frozen face steers the play agent to queue the next designation - Config now
defaults to `[water, dig_complete, dig_stalled, tile_state_lost, game_crashed,
game_offline]`. One wake per alert lifetime (`wakeSent`), so a stall that keeps
repeating (its evidence line changes every read) cannot spam the agent.

## 21. The player's water at Z25 - and why the alert was blind (measured)

The player reported "on Z25 the stairs stopped because of water". Probing:

- `column` at the stairwell (43-44, 121-122): elev 30-26 are dug/stairs, **25 is solid**,
  below is fog; `hazards` at 24-25 in that 7x7 window read clean.
- Widening: `shaft` probe reports the **aquifer at elev 23-25** in the adit columns
  (46,121 -> [23,24,25] ... 62,122 -> [25]); `warnings` shows **12 DAMP
  designations** in `x43-44 y121-122 elev22-24` - the designations below the
  stairwell breach the aquifer, which is exactly what the player hit.

The observer was blind to it for one Config reason: `elev0: 26` cut the window
above 25, so the damp designations (22-24) were outside the warning scan. The
live row is now `elev0: 22`, the stairwell faces `x43-44 y121-122` at **elev 25
and 29** are monitored boxes, and the adit's own columns (46/50/54/58/62/66) are
shafts. Result: `A18 damp_hot [x43-44 y121-122 elev22-24]: 12 DAMP
designations` - the alert fires with the box, and the filter's headline names it:

```
HEADLINE: 12 DAMP designations breach aquifer elev22-24; elev35 tunnel at 15/44 dug; zero drink for 21 dwarves
```

Lesson for the plugin's own defaults: the monitoring window must cover the
levels the fort is *working on*, not just the embarking tunnel - so the
stairwell and any adit are inside `elev0..elev1`, and every face the ledger
watches joins `monitored_boxes` (the durable ledger keeps them across reloads).

## 22. Acceptance (d) proven live, and the panel's blind chips

The player paused the game; the next briefing header read **`PAUSED`** and the
payload carried `paused: true, pauseSource: "game-auto-or-player"`, while
`pauseAuthority: "off"` (the plugin wrote nothing - still zero
`POST /set-pause-state`). `df_status scope=health` shows the attribution line:

```
tick 8 | PAUSED | gen 1 | last pause: game-auto-or-player @tick 8
  pause event @tick 8: paused (game-auto-or-player)
```

Cadence proved itself in the same payload: `poll` moved **8000 -> 60000** the
moment the game paused (the observer adapts to `poll_paused_ms`), and the filter
reported the pause in its headline - plus it noticed `damp_hot` leaving the alert
list when the designations were cleared.

Panel defect found and fixed (0.1.45): the projection `stateSchema` did not
declare `poll`, `pauseAuthority` or `filter`, so the folded state dropped them -
the chips read `poll ?`, `pause ?` and the filter chip never rendered. The
schema and payload are now asserted in sync by a test (42 checks).
