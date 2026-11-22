# DSH harness bug: `agent.inject()` persists an unvalidated payload, and one plugin string makes a session permanently unloadable

**Severity:** data loss (unrecoverable session history), triggered by a plugin type error.
**Version measured:** DSH `0.2.2-rc.5` — `@deepseek-ai/dsh-agent-loop`, `dsh-session-format-v3-to-v4`,
`dsh-session-persistence-jsonl`, `dsh-session` (all 0.2.2-rc.5), Node v24.14.1, Linux.
**Found by:** the `@local/df-observer` plugin (workspace `plugin/df-observer/`), 2026-11-21.
**Plugin-side status:** fixed in df-observer 0.1.91 (see `plugin/df-observer/PLAN.md` §34). This report
is about the harness gap that let a plugin cause it.

## Symptom

The GUI refuses to open a session that worked for hours:

```
Failed to load history: stored session "session-e33668b6-ccf1-45b5-b5ea-acffa7cead57" is corrupt:
session "session-e33668b6-ccf1-45b5-b5ea-acffa7cead57": stored log is corrupt:
SessionFormatError: agent/inbox/spliced requires message objects
(raw log: ~/.dsh/sessions/--home-markus-github-dfhack-mcp--/session-e33668b6-…/session.v4.jsonl.zstd)
(gateway/internal)
```

The history entry is gone for good, together with every later event in that session. In one profile
this happened to **84 stored sessions (96 bad rows)** — 44 of them the plugin's own filter subagents.

## Root cause

`Agent.inject/steer/followup` are typed `inject(message: UserMessage)`
(`dsh-agent/lib/types/runtime-types.d.ts:237`, and `dsh-agent-loop/lib/types/agent.d.ts:53`), i.e. an
object with `role`/`content`/`id`/`source`. The type is erased at runtime and nothing enforces it on
the write path:

```js
// @deepseek-ai/dsh-agent-loop/lib/index.js
inject(input) { this.send(input, "next-step", false) }            // :585
send(message, target, wakeup) { this.inbox.splice(resolvedTarget, Infinity, 0, [message]); … }
splice(target, start, deleteCount, inserted) { return this.mutate(target, start, deleteCount, inserted, true) }
mutate(target, start, deleteCount, inserted, discardRemoved) {    // :174
  …
  for (const message of …) {
    if (ids.has(message.id)) throw new Error(`message "${message.id}" is already pending`)
    ids.add(message.id)                                          // a string has no id → `undefined` passes
  }
  const splice = { target, start, …, inserted, … }
  const event = this.session.append("agent/inbox/spliced", splice)   // :198  ← raw value persisted
}
```

A bare string therefore lands verbatim in the durable log:

```json
{"type":"agent/inbox/spliced","seq":2107,"data":{"target":"next-step","start":0,
  "inserted":["DF-OBSERVER injections PAUSED (command): 0 briefings and 0 wakes suppressed so far…"]}}
```

The write succeeds, the live session keeps running (and can corrupt further), and the failure appears
only when the log is opened again: `mapEventMessages` walks every `inserted` payload
(`dsh-session-format-v3-to-v4/lib/index.js:39` → `src/sources.ts:32`) and throws
`SessionFormatError: agent/inbox/spliced requires message objects`; the inbox fold
(`inboxProjectionDefinition.apply`, `dsh-agent-loop/lib/index.js:26-56`) rejects the same row
(`message.id` on a string is `undefined`, so the second one hits "already pending").
`decodeStoredLog` turns it into `SessionPersistenceCorruptionError`
(`dsh-session-persistence-jsonl/lib/index.js:2847`, `:2869`) — "stored log is corrupt", with no
repair, downgrade, or skip-and-continue path.

Validation exists only on the read side, so any writer — a plugin, a future first-party feature —
can write a session that its own harness cannot read.

## Minimal reproduction

Host plugin, `apply(ctx)` body:

```js
const agent = ctx.agents.list()[0]
agent.inject('hello')            // no throw, no warning
```

