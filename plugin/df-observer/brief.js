/**
 * df-observer - the briefing builder.
 *
 * Produces the bounded, evidence-carrying text injected with `agent.inject()`
 * (never waking the agent). The rules baked into the payload are the lessons of
 * this project (PLAN.md §4.1):
 *   - progress is POSITIVE tile state (to_dig/dug), never "no walls seen";
 *   - every number carries its box/elevation and the window it came from;
 *   - "no data" is labelled as such - never read as a fact;
 *   - each to-do carries its acceptance test;
 *   - each next-action cites the skill section that explains how.
 * All elevations are IN-GAME ELEVATION panel numbers.
 *
 * Know-how stays in the skills (`df-dig`, `df-stockpile`): the briefing cites
 * sections; it carries state, not doctrine.
 *
 * @module @local/df-observer/brief
 */

import { PAYLOAD_PREFIX } from './model.js'

const num = (v) => Number(v ?? 0)

/** ~4 chars/token - the plugin's own budget accounting (Config.briefing_max_tokens). */
export function estimateTokens(text) {
  return Math.ceil(text.length / 4)
}

function picksText(f) {
  return f.picks === null || f.picks === undefined ? '? (probe has no data)' : `${num(f.picks)}`
}

function factsLine(payload) {
  const f = payload.facts
  if (!f) return 'FORT BRIEFING | facts: NO DATA this tick (labelled, not a fact)'
  const popText = f.dwarves == null ? `pop ${num(f.pop)} (dwarves ?)` : `dwarves ${num(f.dwarves)} (units ${num(f.pop)})`
  const SEASONS = ['Spring', 'Summer', 'Autumn', 'Winter']
  // The probe reports the DF MONTH index (0-11); the season is month/3.
  const dateText = f.date && f.date.month != null
    ? `Y${f.date.year} ${(SEASONS[Math.floor(f.date.month / 3)] ?? '?').slice(0, 3)} M${f.date.month} d${f.date.day}`
    : (f.date ? `Y${f.date.year} month ? d${f.date.day}` : null)
  const seedsText = f.seeds == null ? null : `seeds ${num(f.seeds)}`
  // Supply as the player ranks it: alcohol > prepared meals > raw food (fort-wide
  // item counts from the status probe). Pile contents are reported per pile.
  const supplyText = (f.alcohol == null && f.meals == null && f.raw_food == null)
    ? 'supply ? (deep probe pending)'
    : [`drink ${f.alcohol == null ? '?' : num(f.alcohol)}`,
       `meals ${f.meals == null ? '?' : num(f.meals)}`,
       `raw ${f.raw_food == null ? '?' : num(f.raw_food)}`].join(' ') + ' (fort items)'
  const parts = [dateText, popText, `miners ${num(f.miners)} of ${picksText(f)} picks`,
    supplyText, seedsText, `wood ${num(f.wood)}`, `stone ${num(f.stone)}`,
    `metal ${num(f.metal)}`,
    // The queued-jobs split: the watched boxes' pending vs the game's total, so a
    // backlog outside the survey list is named as such (measured: 103 pending).
    `pending ${num(f.pending_dig)} (watched ${pendingWatched(payload)}/out ${pendingOutside(f, payload)})`].filter(Boolean)
  const state = payload.online === false ? 'GAME OFFLINE' : (payload.paused ? 'PAUSED' : 'running')
  const who = payload.pauseSource ? ` (last pause: ${payload.pauseSource}${payload.pauseAtTick != null ? ` @tick ${payload.pauseAtTick}` : ''})` : ''
  return `FORT BRIEFING (in-game elevations) | ${state}${payload.paused ? who : ''} | ${parts.join(' | ')}`
}

/** The game's own announcements - its hints ("cancels Eat: Item inaccessible",
 * "Out of food!", caravan arrivals) - quoted, not paraphrased. */
export function gameSaysLines(payload, max = 4) {
  const reports = payload.reports ?? []
  // No line at all when the read is empty: the game's hints are worth tokens,
  // an empty label is not (measured: it pushed the shaft/alert lines out of the
  // trimmed body).
  if (!reports.length || max <= 0) return []
  return reports.slice(-max).map((r) => {
    const when = r.year != null ? `Y${r.year}` : ''
    return `GAME SAYS [${when}]: ${truncate(String(r.text ?? ''), 110)}`
  })
}

