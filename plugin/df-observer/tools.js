/**
 * df-observer - the pull tools for depth: df_status, df_todo, df_alerts.
 *
 * Definitions follow the tool-design rules: bounded outputs with ids to fetch
 * more, parameter rules on the parameter, behaviour not implementation, and no
 * duplication of the standing reading rules (those live in the system-prompt
 * section, once).
 *
 * Ledger mutations (add/done) and acks return the complete post-change state as
 * a payload line, so the session log carries every status flip with its evidence
 * and the projection can refold it.
 *
 * @module @local/df-observer/tools
 */

import { buildBriefing } from './brief.js'
import { PAYLOAD_PREFIX, runAcceptance } from './model.js'

const num = (v, d = 0) => (Number.isFinite(Number(v)) ? Number(v) : d)

function sessionOf(exec) {
  return exec?.agent?.session
}

/** Lossless-JSON guard: undefined -> null, non-finite numbers -> null (labelled
 * by key), functions dropped. The Host rejects non-lossless tool output. */
export function jsonSafe(value, depth = 0) {
  if (depth > 8) return null
  if (value === undefined) return null
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'bigint') return null
  if (typeof value === 'function' || typeof value === 'symbol') return undefined
  if (Array.isArray(value)) return value.map((item) => jsonSafe(item, depth + 1))
  if (value && typeof value === 'object') {
    const out = {}
    for (const key of Object.keys(value)) {
      const safe = jsonSafe(value[key], depth + 1)
      if (safe !== undefined) out[key] = safe
    }
    return out
  }
  return value
}