Then close the session and open it again: "Failed to load history … stored log is corrupt:
agent/inbox/spliced requires message objects". The row is visible in the raw `.v4.jsonl.zstd`.

Reproducible with `steer`/`followup` too (same `send` path), and with `null`, arrays, numbers.

## Suggested fix (one place, on the write path)

Validate at the inbox boundary, so the plugin gets a loud, local error at the call that broke the
contract, and the log never receives an unreadable row:

```js
// dsh-agent-loop/lib/index.js — ReactLoopInbox.mutate, before session.append()
for (const message of inserted) {
  if (message === null || typeof message !== 'object' || Array.isArray(message))
    throw new TypeError(`agent/inbox/${target} requires message objects (got ${typeof message})`)
  if (typeof message.id !== 'string' || message.id.length === 0)
    throw new TypeError('agent inbox payload requires a non-empty message id')
  if (!Array.isArray(message.content))
    throw new TypeError('agent inbox payload requires a content array')
}
```

`send()` is the shared entry point (`inject`/`steer`/`followup` all funnel there), so the guard
belongs before `splice`, and it costs three checks per injected message.

Worth considering alongside it:

- **Reuse the read validator on append.** `assertV4RowAdmission(event, KNOWN_SESSION_EVENT_TYPES)`
  already runs per row when the log is read (`dsh-session-persistence-jsonl/lib/index.js:1095-1098`).
  Running the same admission on durable appends turns "unreadable session" into "the append failed
  now, with a stack".
- **A recovery path for already-corrupt logs.** `mapEventMessages` throwing means data loss with no
  escape; a `--force` / "drop unreadable rows" open mode (labelled in the log) would at least
  preserve the rest of the history. The repair script in this repo
  (`scratch/repair_observer_sessions.mjs`) is the manual version of exactly that: it lifts each bare
  string into the plugin's own message shape (identical text, fresh `id`) and replays
  `createRestore` + `decodeRow` + `finish` + the inbox fold over every log before swapping anything
  in — 304/304 sessions load clean afterwards (`scratch/verify_repaired_histories.mjs`).
- **Type-surface note for plugin authors:** `agent.inject()` is the only durable-write API that takes
  a caller-built object with no schema; `ctx.tools.register`, `ctx.slots.register` and
  `ctx.commands.register` all validate their inputs at registration. If the API surface is
  documented as schema-checked anywhere, `inject`/`steer`/`followup` should say they are not.

## Evidence in this profile

- Raw rows: 96 across 84 logs, all with the plugin's `DF-OBSERVER injections PAUSED …` /
  `DF-OBSERVER CATCH-UP …` text (the two paths that passed a string), scanned by
  `scratch/scan_session_corruption.mjs`.
- Repair + verification: `scratch/repair_observer_sessions.mjs --stage/--prove/--check/--install`,
  `scratch/verify_repaired_histories.mjs`.
- Plugin-side guard and the loader-contract test that now enforces it:
  `plugin/df-observer/index.js` (`observerMessage`, `injectMessage`) and
  `tests/test_df_observer.mjs` ("every inbox boundary takes a message object").

---

# Bug 2 (same investigation, found after Bug 1 was fixed): `turn/end` is written with `reason: null`, and a first-party fold throws on it

**Severity:** sessions become unlistable/unsearchable in the GUI, from a row the harness
itself wrote.
**Measured:** 35 sessions in this profile, one row each — every `@local/df-observer`
filter subagent session. Reproduced on the exact session the player pasted
(`df-observer-filter-mwqy3jr3`, seq 48).

## Symptom

```
Failed to load history: failed to project session "df-observer-filter-mwqy3jr3":
Cannot read properties of null (reading 'kind')   (gateway/internal)
```

`SESSION_QUERY_CORRUPT_SESSION` from `dsh-session-query/lib/index.js:426`
(`preparedProjections`) — the session's log reads fine; the projection pipeline over it
throws, so the row in the session list is an error and the session cannot be opened
from there.