function ladderLines(payload, max = 8) {
  const rungs = payload.rungs ?? []
  if (!rungs.length) return ['LADDER: NO DATA this tick (deep read pending)']
  const open = rungs.filter((r) => !r.done)
  const done = rungs.filter((r) => r.done)
  const lines = []
  lines.push(`LADDER: ${done.length} of ${rungs.length} done [${done.map((r) => r.id).join(' ') || '-'}]`)
  if (payload.next) {
    const at = payload.next.at
      ? ` at x${payload.next.at.x0}-${payload.next.at.x1} y${payload.next.at.y0}-${payload.next.at.y1} elev${payload.next.at.elev} (region ${payload.next.at.region}, ${payload.next.at.region_tiles} walkable tiles)`
      : ''
    lines.push(`NEXT:   ${payload.next.id}: ${payload.next.title}${at}`)
    lines.push(`        keys: ${payload.next.keys}`)
    lines.push(`        why:  ${payload.next.why}`)
  } else if (!open.length) {
    lines.push('NEXT:   every quickstart rung is satisfied - fortify, expand, defend')
  }
  for (const rung of open.slice(0, Math.max(0, max - 3))) {
    lines.push(`  [ ] ${rung.id}: ${rung.title}`)
  }
  return lines
}

function todoLines(payload, max = 12, evidenceMax = 160) {
  const todos = (payload.todos ?? []).filter((t) => t.state !== 'done')
  const done = (payload.todos ?? []).filter((t) => t.state === 'done')
  if (!todos.length && !done.length) return ['TODOS: ledger empty (the plugin seeds it from the first ladder read)']
  const lines = [`TODOS: ${todos.length} open, ${done.length} done`]
  for (const todo of todos.slice(0, max)) {
    const box = todo.box ? ` x${todo.box[0]}-${todo.box[2]} y${todo.box[1]}-${todo.box[3]} elev${todo.box[4]}` : ''
    const origin = todo.origin === 'auto' ? ' (auto)' : todo.origin === 'ladder' ? ' (ladder)' : ''
    lines.push(`  #${todo.id} ${todo.title}${box}${origin}`)
    lines.push(`      accept: ${acceptText(todo)} | last: ${truncate(todo.evidence, evidenceMax)}`)
  }
  return lines
}

function acceptText(todo) {
  const a = todo.accept ?? {}
  switch (a.kind) {
    case 'tiles_progress': return `to_dig==0 && dug>=${a.min_dug ?? 1} in box`
    case 'pile_category': return `a pile accepts ${a.category} with >=${a.min_items ?? 1} items on it`
    case 'pile_categories': return `a pile accepts ${(a.categories ?? []).join('+')}`
    case 'pile_general': return 'a pile takes >=8 categories, excludes stone+wood'
    case 'rung': return `ladder rung ${a.rung} satisfied`
    case 'buildings_kw': return `>=${a.min ?? 1} building(s) named *${a.keyword}*`
    case 'crew_picks': return 'picks >= designated miners'
    case 'manual': return 'closed by the model with evidence'
    default: return `${a.kind} (not runnable by the plugin)`
  }
}

function alertLines(payload, max = 8, evidenceMax = 170) {
  const alerts = (payload.alerts ?? []).filter((a) => !a.acked)
  if (!alerts.length) return ['ALERTS: none active']
  const lines = [`ALERTS: ${alerts.length} active`]
  for (const alert of alerts.slice(0, max)) {
    const coord = alert.coord ? ` [${alert.coord}]` : ''
    lines.push(`  !${alert.id} ${alert.kind}${coord} (x${alert.count}): ${truncate(alert.evidence, evidenceMax)}`)
    if (alert.suggested) lines.push(`     -> ${truncate(alert.suggested, 130)}`)
  }
  return lines
}

function healthLines(payload, max = 6) {
  const lines = []
  const notes = payload.notes ?? []
  if (payload.online === false) lines.push('HEALTH: game OFFLINE - the poller is tolerating it (backoff; long game-off is normal)')
  if (payload.crashlog) {
    const files = (payload.crashlog.files ?? []).slice(0, 3)
    lines.push(files.length
      ? `HEALTH: crash logs in ${payload.crashlog.dir}: ${files.map((f) => `${f.name} (${f.bytes}b)`).join(', ')} (names/sizes only; contents out of scope)`
      : `HEALTH: crash-log dir ${payload.crashlog.dir} - none seen`)
  }
  for (const note of notes.slice(0, max)) lines.push(`NOTE:   ${truncate(note, 150)}`)
  if (payload.overload) {
    lines.push(`OVERLOAD: ${payload.overload.alerts} unacked alerts, ${payload.overload.todos} open to-dos - work the queue in order, one item at a time (the plugin pauses only if Config.pause_on allows it)`)
  }
  return lines
}