export function toolDefinitions({ model, projection, config, sessionFilter }) {
  // The tools answer from the LIVE model (the observer's MCP-read memory - a
  // derived cache, per practices.md; the log it derives from is the briefings).
  // The session's folded tick is reported beside the answer so thin folds are
  // visible, not silent: `logTick`.
  const stateOf = (exec) => model.snapshot()
  const logTickOf = (exec) => {
    if (!(projection && sessionFilter(exec))) return null
    try { return projection.stateOf(sessionOf(exec))?.tick ?? 0 } catch { return null }
  }
  const payloadLine = (state, form) => `${PAYLOAD_PREFIX}${JSON.stringify({ form, tick: state.tick, data: state })}`

  const status = {
    name: 'df_status',
    description: [
      'Read the observer\'s current fort state: ladder, per-box dig progress by tile categories, pile health,',
      'monitored shaft columns, hazards, crew facts, alerts, the to-do ledger, and game/session health.',
      'The plugin polls the game continuously and injects a briefing when meaningful state changes; use this',
      'tool for the detail the briefing keeps short. Numbers carry their box/elevation (in-game panel numbers);',
      'a field labelled NO DATA means the read produced nothing - never infer from silence.',
    ].join(' '),
    parameters: {
      type: 'object',
      properties: {
        scope: {
          type: 'string',
          enum: ['summary', 'boxes', 'piles', 'shafts', 'ladder', 'health', 'full'],
          description: 'Which slice to return. summary: one bounded digest (default). boxes: every monitored box with to_dig/dug/pending and the last dig events. piles: watched pile ids with extents, accepted categories, container limits, on-pile counts. shafts: monitored columns with aquifer/water/magma elevations. ladder: rungs with keys/why and the NEXT suggestion. health: pause state + attribution, generation, offline window, crash-log file names/sizes. full: everything, capped. Use a named scope instead of full when you can.',
        },
      },
      required: [],
      additionalProperties: false,
    },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (args, value) => [{ type: 'text', text: typeof value?.text === 'string' ? value.text : JSON.stringify(value, null, 1) }],
    },
    async execute(args, exec) {
      const state = stateOf(exec)
      const logTick = logTickOf(exec)
      const scope = args.scope ?? 'summary'
      const body = statusBody(state, scope, config)
      const foldLine = logTick == null ? '' : `\n[session log folded at tick ${logTick}${logTick < state.tick ? ` - older than this read (tick ${state.tick}): the live model above is authoritative for a live game; the log keeps what this session was told]` : ''}`
      return jsonSafe({ scope, tick: state.tick, logTick, ...body, text: `${body.text ?? ''}${foldLine}` })
    },
  }

  const todo = {
    name: 'df_todo',
    description: [
      'The to-do ledger with plugin-run acceptance tests. The plugin seeds it from the quickstart ladder and',
      'auto-creates the basics (piles, meeting area, farm plot, doors...) from ladder gaps, then runs each test',
      'against live MCP reads on its poll cycle: a status flip is reported with the evidence quoted. Add your',
      'own tasks so the plugin owns their verification; close them with evidence when you prove them yourself.',
    ].join(' '),
    parameters: {
      type: 'object',
      properties: {
        action: {
          type: 'string',
          enum: ['list', 'add', 'done', 'why'],
          description: 'list: the ledger (bounded). add: register a task the plugin will verify. done: close task `id` - provide the evidence you proved. why: the evidence trail of task `id` (acceptance spec, last check, status flips).',
        },
        title: { type: 'string', description: 'For add: short task name. Required with add.' },
        accept: {
          type: 'object',
          additionalProperties: true,
          description: 'For add: the acceptance test the plugin runs. kind: tiles_progress (params elev,x0,y0,x1,y1,min_dug; passes at to_dig==0 && dug>=min_dug in that box - it must be a monitored box, else the test reports NO DATA) | pile_category (category,min_items) | pile_categories (categories[]) | pile_general | rung (rung: ladder rung id) | buildings_kw (keyword,min) | crew_picks | manual (no plugin check). Omit for manual.',
        },
        box: {
          type: 'array', items: { type: 'number' },
          description: 'For add with tiles_progress: [x0,y0,x1,y1,elev] in in-game elevations - the plugin watches this box in its survey list from now on.',
        },
        id: { type: 'number', description: 'For done/why: the task id from list.' },
        evidence: { type: 'string', description: 'For done: the proof you measured (tile counts, building ids, probe output). A close without evidence is labelled as the model\'s own claim.' },
      },
      required: ['action'],
      additionalProperties: false,
    },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (args, value) => [{ type: 'text', text: typeof value?.text === 'string' ? value.text : JSON.stringify(value, null, 1) }],
    },
    async execute(args, exec) {
      const action = args.action
      if (action === 'list') {
        const state = stateOf(exec)
        const lines = (state.todos ?? []).map((t) =>
          `#${t.id} [${t.state}] ${t.origin}: ${t.title} | accept: ${t.accept?.kind ?? 'manual'} | ${short(t.evidence)}`)
        return { action, count: lines.length, text: lines.join('\n') || 'ledger empty' }
      }
      if (action === 'add') {
        if (!args.title) return { action, error: 'add needs title' }
        const todoItem = model.addTodo({ title: args.title, accept: args.accept, box: args.box })
        if (Array.isArray(args.box) && args.box.length === 5) model.watchBox(args.box)
        const state = model.snapshot()
        const text = `#${todoItem.id} added: ${todoItem.title} | accept: ${JSON.stringify(todoItem.accept)} | first check: ${short(runAcceptance(todoItem, state).evidence)}`
        return { action, id: todoItem.id, text, payload: payloadLine(state, 'tool') }
      }
      if (action === 'done') {
        const closed = model.closeTodo(args.id, args.evidence)
        if (!closed) return { action, error: `no task id ${args.id}` }
        const state = model.snapshot()
        return { action, id: closed.id, text: `#${closed.id} closed: ${short(closed.evidence)}`, payload: payloadLine(state, 'tool') }
      }
      if (action === 'why') {
        const state = stateOf(exec)
        const item = (state.todos ?? []).find((t) => t.id === Number(args.id))
        if (!item) return { action, error: `no task id ${args.id}` }
        const flips = (state.digEvents ?? []).filter((e) => e.kind === 'todo_done' && e.todo === item.id)
        return {
          action, id: item.id,
          text: [`#${item.id} ${item.title}`, `state: ${item.state} (since tick ${item.statusTick})`,
            `accept: ${JSON.stringify(item.accept)}`, `evidence: ${item.evidence}`,
            `last check: ${item.lastCheck ? `tick ${item.lastCheck.tick} ${item.lastCheck.ok ? 'PASS' : 'fail'}: ${short(item.lastCheck.evidence)}` : 'none'}`,
            `flips: ${flips.map((f) => `tick ${f.tick}: ${short(f.evidence)}`).join('\n         ') || 'none'}`].join('\n'),
        }
      }
      return { action, error: `unknown action ${action}` }
    },
  }

  const alerts = {
    name: 'df_alerts',
    description: [
      'The observer\'s alert feed: what changed, the evidence, the coordinates, the suggested action. Ack a',
      'quiet alert to silence its repeats until the condition clears; history covers what fired before.',
    ].join(' '),
    parameters: {
      type: 'object',
      properties: {
        action: {
          type: 'string',
          enum: ['list', 'ack', 'history'],
          description: 'list: active alerts (bounded, newest first). ack: silence repeats of alert `id` until its condition clears. history: the last fired alerts with their evidence.',
        },
        id: { type: 'string', description: 'For ack: the alert id from list (e.g. "A12").' },
        limit: { type: 'number', description: 'For list/history: max rows (default 12, max 40).' },
      },
      required: ['action'],
      additionalProperties: false,
    },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (args, value) => [{ type: 'text', text: typeof value?.text === 'string' ? value.text : JSON.stringify(value, null, 1) }],
    },
    async execute(args, exec) {
      const limit = Math.min(num(args.limit, 12), 40)
      if (args.action === 'list') {
        const state = stateOf(exec)
        const rows = (state.alerts ?? []).filter((a) => !a.cleared).slice(0, limit).map((a) =>
          `${a.id} ${a.kind}${a.coord ? ` [${a.coord}]` : ''} x${a.count}${a.acked ? ' (acked)' : ''}: ${short(a.evidence)}`)
        return { action: 'list', count: rows.length, text: rows.join('\n') || 'no active alerts' }
      }
      if (args.action === 'ack') {
        const acked = model.ackAlert(args.id)
        if (!acked) return { action: 'ack', error: `no alert id ${args.id}` }
        const state = model.snapshot()
        return { action: 'ack', id: acked.id, text: `alert ${acked.id} (${acked.kind}) acknowledged: repeats silenced until the condition clears`, payload: payloadLine(state, 'tool') }
      }
      if (args.action === 'history') {
        const state = stateOf(exec)
        const rows = (state.alertHistory ?? []).slice(0, limit).map((h) =>
          `${h.id} tick ${h.tick} ${h.kind}: ${short(h.evidence)}`)
        return { action: 'history', count: rows.length, text: rows.join('\n') || 'no alert history' }
      }
      return { action: args.action, error: `unknown action ${args.action}` }
    },
  }

  // Every tool result passes the lossless-JSON guard at the boundary - the
  // Host rejects non-lossless output (measured: df_status scope=health).
  return [status, todo, alerts].map((def) => ({
    ...def,
    async execute(args, exec) { return jsonSafe(await def.execute(args, exec)) },
  }))
}