## Root cause: a write/read contract mismatch inside the harness

Write side — `@deepseek-ai/dsh-agent-loop/lib/index.js`:

```js
let turnEnds = null;                                   // :749
…
} finally {
  this.session.append("turn/end", { turn, reason: turnEnds })   // :832-834
}
```

A turn that exits through a path that never assigns `turnEnds` — which is what happens
when an Agent is cancelled/disposed at its boundary — appends literal `"reason": null`.
`TurnEndReason` (`dsh-session/lib/types/types.d.ts:170-216`) has **no null variant**:
`aborted` requires `reason: TurnEndCancelCause`. Per-row admission accepts the row
(`assertV4RowAdmission` does not inspect the reason shape), so it persists.

Read side — `@deepseek-ai/dsh-subagent/lib/index.js`:

```js
function epochStopReason(events) {
  const { end, droppedUnrun } = foldConsumedWork(events);
  switch (end?.data.reason.kind) {        // :172  ← `null.kind` throws
```

The optional chain stops at `end?.data` and then dereferences `reason.kind`. The switch
handles `case void 0` (an absent reason), so the author expected omission — not `null`.
One null row per session takes the whole session out of the list.

## Minimal reproduction

1. Start an Agent, begin a turn, then cancel/dispose it at a step boundary so the loop
   exits before any `turnEnds` assignment.
2. The session log gains `{"type":"turn/end","data":{"turn":N,"reason":null}}`.
3. With `dsh-subagent` mounted (any subagent-bearing profile), list/query sessions:
   that session reports `failed to project session "…": Cannot read properties of null
   (reading 'kind')`.

## Suggested fixes

1. **Write side (one line):** in `dsh-agent-loop`, never persist a null reason —
   `this.session.append("turn/end", { turn, ...turnEnds ? { reason: turnEnds } : {} })`,
   or initialise `turnEnds` to the truthful closer (`{ kind: 'aborted', reason: cause }`
   when the turn was cut) instead of `null`.
2. **Read side (defence in depth):** `end?.data?.reason?.kind` in
   `dsh-subagent:epochStopReason`. A fold reading durable history must not be one
   nullable field away from making a whole session unopenable.
3. **Admission side:** `assertV4RowAdmission` should validate `turn/end.data.reason`
   against `TurnEndReason` (union of `TurnEndReasonMap`, absent allowed) so a
   non-conforming variant fails at the append, where the writer has a stack, instead of
   degrading into a read-time "corrupt session" label.
4. Same theme as Bug 1: **the durable write path needs the read path's schema.** Two of
   the three ways this profile lost history (inbox payloads, turn-end reasons) are
   writes accepted by the writer and rejected by a fold.

## Repair used on the existing logs

`scratch/repair_turn_end_reasons.mjs` rewrites each `{"turn":N,"reason":null}` to
`{"turn":N,"reason":{"kind":"aborted","reason":{"kind":"disposed"}}}` — the canonical
variant for a turn cut by disposal, so `epochStopReason` reports `aborted` (the truth)
instead of throwing. It stages the result, replays the full read path
(`createRestore`/`decodeRow`/`finish`, `assertV4RowAdmission`,
`assertReleasedV4Relationships`, `validateStoredEvents`, `adoptSessionEvent`) plus the
turn-end fold, and installs only after all gates pass. 35 sessions repaired, originals
kept under `scratch/turn-end-repair/backup/`.

## Plugin-side changes that stop generating these rows (df-observer 0.1.92)

- `agent.cancel({ kind: 'disposed' })` instead of `agent.cancel('…string…')`: a string
  is not an `AgentCancelCause`, and the cause-less disposal path is what produced the
  null-reason turn end.
- `Config.pending_inject_cap` (default 8): the plugin no longer writes into an inbox
  boundary that is not draining. The filter sessions each held ~400 durable injections
  stacked on a driver that had stopped consuming; the cap skips, counts
  (`suppressed.pending`) and announces those injections instead of appending them.