/** The facts a panel header cannot do without, in the fewest keys. */
/** Queued dig jobs inside the watched boxes, and the remainder the survey cannot place. */
function pendingWatched(p) {
  return (p.boxes ?? []).reduce((n, b) => n + (Number(b.pending) || 0), 0)
}
function pendingOutside(f, p) {
  const outside = num(f.pending_dig) - pendingWatched(p)
  return outside >= 0 ? outside : '?'
}

/** The designation scan, small enough for the lean wire payload: totals plus the
 * biggest clusters. The panel shows it; the full scan stays in df_status. */
export function compactDesignations(d) {
  // Idempotent: the lean levels call it inline AND shrinkPayloadLevel calls it
  // on the result - compaction must not eat an already-compact summary (measured:
  // levels 6-8 came back with top: [] because the second pass saw no by_elev).
  if (d && Array.isArray(d.top)) return { designated_total: d.designated_total ?? null, top: d.top.slice(0, 4) }
  if (!d?.by_elev?.length) return d ? { designated_total: d.designated_total ?? null, top: [] } : null
  const top = []
  for (const e of d.by_elev) {
    for (const c of (e.clusters ?? []).slice(0, 2)) top.push({ elev: e.elev, x0: c.x0, y0: c.y0, x1: c.x1, y1: c.y1, tiles: c.tiles })
  }
  top.sort((a, b) => b.tiles - a.tiles)
  return { designated_total: d.designated_total ?? null, top: top.slice(0, 4) }
}

function compactFacts(f) {
  if (!f) return null
  const out = {}
  for (const k of ['pop', 'dwarves', 'miners', 'picks', 'food', 'drink', 'alcohol', 'meals', 'raw_food',
    'seeds', 'pending_dig', 'water_tiles', 'magma_tiles', 'date']) {
    if (f[k] !== undefined && f[k] !== null) out[k] = f[k]
  }
  return out
}

function truncate(text, max) {
  const s = String(text ?? '')
  return s.length <= max ? s : `${s.slice(0, max - 1)}…`
}

/**
 * The briefing message: bounded, evidence-carrying, and self-describing. The
 * machine payload line at the end is the COMPLETE post-change state - the
 * session projection folds from it, so the log alone reconstructs what the
 * model saw (fork/resume safe).
 */
const shrinkCompactTodo = (t) => ({ id: t.id, state: t.state, title: String(t.title ?? '').slice(0, 90),
  accept: t.accept, evidence: String(t.evidence ?? '').slice(0, 110) })
const shrinkCompactAlert = (a) => ({ id: a.id, kind: a.kind, coord: a.coord, count: a.count,
  acked: !!a.acked, evidence: String(a.evidence ?? '').slice(0, 110) })