function short(text, max = 180) {
  const s = String(text ?? '')
  return s.length <= max ? s : `${s.slice(0, max - 1)}…`
}

function statusBody(state, scope, config) {
  const facts = state.facts
  const digest = {
    tick: state.tick, online: state.online, paused: state.paused, pauseSource: state.pauseSource,
    generation: state.generation,
  }
  if (scope === 'summary') {
    const lines = [
      `tick ${state.tick} | ${state.online === false ? 'GAME OFFLINE' : state.paused ? 'paused' : 'running'}${state.pauseSource ? ` (last pause: ${state.pauseSource})` : ''} | gen ${state.generation ?? '?'}`,
      facts ? `${facts.dwarves == null ? `pop ${num(facts.pop)} (dwarves ?)` : `dwarves ${num(facts.dwarves)} (units ${num(facts.pop)})`} miners ${num(facts.miners)}/${facts.picks == null ? '? (deep probe has no data)' : num(facts.picks)} picks | food ${num(facts.food)} drink ${num(facts.drink)} (ON WATCHED PILES, not the game's fort-wide Food/Drink bars) wood ${num(facts.wood)} stone ${num(facts.stone)} | pending dig ${num(facts.pending_dig)}`
        : 'facts: NO DATA this tick',
      ...(state.boxes ?? []).map((b) => `elev${b.elev} x${b.box[0]}-${b.box[2]} y${b.box[1]}-${b.box[3]}: to_dig ${b.to_dig} dug ${b.dug} pending ${b.pending}`),
      ...(state.rungs ?? []).filter((r) => !r.done).slice(0, 6).map((r) => `[ ] ${r.id}: ${short(r.title, 90)}`),
      ...(state.alerts ?? []).filter((a) => !a.acked && !a.cleared).slice(0, 6).map((a) => `!${a.id} ${a.kind}${a.coord ? ` [${a.coord}]` : ''}: ${short(a.evidence, 120)}`),
      ...(state.todos ?? []).filter((t) => t.state === 'open').slice(0, 8).map((t) => `#${t.id} ${short(t.title, 90)} (${acceptKind(t)})`),
      ...(state.notes ?? []).slice(0, 4).map((n) => `NOTE: ${short(n, 120)}`),
    ]
    return { text: lines.join('\n'), digest }
  }
  if (scope === 'boxes') {
    return { boxes: state.boxes, digEvents: (state.digEvents ?? []).slice(0, 20), digest }
  }
  if (scope === 'piles') {
    return { piles: state.piles, digest }
  }
  if (scope === 'shafts') {
    return { shafts: state.shafts, warnings: state.warnings, digest }
  }
  if (scope === 'ladder') {
    return { rungs: state.rungs, next: state.next, digest }
  }
  if (scope === 'health') {
    const lines = [
      `tick ${state.tick} | ${state.online === false ? 'GAME OFFLINE' : state.paused ? 'PAUSED' : 'running'}`
      + ` | gen ${state.generation ?? '?'}${state.pauseSource ? ` | last pause: ${state.pauseSource}${state.pauseAtTick != null ? ` @tick ${state.pauseAtTick}` : ''}` : ''}`,
      ...(state.pauseEvents ?? []).slice(0, 8).map((e) => `  pause event @tick ${e.tick}: ${e.paused ? 'paused' : 'resumed'} (${e.source ?? 'unknown'})`),
      ...((state.notes ?? []).slice(0, 8).map((n) => `NOTE: ${short(String(n), 160)}`)),
      ...(state.crashlog?.dir ? (state.crashlog.files ?? []).slice(0, 5).map((f) => `crashlog ${String(f.name)} ${f.bytes}B`) : ['crash log dir: not configured (Config.game_dir)']),
    ]
    return {
      paused: state.paused, pauseSource: state.pauseSource, pauseAtTick: state.pauseAtTick,
      pauseEvents: (state.pauseEvents ?? []).slice(0, 10),
      generation: state.generation, online: state.online,
      crashlog: state.crashlog, notes: state.notes, digest,
      text: lines.join('\n'),
    }
  }
  const brief = buildBriefing(state, { maxTokens: config.briefing_max_tokens })
  return { text: brief.text, tokens: brief.tokens, digest }
}

function acceptKind(todo) {
  return todo.accept?.kind ?? 'manual'
}
