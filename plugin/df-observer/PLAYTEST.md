# Fresh-embark play test (acceptance c + d, with the token measurement)

The observer is built to be **read-only towards the game**; the digging is done
by the play agent in its own session with the DFHack MCP tools. Run this in a
NEW session (Plugins → df-observer enabled; server up; game unpaused).

## 0. Set the stage (you, the player)

- `./start.sh status` shows the MCP server; `curl -s 127.0.0.1:8000/get-status | head -c 200` says `"connected": true`.
- Plugin row shows **DF observer** in Plugins, enabled. Leave `pause_authority: off` (default) for this run.
- Start a **New Session** in the `dfhack-mcp` workspace.

## 1. The instruction to paste (tell, do not ask)

```
Dig a 3-wide tunnel 15 long with the mountain kept on top, a 6x6 room off it,
a 2-wide stairwell down 3 levels, and the basic stockpiles (general, stone,
wood, refuse). Use the df-dig skill for how, the MCP tools for orders, and
finish each piece with tile-number proof, not claims.
```

## 2. What the observer must show (capture each)

| # | thing | where to look | pass condition |
|---|---|---|---|
| a | first briefing | chat, unprompted, ≤ 1-2 min | arrives in the next admitted step, ≤ `briefing_max_tokens` (700) |
| b | numbers are real | compare briefing `elevNN x.. y..` line with `df_status({scope:"boxes"})` and a manual `curl ".../survey?elev=..&x0=..&y0=..&x1=..&y1=.."` at the same moment | all three agree on `to_dig/dug/pending` |
| c | **dig_complete wake** | when a box's `to_dig` hits 0 with `dug>0` | a `DF-OBSERVER ALERT` arrives **without any tool call from you**, quoting the box and numbers |
| c2 | **water wake** | if a monitored shaft meets water | same, with the column + elevation |
| d | stall / crew | if designations drain with no dug change, picks < miners | alerts appear in the briefing `ALERTS:` line and in the panel |
| e | to-do flips | `df_todo({action:"list"})` then `df_todo({action:"why", id:N})` | a flip quotes the acceptance evidence (tile counts) |
| f | Ack | panel → Ack, or type `/df-ack A12` in the composer | the alert's repeats stop (panel shows it acked; `df_alerts` shows `acked`) |
| g | ladder | `df_status({scope:"ladder"})` | NEXT names a rung with keys and a coordinate box inside a walkable region |

## 3. Token measurement (the with/without numbers)

Baseline (no plugin) — with the plugin disabled in Plugins, new session, same paste:

- first model request's input tokens: the footer context meter (e.g. `108M tok · Cache hit 98%` shows cumulative; the per-turn number is the step's input tokens in the trajectory header). Write it down at step 1.

With the plugin enabled — new session, same paste:

- the plugin's measured first-turn delta: `rules_mode: compact` 116 tokens +
  first briefing (measured live at embark: 600-650 tokens, cap 700) = **~750
  tokens**; re-measure the briefing text length / 4 from the injected message.
- steady state: a briefing per `inject_min_spacing_ms` (30 s) at most, each
  ≤ 700 (rule mode) or ≤ 340 (filter mode).

Record the two numbers (baseline, with-plugin) and the briefing sizes in your
report. `node scratch/measure_plugin_tokens.mjs` prints the current plugin-side
cost from the same code the plugin runs.

## 4. Acceptance (d): pause attribution

Keep `pause_authority: off` → the server log must stay at **zero**
`POST /set-pause-state` lines from the plugin: `grep -c "POST /set-pause-state" dfhack-mcp.log`.
Then pause the game yourself (`Escape`/pause key) and watch the next briefing
header: `PAUSED (last pause: game-auto-or-player @tick N)`. Resume and the
header returns to `running`. The plugin never unpauses (auto_resume is false by
default and its own pause is disabled).

## 5. Expected values, measured in production (0.1.48)

Measured from the durable session log (`request/header` events carry the whole
header), in this profile with the plugin installed:

| measurement | expected |
|---|---|
| header tool schemas, total (132-137 tools incl. MCP) | ~25,300-26,400 tokens |
| plugin's 3 tools, long docs (`tool_docs: full`) | **987** (3.74%) |
| plugin's 3 tools, compact docs (default) | **762** (2.91%) |
| standing rules section (compact) | 148 |
| one briefing injection (filtered / rule mode) | 250-290 / 456-538 |
| poll cadence (observed in payloads) | 8,000 ms running → 60,000 ms paused |
| first briefing after activation | within ~2 deep ticks (~16-20 s) |
| ledger seeds on a fresh save | ladder rungs as to-dos, ids from 1, pile tasks deduped |

Fresh-embark run: create the session (no scheduling needed - the plugin targets
every session when `agents: []`), paste the §1 instruction line, then fill the §2
evidence table. The plugin must be seen doing: tools present in the first header,
`ledger restored`/seeded to-dos in `df_status scope=summary`, first briefing
within ~20 s, `dig_complete` wake with quoted evidence when the first box drains.

## 6. The fresh-agent pass, run by a read-only probe (measured)

Items §2 (a)-(e) were run by a fresh child agent (`play-probe`) with a strict
read-only rule. It reported:

- ladder NEXT quoted verbatim, box table, alerts with evidence, ledger specs -
  all readable from the observer;
- `survey({elev:25,x0:43,y0:121,x1:44,y1:122})` -> 4/4 FLSTONE,
  `safe_to_designate: true`, matching the observer's `to_dig 4, dug 0, pending 0`;
- it overruled an alert's advice using the observer's own shaft data (elev25 is
  inside the aquifer) and ordered `NO_DIG elev22-24` first.

Channel caveat (measured, PLAN §25): in that child agent every MCP-routed call
returned `-32600 Session not found` after the DFHack server restart. `deploy.sh
channel` now diagnoses exactly this: `poll GET /observe-stats` + `read GET
/observe` (the plugin's path) versus `mcp POST /mcp` (the tool path) - 404 =
dead session binding, 406/400 = endpoint alive. The observer's own `df_*` tools
answer from the live model, so a session can always read the fort; reconnect the
MCP client for the `mcp__dfhack__*` tools.