/** The payload shrink ladder: decoration first, detail last. Level 0 is complete. */
const SHRINK_LEVELS = [
  (p) => p,
  // The game's own hints stay in the payload at every level: they are the
  // player's channel, and 4 short strings is the cheapest thing we carry.
  (p) => ({ ...p, buildings: undefined, reports: (p.reports ?? []).slice(-4), crashlog: undefined }),
  (p) => ({ ...p, shafts: undefined, warnings: undefined, pauseEvents: undefined }),
  (p) => ({ ...p, digEvents: undefined, notes: undefined,
    rungs: (p.rungs ?? []).map((r) => ({ id: r.id, done: r.done })) }),
  (p) => ({ ...p, buildings: undefined, facts: p.facts, piles: (p.piles ?? []).slice(0, 8),
    boxes: (p.boxes ?? []).slice(0, 6),
    rungs: (p.rungs ?? []).map((r) => ({ id: r.id, done: r.done })),
    next: p.next ? { id: p.next.id, title: p.next.title,
      at: p.next.at ? { elev: p.next.at.elev, x0: p.next.at.x0, x1: p.next.at.x1, y0: p.next.at.y0, y1: p.next.at.y1 } : undefined } : null,
    todos: (p.todos ?? []).slice(0, 20).map(shrinkCompactTodo),
    alerts: (p.alerts ?? []).slice(0, 8).map(shrinkCompactAlert) }),
  (p) => ({ ...p, facts: p.facts ? { pop: p.facts.pop, dwarves: p.facts.dwarves, miners: p.facts.miners, picks: p.facts.picks,
    seeds: p.facts.seeds, date: p.facts.date, site_id: p.facts.site_id,
    alcohol: p.facts.alcohol, meals: p.facts.meals, raw_food: p.facts.raw_food,
    pending_dig: p.facts.pending_dig } : null, piles: (p.piles ?? []).slice(0, 4),
    boxes: (p.boxes ?? []).slice(0, 6),
    next: p.next ? { id: p.next.id, title: p.next.title,
      at: p.next.at ? { elev: p.next.at.elev, x0: p.next.at.x0, x1: p.next.at.x1, y0: p.next.at.y0, y1: p.next.at.y1 } : undefined } : null,
    todos: (p.todos ?? []).slice(0, 16).map(shrinkCompactTodo),
    alerts: (p.alerts ?? []).slice(0, 8).map(shrinkCompactAlert) }),
  (p) => ({ tick: p.tick, online: p.online, paused: p.paused, pauseSource: p.pauseSource,
    generation: p.generation, poll: p.poll, pauseAuthority: p.pauseAuthority, filter: p.filter,
    facts: p.facts, overload: p.overload, reports: (p.reports ?? []).slice(-3),
    designations: compactDesignations(p.designations),
    boxes: (p.boxes ?? []).slice(0, 6),
    todos: (p.todos ?? []).slice(0, 14).map(shrinkCompactTodo),
    alerts: (p.alerts ?? []).slice(0, 10).map(shrinkCompactAlert),
    digEvents: (p.digEvents ?? []).slice(0, 6) }),
  (p) => ({ tick: p.tick, online: p.online, paused: p.paused, generation: p.generation ?? null,
    poll: p.poll ?? null, pauseSource: p.pauseSource ?? null, pauseAuthority: p.pauseAuthority ?? null,
    facts: compactFacts(p.facts), reports: (p.reports ?? []).slice(-2),
    designations: compactDesignations(p.designations),
    todos: (p.todos ?? []).slice(0, 24).map((t) => ({ id: t.id, state: t.state, evidence: String(t.evidence ?? '').slice(0, 60) })),
    alerts: (p.alerts ?? []).slice(0, 8).map((a) => ({ id: a.id, kind: a.kind, acked: !!a.acked, coord: a.coord })),
    digEvents: (p.digEvents ?? []).slice(0, 6) }),
  (p) => ({ tick: p.tick, online: p.online, paused: p.paused, poll: p.poll,
    generation: p.generation ?? null, pauseSource: p.pauseSource ?? null,
    pauseAuthority: p.pauseAuthority, facts: compactFacts(p.facts), reports: (p.reports ?? []).slice(-1),
    designations: compactDesignations(p.designations),
    todos: (p.todos ?? []).slice(0, 30).map((t) => ({ id: t.id, state: t.state })),
    // coord stays: it is the shortest part of an alert that still tells a reader
    // WHERE, and the panel renders a row from it when evidence is trimmed away.
    alerts: (p.alerts ?? []).slice(0, 4).map((a) => ({ id: a.id, kind: a.kind, acked: !!a.acked, coord: a.coord })) }),
]

export const SHRINK_MAX = SHRINK_LEVELS.length - 1

/** Small versions of the panel's sections, so the leanest wire levels can still
 * feed a panel: same field NAMES the panel reads, just fewer and shorter. */
function compactPanelSections(p) {
  return {
    boxes: (p.boxes ?? []).slice(0, 4).map((b) => ({ elev: b.elev, box: b.box, to_dig: b.to_dig ?? null, dug: b.dug ?? null, pending: b.pending ?? null })),
    piles: (p.piles ?? []).slice(0, 4).map((pl) => ({ id: pl.id, found: pl.found, total: pl.total ?? 0, accepts: pl.accepts ?? null })),
    rungs: (p.rungs ?? []).slice(0, 24).map((r) => ({ id: r.id, done: !!r.done, title: String(r.title ?? '').slice(0, 48) })),
    notes: (p.notes ?? []).slice(0, 3).map((n) => String(n).slice(0, 120)),
    designations: compactDesignations(p.designations),
  }
}

export function shrinkPayloadLevel(payload, level) {
  const data = SHRINK_LEVELS[Math.max(0, Math.min(level, SHRINK_MAX))](payload)
  // One shape for the designation scan at every level: the panel renders it, and
  // the wire payload stays small (measured: levels 0-5 passed the full scan).
  if (data.designations !== undefined) data.designations = compactDesignations(data.designations)
  return data
}

/** Pick the richest payload level whose size fits `cap`.
 *
 * The panel renders its sections from THIS payload (the projection wire - the
 * @Remote panel surface a third-party bundle cannot reach), so the compact panel
 * fields ride along WHEN THEY FIT: measured at cap 300 the fill pushed the
 * briefing to 350 tokens and broke the ledger-first contract, so the fill is
 * tried per level and dropped when the budget cannot pay for it. */
function shrinkPayload(payload, cap) {
  const fill = compactPanelSections(payload)
  const fits = (data) => estimateTokens(JSON.stringify({ form: 'briefing', tick: payload.tick, data })) <= cap
  for (let level = 0; level <= SHRINK_MAX; level += 1) {
    const data = shrinkPayloadLevel(payload, level)
    const withFill = { ...data }
    for (const key of Object.keys(fill)) if (withFill[key] === undefined) withFill[key] = fill[key]
    if (fits(withFill)) return withFill
    if (fits(data)) return data
  }
  return shrinkPayloadLevel(payload, SHRINK_MAX)
}

export function buildBriefing(payload, { maxTokens, form = 'briefing' }) {
  // Order of preference: richest PAYLOAD first (the log must stay
  // reconstructable), richest body within that; first candidate that fits wins.
  const bodyOf = (caps) => [
    factsLine(payload),
    ...ladderLines(payload, caps.ladderOpen + 3),
    ...boxLines(payload),
    ...todoLines(payload, caps.todos, caps.evidence),
    ...alertLines(payload, caps.alerts, caps.evidence),
    // The game's hints are never trimmed away: 4 short quoted lines, added after
    // the trimmable body so budgets cut OUR prose first (measured: at 700 tokens
    // the section vanished entirely when it was part of the trimmed body).
    ...gameSaysLines(payload, 4),
    ...healthLines(payload, caps.health),
    'Rules: progress = positive tile state, never absence; every number names its box/elev; NO DATA is labelled; each to-do carries its acceptance test; know-how: skills df-dig / df-stockpile.',
  ].join('\n')
  for (let level = 0; level <= SHRINK_MAX; level += 1) {
    const data = shrinkPayloadLevel(payload, level)
    const stateLine = `${PAYLOAD_PREFIX}${JSON.stringify({ form, tick: payload.tick, data })}`
    const payloadTokens = estimateTokens(stateLine)
    if (payloadTokens > maxTokens - 40 && level < SHRINK_MAX) continue
    // Trim ONLY as much as the budget needs: start at body=0 (full prose) and
    // increase. Measured regression: running 7 -> 0 returned the most trimmed
    // variant whenever it fit, so `ALERTS: 1 active` lost its evidence lines at a
    // 1,200-token budget.
    for (let body = 0; body <= 7; body += 1) {
      const caps = {
        ladderOpen: Math.max(0, 5 - body),
        todos: Math.max(1, 12 - 2 * body),
        alerts: Math.max(0, 8 - 2 * body),
        health: Math.max(0, 6 - body),
        reports: Math.max(0, 4 - body),
        evidence: Math.max(40, 160 - 15 * body),
      }
      const text = bodyOf(caps)
      const out = `${text}\n${stateLine}`
      const tokens = estimateTokens(out)
      if (tokens <= maxTokens) return { text: out, tokens, payloadLevel: level }
    }
  }
  // Last resort: leanest payload, header-only body (labelled as budget-trimmed).
  const data = shrinkPayloadLevel(payload, SHRINK_MAX)
  const stateLine = `${PAYLOAD_PREFIX}${JSON.stringify({ form, tick: payload.tick, data })}`
  const out = `FORT BRIEFING (budget-trimmed) tick ${payload.tick}\n${stateLine}`
  return { text: out, tokens: estimateTokens(out), payloadLevel: SHRINK_MAX, overBudget: estimateTokens(out) > maxTokens }
}

function boxLines(payload) {
  const boxes = (payload.boxes ?? []).map((b) =>
    `  elev${b.elev} x${b.box[0]}-${b.box[2]} y${b.box[1]}-${b.box[3]}: to_dig ${b.to_dig} | dug ${b.dug} | pending ${b.pending}`
    + (b.water ? ` | water ${b.water}` : '') + (b.building ? ` | building ${b.building}` : ''))
  return boxes.length
    ? ['DIG (tile categories; complete = to_dig 0 && dug >0):', ...boxes]
    : ['DIG: no monitored boxes answered this tick (no data, not a fact)']
}

/** The wake message for `agent.followup()` - an alert, not a status dump. */
export function buildWake(alert, payload, { maxTokens }) {
  const header = `DF-OBSERVER ALERT ${alert.id} (${alert.kind})${alert.coord ? ` at ${alert.coord}` : ''}:`
  const lines = [
    header,
    alert.evidence,
    alert.suggested ? `-> ${alert.suggested}` : '',
    `Full state: df_status({scope:"summary"}). To silence repeats: df_alerts({action:"ack", id:"${alert.id}"}).`,
  ].filter(Boolean)
  const text = lines.join('\n')
  for (let level = 0; level <= SHRINK_MAX; level += 1) {
    const data = shrinkPayloadLevel(payload, level)
    const stateLine = `${PAYLOAD_PREFIX}${JSON.stringify({ form: 'alert', tick: payload.tick, data })}`
    const out = `${text}\n${stateLine}`
    const tokens = estimateTokens(out)
    if (tokens <= maxTokens) return { text: out, tokens }
    if (level === SHRINK_MAX) {
      const minimal = { form: 'alert', tick: payload.tick,
        data: { tick: payload.tick, online: payload.online, paused: payload.paused,
          alerts: [{ id: alert.id, kind: alert.kind, coord: alert.coord, evidence: String(alert.evidence).slice(0, 120), acked: false }],
          todos: (payload.todos ?? []).slice(0, 6).map((t) => ({ id: t.id, state: t.state })) } }
      const out2 = `${lines.slice(0, 2).join('\n')}\n${PAYLOAD_PREFIX}${JSON.stringify(minimal)}`
      return { text: out2, tokens: estimateTokens(out2), overBudget: estimateTokens(out2) > maxTokens }
    }
  }
  return { text, tokens: estimateTokens(text) }
}

/** Standing reading rules for `ctx.systemPrompt.section()`. */
export const STANDING_RULES = [
  'DF observer: this session is watched through the Dwarf Fortress MCP server by the df-observer plugin.',
  'A FORT BRIEFING arrives unprompted as an injected user message; it is state, not a request - act on it only when it concerns your current task.',
  'Read it by these rules: every dig number is a tile-category count (to_dig = rock+hidden, dug = floor+stairs+ramp) for the named box at the named IN-GAME Elevation; completion is only ever claimed from to_dig==0 AND dug>0, never from absence, painted glyphs, truncation, or a designation count.',
  'Lines labelled NO DATA / not computed mean the read produced nothing: say so, never infer from silence. Elevations in your own writing must stay in-game panel numbers (z = elev + 129).',
  'Designations go on ROCK tiles only: survey first, never a whole box - DEFAULT_DIG over floors, stairs, ramps, buildings or items destroys them (measured: two stairs lost that way). A dug count that DECREASES raises the tile_state_lost alert: rebuild, then designate rock only.',
  'Each to-do lists its acceptance test; the plugin runs it against MCP reads and quotes the evidence when a status flips - cite the evidence, not the flip.',
  'For how-to, load the skills: df-dig (tunnels, depth, damp/hot, shafts, proof of a dig) and df-stockpile (piles, categories, hauling proof). The observer carries state, not doctrine.',
  'Deeper queries: df_status (scope summary|boxes|piles|shafts|ladder|health|full), df_todo, df_alerts. The plugin is read-only towards the game; with Config.pause_authority=off it never pauses anything.',
].join('\n')

/**
 * The rules section is paid for on EVERY assembly, so it is Config-tunable:
 *   full    - the doctrine text below (what the plugin guarantees + how to read);
 *   compact - only what a briefing line cannot say (default);
 *   off     - nothing (the briefing body still carries its reading rules).
 */
export const STANDING_RULES_COMPACT = [
  'DF observer (Dwarf Fortress MCP): a FORT BRIEFING is injected state, not a request - act on it only when it concerns your task.',
  'Dig numbers are tile categories at a named box/IN-GAME elevation; complete = to_dig==0 AND dug>0; NO DATA means the read produced nothing - never infer from silence.',
  'Designate rock tiles only (survey first) - over floors/stairs/items it destroys them; a dug count dropping = tile_state_lost. To-dos carry acceptance tests run against live MCP reads: cite the quoted evidence. Details: df_status / df_todo / df_alerts. The plugin is read-only towards the game.',
].join('\n')

export function standingRules(mode = 'compact') {
  if (mode === 'off') return ''
  return mode === 'full' ? STANDING_RULES : STANDING_RULES_COMPACT
}
