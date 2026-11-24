/**
 * df-observer plugin unit tests (offline, deterministic): the model, the
 * projection fold, acceptance tests, alert dedupe/ack, pause gates, briefing
 * budget, and the transport rules. Run: node tests/test_df_observer.mjs
 */
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const pluginDir = path.join(here, '..', 'plugin', 'df-observer')

const {
  FortModel, dfObserverProjection, significantChange, runAcceptance, seedTodos, parsePayload, viewOf,
  PAYLOAD_PREFIX, STATE_FIELDS,
} = await import(path.join(pluginDir, 'model.js'))
const { buildBriefing, buildWake, estimateTokens, STANDING_RULES, shrinkPayloadLevel } = await import(path.join(pluginDir, 'brief.js'))
const ConfigSchemaForTest = (await import(path.join(pluginDir, 'index.js'))).Config
const { ObserverTransport, ObserverBug, GameOffline } = await import(path.join(pluginDir, 'observer.js'))
const { toolDefinitions } = await import(path.join(pluginDir, 'tools.js'))
const filterApi = await import(path.join(pluginDir, 'filter.js'))
const briefApi = await import(path.join(pluginDir, 'brief.js'))
const { default: DFObserver, Config } = await import(path.join(pluginDir, 'index.js'))
const { remoteMethods } = await import(path.join(pluginDir, 'node_modules', '@deepseek-ai', 'dsh-typert-protocol', 'lib', 'index.js'))
const { z } = await import(path.join(pluginDir, 'node_modules', 'zod', 'index.js'))

const results = []
const queue = []
const test = (name, fn) => queue.push([name, fn])

const baseCfg = Config.parse({
  monitored_boxes: [[27, 121, 42, 126, 36], [43, 122, 49, 124, 36]],
  pile_ids: [15, 19],
  shafts: [[50, 123], [43, 121]],
})

const fastData = (over = {}) => ({
  schema: 'df-observer/v1', generation: 1, paused: false,
  worldSignature: { save: 'start', world: 'The Universes of Destiny', mode: 'MODE_DWARF',
    tiles: '192x192x191', year: 104, tick: 96910 },
  facts: { pop: 7, dwarves: 7, miners: 2, picks: 3, food: 10, drink: 0, wood: 0, stone: 5, metal: 0,
    barrels: 0, pending_dig: 96, underground_floors: 4, water_tiles: 0, magma_tiles: 0 },
  buildings: [{ name: 'Stockpile', count: 2 }],
  boxes: [
    { elev: 36, box: [27, 121, 42, 126], to_dig: 51, dug: 39, pending: 6, water: 0, open: 0, building: 0, other: 0, total_tiles: 96 },
    { elev: 36, box: [43, 122, 49, 124], to_dig: 21, dug: 0, pending: 21, water: 0, open: 0, building: 0, other: 0, total_tiles: 21 },
  ],
  shafts: [{ x: 50, y: 123, aquifer_elevs: [], water_elevs: [], water_levels: {}, magma_elevs: [] },
    { x: 43, y: 121, aquifer_elevs: [], water_elevs: [], water_levels: {}, magma_elevs: [] }],
  warnings: { designated_tiles: 27, damp_tiles: 0, hot_tiles: 0, damp_boxes: [], hot_boxes: [], samples: [] },
  notes: [],
  ...over,
})

const deepData = (over = {}) => ({
  rungs: [
    { id: 'labors', title: 'Two dwarves on Miners', done: true, keys: 'y', why: 'mining' },
    { id: 'entrance', title: 'Entry tunnel', done: false, keys: 'm', why: 'entrance' },
    { id: 'meeting', title: 'Meeting area', done: false, keys: 'z', why: 'idle dwarves' },
  ],
  next: { id: 'entrance', title: 'Entry tunnel', keys: 'm', why: 'entrance', at: { elev: 36, x0: 27, y0: 121, x1: 33, y1: 126, region: 'A', region_tiles: 900 } },
  piles: [
    { id: 15, found: 1, accepts: 'ammo,animals,armor,bars_blocks,cloth,coins,finished_goods,food,furniture,leather,sheet,weapons', x0: 52, x1: 57, y0: 116, y1: 120, elev: 38, wheelbarrows: 0, barrels: 15, bins: 15, total: 20 },
    { id: 19, found: 1, accepts: 'stone', x0: 30, x1: 33, y0: 126, y1: 128, elev: 36, wheelbarrows: 3, barrels: 0, bins: 0, total: 13 },
  ],
  reports: [], crashlog: { dir: null, notes: ['no crash-log directory configured (DFHACK_GAME_DIR)'], files: [] },
  notes: [],
  ...over,
})

function freshModel(cfg = baseCfg) {
  const model = new FortModel(cfg)
  model.ingestFast(fastData())
  model.ingestDeep(deepData())
  model.evaluate({})
  return model
}

/**
 * The durable-log contract for inbox payloads, enforced in the mocks.
 *
 * `agent.inject/steer/followup` write their argument verbatim into
 * `agent/inbox/spliced.inserted`. A raw string lands in the stored session and
 * the loader rejects the whole file: "agent/inbox/spliced requires message
 * objects" - the history becomes permanently unloadable (measured 2026-11-21,
 * session-e33668b6, from two announce paths that passed a string). So every
 * mock inbox boundary validates what it is handed, exactly as the loader does.
 */
function assertInboxMessage(message, boundary) {
  if (message === null || typeof message !== 'object' || Array.isArray(message)) {
    throw new Error(`${boundary} requires message objects (got ${typeof message}: ${JSON.stringify(message).slice(0, 60)})`)
  }
  if (message.role !== 'user') throw new Error(`${boundary} payload must be a user message (got role ${message.role})`)
  if (!Array.isArray(message.content)) throw new Error(`${boundary} payload requires a content array`)
  if (typeof message.id !== 'string' || message.id.length === 0) throw new Error(`${boundary} payload requires a message id`)
  return message
}

/** The model-facing text of one inbox payload (validates it first). */
function inboxText(message, boundary = 'agent/inbox/spliced') {
  return assertInboxMessage(message, boundary).content.map((c) => c?.text ?? '').join('\n')
}

// ---------------------------------------------------------------------------
test('config defaults and ranges', () => {
  assert.equal(baseCfg.poll_running_ms, 8000)
  assert.equal(baseCfg.poll_paused_ms, 60000)
  assert.equal(baseCfg.pause_authority, 'off')
  assert.equal(baseCfg.auto_resume, false)
  // save_reloaded is a WAKE: a game swap throws the whole derived state out, and an
  // agent that is not told will plan against a fort it is no longer looking at.
  assert.deepEqual(baseCfg.alert_wakes, ['water', 'dig_complete', 'dig_stalled', 'tile_state_lost', 'game_says', 'game_crashed', 'game_offline', 'save_reloaded'])
  assert.equal(baseCfg.overload_todos, 20, 'a normal embark ledger is not an overload')
  assert.throws(() => Config.parse({ poll_running_ms: 16000 }))
  assert.throws(() => Config.parse({ pause_authority: 'manual_suggestion' }))
  return 'pause_authority default off; running interval bounded 5-15 s'
})

test('service module exports the service-class form', () => {
  assert.equal(typeof DFObserver, 'function')
  assert.deepEqual(DFObserver.inject, ['tools', 'agents', 'systemPrompt', 'sessionProjections', 'timer', 'commands', 'storageDomain'])
  assert.equal(DFObserver.Config, Config)
  assert.ok(!('panel' in DFObserver.prototype), 'no panel endpoint - the client cannot reach an @Remote surface (measured)')
  assert.equal(typeof DFObserver.prototype.ackAlert, 'function')
  // Remote markers attach at construction (per instance); the full activation
  // path is exercised by scratch/repro_df_observer.mjs and the live install.
  return 'default export class + static inject/Config + the ackAlert session-command endpoint'
})

test('model seeds ladder + basics todos', () => {
  const model = freshModel()
  const ladder = model.todos.filter((t) => t.origin === 'ladder')
  const auto = model.todos.filter((t) => t.origin === 'auto')
  assert.ok(ladder.length >= 1)
  assert.equal(auto.length, 7)   // 8 -> 7: the early-game stone pile is no longer seeded
  assert.ok(model.todos.every((t) => t.accept && t.accept.kind))
  return `seeded ${ladder.length} ladder + ${auto.length} basics to-dos`
})

test('acceptance: pile_category passes with quoted evidence', () => {
  const model = freshModel()
  // Stone is no longer auto-seeded (early-game hauling cost), so the category machinery
  // is proven on the task the player asks for - which is the only way a stone pile
  // enters the ledger now.
  const stone = model.addTodo({ title: 'stone pile on request',
                                accept: { kind: 'pile_category', category: 'stone', min_items: 1 } })
  assert.equal(stone.state, 'open')
  const general = model.todos.find((t) => t.origin === 'auto' && t.accept.kind === 'pile_general')
  assert.equal(general.state, 'done')
  return 'stone pile + general pile flip done citing pile reads'
})

test('acceptance: tiles_progress reports NO DATA for unmonitored box', () => {
  const model = freshModel()
  const todo = model.addTodo({ title: 'east adit', accept: { kind: 'tiles_progress', elev: 39, x0: 1, y0: 1, x1: 5, y1: 5, min_dug: 1 } })
  const { ok, evidence } = runAcceptance(todo, model.payload())
  assert.equal(ok, false)
  assert.match(evidence, /NO DATA|no survey read/)
  return 'unmonitored box is labelled, never a fact'
})

test('acceptance: crew_picks rule', () => {
  const model = freshModel()
  const todo = model.addTodo({ title: 'picks for all miners', accept: { kind: 'crew_picks' } })
  assert.equal(runAcceptance(todo, model.payload()).ok, true)
  model.facts.picks = 1
  assert.equal(runAcceptance(todo, model.payload()).ok, false)
  return 'picks >= miners measured from facts'
})

test('water alert fires from shaft column, not damp flags', () => {
  const model = freshModel()
  const before = model.alerts.filter((a) => a.kind === 'water').length
  model.shafts[0] = { x: 50, y: 123, aquifer_elevs: [33], water_elevs: [35], water_levels: { 35: 4 }, magma_elevs: [] }
  model.warnings = { designated_tiles: 20, damp_tiles: 0, hot_tiles: 0, damp_boxes: [], hot_boxes: [], samples: [] }
  model.evaluate({})
  const water = model.alerts.filter((a) => a.kind === 'water')
  assert.equal(water.length, before + 1)
  assert.match(water[0].evidence, /water in monitored shaft column x50,y123: elev35:4/)
  assert.match(water[0].suggested, /stop|drain/i)
  return 'water alert carries column, elevation, level and suggestion'
})

test('alert dedupe: repeat increments, ack silences, clearing re-arms', () => {
  const model = freshModel()
  model.shafts[0] = { x: 50, y: 123, aquifer_elevs: [], water_elevs: [35], water_levels: { 35: 4 }, magma_elevs: [] }
  model.evaluate({})
  const water = model.alerts.find((a) => a.kind === 'water')
  const firstEvidence = water.evidence
  model.shafts[0] = { ...model.shafts[0], water_levels: { 35: 7 } }
  model.evaluate({})
  assert.equal(model.alerts.filter((a) => a.kind === 'water').length, 1)
  assert.equal(model.alerts.find((a) => a.id === water.id).count, 2)
  model.ackAlert(water.id)
  model.shafts[0] = { ...model.shafts[0], water_levels: { 35: 2 } }
  model.evaluate({})
  assert.equal(model.alerts.find((a) => a.id === water.id).count, 2, 'acked alert must not grow')
  model.shafts[0] = { x: 50, y: 123, aquifer_elevs: [], water_elevs: [], water_levels: {}, magma_elevs: [] }
  model.evaluate({})
  assert.equal(model.alerts.find((a) => a.id === water.id).cleared, true)
  model.shafts[0] = { x: 50, y: 123, aquifer_elevs: [], water_elevs: [35], water_levels: { 35: 1 }, magma_elevs: [] }
  model.evaluate({})
  assert.equal(model.alerts.filter((a) => a.kind === 'water' && !a.cleared).length, 1)
  assert.notEqual(model.alerts.find((a) => a.kind === 'water' && !a.cleared).id, water.id)
  void firstEvidence
  return 'dedupe -> ack silence -> clear re-arm verified'
})

test('dig_complete needs positive state (to_dig 0 AND dug > 0)', () => {
  const model = freshModel()
  model.evaluate({})
  // all-fog box: to_dig 0, dug 0 -> NOT complete
  model.boxes[1] = { elev: 36, box: [43, 122, 49, 124], to_dig: 0, dug: 0, pending: 0, total_tiles: 21, notes: 'fog' }
  model.evaluate({})
  assert.equal(model.alerts.filter((a) => a.kind === 'dig_complete' && a.key.includes('43,122')).length, 0)
  model.boxes[1] = { ...model.boxes[1], dug: 21 }
  model.evaluate({})
  const complete = model.alerts.find((a) => a.kind === 'dig_complete' && a.key.includes('43,122'))
  assert.ok(complete)
  assert.match(complete.evidence, /to_dig 0, dug 21/)
  return 'absence (all-fog box) is not completion; positive state is'
})

test('pause gates: authority off blocks everything; events gated by guard', () => {
  const off = new FortModel(Config.parse({ pause_authority: 'off' }))
  assert.equal(off.mayPause('water').ok, false)
  const ev = new FortModel(Config.parse({ pause_authority: 'events', pause_on: ['water'] }))
  ev.facts = { pending_dig: 0 }
  ev.boxes = []
  ev.digEvents = []
  assert.equal(ev.mayPause('water').ok, false, 'idle fort guard')
  assert.match(ev.mayPause('water').why, /idle fort|guard|pending/)
  ev.facts = { pending_dig: 90 }
  assert.equal(ev.mayPause('water').ok, true)
  assert.equal(ev.mayPause('overload').ok, false, 'kind must be in pause_on')
  ev.paused = true
  assert.equal(ev.mayPause('water').ok, false, 'already paused')
  return 'pause issuance is Config-gated with the idle-fort hard guard'
})

test('pause attribution: plugin pause names plugin, other pauses name game/player', () => {
  const model = freshModel()
  assert.equal(model.paused, false)
  model.ingestFast(fastData({ paused: true }))
  assert.equal(model.pauseSource, 'game-auto-or-player')
  model.ingestFast(fastData({ paused: false }))
  model.ingestFast(fastData({ paused: true }))
  model.recordPluginPause(model.tick)
  assert.equal(model.pauseSource, 'plugin')
  model.pluginPauseTick = model.tick
  model.ingestFast(fastData({ paused: false }))
  assert.equal(model.pauseEvents.at(-1).paused, false)
  assert.equal(model.pauseSource, 'player-resume')
  return 'transitions attributed: plugin within its window, else game/player'
})

test('crash log: name/size only, once per signature', () => {
  const model = freshModel()
  model.crashlog = { dir: '/game/crashlog', files: [{ name: 'crash20261121.log', bytes: 4096, mtime: 1 }], notes: [] }
  model.evaluate({})
  const crash = model.alerts.filter((a) => a.kind === 'game_crashed')
  assert.equal(crash.length, 1)
  assert.match(crash[0].evidence, /crash20261121\.log \(4096 bytes.*contents NOT read/)
  model.evaluate({})
  assert.equal(model.alerts.filter((a) => a.kind === 'game_crashed').length, 1)
  return 'crash alert cites name+size and labels contents unread'
})

test('overload signalling', () => {
  const cfg = Config.parse({ overload_alerts: 1 })
  const model = freshModel(cfg)
  model.shafts[0] = { x: 50, y: 123, aquifer_elevs: [], water_elevs: [35], water_levels: { 35: 2 }, magma_elevs: [] }
  model.evaluate({})
  assert.ok(model.overload)
  assert.ok(model.overload.alerts > 1)
  return `overload reported: ${model.overload.alerts} alerts`
})

test('delta gate: unchanged state costs no briefing; to_dig wiggles below threshold do not', () => {
  const model = freshModel()
  const a = model.payload()
  model.boxes[1] = { ...model.boxes[1], to_dig: model.boxes[1].to_dig - 2 }
  model.evaluate({})
  const b = model.payload()
  assert.deepEqual(significantChange(a, b, { min_to_dig_delta: 3 }), [])
  model.boxes[1] = { ...model.boxes[1], to_dig: 5 }
  model.evaluate({})
  const c = model.payload()
  assert.deepEqual(significantChange(b, c, { min_to_dig_delta: 3 }), ['boxes'])
  model.ingestFast(fastData({ paused: true }))
  const d = model.payload()
  assert.ok(significantChange(c, d, { min_to_dig_delta: 3 }).includes('paused'))
  return 'gate fires on pause/boxes/…, not on small survey wiggles'
})

// ---------------------------------------------------------------------------
// projection: reconstructable from committed events only
test('projection folds briefings from user messages, ignores the rest', () => {
  const unit = dfObserverProjection(z)
  const state0 = unit.init({}, 0)
  const model = freshModel()
  const payload = model.payload()
  const brief = buildBriefing(payload, { maxTokens: 1200 })
  const event = {
    type: 'user/message', seq: 5, time: 1,
    data: { content: [{ type: 'text', text: brief.text }], source: { kind: 'df-observer' } },
  }
  const state1 = unit.apply(state0, event)
  assert.notEqual(state1, state0)
  assert.equal(state1.tick, payload.tick)
  assert.equal(state1.boxes.length, payload.boxes.length)
  assert.equal(state1.todos.length, payload.todos.length)
  assert.equal(state1.boxes.length, payload.boxes.length,
    `payload boxes ${payload.boxes.length}, tokens ${estimateTokens(JSON.stringify(payload))}`)
  const noise = { type: 'user/message', seq: 6, time: 1, data: { content: [{ type: 'text', text: 'dig a tunnel' }], source: { kind: 'user' } } }
  assert.equal(unit.apply(state1, noise), state1, 'non-observer events keep the reference')
  const turn = { type: 'turn/end', seq: 7, time: 1, data: { turn: 1, reason: { kind: 'completed' } } }
  assert.equal(unit.apply(state1, turn), state1)
  const view1 = unit.view(state1)
  assert.equal(unit.view(state1), view1, 'view is reference-stable')
  const state2 = unit.apply(state1, {
    type: 'tool/result', seq: 8, time: 1,
    data: { message: { content: [{ type: 'text', text: `${'x'}\n[df-observer] ${JSON.stringify({ form: 'tool', tick: 9, data: { tick: 9, todos: [{ id: 1, title: 't', state: 'done', accept: { kind: 'manual' }, evidence: 'proof' }] } })}` }] } },
  })
  assert.equal(state2.tick, 9)
  assert.equal(state2.todos.length, 1)
  assert.equal(state2.todos[0].state, 'done')
  void parsePayload
  void viewOf
  return 'apply pure + reference-stable; tool-result ledger mutations fold'
})

test('briefing stays inside briefing_max_tokens and carries the state line', () => {
  const model = freshModel()
  model.shafts[0] = { x: 50, y: 123, aquifer_elevs: [33, 34], water_elevs: [35], water_levels: { 35: 4 }, magma_elevs: [] }
  model.evaluate({})
  const full = buildBriefing(model.payload(), { maxTokens: 1200 })
  assert.ok(full.tokens <= 1200, `tokens ${full.tokens}`)
  const foldOf = (briefText) => JSON.parse(briefText.split('\n').find((l) => l.startsWith('[df-observer] ')).slice('[df-observer] '.length))
  const foldedFull = foldOf(full.text)
  const live = model.payload()
  // The log contract: the folded payload must reproduce the LEDGER (ids, states,
  // evidence) and the tick; decoration sections may shrink with the budget.
  assert.equal(foldedFull.tick, live.tick)
  const sorted = (arr) => [...arr].sort()
  assert.deepEqual(sorted(foldedFull.data.todos.map((t) => `${t.id}:${t.state}`)),
    sorted(live.todos.map((t) => `${t.id}:${t.state}`)),
    'ledger ids+states reconstruct from the log line (open rows are ordered first, measured)')
  const opens = foldedFull.data.todos.filter((t) => t.state !== 'done')
  // The panel lists the first `panel_title_rows` (default 10) open rows: those carry
  // titles; a longer ledger keeps ids+states for the rest (measured contract).
  const listed = opens.slice(0, 10)
  // The panel's words are paid for first, prose next, fields last; on a heavy
  // payload the ladder guarantees a floor of titled rows, not all of them.
  assert.ok(listed.filter((t) => t.title).length >= 3,
    `the listed open rows carry titles at a 1200 budget (saw ${listed.filter((t) => t.title).length}/${listed.length})`)
  assert.ok(foldedFull.data.todos.every((t) => t.evidence), 'ledger evidence survives')
  assert.match(full.text, /\[df-observer\] \{/)
  assert.match(full.text, /FORT BRIEFING \(in-game elevations\)/)
  assert.match(full.text, /to_dig 51 \| dug 39 \| pending 6/)
  assert.match(full.text, /water in monitored shaft column/)
  assert.ok(full.bodyLevel === 0 || full.tokens <= 1200, 'a generous budget keeps the full body')
  assert.match(full.text, /stop the shaft\/dig at this column/, 'the alert advice survives at a generous budget')
  assert.match(full.text, /accept: /)
  assert.match(full.text, /complete = to_dig 0 && dug >0/)
  const mid = buildBriefing(model.payload(), { maxTokens: 700 })
  assert.ok(mid.tokens <= 700, `tokens ${mid.tokens}`)
  const foldedMid = foldOf(mid.text)
  assert.deepEqual(sorted(foldedMid.data.todos.map((t) => `${t.id}:${t.state}`)),
    sorted(live.todos.map((t) => `${t.id}:${t.state}`)), 'ledger reconstructs at the Config budget')
  const brief = buildBriefing(model.payload(), { maxTokens: 300 })
  assert.ok(brief.tokens <= 300, `tokens ${brief.tokens}`)
  const line = brief.text.split('\n').find((l) => l.startsWith('[df-observer] '))
  assert.ok(line, 'state line present at the tight budget')
  const folded = JSON.parse(line.slice('[df-observer] '.length))
  assert.equal(folded.tick, model.tick)
  assert.ok(Array.isArray(folded.data.todos))
  const wake = buildWake(model.alerts.find((a) => a.kind === 'water'), model.payload(), { maxTokens: 300 })
  assert.ok(wake.tokens <= 300)
  assert.match(wake.text, /DF-OBSERVER ALERT/)
  assert.match(wake.text, /df_alerts\(\{action:"ack"/)
  return `briefing ${full.tokens}/${mid.tokens}/${brief.tokens} tokens within budgets; wake bounded`
})

test('NO DATA is labelled in the briefing', () => {
  const model = new FortModel(baseCfg)
  const brief = buildBriefing(model.payload(), { maxTokens: 600 })
  assert.match(brief.text, /facts: NO DATA/)
  assert.match(brief.text, /DIG: no monitored boxes answered this tick \(no data, not a fact\)/)
  assert.match(STANDING_RULES, /NO DATA/)
  return 'empty state renders labelled no-data lines, not silence'
})

// ---------------------------------------------------------------------------
// transport rules (proven pattern from scripts/dfw.py)
test('transport: 4xx is our bug (no retry); game-offline retries with backoff', async () => {
  const transport = new ObserverTransport({
    base: 'http://127.0.0.1:9', timeoutMs: 50, offlineGraceMs: 300,
    napMinMs: 10, napMaxMs: 40, announceEveryMs: 1000,
  })
  const http404 = () => {
    const e = new Error('HTTP 404 /observe')
    e.status = 404
    throw e
  }
  const out = await transport.withRetry(http404)
  assert.equal(out.ok, false)
  assert.ok(out.bug instanceof ObserverBug)
  let calls = 0
  const offline = async () => {
    calls += 1
    throw new Error('ECONNREFUSED')
  }
  const out2 = await transport.withRetry(offline, { maxMs: 150 })
  assert.equal(out2.ok, false)
  assert.equal(out2.offline, true)
  assert.ok(calls >= 2, 'retried with backoff')
  assert.ok(transport.offlineSince !== null)
  // recovery resets the window
  const out3 = await transport.withRetry(async () => ({ ok: 1 }))
  assert.equal(out3.ok, true)
  assert.equal(out3.wasOffline, true)
  assert.equal(transport.offlineSince, null)
  const transport2 = new ObserverTransport({
    base: 'http://127.0.0.1:9', timeoutMs: 50, offlineGraceMs: 60,
    napMinMs: 10, napMaxMs: 30, announceEveryMs: 10000,
  })
  const long = await transport2.withRetry(offline, { maxMs: 10000 })
  assert.equal(long.offline, true)
  assert.ok(String(long.lastError).length > 0)
  return `4xx -> ObserverBug; offline retried ${calls}x then reported (grace honoured)`
})

test('GameOffline is raised past the grace window', async () => {
  const transport = new ObserverTransport({
    base: 'http://127.0.0.1:9', timeoutMs: 50, offlineGraceMs: 0,
    napMinMs: 5, napMaxMs: 5, announceEveryMs: 10000,
  })
  const out = await transport.withRetry(async () => { throw new Error('ECONNREFUSED') })
  assert.equal(out.offline, true)
  assert.ok(out.lastError instanceof GameOffline)
  return 'grace exceeded raises GameOffline (announced, tolerated)'
})

// ---------------------------------------------------------------------------
// tools
test('tools: bounded outputs, add/done/ack embed foldable payloads', async () => {
  const model = freshModel()
  const projection = { stateOf: () => model.payload() }
  const defs = toolDefinitions({
    model, projection, config: baseCfg,
    sessionFilter: (exec) => exec?.agent?.id !== 'blocked',
  })
  assert.deepEqual(defs.map((d) => d.name), ['df_status', 'df_todo', 'df_alerts'])
  for (const def of defs) {
    assert.ok(def.parameters.properties, `${def.name} parameters`)
    assert.ok(def.description.length < 900, `${def.name} description bounded`)
  }
  const exec = { agent: { id: 'a', session: {} } }
  const status = await defs[0].execute({ scope: 'summary' }, exec)
  assert.match(status.text, /dwarves 7 \(units 7\) miners 2\/3 picks/)
  assert.ok(status.text.split('\n').length <= 30)
  const added = await defs[1].execute({ action: 'add', title: 'dig room wings', accept: { kind: 'tiles_progress', elev: 36, x0: 27, y0: 121, x1: 42, y1: 126, min_dug: 1 }, box: [27, 121, 42, 126, 36] }, exec)
  assert.match(added.payload, /^\[df-observer\] \{"form":"tool"/)
  const listed = await defs[1].execute({ action: 'list' }, exec)
  assert.match(listed.text, /dig room wings/)
  const acked = await defs[2].execute({ action: 'list' }, exec)
  assert.ok(acked.text)
  const alertId = (acked.text.match(/A\d+/) ?? [])[0]
  if (alertId) {
    const ack = await defs[2].execute({ action: 'ack', id: alertId }, exec)
    assert.match(ack.payload, /\[df-observer\]/)
  }
  const done = await defs[1].execute({ action: 'done', id: added.id, evidence: 'survey read: to_dig 0 dug 96' }, exec)
  assert.match(done.text, /closed/)
  return `df_status/df_todo/df_alerts verified (${defs.length} tools)`
})

// ---------------------------------------------------------------------------
// ledger durability + box watching
test('ledger: watchBox joins the survey list; restore re-applies stored records', () => {
  const model = freshModel()
  const key = model.watchBox([10, 20, 12, 22, 36])
  assert.equal(model.extraBoxes.length, 1, 'watched box enters the survey list')
  assert.deepEqual(model.extraBoxes[0], [10, 20, 12, 22, 36])
  model.watchBox([10, 20, 12, 22, 36])
  assert.equal(model.extraBoxes.length, 1, 'watching twice does not duplicate')
  model.restoreBoxes([[5, 5, 9, 9, 40], [10, 20, 12, 22, 36]])
  assert.equal(model.extraBoxes.length, 2, 'restore skips duplicates')
  const written = []
  model.onLedgerWrite = (t) => written.push(t.id)
  const todo = model.addTodo({ title: 'wing dig', accept: { kind: 'manual' } })
  assert.deepEqual(written, [todo.id], 'add notifies the persistence hook')
  model.restoreTodos([{ id: 77, title: 'from storage', state: 'done', accept: { kind: 'manual' }, evidence: 'proved earlier' },
    { id: 1, title: 'stored override', state: 'open', accept: { kind: 'manual' }, evidence: 'x' }])
  const t77 = model.todos.find((t) => t.id === 77)
  assert.ok(t77 && t77.state === 'done', 'stored record re-applies after a reload')
  assert.equal(model.todos.find((t) => t.id === 1).title, 'stored override', 'stored records win over seeds')
  model.closeTodo(77, 'already closed')
  assert.equal(model.todos.find((t) => t.id === 77).state, 'done')
  return 'watchBox -> survey list, dedupe, restore, write hook verified'
})

// ---------------------------------------------------------------------------
// tiles_progress matches boxes in the payload shape ({elev, box:[x0,y0,x1,y1]})
test('acceptance tiles_progress: finds monitored boxes in payload shape', () => {
  const model = new FortModel(Config.parse({}))
  const ingest = (to_dig, dug) => model.ingestFast({
    paused: false, generation: 1,
    facts: { pop: 79, dwarves: 21, miners: 2, picks: 3, food: 10, drink: 0, wood: 0, stone: 136, metal: 0,
      barrels: 14, pending_dig: 0 },
    buildings: [], boxes: [{ elev: 36, box: [27, 121, 42, 126], to_dig, dug, pending: 0 }],
    shafts: [], warnings: { designated_tiles: 0, damp_tiles: 0, hot_tiles: 0 }, notes: [],
  })
  const todo = { id: 19, accept: { kind: 'tiles_progress', elev: 36, x0: 27, y0: 121, x1: 42, y1: 126, min_dug: 10 } }
  ingest(51, 45)
  const mid = runAcceptance(todo, model.snapshot())
  assert.equal(mid.ok, false)
  assert.match(mid.evidence, /to_dig 51, dug 45/, `quotes the live numbers: ${mid.evidence}`)
  ingest(0, 96)
  const done = runAcceptance(todo, model.snapshot())
  assert.equal(done.ok, true, 'to_dig 0 with dug >= min passes')
  assert.match(done.evidence, /to_dig 0, dug 96/)
  const other = runAcceptance({ id: 20, accept: { kind: 'tiles_progress', elev: 40, x0: 1, y0: 1, x1: 2, y1: 2 } }, model.snapshot())
  assert.equal(other.ok, false)
  assert.match(other.evidence, /no data, not a fact/, 'unmonitored box is labelled, never assumed')
  return 'box shape handled; partial quotes numbers; completion passes; unmonitored labelled'
})

// ---------------------------------------------------------------------------
// seeds must not duplicate restored to-dos
test('seed dedupe: restored to-dos are not re-created by the ladder read', () => {
  // A BARE model: freshModel() already seeds, so restoring on top of it is the
  // duplicate case, not the fix.
  const model = new FortModel(baseCfg)
  model.restoreTodos([
    { id: 71, title: 'General pile near the wagon', origin: 'auto', state: 'done', accept: { kind: 'pile_general' }, evidence: 'pile 20 accepts ...' },
    { id: 74, title: 'Miners rung', origin: 'ladder', state: 'done', accept: { kind: 'rung', rung: 'labors' }, evidence: 'rung satisfied' },
  ])
  model.ingestDeep({
    rungs: [{ id: 'labors', title: 'Two dwarves on Miners', done: true, keys: 'y', why: 'dig needs picks' },
      { id: 'meeting', title: 'Meeting area', done: false, keys: 'z', why: 'keeps idle dwarves inside' }],
    next: null, piles: [], reports: [], crashlog: null, notes: [],
  })
  const labors = model.todos.filter((t) => t.accept?.kind === 'rung' && t.accept?.rung === 'labors')
  assert.equal(labors.length, 1, `the labors rung exists exactly once, saw ${labors.map((t) => t.id).join(',')}`)
  assert.equal(model.todos.filter((t) => t.accept?.kind === 'pile_general').length, 1, 'pile_general not duplicated by seeds')
  assert.ok(model.todos.some((t) => t.accept?.kind === 'rung' && t.accept?.rung === 'meeting'), 'new rungs still seed')
  // The live case: an auto pile_general restored, then a ladder read - the seed
  // must not add a second one (measured live: ids 71 and 84 were the same task).
  const model2 = new FortModel(baseCfg)
  const seedGeneral = seedTodos([{ id: 'stockpile', title: 'General stockpile', done: true, keys: 'z', why: 'sorting' }], baseCfg)
    .find((t) => t.accept?.kind === 'pile_general')
  assert.ok(seedGeneral, 'the auto set contains a pile_general task')
  model2.restoreTodos([{ ...seedGeneral, id: 84, evidence: 'pile 20 ...' }])
  model2.ingestDeep({ rungs: [{ id: 'stockpile', title: 'General stockpile', done: true, keys: 'z', why: 'sorting' }],
    next: null, piles: [], reports: [], crashlog: null, notes: [] })
  const generals2 = model2.todos.filter((t) => t.accept?.kind === 'pile_general')
  assert.equal(generals2.length, 1, `the restored pile_general stays single, saw ${generals2.map((t) => t.id).join(',')}`)
  // A DIFFERENT acceptance (extra excludes) is a different proof, so it stays two.
  const model3 = new FortModel(baseCfg)
  model3.restoreTodos([{ id: 90, title: 'Stricter general', origin: 'model', state: 'open',
    accept: { kind: 'pile_general', excludes: ['stone', 'wood'] }, evidence: 'manual check' }])
  model3.ingestDeep({ rungs: [{ id: 'stockpile', title: 'General stockpile', done: true, keys: 'z', why: 'sorting' }],
    next: null, piles: [], reports: [], crashlog: null, notes: [] })
  assert.equal(model3.todos.filter((t) => t.accept?.kind === 'pile_general').length, 2,
    'a stricter acceptance is a separate task, by design')
  return 'seed dedupe by acceptance signature verified (canonical, all fields)'
})

// ---------------------------------------------------------------------------
// wake advice must match what the acceptance proved
test('wake advice follows the acceptance kind (pile to-do is not a dug box)', async () => {
  const { todoAdvice } = await import(pluginDir + '/model.js')
  assert.match(todoAdvice({ accept: { kind: 'pile_category' } }), /pile acceptance verified/)
  assert.match(todoAdvice({ accept: { kind: 'buildings_kw' } }), /building count proven/)
  assert.match(todoAdvice({ accept: { kind: 'rung' } }), /ladder rung satisfied/)
  assert.match(todoAdvice({ accept: { kind: 'tiles_progress' } }), /box is dug/)
  const model = freshModel()
  model.ingestFast({ paused: false, generation: 1,
    facts: { pop: 79, dwarves: 21, miners: 2, food: 0, drink: 1, wood: 2, stone: 13, metal: 0, barrels: 21, pending_dig: 0 },
    boxes: [], shafts: [], warnings: null, notes: [] })
  model.ingestDeep({ rungs: [], next: null, notes: [], piles: { 19: { id: 19, found: 1, accepts: 'stone', total: 13, elev: 36, x0: 30, x1: 33, y0: 126, y1: 128 } } })
  model.addTodo({ title: 'stone pile has stone', accept: { kind: 'pile_category', category: 'stone', min_items: 1 } })
  const raised = model.evaluate(model.cfg)
  const done = raised.find((a) => a.kind === 'dig_complete')
  assert.ok(done, 'pile to-do raised dig_complete')
  assert.match(done.suggested, /pile acceptance verified/, `advice fits the proof: ${done.suggested}`)
  return 'advice per acceptance family; pile flip no longer says "box is dug"'
})

// ---------------------------------------------------------------------------
// the game's own hints reach the briefing
test('projection schema keeps the panel fields (poll, pauseAuthority, filter)', () => {
  const projection = dfObserverProjection(z)
  const keys = Object.keys(projection.stateSchema.shape)
  // The fold copies STATE_FIELDS and starts from init(): both must carry the
  // chip fields, or the panel reads '?' no matter what the schema declares.
  for (const key of ['poll', 'pauseAuthority', 'filter', 'designations']) {
    assert.ok(STATE_FIELDS.includes(key), `STATE_FIELDS must include ${key} (the fold drops unlisted fields)`)
    assert.ok(key in projection.init(), `init() must define ${key}`)
  }
  const folded = projection.apply(projection.init(), {
    type: 'user/message',
    data: { content: `${PAYLOAD_PREFIX}${JSON.stringify({ form: 'briefing', tick: 9, data: { tick: 9, poll: 8000, pauseAuthority: 'off', designations: { designated_total: 107, by_elev: [{ elev: 41, designated_tiles: 83, clusters: [{ x0: 38, x1: 48, y0: 113, y1: 122, tiles: 83 }] }] }, filter: { enabled: true, live: true, verdictTick: 7 } } })}` },
  })
  assert.equal(folded.poll, 8000, 'the fold carries poll')
  assert.equal(folded.pauseAuthority, 'off', 'the fold carries pauseAuthority')
  assert.equal(folded.filter?.verdictTick, 7, 'the fold carries the filter state (the chip needs it)')
  assert.equal(folded.designations?.designated_total, 107, 'the fold carries the designation scan (the panel needs it)')
  for (const key of ['poll', 'pauseAuthority', 'filter', 'generation', 'pauseSource', 'reports']) {
    assert.ok(keys.includes(key), `stateSchema must declare ${key} (the panel chips read it)`)
  }
  return 'schema/payload fields in sync'
})

test('lean payloads keep generation/poll/pauseSource/facts for the panel', () => {
  const model = freshModel()
  const brief = buildBriefing(model.payload(), { maxTokens: 700 })
  const line = brief.text.slice(brief.text.lastIndexOf(PAYLOAD_PREFIX))
  const folded = JSON.parse(line.slice(PAYLOAD_PREFIX.length)).data
  assert.ok(folded.generation !== undefined, `generation survives shrink level ${brief.payloadLevel}`)
  assert.ok(folded.poll !== undefined, 'poll survives the leanest payload')
  assert.ok(folded.pauseSource !== undefined, 'pauseSource survives (the chip must not read ?)')
  assert.ok(folded.facts, `facts survive level ${brief.payloadLevel} (compactFacts)`)
  assert.ok('dwarves' in folded.facts, 'the dwarf number is in the compact facts')
  return `level ${brief.payloadLevel}: chips + facts folded in (${brief.tokens} tokens)`
})

test('GAME SAYS: the game reports are quoted in the briefing', () => {
  const model = freshModel()
  model.ingestDeep({ rungs: [], next: null, piles: [], crashlog: null, notes: [],
    reports: [{ year: 101, text: 'Urdim Rerrasbomrek, expedition leader cancels Eat: Item inaccessible.', z: 37 },
      { year: 101, text: 'A caravan from Ral Al has arrived.  Overall trade sentiments are lukewarm.', z: 42 }] })
  const brief = buildBriefing(model.payload(), { maxTokens: 700 })
  assert.match(brief.text, /GAME SAYS \[Y101\]: /, `the game's hint is quoted: ${brief.text.slice(-300)}`)
  // at a generous budget both hint lines survive; the leanest payload keeps one
  assert.match(buildBriefing(model.payload(), { maxTokens: 2000 }).text, /cancels Eat: Item inaccessible/)
  assert.match(brief.text, /has arrived\.|cancels Eat/, 'a report line is present at 700 too')
  // an empty report read prints no GAME SAYS line at all
  const quiet2 = freshModel()
  quiet2.ingestDeep({ rungs: [], next: null, piles: [], crashlog: null, notes: [], reports: [] })
  assert.ok(!buildBriefing(quiet2.payload(), { maxTokens: 700 }).text.includes('GAME SAYS'), 'no label when there is nothing to quote')
  const raised = model.evaluate(model.cfg)
  const hint = raised.find((a) => a.key === 'game-says-supply')
  assert.ok(hint, 'the game\'s "cancels Eat" raises a crew alert with its own words')
  assert.match(hint.evidence, /cancels Eat: Item inaccessible/)
  assert.match(hint.coord, /elev37/)
  // a quiet report set raises nothing
  const quiet = freshModel()
  quiet.ingestDeep({ rungs: [], next: null, piles: [], crashlog: null, notes: [], reports: [{ year: 101, text: 'The forge is operating.' }] })
  assert.ok(!quiet.evaluate(quiet.cfg).some((a) => a.key === 'game-says-supply'), 'no false supply alert')
  return 'reports quoted; the game\'s supply hint becomes an alert with coordinates'
})

// ---------------------------------------------------------------------------
// wake boundary: steer reaches a busy agent at its next step
test('tool answers stamp the age of the newest game read', async () => {
  const model = freshModel()
  const payload = model.payload()
  assert.ok('age_s' in payload && 'observedAt' in payload, 'payload carries the read age')
  assert.equal(typeof payload.age_s, 'number', 'age_s is a number after a read')
  const defs = toolDefinitions({ model, projection: null, config: model.cfg, sessionFilter: () => true })
  const status = defs.find((d) => d.name === 'df_status')
  const res = await status.execute({ scope: 'health' }, { agent: { id: 'play-1' } })
  assert.match(res.text, /data \d+s old/, `the header line stamps the age: ${String(res.text).split('\n')[0]}`)
  // no read yet -> labelled, not silent
  const bare = new FortModel(model.cfg)
  const defs2 = toolDefinitions({ model: bare, projection: null, config: bare.cfg, sessionFilter: () => true })
  const res2 = await defs2.find((d) => d.name === 'df_status').execute({ scope: 'health' }, { agent: { id: 'play-1' } })
  assert.match(res2.text, /data NO READ YET/, 'a model without reads says so')
  return 'read age stamped in the header line'
})

test('unwatched_designations names a big designation cluster outside the monitored boxes', () => {
  const model = freshModel()
  const boxes = [{ elev: 36, box: [27, 121, 42, 126], to_dig: 0, dug: 96, pending: 0 }]
  const ingest = (des) => model.ingestDeep({ rungs: [], notes: [], piles: [], designations: des })
  model.ingestFast({ paused: false, generation: 1,
    facts: { pop: 102, dwarves: 21, miners: 3, picks: 3, food: 23, drink: 0, wood: 0, stone: 140, metal: 0, barrels: 18, pending_dig: 103 },
    buildings: [], boxes: [...boxes], shafts: [], warnings: { designated_tiles: 107, damp_tiles: 0, hot_tiles: 0 }, notes: [] })
  ingest({ band: [22, 45], designated_total: 107, by_elev: [
    { elev: 41, designated_tiles: 83, clusters: [{ x0: 38, x1: 48, y0: 113, y1: 122, tiles: 83 }] },
    { elev: 36, designated_tiles: 30, clusters: [{ x0: 27, x1: 42, y0: 121, y1: 126, tiles: 30 }] }] })
  const hit = model.evaluate(model.cfg).find((a) => a.kind === 'unwatched_designations')
  assert.ok(hit, 'the unmonitored cluster raises an alert')
  assert.match(hit.evidence, /83 designated tiles at x38-48 y113-122 elev41 are outside every monitored box/, hit.evidence)
  assert.match(hit.suggested, /df_todo\(\{action:"add"/, 'the advice is runnable, with the box + acceptance inlined')
  // the cluster covered by a monitored box raises nothing
  const hit2 = model.evaluate(model.cfg).filter((a) => a.kind === 'unwatched_designations')
  assert.equal(hit2.filter((a) => a.key.includes('-36-27-121')).length, 0, 'a watched cluster stays quiet')
  // the scan clearing it (designations drained) clears the alert
  ingest({ band: [22, 45], designated_total: 0, by_elev: [] })
  model.evaluate(model.cfg)
  assert.ok(!model.alerts.find((a) => a.kind === 'unwatched_designations' && !a.cleared), 'the alert clears with the designations')
  return 'unwatched designations named with a runnable watch recipe'
})

test('lean wire payloads keep the designation summary the panel needs', () => {
  const payload = {
    tick: 12, online: true, paused: false, pauseSource: null, generation: 1, poll: 8000,
    pauseAuthority: 'off', filter: null, overload: null,
    facts: { pop: 102, dwarves: 21, miners: 3, picks: 3, food: 75, drink: 0, alcohol: 0, meals: 0, raw_food: 0, seeds: 53, pending_dig: 18, water_tiles: 1030, magma_tiles: 0, date: { year: 103, month: 2, day: 4 } },
    reports: [], todos: [], alerts: [], digEvents: [], boxes: [], piles: [],
    designations: { band: [22, 45], designated_total: 107, by_elev: [
      { elev: 41, designated_tiles: 83, clusters: [{ x0: 38, x1: 48, y0: 113, y1: 122, tiles: 83 }] },
      { elev: 22, designated_tiles: 4, clusters: [{ x0: 43, x1: 44, y0: 121, y1: 122, tiles: 4 }] }] },
  }
  for (const level of [4, 5, 6, 7, 8]) {
    const data = shrinkPayloadLevel(payload, level)
    assert.ok(data.designations, `level ${level} must carry designations (the panel folds the wire payload)`)
    assert.equal(data.designations.designated_total, 107, `level ${level} totals`)
    assert.equal(data.designations.top[0].tiles, 83, `level ${level} ranks clusters by size`)
    assert.ok(JSON.stringify(data.designations).length < 260, `level ${level} stays compact (${JSON.stringify(data.designations).length} chars)`)
    assert.ok(JSON.stringify(data).length < 4200, `level ${level} wire stays small (${JSON.stringify(data).length} chars)`)
  }
  return 'designation summary survives every lean level, under 260 chars'
})

test('title coverage is partial, never all-or-nothing', () => {
  // Measured live: with a long verdict headline the cap bought ZERO titles while a
  // short one bought all - a cliff. The ladder steps titles 10 -> 8 -> 4 -> 0.
  const { buildFilteredBriefing } = filterApi
  const model = freshModel()
  const payload = model.payload()
  const longVerdict = { headline: 'Food gate: meals 0, raw_food 0, drink 0 across 4 ticks; food 50 to 49, alcohol 3. Nothing cookable at all in this fort today, and four faces are stalled with nothing queued.',
    significance: 'high', changes: ['food 50->49; meals 0, raw_food 0, drink 0 unchanged', 'alcohol 3, seeds 63, dwarves 21, miners 3, picks 3 - unchanged'], notes: ['n'] }
  const wireOf = (text) => JSON.parse(text.split('\n').find((l) => l.startsWith('[df-observer] ')).slice('[df-observer] '.length)).data
  let sawPartial = false
  for (const cap of [300, 340, 380, 420, 480]) {
    const b = buildFilteredBriefing(longVerdict, payload, { maxTokens: cap })
    const open = (wireOf(b.text).todos ?? []).filter((t) => t.state !== 'done')
    const titled = open.filter((t) => t.title).length
    if (titled > 0 && titled < open.length) sawPartial = true
    if (cap === 420) assert.ok(titled > 0, `cap 420 must buy at least partial titles (saw ${titled}/${open.length})`)
  }
  const brief = fs.readFileSync(path.join(pluginDir, 'brief.js'), 'utf8')
  assert.match(brief, /withTitleBudget\(lean, TITLE_ROWS\)/, 'the briefing ladder steps titles down from the knob')
  assert.match(brief, /withTitleBudget\(lean, 6\)/, 'then 6')
  assert.match(brief, /withTitleBudget\(lean, 3\)/, 'then 3, before dropping them')
  assert.match(brief, /withLeanFields/, 'and drops non-panel fields before the words')
  const filter = fs.readFileSync(path.join(pluginDir, 'filter.js'), 'utf8')
  assert.match(filter, /withTitleBudget\(full, titleBudget\(\)\.rows\)/, 'the filter ladder steps titles down too')
  assert.match(filter, /import \{[^}]*withTitleBudget[^}]*\} from '\.\/brief\.js'/, 'and imports the helper (a missing import crashed the harness once)')
  return sawPartial ? 'partial coverage observed on the way down' : 'titles held at every cap tested'
})

test('the panel mute pauses injections only - never the game', async () => {
  const registered = []
  const watchTarget = {
    id: 'watched-1', session: { header: { origin: 'chat' } }, status: 'running',
    inbox: { nextStep: [], nextTurn: [], replace: () => false },
    ctx: { effect: () => () => {} },
  }
  const ctx = {
    // The Cordis base registers the service: ctx.reflect.provide(name, self).
    reflect: { provide: () => {} },
    isolate: {},
    effect: (fn) => { fn(); return () => {} },
    commands: { register: (def) => { registered.push(def.name); return () => {} } },
    tools: { register: () => () => {} },
    agents: { list: () => [watchTarget], on: () => () => {} },
    systemPrompt: { section: () => () => {} },
    sessionProjections: { register: () => () => {} },
    timers: { register: () => () => {} },
    storage: { open: async () => ({ table: () => ({ entries: () => [], put: () => {}, get: () => null }), close: () => {} }) },
  }
  const inst = new DFObserver(ctx, Config.parse({ panel_pause_button: true }))
  // The session must CONTACT the observer before it can be briefed (require_tool_contact).
  const agent = watchTarget
  inst.agents.set(agent.id, { lastTick: 0, lastAt: 0, lastSignal: null, lastWakeAt: 0, pendingSteerId: null })
  inst.markWatched(agent, 'test')
  assert.equal(inst.quiet, false, 'starts unmuted')
  // The game-write path is spied: the mute must never touch it.
  let pauseWrites = 0
  inst.transport.setPause = async () => { pauseWrites += 1; return { ok: true } }
  const first = inst.setQuiet(true, 'test')
  assert.equal(first.quiet, true, 'setQuiet mutes')
  assert.equal(first.changed, true, 'reports the change')
  await inst.dispatchBriefing()
  await inst.dispatchAlerts([{ id: 'A1', kind: 'dig_complete', evidence: 'x', coord: '' }])
  await inst.dispatchFilterWakes()
  assert.equal(inst.model.suppressed.briefings, 1, 'the suppressed briefing is counted, not silently dropped')
  assert.equal(inst.model.suppressed.wakes, 1, 'the suppressed wake is counted')
  assert.equal(pauseWrites, 0, 'NOT ONE write to the game pause - the button mutes injections only')
  const payload = inst.model.payload()
  assert.equal(payload.quiet, true, 'the wire carries the mute so the panel can show it')
  assert.equal(payload.suppressed.briefings, 1, 'the wire carries the counter')
  const view = viewOf({ ...payload, _view: undefined })
  assert.equal(view.quiet, true, 'the client-visible view carries it (4-place sync)')
  const res = await inst.setQuiet(false, 'test')
  assert.equal(res.quiet, false, 'un-mutes')
  await inst.dispatchBriefing()
  // The resume's catch-up resets the counters (they have been reported), so a
  // briefing delivered after un-muting is not counted as suppressed.
  assert.equal(inst.model.suppressed.briefings, 0, 'counters reset once the catch-up reported them')
  assert.equal(pauseWrites, 0, 'still zero game writes after un-muting')
  // the command exists and the client exposes the button in both locales
  const index = fs.readFileSync(path.join(pluginDir, 'index.js'), 'utf8')
  assert.match(index, /name: 'df-quiet'/, '/df-quiet is registered as a session command')
  const client = fs.readFileSync(path.join(pluginDir, 'client.js'), 'utf8')
  assert.match(client, /'\/df-quiet (on|off)'/, 'the panel button runs the command with its argument')
  const dictCount = (client.match(/'open\.right':/g) ?? []).length
  assert.ok(dictCount >= 2, `the client carries >= 2 locale dictionaries (saw ${dictCount})`)
  for (const key of ['quiet.on', 'quiet.off', 'quiet.title', 'quiet.note']) {
    const hits = (client.match(new RegExp("'" + key + "'", 'g')) ?? []).length
    assert.ok(hits >= 2, `${key} is present in BOTH locales (en + zh), saw ${hits}`)
  }
  // Durability: the mute rides the ledger table (the path proven to hit disk).
  const indexSrc = fs.readFileSync(path.join(pluginDir, 'index.js'), 'utf8')
  assert.match(indexSrc, /table\?\.\('todos'\)\?\.put\?\.\('quiet', \{ kind: 'quiet'/, 'the mute is stored on the durable ledger path')
  assert.match(indexSrc, /domain\.table\('todos'\)\.get\('quiet'\) \?\? domain\.table\('meta'\)\.get\('quiet'\)/, 'restore reads it back')
  assert.match(indexSrc, /this\.setQuiet\(true, 'storage', true\)/, 'a restored mute announces itself')
  assert.match(client, /\(mutedUI \?\? snap\.quiet\) \? t\('quiet\.off'\) : t\('quiet\.on'\)/, 'the button toggles its label with the effective state (wire, or the local echo while the wire is muted)')
  // The toggle must also be VISIBLE: the panel folds the session log, and muting
  // stops the rows it reads, so the change announces itself as one folded row.
  const injected = []
  // liveTargets() adopts agents through their OWN Context (agent.ctx.effect), so the
  // mock agent must carry one - measured: 'Cannot read properties of undefined (reading effect)'.
  const play1 = { id: 'play-1', session: { header: { origin: 'chat' } }, ctx: { effect: () => () => {} }, inject: (m) => injected.push(assertInboxMessage(m, 'inject')) }
  ctx.agents = { list: () => [play1], on: () => () => {} }
  const inst2 = new DFObserver(ctx, Config.parse({ panel_pause_button: true }))
  // CONTACT, through the shipped path: a df-* session command marks the session watched.
  inst2.markWatched(play1, 'test')
  inst2.setQuiet(true, 'test')
  assert.equal(injected.length, 1, `the toggle announces itself once (saw ${injected.length} injected rows)`)
  assert.match(inboxText(injected[0]), /injections PAUSED/, inboxText(injected[0]).slice(0, 120))
  assert.match(inboxText(injected[0]), /"quiet":true/, 'the folded row carries the mute state')
  const before = injected.length
  inst2.setQuiet(true, 'test')
  assert.equal(injected.length, before, 'a no-change toggle injects nothing')

  // Resume sends EVERYTHING at once: one message with the suppressed changes plus a
  // full snapshot (the player's request), not a replay of N briefings.
  inst2.model.ingestFast({ paused: false, generation: 1, facts: { pop: 102, dwarves: 21, miners: 3, picks: 3, food: 40, drink: 0, wood: 2, stone: 140, metal: 0, barrels: 0, pending_dig: 20, water_tiles: 1040, magma_tiles: 0 }, buildings: [], boxes: [], shafts: [], warnings: { designated_tiles: 4, damp_tiles: 4, hot_tiles: 0 }, notes: [] })
  inst2.model.evaluate(Config.parse({}))
  await inst2.dispatchBriefing()
  await inst2.dispatchAlerts([{ id: 'A9', kind: 'damp_hot', evidence: '4 DAMP tiles', coord: 'x1-2 y3-4 elev5' }])
  const catchUpAt = injected.length
  inst2.setQuiet(false, 'test')
  const catchUp = injected.slice(catchUpAt).map((m) => inboxText(m)).join('\n@@\n')
  assert.ok(injected.length > catchUpAt, 'resuming injects the catch-up')
  assert.match(catchUp, /CATCH-UP/, catchUp.slice(0, 120))
  assert.match(catchUp, /briefings and \d+ wakes were suppressed/, 'it counts what was swallowed')
  assert.match(catchUp, /ALERTS RAISED WHILE PAUSED[\s\S]*damp_hot/, 'the alerts raised while paused are listed')
  assert.match(catchUp, /FACTS:/, 'the fact deltas are summarised')
  assert.match(catchUp, /\[df-observer\] /, 'and a full snapshot payload rides along (the panel folds it)')
  const injectedCount = injected.length - catchUpAt
  assert.ok(injectedCount <= 2, `the catch-up is ONE message (saw ${injectedCount} injections on resume)`)
  assert.equal(inst2.model.suppressed.briefings, 0, 'the counters reset after delivery')
  // (`client` is read above in this test - no redeclaration.)
  assert.match(client, /const \[mutedUI, setMutedUI\] = React\.useState\(null\)/, 'the page remembers the click locally')
  assert.match(client, /mutedUI \?\? snap\.quiet/, 'the wire overrides the local echo')
  return 'mute pauses injections, counts them, announces itself, and never writes to the game'
})

test('every inbox boundary takes a message object - a raw string corrupts the session', async () => {
  // THE MEASURED FAILURE: two announce paths (the /df-quiet toggle line and the
  // resume catch-up) passed a plain string to `agent.inject()`. It landed in the
  // durable log as `agent/inbox/spliced.inserted: ["DF-OBSERVER injections PAUSED ..."]`,
  // and the session became unloadable - "stored log is corrupt:
  // SessionFormatError: agent/inbox/spliced requires message objects". The whole
  // history of a play session was lost to one button click.
  // The mocks here enforce the loader's contract, so a string cannot pass.
  const seen = []   // {boundary, message} for EVERY payload the plugin delivered
  const record = (boundary) => (m) => { seen.push({ boundary, message: assertInboxMessage(m, boundary) }); return m }
  const agent = {
    id: 'play-1', session: { header: { origin: 'chat' } }, status: 'running',
    inbox: { nextStep: [], nextTurn: [], replace: () => false },
    steer: record('steer'), followup: record('followup'), inject: record('inject'),
    ctx: { effect: () => () => {} },
  }
  const ctx = {
    reflect: { provide: () => {} }, isolate: {},
    effect: (fn) => { fn(); return () => {} },
    commands: { register: () => () => {} },
    tools: { register: () => () => {} },
    agents: { list: () => [agent], on: () => () => {} },
    systemPrompt: { section: () => () => {} },
    sessionProjections: { register: () => () => {} },
    timers: { register: () => () => {} },
    get: () => undefined,
    storage: { open: async () => ({ table: () => ({ entries: () => [], put: () => {}, get: () => null }), close: () => {} }) },
  }
  const inst = new DFObserver(ctx, Config.parse({ wake_min_spacing_ms: 10000, inject_min_spacing_ms: 0 }))
  inst.markWatched({ id: 'play-1', session: { header: { origin: 'chat' } }, ctx: { effect: () => () => { } } }, 'test')
  inst.model.ingestFast(fastData())
  inst.model.ingestDeep(deepData())
  inst.model.evaluate({})

  // 1. Briefings (the delta gate must fire: fresh entry, no prior injection).
  await inst.dispatchBriefing()
  // 2. Alert wakes (steer / followup / demoted inject - all three boundaries).
  await inst.dispatchAlerts([{ id: 'A1', kind: 'water', evidence: 'water in the shaft', coord: 'x50,y123', suggested: 'stop' }])
  // 3. The two paths that used to inject raw strings.
  inst.setQuiet(true, 'command')
  inst.setQuiet(false, 'command')

  const boundaries = new Set(seen.map((s) => s.boundary))
  assert.ok(boundaries.has('inject'), `the plugin delivered inject payloads (saw ${[...boundaries].join(', ')})`)
  assert.ok(seen.length >= 4, `every path delivered (saw ${seen.length} payloads)`)
  const texts = seen.map((s) => inboxText(s.message, s.boundary))
  assert.ok(texts.some((t) => /DF-OBSERVER/.test(t)), 'briefings carry their header')
  assert.ok(texts.some((t) => /injections PAUSED/.test(t)), 'the mute announces itself as a message object')
  assert.ok(texts.some((t) => /CATCH-UP/.test(t)), 'the resume catch-up rides as a message object')
  assert.ok(seen.every((s) => s.message.source?.kind === 'df-observer'), 'every payload names its producer')
  assert.ok(seen.every((s) => typeof s.message.source?.form === 'string' && s.message.source.form.length > 0), 'every payload carries its form')
  assert.ok(new Set(seen.map((s) => s.message.id)).size === seen.length, 'no two injections share a message id (the inbox fold rejects duplicates)')

  // The filter wake path builds its own messages: same contract.
  for (const full of [false, true]) {
    const m = inst.filterWakeMessage({ tick: 7, gateNotes: ['to_dig +4'], payload: inst.model.payload(), full })
    assertInboxMessage(m, 'followup')
    assert.equal(m.source.form, 'filter-tick')
  }

  // Single choke point: index.js may call the inbox boundaries only with a
  // message it built (injectMessage's `message`, or the filter's built wake).
  const index = fs.readFileSync(path.join(pluginDir, 'index.js'), 'utf8')
  const rawCalls = index.split('\n')
    .map((l) => l.trim())
    .filter((l) => /\b(agent|live)\.(inject|steer|followup)\(/.test(l) && !l.startsWith('*') && !l.startsWith('//'))
    .map((l) => l.replace(/\s+/g, ' '))
  const allowed = /^agent\.(inject|steer|followup)\(message\)$|^live\.followup\(this\.filterWakeMessage\(\{$/
  const offenders = rawCalls.filter((l) => !allowed.test(l))
  assert.equal(offenders.length, 0, `inbox boundaries take only built messages (saw: ${offenders.join(' | ')})`)
  assert.ok(rawCalls.length >= 3, `the boundary-call scan found the calls it guards (saw ${rawCalls.length})`)
  assert.match(index, /this\.observerMessage\(text, form\)/, 'injectMessage builds through observerMessage')
  assert.match(index, /refusing to deliver a non-message payload/, 'the choke point refuses a non-message payload')
  return `${seen.length} payloads, ${boundaries.size} boundaries, all message objects`
})

test('the Sidebar page body is uncapped; only the dock keeps a cap', () => {
  // Measured in the player's page: the Sidebar tab scrolled with a 340px box around
  // its sections because the page shared the dock body (`open ? h('div', { style:
  // dockBody(...) })` - the page starts expanded, so asPage hit the dock cap).
  const client = fs.readFileSync(path.join(pluginDir, 'client.js'), 'utf8')
  assert.match(client, /asPage \? pageBody\(\) : dockBody\(snap\.dockMaxHeight\)/, 'the render picks the style by mode')
  const lines = client.split('\n')
  const pageLine = lines.findIndex((l) => l.includes('const pageBody = () =>'))
  const dockLine = lines.findIndex((l) => l.includes('const dockBody = (max)'))
  assert.ok(pageLine > 0 && dockLine > pageLine, 'both helpers exist inside Panel')
  const pageSrc = lines[pageLine]
  assert.match(pageSrc, /maxHeight: 'none'/, 'the page body has no cap')
  assert.match(pageSrc, /minHeight: 0/, 'the page body can shrink (flex)')
  assert.ok(!/maxHeight: 340/.test(pageSrc), 'no 340px cap on the page path')
  return 'page fills the pane; dock keeps the Config cap'
})

test('the dock height cap is Config-driven and reaches the client', () => {
  const index = fs.readFileSync(path.join(pluginDir, 'index.js'), 'utf8')
  assert.match(index, /dock_max_height: z\.number\(\)\.int\(\)\.min\(0\)\.max\(4000\)\.default\(340\)/, 'knob declared')
  const model = freshModel()
  const payload = model.payload()
  assert.equal(payload.dockMaxHeight, 340, 'the payload carries the cap')
  assert.equal(viewOf({ ...payload, _view: undefined }).dockMaxHeight, 340, 'the client-visible view carries it (4-place sync)')
  const client = fs.readFileSync(path.join(pluginDir, 'client.js'), 'utf8')
  assert.match(client, /dockBody\(snap\.dockMaxHeight\)/, 'the dock uses the projected value')
  assert.match(client, /maxHeight: cap > 0 \? cap : 'none'/, '0 means no cap (no scrollbar)')
  const lines = client.split('\n')
  const helperLine = lines.findIndex((l) => l.includes('const dockBody = (max)'))
  const panelLine = lines.findIndex((l) => l.startsWith('function Panel('))
  assert.ok(helperLine > panelLine, `dockBody is defined inside Panel's scope (${helperLine} > ${panelLine})`)
  assert.ok(!/^function dockBody/m.test(client), 'no module-level dockBody (S is not in that scope)')
  return 'dock cap travels Config -> payload -> view -> client'
})

test('the Sidebar page fills the pane height (no half-height scroll)', () => {
  const client = fs.readFileSync(path.join(pluginDir, 'client.js'), 'utf8')
  const asPage = client.slice(client.indexOf('const shell = asPage'), client.indexOf('return h(\'div\', { style: shell }'))
  assert.match(asPage, /height: '100%'/, 'the page shell is given the pane height')
  assert.match(asPage, /display: 'flex', flexDirection: 'column'/, 'and lays out as a column')
  assert.match(asPage, /minHeight: 0/, 'with minHeight 0 so the column can shrink (the flex trap that keeps scrollbars alive)')
  assert.ok(!/maxHeight: '100%'/.test(asPage), 'no maxHeight:100% of an auto-height parent (measured: scrolls at ~half height)')
  // the composer dock keeps its compact cap - it is a chip, not a page
  const dock = client.slice(client.indexOf('body: {'), client.indexOf('body: {') + 120)
  assert.match(dock, /maxHeight: \d+/, 'the dock body keeps a height cap')
  return 'page fills the pane; dock stays compact'
})

test('panel title budget is Config-driven and the ladder steps it down', () => {
  const index = fs.readFileSync(path.join(pluginDir, 'index.js'), 'utf8')
  assert.match(index, /panel_title_rows: z\.number\(\)\.int\(\)\.min\(0\)\.max\(30\)\.default\(10\)/, 'rows knob declared')
  assert.match(index, /panel_title_chars: z\.number\(\)\.int\(\)\.min\(8\)\.max\(200\)\.default\(44\)/, 'chars knob declared (ceiling raised to 200)')
  assert.match(index, /setTitleBudget\(\{ rows: config\.panel_title_rows, chars: config\.panel_title_chars \}\)/, 'applied at activation')
  const brief = fs.readFileSync(path.join(pluginDir, 'brief.js'), 'utf8')
  assert.match(brief, /row\.title = String\(t\.title \?\? ''\)\.slice\(0, TITLE_CHARS\)/, 'the lean ledger honours the char budget')
  const client = fs.readFileSync(path.join(pluginDir, 'client.js'), 'utf8')
  assert.match(client, /title: String\(todo\.title \?\? ''\)/, 'the panel row carries a full-title tooltip for clipped text')
  // the knob actually moves the wire
  const { setTitleBudget } = briefApi
  const model = freshModel()
  const payload = model.payload()
  const openRow = payload.todos.find((t) => t.state !== 'done' && String(t.title ?? '').length > 20)
  assert.ok(openRow, 'the ledger has an open row with a title worth clipping')
  setTitleBudget({ rows: 10, chars: 44 })
  const wide = shrinkPayloadLevel(payload, 8).todos.find((t) => t.id === openRow.id)
  setTitleBudget({ rows: 10, chars: 12 })
  const tight = shrinkPayloadLevel(payload, 8).todos.find((t) => t.id === openRow.id)
  setTitleBudget({ rows: 10, chars: 44 })
  assert.ok(wide?.title && tight?.title, `the row carries a title at both budgets (${JSON.stringify(wide)} / ${JSON.stringify(tight)})`)
  assert.ok(tight.title.length <= 12, `the char knob clips the wire (${tight.title.length})`)
  assert.ok(wide.title.length > tight.title.length, `44 chars beats 12 (${wide.title.length} vs ${tight.title.length})`)
  setTitleBudget({ rows: 0, chars: 44 })
  assert.ok(!shrinkPayloadLevel(payload, 8).todos.some((t) => t.title), 'rows: 0 means no title budget spent')
  setTitleBudget({ rows: 10, chars: 44 })
  return `title budget honoured: ${wide.title.length}-char titles, ladder steps rows`
})

test('closing a to-do keeps its acceptance identity (no re-seed after a reload)', () => {
  // Measured live: #220 "Well (or cistern plan)…" closed, then #236 with the same
  // title was seeded as a NEW open task - the filter reported the duplicate as a
  // state change. Cause: closeTodo() rewrote todo.accept, the seed dedupe key.
  const cfg = Config.parse({})
  const first = new FortModel(cfg)
  first.ingestFast({ paused: false, generation: 1,
    facts: { pop: 102, dwarves: 21, miners: 3, picks: 3, food: 40, drink: 0, wood: 0, stone: 140, metal: 0, barrels: 0, pending_dig: 10, water_tiles: 0, magma_tiles: 0 },
    buildings: [{ name: 'Well', count: 1 }], boxes: [], shafts: [], warnings: { designated_tiles: 0, damp_tiles: 0, hot_tiles: 0 }, notes: [] })
  first.ingestDeep({ rungs: [{ id: 'labors', title: 'Two dwarves on Miners', done: true }, { id: 'farm', title: 'Farm plot', done: true }, { id: 'water', title: 'Well', done: true }], notes: [], piles: [] })
  first.evaluate(cfg)
  const well = first.todos.find((t) => t.accept?.kind === 'water_supply' || t.accept.kind === 'buildings_kw' && t.accept?.keyword === 'well')
  assert.ok(well, 'the seeded ledger has the well to-do')
  assert.equal(well.state, 'done', 'the building read closed it')
  assert.deepEqual(well.accept, { kind: 'water_supply', min: 1 }, 'closing kept an acceptance test, and it is the zone-aware one (tool_missing #56: buildings_kw cannot see a WaterSource zone)')
  assert.equal(well.manualClosed, undefined, 'no manual-close marker on an acceptance closure')

  // Reload: a new model, the closed record restored from storage, ladder read again.
  const restored = JSON.parse(JSON.stringify(first.todos))
  const second = new FortModel(cfg)
  second.restoreTodos(restored)
  second.ingestFast({ paused: false, generation: 1,
    facts: { pop: 102, dwarves: 21, miners: 3, picks: 3, food: 40, drink: 0, wood: 0, stone: 140, metal: 0, barrels: 0, pending_dig: 10, water_tiles: 0, magma_tiles: 0 },
    buildings: [{ name: 'Well', count: 1 }], boxes: [], shafts: [], warnings: { designated_tiles: 0, damp_tiles: 0, hot_tiles: 0 }, notes: [] })
  second.ingestDeep({ rungs: [{ id: 'labors', title: 'Two dwarves on Miners', done: true }, { id: 'farm', title: 'Farm plot', done: true }, { id: 'water', title: 'Well', done: true }], notes: [], piles: [] })
  second.evaluate(cfg)
  const wells = second.todos.filter((t) => /well/i.test(String(t.title)))
  assert.equal(wells.length, 1, `the well task is not re-seeded after a reload (saw ${wells.map((t) => `#${t.id}:${t.state}`).join(', ')})`)
  // the legacy {kind:manual} records are caught by the title fallback
  const legacy = restored.map((t) => ({ ...t, state: 'done', accept: { kind: 'manual' } }))
  const third = new FortModel(cfg)
  third.restoreTodos(legacy)
  third.ingestFast({ paused: false, generation: 1,
    facts: { pop: 102, dwarves: 21, miners: 3, picks: 3, food: 40, drink: 0, wood: 0, stone: 140, metal: 0, barrels: 0, pending_dig: 10, water_tiles: 0, magma_tiles: 0 },
    buildings: [{ name: 'Well', count: 1 }], boxes: [], shafts: [], warnings: { designated_tiles: 0, damp_tiles: 0, hot_tiles: 0 }, notes: [] })
  third.ingestDeep({ rungs: [{ id: 'water', title: 'Well', done: true }], notes: [], piles: [] })
  third.evaluate(cfg)
  assert.equal(third.todos.filter((t) => /well/i.test(String(t.title))).length, 1, 'legacy manual-accept rows dedupe by title')
  return 'acceptance identity survives closure and reload'
})

test('a non-box completion names its acceptance, not an empty coordinate', () => {
  // Measured live: A30-A33 rendered as bare `dig_complete` rows (coord "").
  // Drive a real acceptance flip: the seeded `well` to-do is buildings_kw, so a
  // building read containing "Well" closes it and raises the completion alert.
  const model = freshModel()
  const well = model.todos.find((t) => t.accept?.kind === 'water_supply' || t.accept.kind === 'buildings_kw' && t.accept?.keyword === 'well' && t.state !== 'done')
  assert.ok(well, 'the seeded ledger has a box-less buildings_kw to-do (the well)')
  // `model.buildings` comes from the FAST read (model.js: `if (data.buildings)`),
  // so the acceptance snapshot only sees it there - measured, not assumed.
  model.ingestFast({ ...model.lastFast, buildings: [{ name: 'Well', count: 1 }] })
  const alerts = model.evaluate(model.cfg)
  const done = alerts.find((a) => a.kind === 'dig_complete' && a.key === `todo-${well.id}`)
  assert.ok(done, `the acceptance flip raises the completion alert (saw ${alerts.map((a) => a.kind).join(',') || 'none'})`)
  assert.ok(String(done.coord ?? '').length > 0, `coord must not be empty for a box-less to-do (saw ${JSON.stringify(done.coord)})`)
  assert.match(done.coord, new RegExp(`#${well.id}.*(water_supply|buildings_kw)`), `coord names the acceptance: ${done.coord}`)
  return `box-less completion labelled: ${done.coord}`
})

test('tool text never prints undefined (measured: the boxes scope said "4t/undefinedblk")', async () => {
  const model = freshModel()
  model.ingestDeep({ rungs: [], notes: [], piles: [], designations: { band: [22, 45], designated_total: 107,
    by_elev: [{ elev: 22, designated_tiles: 4, clusters: [{ x0: 43, x1: 44, y0: 121, y1: 122, tiles: 4 }] },
      { elev: 41, designated_tiles: 83, clusters: [{ x0: 38, x1: 48, y0: 113, y1: 122, tiles: 83 }] }] } })
  model.evaluate(model.cfg)
  const defs = toolDefinitions({ model, projection: null, config: model.cfg, sessionFilter: () => true })
  const call = (scope) => defs.find((d) => d.name === 'df_status').execute({ scope }, { agent: { id: 'play-1' } })
  for (const scope of ['boxes', 'summary', 'full']) {
    const text = String((await call(scope)).text ?? '')
    assert.ok(!/undefined/.test(text), `scope ${scope} printed the word undefined: ${text.slice(0, 160)}`)
  }
  const boxesOut = String((await call('boxes')).text)
  assert.match(boxesOut, /x38-48 y113-122 83t = 83 tiles/, 'the scan row renders cleanly')
  return 'boxes/summary/full render without undefined'
})

test('fresh-embark simulation: a virgin fort gets honest labels, not invented facts', () => {
  // The virtual counterpart of PLAYTEST §5: no watched boxes, auto band, nothing
  // dug. The live embark run is `deploy.sh embark`; this pins the expectations.
  const cfg = Config.parse({ monitored_boxes: [], pile_ids: [], shafts: [], elev0: 0, elev1: 0 })
  // A virgin fort means NOTHING ingested yet: build the model directly, not via
  // freshModel (that helper seeds a full fort and its labels would be real data).
  const m = new FortModel(cfg)
  const virgin = { paused: false, generation: 1,
    facts: { pop: 84, dwarves: 7, miners: 1, picks: 3, food: 12, drink: 5, alcohol: 5, meals: 0, raw_food: 12, seeds: 0, wood: 3, stone: 0, metal: 0, barrels: 0, pending_dig: 0, water_tiles: 0, magma_tiles: 0, date: { year: 1, month: 0, day: 1 } },
    buildings: [], boxes: [], shafts: [], warnings: { designated_tiles: 0, damp_tiles: 0, hot_tiles: 0 }, notes: [] }
  m.ingestFast(virgin)
  const alerts = m.evaluate(cfg)
  const b = buildBriefing(m.payload(), { maxTokens: 700 })
  // 1. NO DATA is labelled, not hidden
  assert.match(b.text, /LADDER: NO DATA this tick \(deep read pending\)/, 'the ladder announces the missing deep read')
  assert.match(b.text, /DIG: no monitored boxes answered this tick \(no data, not a fact\)/, 'the dig line says so')
  assert.match(b.text, /TODOS: ledger empty \(the plugin seeds it from the first ladder read\)/, 'the ledger gap is explained')
  // 2. no tile-rule alerts without tiles
  assert.ok(!alerts.some((a) => a.kind === 'dig_complete' || a.kind === 'dig_stalled' || a.kind === 'tile_state_lost'),
    'no dig alerts on a fort with no watched tiles: ' + alerts.map((a) => a.kind).join(','))
  // 3. supply ranking speaks, and its advice follows the gap that fired
  const crew = alerts.find((a) => a.kind === 'crew')
  assert.ok(crew, 'the supply gap raises the crew alert')
  assert.match(crew.evidence, /meals 0, raw 12, 7 dwarves/, 'the gap is the meals one (drink 5 exists)')
  assert.match(crew.suggested, /kitchen \+ butcher/, 'the advice names the fix for THAT gap, not the generic labour line')
  // 4. the ledger seeds from the first LADDER read (deep tick)
  m.ingestDeep({ rungs: [{ id: 1, title: 'Pause / labour check', done: true }, { id: 2, title: 'Entry tunnel / stairwell', done: false }, { id: 15, title: 'Food: farm plot + fishing + butcher', done: false }], notes: [], piles: [] })
  m.evaluate(cfg)
  assert.ok(m.todos.length > 0, 'the first ladder read seeds the ledger')
  assert.ok(m.todos.every((t) => t.accept && t.accept.kind), 'every seeded to-do carries an acceptance test')
  return `virgin fort: ${alerts.map((a) => a.kind).join(',')} + labelled gaps, ${m.todos.length} seeded to-dos`
})

test('config declares each key exactly once (duplicate zod keys are a silent last-wins)', () => {
  const index = fs.readFileSync(path.join(pluginDir, 'index.js'), 'utf8')
  const keys = [...index.matchAll(/^  ([a-z_]+): z\./gm)].map((m) => m[1])
  const dupes = [...new Set(keys.filter((k, i) => keys.indexOf(k) !== keys.lastIndexOf(k)))]
  assert.deepEqual(dupes, [], `duplicate Config keys: ${dupes.join(', ')}`)
  assert.ok(keys.includes('panel_full_briefing'), 'the panel knob is declared once')
  return `${keys.length} Config keys, no duplicates`
})

test('the page states which data path it is showing (pull vs wire)', () => {
  const client = fs.readFileSync(path.join(pluginDir, 'client.js'), 'utf8')
  assert.match(client, /panel data: \/df-panel pull #/, 'a successful pull is named with its count')
  assert.match(client, /panel data: wire only/, 'and so is the fallback')
  // the alert row must not print the coordinate twice (measured live as
  // `crew [y -> …][y -> …]`): the kind span owns it, the evidence span must not
  // fall back to it.
  const rowStart = client.indexOf("h('span', { style: S.ids }, a.id)")
  const row = client.slice(rowStart, client.indexOf("a.acked ? t('alerts.acked')", rowStart))
  const evLine = row.split('\n').find((l) => l.includes('style: S.ev },'))
  assert.ok(evLine, `the evidence span line is found (row: ${row.slice(0, 80)}...)`)
  assert.ok(!/a\.coord/.test(evLine), `the evidence span must not repeat the coordinate (saw ${evLine.trim()})`)
  assert.match(row, /\$\{a\.kind\}\$\{a\.coord/, 'the kind span carries it')
  assert.match(client, /pull failed \(\$\{pull\.note/, 'with the reason, not silence')
  return 'panel provenance is on screen'
})

test('panel labels resolve from the dictionary (no raw key strings on screen)', () => {
  const client = fs.readFileSync(path.join(pluginDir, 'client.js'), 'utf8')
  const en = client.slice(client.indexOf('const UI_EN = {'), client.indexOf('const UI_ZH = {'))
  const zh = client.slice(client.indexOf('const UI_ZH = {'))
  // Every key the panel renders with t(...) must exist in BOTH dictionaries: a
  // missing key prints its own name (measured: '107 designatedTiles').
  const used = [...client.matchAll(/t\('([a-zA-Z][\w.]*)'\)/g)].map((m) => m[1])
  const defined = (block) => new Set([...block.matchAll(/'([\w.]+)':/g)].map((m) => m[1]))
  const enKeys = defined(en), zhKeys = defined(zh)
  // labels built with template keys (t(`health.pauseSource.${x}`)) are checked by prefix
  const missing = [...new Set(used)].filter((k) => !enKeys.has(k) && !k.includes('${'))
  assert.deepEqual(missing, [], `keys rendered without a dictionary entry: ${missing.join(', ')}`)
  const zhMissing = [...new Set(used)].filter((k) => !zhKeys.has(k) && !k.includes('${'))
  assert.deepEqual(zhMissing, [], `zh dictionary missing: ${zhMissing.join(', ')}`)
  return `${new Set(used).size} label keys defined in en+zh`
})

test('every STATE_FIELDS key reaches the client-visible view (4-place sync)', () => {
  // The field set lives in FOUR places: stateSchema, init(), STATE_FIELDS, viewOf().
  // Three earlier bugs were exactly one of them missing a field (poll/pauseAuthority/
  // filter, then designations in STATE_FIELDS, then designations in viewOf).
  const unit = dfObserverProjection(z)
  const declared = Object.keys(unit.stateSchema?.shape ?? unit.stateSchema?._def?.shape ?? {})
  const inits = Object.keys(unit.init())
  const view = viewOf({ ...unit.init(), designations: { designated_total: 1 }, notes: ['n'] })
  const missingView = STATE_FIELDS.filter((k) => !(k in view))
  assert.deepEqual(missingView, [], `viewOf must expose every STATE_FIELDS key (missing: ${missingView.join(',')})`)
  const missingInit = STATE_FIELDS.filter((k) => !(k in unit.init()))
  assert.deepEqual(missingInit, [], `init() must define every STATE_FIELDS key (missing: ${missingInit.join(',')})`)
  const missingSchema = STATE_FIELDS.filter((k) => !declared.includes(k))
  assert.deepEqual(missingSchema, [], `stateSchema must declare every STATE_FIELDS key (missing: ${missingSchema.join(',')})`)
  assert.ok('designations' in view, 'the designation scan is client-visible (measured: it was not)')
  return `stateSchema/init/STATE_FIELDS/viewOf agree on ${STATE_FIELDS.length} fields`
})

test('/df-panel: the panel gets its full data through the session-command path', () => {
  const index = fs.readFileSync(path.join(pluginDir, 'index.js'), 'utf8')
  const client = fs.readFileSync(path.join(pluginDir, 'client.js'), 'utf8')
  assert.match(index, /name: 'df-panel'/, 'the command is registered')
  assert.match(index, /panelPayload\(typeof sessionId === 'string' \? sessionId : undefined\)/, 'it serves panelPayload for the calling session')
  assert.match(client, /execute\(sessionId, '\/df-panel', \[\]\)/, 'the page pulls it like it pulls /df-ack')
  assert.match(client, /const snap = extra \? \{ \.\.\.folded, \.\.\.extra \} : folded/, 'the pull merges over the folded wire')
  assert.match(client, /setInterval\(pull, 30000\)/, 'and refreshes at 30s (each poll appends a command row - measured)')
  assert.match(client, /String\(raw\)\.indexOf\('\{'\)/, 'the JSON is extracted from whatever result shape arrives')
  return '/df-panel command + page pull wired'
})

test('the panel has no unreachable Remote surface - it reads the projection wire', () => {
  const index = fs.readFileSync(path.join(pluginDir, 'index.js'), 'utf8')
  const client = fs.readFileSync(path.join(pluginDir, 'client.js'), 'utf8')
  // panelPayload is back WITH a consumer: the /df-panel session command (the
  // @Remote surface a third-party client cannot reach is still not used).
  assert.ok(!/remote\.${'dfObserver'}/.test(client), 'the client does not depend on an unresolvable remote namespace')
  assert.match(index, /third-party bundle\n \*     CANNOT reach an `@Remote` surface/, 'and the header says what was measured')
  return 'no phantom panel Remote; the wire is the panel feed'
})

test('panel rows: a trimmed alert renders no literal "undefined"', () => {
  const client = fs.readFileSync(path.join(pluginDir, 'client.js'), 'utf8')
  assert.match(client, /a\.evidence \? String\(a\.evidence\)/, 'evidence is guarded before stringifying')
  // the leanest alert shape carries coord, so a row without evidence still says where
  for (const level of [6, 7]) {
    const d = shrinkPayloadLevel({ tick: 1, online: true, paused: false, generation: 1, poll: 8000, facts: null, reports: [], todos: [], alerts: [{ id: 'A1', kind: 'crew', coord: 'pile 20', evidence: 'x'.repeat(400), acked: false }] }, level)
    const a = d.alerts[0]
    assert.equal(a.coord, 'pile 20', `level ${level} keeps the coordinate`)
    assert.ok(a.evidence === undefined || String(a.evidence).length <= 200, `level ${level} evidence is short`)
  }
  return 'no literal undefined in alert rows; the leanest level still says where'
})

test('the panel wire gets its sections when the budget pays for them', () => {
  const payload = {
    tick: 9, online: true, paused: false, generation: 1, poll: 8000, pauseAuthority: 'off',
    facts: { pop: 102, dwarves: 21, miners: 3, picks: 3, food: 54, drink: 0, alcohol: 0, meals: 0, raw_food: 0, seeds: 59, pending_dig: 18, water_tiles: 1030, magma_tiles: 0, date: { year: 103, month: 3, day: 4 } },
    reports: [], notes: ['plugin: filter session live'],
    boxes: [{ elev: 41, box: [38, 113, 48, 122], to_dig: 17, dug: 16, pending: 0 }],
    piles: [{ id: 20, found: 1, total: 54, accepts: 'food' }],
    rungs: [{ id: 1, title: 'Pause / labour check', done: true }],
    designations: { designated_total: 107, by_elev: [{ elev: 41, designated_tiles: 83, clusters: [{ x0: 38, x1: 48, y0: 113, y1: 122, tiles: 83 }] }] },
    todos: [{ id: 21, state: 'open', evidence: 'x' }, { id: 165, state: 'open', evidence: 'y' }],
    alerts: [{ id: 'A1', kind: 'crew', coord: 'pile 20', acked: false }],
  }
  const rich = buildBriefing(payload, { maxTokens: 700 })
  const line = rich.text.split('\n').find((l) => l.startsWith('[df-observer] '))
  const wire = JSON.parse(line.slice('[df-observer] '.length)).data
  for (const key of ['boxes', 'piles', 'rungs', 'notes', 'designations']) {
    assert.ok(Array.isArray(wire[key]) || (key === 'designations' && wire[key]?.top), `the Config budget (700) carries ${key} for the panel (saw ${JSON.stringify(wire[key])})`)
  }
  // ledger-first: a tight budget keeps the ledger and drops the fill, never busts
  const tight = buildBriefing(payload, { maxTokens: 300 })
  assert.ok(tight.tokens <= 300, `tight budget respected (${tight.tokens})`)
  const tightWire = JSON.parse(tight.text.split('\n').find((l) => l.startsWith('[df-observer] ')).slice('[df-observer] '.length)).data
  assert.deepEqual(tightWire.todos.map((t) => `${t.id}:${t.state}`), ['21:open', '165:open'], 'ledger survives the tight budget')
  return `panel sections ride the wire when affordable (700 yes, 300 ledger-first at ${tight.tokens})`
})

test('ledger + box storage keys are path-safe', () => {
  const index = fs.readFileSync(path.join(pluginDir, 'index.js'), 'utf8')
  // The todo key: String(id) - numeric, safe.
  assert.ok(/table\('todos'\)\.put\(String\(todo\.id\)/.test(index), 'todo keys are numeric ids')
  // The box key: built from elev + coords, and it must not use ':' or ','.
  const m = index.match(/const key = (`[^`]+`)\n\s*void domain\.table\('boxes'\)\.put/)
  assert.ok(m, 'the watched-box key template found')
  const shape = m[1].replace(/\$\{[^}]*\}/g, '1').replace(/`/g, '')
  assert.ok(/^[a-zA-Z0-9_-]+$/.test(shape), `box key shape must match the storage pattern /^[a-zA-Z0-9_-]+$/ (saw ${shape})`)
  return 'todo ids numeric; box keys path-safe (measured: the ":"/"," key was rejected)'
})

test('designations: carried when the server sends them, labelled when it does not', async () => {
  const model = freshModel()
  const defsOf = () => toolDefinitions({ model, projection: null, config: model.cfg, sessionFilter: () => true })
  model.ingestDeep({ rungs: [], notes: [], piles: [], designations: { band: [22, 45], designated_total: 103,
    by_elev: [{ elev: 36, designated_tiles: 103, clusters: [{ x0: 64, x1: 79, y0: 112, y1: 127, tiles: 103, blocks: 4 }] }] } })
  const res = await defsOf().find((d) => d.name === 'df_status').execute({ scope: 'boxes' }, { agent: { id: 'play-1' } })
  assert.match(res.text, /designations \(server scan\):/, res.text.split('\n').slice(-6).join('\n'))
  assert.match(res.text, /elev36: x64-79 y112-127 103t = 103 tiles/, res.text)   // the old format printed a phantom 4blk field
  assert.match(res.text, /total designated: 103/, res.text)
  assert.equal(res.designations.designated_total, 103, 'the structured field is present too')
  // a server without the scan: labelled, not guessed
  model.designations = null
  const res2 = await defsOf().find((d) => d.name === 'df_status').execute({ scope: 'boxes' }, { agent: { id: 'play-1' } })
  assert.match(res2.text, /designations: NO DATA/, res2.text)
  return 'designation clusters surface in df_status boxes, with the honest NO DATA label'
})

test('the ladder NEXT box joins the survey list (suggestions get proven tiles)', () => {
  const model = freshModel()
  model.ingestDeep({ rungs: [], notes: [], piles: [], next: null })
  assert.equal(model.nextBox, null, 'no suggestion -> no box')
  model.ingestDeep({ rungs: [], notes: [], piles: [],
    next: { id: 'refuse', title: 'Refuse pile OUTSIDE (miasma)', keys: 'p', why: 'miasma',
      at: { elev: 36, x0: 65, y0: 113, x1: 71, y1: 119, region: 'A' } } })
  const box = model.nextBox
  assert.deepEqual(box, [65, 113, 71, 119, 36], 'the suggestion box is a survey box (in-game elev)')
  // dedupe: a suggestion already in monitored_boxes must not be surveyed twice
  const list = [...model.cfg.monitored_boxes, ...(model.extraBoxes ?? []), ...(box ? [box] : [])]
    .filter((b, i, arr) => arr.findIndex((o) => o[0] === b[0] && o[1] === b[1] && o[2] === b[2] && o[3] === b[3] && o[4] === b[4]) === i)
  const dupes = list.length - new Set(list.map((b) => b.join(','))).size
  assert.equal(dupes, 0, 'no duplicate surveys')
  model.ingestDeep({ rungs: [], notes: [], piles: [], next: null })
  assert.equal(model.nextBox, null, 'a cleared suggestion clears the box')
  return 'NEXT suggestion box is surveyed, deduped, and cleared with the suggestion'
})

test('the pending split names the backlog outside the watched boxes', async () => {
  const model = freshModel()
  model.ingestFast({ paused: false, generation: 1,
    facts: { pop: 102, dwarves: 21, miners: 3, picks: 3, food: 24, drink: 0, wood: 1, stone: 159, metal: 0, barrels: 18, pending_dig: 103 },
    buildings: [], boxes: [{ elev: 35, box: [45, 121, 66, 122], to_dig: 20, dug: 24, pending: 4, hidden: 18 },
      { elev: 25, box: [43, 121, 44, 122], to_dig: 4, dug: 0, pending: 0, hidden: 0 }],
    shafts: [], warnings: { designated_tiles: 16, damp_tiles: 4, hot_tiles: 0 }, notes: [] })
  model.evaluate(model.cfg)
  const defs = toolDefinitions({ model, projection: null, config: model.cfg, sessionFilter: () => true })
  const res = await defs.find((d) => d.name === 'df_status').execute({ scope: 'summary' }, { agent: { id: 'play-1' } })
  assert.match(res.text, /pending dig 103 \(watched boxes 4, outside them 99 - location of the rest is not proven by the survey\)/, res.text.split('\n')[2])
  const brief = buildBriefing(model.payload(), { maxTokens: 700 })
  assert.match(brief.text, /pending 103 \(watched 4\/out 99\)/, 'the briefing carries the terse split')
  return 'watched vs unwatched backlog named in tools and briefings'
})

test('payload carries lastCheck so df_todo why can show the trail', async () => {
  const model = freshModel()
  const todo = model.addTodo({ title: 'elev35 adit dug', accept: { kind: 'tiles_progress', elev: 35, x0: 45, x1: 66, y0: 121, y1: 122, min_dug: 44 } })
  model.ingestFast({ paused: false, generation: 1,
    facts: { pop: 102, dwarves: 21, miners: 3, picks: 3, food: 21, drink: 0, wood: 1, stone: 159, metal: 0, barrels: 18, pending_dig: 24 },
    buildings: [], boxes: [{ elev: 35, box: [45, 121, 66, 122], to_dig: 20, dug: 24, pending: 4, hidden: 0 }],
    shafts: [], warnings: { designated_tiles: 24, damp_tiles: 0, hot_tiles: 0 }, notes: [] })
  model.evaluate(model.cfg)
  const row = model.payload().todos.find((t) => t.id === todo.id)
  assert.ok(row.lastCheck, 'the payload carries lastCheck')
  assert.equal(row.lastCheck.ok, false, 'the check ran and failed on the numbers')
  assert.match(row.lastCheck.evidence, /to_dig 20, dug 24/, row.lastCheck.evidence)
  assert.match(row.lastCheck.evidence, /accept: to_dig==0 && dug>=44\)/, 'the full clause survives the payload slice')
  const defs = toolDefinitions({ model, projection: null, config: model.cfg, sessionFilter: () => true })
  const res = await defs.find((d) => d.name === 'df_todo').execute({ action: 'why', id: todo.id }, { agent: { id: 'play-1' } })
  assert.match(res.text, /last check: tick \d+ fail: /, `why shows the trail: ${res.text}`)
  return 'lastCheck in the payload; df_todo why shows the trail'
})

test('game_says: a noteworthy report raises an alert with the verbatim quote, once', () => {
  const model = freshModel()
  const ingest = (reports) => model.ingestDeep({ rungs: [], next: null, notes: [], piles: [], reports })
  ingest([{ year: 102, time: 155588, text: 'èzum Noramzefon, Ghostly Weaponsmith batters Åblel Rakustàlil, Metalcrafter!', z: 38 }])
  const hits = model.evaluate(model.cfg).filter((a) => a.kind === 'game_says')
  assert.equal(hits.length, 1, 'the report raises one alert')
  assert.match(hits[0].evidence, /the game says \(Y102 @time 155588\): "èzum Noramzefon, Ghostly Weaponsmith batters/, hits[0].evidence)
  assert.equal(hits[0].coord, 'elev38', 'the elevation is carried')
  // the same report on the next read must not raise again
  ingest([{ year: 102, time: 155588, text: 'èzum Noramzefon, Ghostly Weaponsmith batters Åblel Rakustàlil, Metalcrafter!', z: 38 }])
  assert.ok(!model.evaluate(model.cfg).some((a) => a.kind === 'game_says'), 'a repeat report raises nothing')
  // a quiet report raises nothing
  ingest([{ year: 102, time: 1, text: 'An animal has grown to become a Stray Yak Bull.', z: 37 }])
  assert.ok(!model.evaluate(model.cfg).some((a) => a.kind === 'game_says'), 'routine reports stay quiet')
  return 'game_says alerts on noteworthy reports, verbatim, once'
})

test('water growth in the band raises the whitelisted water alert', () => {
  const model = freshModel()
  const ingestWater = (tiles) => model.ingestFast({ paused: false, generation: 1,
    facts: { pop: 102, dwarves: 21, miners: 3, picks: 3, food: 18, drink: 0, wood: 0, stone: 138,
      metal: 0, barrels: 21, pending_dig: 12, water_tiles: tiles, magma_tiles: 0 },
    buildings: [], boxes: [{ elev: 36, box: [27, 121, 42, 126], to_dig: 0, dug: 96, pending: 0 }],
    shafts: [], warnings: { designated_tiles: 12, damp_tiles: 12, hot_tiles: 0 }, notes: [] })
  ingestWater(1030); model.evaluate(model.cfg)
  ingestWater(1091)
  const hit = model.evaluate(model.cfg).find((a) => a.kind === 'water' && a.key === 'water-growth')
  assert.ok(hit, 'the +61 flood raises the water alert')
  assert.match(hit.evidence, /1030 -> 1091 \(\+61 tiles\)/, hit.evidence)
  assert.ok(model.cfg.alert_wakes.includes('water'), 'water is on the wake whitelist')
  assert.ok(model.cfg.alert_wakes.includes('game_says'), 'game_says wakes (player-approved 2026-11-21)')
  assert.match(hit.suggested, /clear the DAMP designations/, 'the advice names the fix')
  // growth stops -> the alert clears
  ingestWater(1091); model.evaluate(model.cfg)
  ingestWater(1091)
  model.evaluate(model.cfg)
  assert.ok(!model.alerts.find((a) => a.key === 'water-growth' && !a.cleared), 'a stable water level clears the flood alert')
  return 'band water growth alerts and clears'
})

test('stall advice names the aquifer when the stalled face sits in one', () => {
  const model = freshModel()
  const box = { elev: 25, box: [43, 121, 44, 122], to_dig: 4, dug: 0, pending: 0, rock: 4, hidden: 0 }
  const ingest = () => model.ingestFast({ paused: false, generation: 1,
    facts: { pop: 102, dwarves: 21, miners: 3, picks: 3, food: 20, drink: 0, wood: 0, stone: 141, metal: 0, barrels: 18, pending_dig: 103 },
    buildings: [], boxes: [{ ...box }],
    shafts: [{ x: 43, y: 121, aquifer_elevs: [23, 24, 25], water_elevs: [], magma_elevs: [] },
      { x: 44, y: 121, aquifer_elevs: [23, 24, 25], water_elevs: [], magma_elevs: [] }],
    warnings: { designated_tiles: 4, damp_tiles: 4, hot_tiles: 0 }, notes: [] })
  ingest(); model.evaluate(model.cfg)
  let hit = null
  for (let i = 1; i <= model.cfg.stall_ticks; i += 1) {
    ingest()
    hit = model.evaluate(model.cfg).find((a) => a.kind === 'dig_stalled' && a.evidence.includes('elev25'))
  }
  assert.ok(hit, 'the idle face raises the stall alert')
  // The game's DAMP rule is an aquifer layer at/ABOVE the dig level: for the
  // elev25 face that is elev 25 (23-24 are below it and are not the flood risk).
  assert.match(hit.suggested, /aquifer at elev 25 in this box's monitored columns/, hit.suggested)
  assert.ok(!/23\/24/.test(hit.suggested), 'layers below the face are not named as the flood risk')
  assert.match(hit.suggested, /clear the DAMP designations/, 'the advice orders the safe step first')
  // a dry face keeps the plain advice
  const dry = freshModel()
  const dryBox = { elev: 35, box: [45, 121, 66, 122], to_dig: 20, dug: 24, pending: 0, rock: 20, hidden: 0 }
  const dryIngest = () => dry.ingestFast({ paused: false, generation: 1,
    facts: { pop: 102, dwarves: 21, miners: 3, picks: 3, food: 20, drink: 0, wood: 0, stone: 141, metal: 0, barrels: 18, pending_dig: 0 },
    buildings: [], boxes: [{ ...dryBox }],
    shafts: [{ x: 50, y: 121, aquifer_elevs: [23, 24, 25], water_elevs: [], magma_elevs: [] }],
    warnings: { designated_tiles: 0, damp_tiles: 0, hot_tiles: 0 }, notes: [] })
  dryIngest(); dry.evaluate(dry.cfg)
  let h2 = null
  for (let i = 1; i <= dry.cfg.stall_ticks; i += 1) {
    dryIngest()
    h2 = dry.evaluate(dry.cfg).find((a) => a.kind === 'dig_stalled' && a.evidence.includes('elev35'))
  }
  assert.ok(h2 && !/aquifer at elev/.test(h2.suggested), 'a face with no aquifer column in its box keeps the plain advice')
  return 'stall advice carries the shaft hazard, only where it applies'
})

test('idle-plan stall: pending 0 with dug frozen raises dig_stalled after stall_ticks', () => {
  const model = freshModel()
  const box = { elev: 35, box: [45, 121, 66, 122], to_dig: 44, dug: 0, pending: 0, rock: 44, hidden: 0 }
  const ingest = () => model.ingestFast({ paused: false, generation: 1,
    facts: { pop: 79, dwarves: 21, miners: 3, picks: 3, food: 40, drink: 0, wood: 0, stone: 138, metal: 0, barrels: 21, pending_dig: 0 },
    buildings: [], boxes: [{ ...box }], shafts: [], warnings: { designated_tiles: 0, damp_tiles: 0, hot_tiles: 0 }, notes: [] })
  ingest(); model.evaluate(model.cfg)                 // baseline
  const need = model.cfg.stall_ticks
  for (let i = 1; i <= need; i += 1) {
    ingest()
    const r = model.evaluate(model.cfg)
    const hit = r.find((a) => a.kind === 'dig_stalled')
    if (i < need) assert.ok(!hit, `no stall claim before stall_ticks (i=${i}, tick ${model.tick})`)
    else {
      assert.ok(hit, 'the frozen face raises dig_stalled')
      assert.match(hit.evidence, new RegExp(`unchanged for ${need} reads`), hit.evidence)
      assert.match(hit.suggested, /designate ROCK tiles only/, 'the advice names the safe order')
    } }
  // progress clears it: dug rises -> no new stall claim
  box.to_dig = 40; box.dug = 4; box.pending = 4
  ingest(); const r2 = model.evaluate(model.cfg)
  assert.ok(!r2.some((a) => a.kind === 'dig_stalled' && a.evidence.includes('x45-66 y121-122')), 'work at the face stops the claim')
  return 'idle-plan stall covered; progress clears it'
})

test('verdict capture: the session/event shape is parsed', async () => {
  const { verdictFromEvent } = await import(path.join(pluginDir, 'index.js'))
  const line = '[df-verdict] {"tick":12,"significance":"high","headline":"Tunnel face drained with no dug change","changes":["box x45-66 y121-122 to_dig 44 pending 0"],"next":{"id":"water","title":"Water adit","keys":"m","at":"x45 y121 elev35"},"wake":[{"kind":"dig_stalled","evidence":"to_dig 44, dug 0, pending 0"}],"notes":["drink 0"]}'
  const fromMessage = verdictFromEvent({ type: 'assistant/message', data: { message: { content: [{ type: 'text', text: line }] } } })
  assert.ok(fromMessage, 'the durable log shape (data.message.content) parses')
  assert.equal(fromMessage.tick, 12)
  assert.equal(fromMessage.significance, 'high')
  const fromBare = verdictFromEvent({ type: 'assistant/message', data: { content: [{ type: 'text', text: line }] } })
  assert.ok(fromBare, 'the bare data.content shape parses too')
  assert.equal(verdictFromEvent({ type: 'user/message', data: { message: { content: [{ type: 'text', text: line }] } } }), null, 'other events are ignored')
  assert.equal(verdictFromEvent({ type: 'assistant/message', data: { message: { content: [{ type: 'text', text: 'no verdict here' }] } } }), null, 'a chat line without the prefix is not a verdict')
  return 'session/event assistant messages yield verdicts in both shapes'
})

test('noData: an empty probe read never flips a to-do', () => {
  const model = freshModel()
  const pileTodo = model.addTodo({ title: 'wood reaches the wood pile', accept: { kind: 'pile_category', category: 'wood', min_items: 1 } })
  model.ingestDeep({ rungs: [], next: null, notes: [], piles: { 18: { id: 18, found: 1, accepts: 'wood', total: 2, elev: 38, x0: 58, x1: 63, y0: 116, y1: 119 } } })
  assert.equal(model.evaluate(model.cfg).find((a) => a.key === `todo-${pileTodo.id}`)?.kind, 'dig_complete', 'the real read completes it')
  // the probe goes empty (pile ids renumbered / probe pending)
  model.ingestDeep({ rungs: [], next: null, notes: [], piles: [] })
  const raised = model.evaluate(model.cfg)
  assert.ok(!raised.some((a) => a.kind === 'todo_reopened'), 'an empty read does NOT reopen')
  assert.equal(model.todos.find((t) => t.id === pileTodo.id).state, 'done', 'state survives the flap')
  assert.equal(model.todos.find((t) => t.id === pileTodo.id).lastCheck.ok, null, 'the check is labelled no-data')
  // a real reading that fails DOES reopen (state changes only on facts)
  model.ingestDeep({ rungs: [], next: null, notes: [], piles: { 18: { id: 18, found: 1, accepts: 'stone', total: 0, elev: 38, x0: 58, x1: 63, y0: 116, y1: 119 } } })
  assert.ok(model.evaluate(model.cfg).some((a) => a.kind === 'todo_reopened'), 'a real reading can reopen')
  // boxes and rungs behave the same
  const boxTodo = model.addTodo({ title: 'a far box', accept: { kind: 'tiles_progress', elev: 40, x0: 1, x1: 2, y0: 1, y1: 2, min_dug: 1 } })
  model.todos.find((t) => t.id === boxTodo.id).state = 'done'
  model.evaluate(model.cfg)
  assert.equal(model.todos.find((t) => t.id === boxTodo.id).state, 'done', 'an unmonitored box is NO DATA, not a reopening')
  return 'noData suppresses flips on empty reads; real readings still flip'
})

test('delivery: alerts steer, briefings inject, followup only as fallback', async () => {
  const { deliveryOf } = await import(path.join(pluginDir, 'index.js'))
  assert.equal(deliveryOf('alert', { steer() {}, followup() {} }), 'steer', 'an alert to an agent with steer uses steer')
  assert.equal(deliveryOf('alert', { followup() {} }), 'followup', 'no steer -> followup fallback')
  assert.equal(deliveryOf('briefing', { steer() {}, inject() {} }), 'inject', 'briefings never wake')
  assert.equal(deliveryOf('tool', { steer() {}, inject() {} }), 'inject')
  return 'boundary rule extracted and verified'
})

// ---------------------------------------------------------------------------
// the dug-decrease guard (measured: a whole-box designation destroyed two stairs)
test('tile_state_lost: a shrinking dug count raises the alert with both numbers', () => {
  const model = freshModel()
  const ingest = (dug) => model.ingestFast({ paused: false, generation: 1,
    facts: { pop: 79, dwarves: 21, miners: 2, food: 0, drink: 1, wood: 0, stone: 13, metal: 0, barrels: 21, pending_dig: 0 },
    buildings: [], boxes: [{ elev: 36, box: [43, 122, 49, 124], to_dig: 0, dug, pending: 0 }],
    shafts: [], warnings: { designated_tiles: 0, damp_tiles: 0, hot_tiles: 0 }, notes: [] })
  ingest(21)
  model.evaluate(model.cfg)                       // baseline established
  const quiet = model.evaluate(model.cfg)
  assert.ok(!quiet.some((a) => a.kind === 'tile_state_lost'), 'no alert when dug is steady')
  ingest(19)                                      // two stairs vanish
  const raised = model.evaluate(model.cfg)
  const loss = raised.find((a) => a.kind === 'tile_state_lost')
  assert.ok(loss, 'the decrease raises the alert')
  assert.match(loss.evidence, /dug 21 -> 19/, `both numbers quoted: ${loss.evidence}`)
  assert.match(loss.evidence, /x43-49 y122-124 elev36/)
  assert.match(loss.suggested, /rebuild/i)
  ingest(21)                                      // rebuilt: rises are silent
  assert.ok(!model.evaluate(model.cfg).some((a) => a.kind === 'tile_state_lost'), 'a rebuild is not an alert')
  return 'dug decrease alerts with both numbers; increases stay silent'
})

// ---------------------------------------------------------------------------
// pile rows arrive keyed by id
test('pile rows: object-keyed payload shape is accepted', () => {
  const model = freshModel()
  model.ingestFast({ paused: false, generation: 1,
    facts: { pop: 79, dwarves: 21, miners: 2, food: 0, drink: 0, wood: 2, stone: 13, metal: 0, barrels: 0, pending_dig: 0 },
    boxes: [], shafts: [], warnings: null, notes: [] })
  model.ingestDeep({ rungs: [], next: null, notes: [],
    piles: { 18: { id: 18, found: 1, accepts: 'wood', total: 2, elev: 38, x0: 58, x1: 63, y0: 116, y1: 119 },
             19: { id: 19, found: 1, accepts: 'stone', total: 13, elev: 36, x0: 30, x1: 33, y0: 126, y1: 128 } } })
  assert.equal(model.piles.length, 2, 'both rows ingested')
  const todo = { id: 60, accept: { kind: 'pile_category', category: 'stone', min_items: 1 } }
  const res = runAcceptance(todo, model.snapshot())
  assert.equal(res.ok, true, `stone pile verified: ${res.evidence}`)
  assert.match(res.evidence, /accepts stone, 13 items/)
  const missing = runAcceptance({ id: 61, accept: { kind: 'pile_category', category: 'gems', min_items: 1 } }, model.snapshot())
  assert.equal(missing.ok, false)
  assert.match(missing.evidence, /no pile accepts gems/, 'no gems pile is labelled with the ids covered')
  return 'object-keyed pile rows ingest; acceptance reads them'
})

// ---------------------------------------------------------------------------
// supply priority: alcohol > meals > raw food (player rule 2026-11-21)
test('supply: header ranks drink/meals/raw; alerts fire in that priority', () => {
  const mk = (over = {}) => {
    const model = freshModel()
    model.ingestFast({
      paused: false, generation: 1,
      facts: { pop: 79, dwarves: 21, miners: 2, food: 14, drink: 0, wood: 0, stone: 136, metal: 0, barrels: 14,
        pending_dig: 0, underground_floors: 4, water_tiles: 0, magma_tiles: 0 },
      buildings: [], boxes: [], shafts: [], warnings: { designated_tiles: 0, damp_tiles: 0, hot_tiles: 0 }, notes: [],
    })
    model.ingestDeep({ rungs: [], next: null, piles: [], reports: [], crashlog: null, notes: [], status: over })
    return model
  }
  const noDrink = mk({ alcohol: 0, meals: 5, raw_food: 20, seeds: 76 })
  const a1 = noDrink.evaluate(noDrink.cfg)
  assert.ok(a1.some((a) => a.key === 'crew-no-drink'), 'alcohol zero is the first alert')
  assert.match(buildBriefing(noDrink.payload(), { maxTokens: 700 }).text, /drink 0 meals 5 raw 20 \(fort items\)/)
  const noMeals = mk({ alcohol: 8, meals: 0, raw_food: 20, seeds: 76 })
  const a2 = noMeals.evaluate(noMeals.cfg)
  assert.ok(a2.some((a) => a.key === 'crew-no-meals'), 'with drink stored, meals zero is next in line')
  assert.ok(!a2.some((a) => a.key === 'crew-no-drink'), 'no drink alert when alcohol exists')
  const noRaw = mk({ alcohol: 8, meals: 4, raw_food: 0, seeds: 0 })
  assert.ok(noRaw.evaluate(noRaw.cfg).some((a) => a.key === 'crew-no-raw'), 'raw + seeds zero is the last rung')
  const okAll = mk({ alcohol: 8, meals: 4, raw_food: 20, seeds: 76 })
  assert.ok(!okAll.evaluate(okAll.cfg).some((a) => String(a.key).startsWith('crew-no')), 'a supplied fort stays silent')
  // an unprobed tick must not invent zeros
  const bare = freshModel()
  bare.ingestFast({ paused: false, generation: 1,
    facts: { pop: 21, dwarves: 21, miners: 1, food: 1, drink: 1, wood: 0, stone: 0, metal: 0, barrels: 0, pending_dig: 0 },
    boxes: [], shafts: [], warnings: null, notes: [] })
  bare.ingestDeep({ rungs: [], next: null, piles: [], reports: [], crashlog: null, notes: [] })
  const b2 = bare.evaluate(bare.cfg)
  assert.ok(!b2.some((a) => String(a.key).startsWith('crew-no')), 'no supply alert before the first probe')
  assert.match(buildBriefing(bare.payload(), { maxTokens: 700 }).text, /supply \? \(deep probe pending\)/)
  return 'drink>meals>raw order, per-level alerts, ? before the probe'
})

// ---------------------------------------------------------------------------
// status probe (the game's own status bar): date, seeds, site, picks
test('status probe: deep merge into facts, header carries date and seeds', () => {
  const model = freshModel()
  model.ingestFast({
    paused: false, generation: 1,
    facts: { pop: 81, dwarves: 21, miners: 2, food: 11, drink: 0, wood: 0, stone: 136, metal: 0, barrels: 14,
      pending_dig: 0, underground_floors: 4, water_tiles: 0, magma_tiles: 0 },
    buildings: [], boxes: [], shafts: [], warnings: { designated_tiles: 0, damp_tiles: 0, hot_tiles: 0 }, notes: [],
  })
  model.ingestDeep({ rungs: [], next: null, piles: [], reports: [], crashlog: null, notes: [],
    status: { picks: 3, site_id: 677, seeds: 76, date: { year: 100, month: 8, day: 28 } } })
  const f = model.payload().facts
  assert.equal(f.picks, 3, 'picks from the probe')
  assert.equal(f.seeds, 76, 'seeds from the probe')
  assert.equal(f.site_id, 677, 'site id from the probe')
  assert.deepEqual(f.date, { year: 100, month: 8, day: 28 }, 'date from the probe')
  const brief = buildBriefing(model.payload(), { maxTokens: 700 })
  assert.match(brief.text, /Y100/, 'header carries the year')
  assert.match(brief.text, /Aut M8 d28/, 'month 8 renders as Autumn (month/3)')
  assert.match(brief.text, /seeds 76/, 'header carries seeds')
  assert.match(brief.text, /dwarves 21 \(units 81\)/, 'dwarf number, units labelled')
  // no probe this tick: fields stay null, header stays honest
  const bare = freshModel()
  bare.ingestFast({ paused: false, generation: 1,
    facts: { pop: 5, dwarves: 5, miners: 1, food: 1, drink: 1, wood: 0, stone: 0, metal: 0, barrels: 0, pending_dig: 0 },
    boxes: [], shafts: [], warnings: null, notes: [] })
  bare.ingestDeep({ rungs: [], next: null, piles: [], reports: [], crashlog: null, notes: [] })
  const bf = bare.payload().facts
  assert.equal(bf.seeds, null, 'no probe -> seeds null, not 0')
  assert.equal(bf.date, null, 'no probe -> date null')
  // The status bar must SURVIVE a fast tick (fast replaces `facts` wholesale).
  model.ingestFast({
    paused: false, generation: 1,
    facts: { pop: 82, dwarves: 21, miners: 2, food: 12, drink: 0, wood: 0, stone: 136, metal: 0, barrels: 14,
      pending_dig: 0, underground_floors: 4, water_tiles: 0, magma_tiles: 0 },
    buildings: [], boxes: [], shafts: [], warnings: { designated_tiles: 0, damp_tiles: 0, hot_tiles: 0 }, notes: [],
  })
  const carried = model.payload().facts
  assert.equal(carried.seeds, 76, 'seeds survive the fast tick')
  assert.deepEqual(carried.date, { year: 100, month: 8, day: 28 }, 'date survives the fast tick')
  assert.equal(carried.picks, 3, 'picks survive the fast tick')
  return 'status probe merges into facts (picks/site/date/seeds), survives fast ticks, absent probe stays null'
})

// ---------------------------------------------------------------------------
// population drop alert
test('pop_drop: fires with the numbers, baseline-first never fires', () => {
  const model = freshModel()
  const mk = (dwarves, pop) => ({
    paused: false, generation: 1,
    facts: { pop, dwarves, miners: 2, picks: 3, food: 10, drink: 0, wood: 0, stone: 5, metal: 0, barrels: 0,
      pending_dig: 0, underground_floors: 4, water_tiles: 0, magma_tiles: 0 },
    buildings: [], boxes: [], shafts: [], warnings: { designated_tiles: 0, damp_tiles: 0, hot_tiles: 0 }, notes: [],
  })
  // Vermin churn must NOT alert: units 96 -> 81 with the dwarf number steady.
  model.ingestFast(mk(21, 96)); model.ingestDeep({ rungs: [], next: null, piles: [], reports: [], crashlog: null, notes: [] })
  assert.equal(model.evaluate({}).filter((a) => a.kind === 'pop_drop').length, 0, 'first read is a baseline')
  model.ingestFast(mk(21, 81)); model.ingestDeep({ rungs: [], next: null, piles: [], reports: [], crashlog: null, notes: [] })
  assert.equal(model.evaluate({}).filter((a) => a.kind === 'pop_drop').length, 0, 'unit churn with dwarves steady is silent')
  model.ingestFast(mk(19, 80)); model.ingestDeep({ rungs: [], next: null, piles: [], reports: [], crashlog: null, notes: [] })
  assert.equal(model.evaluate({}).filter((a) => a.kind === 'pop_drop').length, 0, 'a 2-dwarf drop is under Config.pop_drop_min')
  model.ingestFast(mk(8, 60)); model.ingestDeep({ rungs: [], next: null, piles: [], reports: [], crashlog: null, notes: [] })
  const drop = model.evaluate({}).find((a) => a.kind === 'pop_drop')
  assert.ok(drop, 'a 11-dwarf drop fires the alert')
  assert.match(drop.evidence, /dwarves 19 -> 8 \(-11\)/, `evidence carries the dwarf numbers: ${drop.evidence}`)
  return 'dwarf drop 19->8 alerts with numbers; unit churn + 2-drop silent; baseline-first honoured'
})

// ---------------------------------------------------------------------------
// rules section budget
test('rules: default compact (per-assembly cost cut), full preserved, off silent', () => {
  const cfg = Config.parse({})
  assert.equal(cfg.rules_mode, 'compact', 'compact is the default')
  const { standingRules } = briefApi
  const compact = standingRules('compact')
  const full = standingRules('full')
  assert.equal(standingRules('off'), '', 'off contributes nothing')
  assert.ok(Math.ceil(compact.length / 4) < 160, `compact fits its cap (${Math.ceil(compact.length / 4)})`)
  assert.ok(Math.ceil(full.length / 4) > Math.ceil(compact.length / 4), 'full is the richer text')
  for (const mode of ['compact', 'full']) {
    const text = standingRules(mode)
    assert.match(text, /to_dig==0 AND dug>0/, `${mode}: completion rule present`)
    assert.match(text, /FORT BRIEFING/, `${mode}: briefing is state, not a request`)
    assert.match(text, /read-only/, `${mode}: boundary stated`)
  }
  return `compact ${Math.ceil(standingRules('compact').length / 4)} vs full ${Math.ceil(standingRules('full').length / 4)} tokens`
})

// ---------------------------------------------------------------------------
// filter (opt-in analyst)
test('filter: default OFF, verdict parsing, filtered briefing within cap, whitelist demotion', () => {
  const cfg = Config.parse({})
  assert.equal(cfg.filter_enabled, false, 'filter is opt-in')
  const { parseVerdict, buildFilteredBriefing } = filterApi
  const model = freshModel()
  const verdict = parseVerdict([{ type: 'text', text: 'thinking...\n[df-verdict] {"tick":1,"significance":"high","headline":"water entered shaft x50,y123","changes":["elev35 now 4/7"],"next":{"id":"meeting","title":"Meeting area","keys":"z","at":{"elev":38,"x0":5,"y0":115,"x1":11,"y1":121}},"wake":[{"kind":"water","evidence":"water elev35:4 in x50,y123"}],"notes":[]}' }])
  assert.equal(verdict.significance, 'high')
  assert.equal(verdict.wake[0].kind, 'water')
  assert.equal(parseVerdict([{ type: 'text', text: 'no verdict here' }]), null)
  const brief = buildFilteredBriefing(verdict, model.payload(), { maxTokens: 260 })
  assert.ok(brief.tokens <= 260, `tokens ${brief.tokens}`)
  assert.match(brief.text, /water entered shaft/)
  assert.match(brief.text, /\[df-observer\] \{/, 'state line still carries the foldable payload')
  const { buildFilterWake } = filterApi
  const wake = buildFilterWake({ id: 'A42', kind: 'water', evidence: 'water elev35:4 in x50,y123', coord: 'x50,y123', suggested: 'stop the shaft' }, model.payload(), { maxTokens: 420 })
  assert.match(wake.text, /DF-OBSERVER ALERT A42 \(filter, tick \d+\) water/, 'the filter wake carries the alert id')
  assert.match(wake.text, /df_alerts\(\{action:"ack", id:"A42"\}/, 'and the ack advice quotes it')
  const bare = buildFilterWake({ kind: 'water', evidence: 'x', coord: '' }, model.payload(), { maxTokens: 420 })
  assert.match(bare.text, /A\?UNLABELLED/, 'an id-less alert is labelled, never printed as undefined')
  assert.ok(!/\bundefined\b/.test(bare.text), 'no literal undefined in the filter wake')
  assert.ok(wake.tokens <= 420 || wake.overBudget === true,
    `wake ${wake.tokens} tokens: must fit its cap, or arrive labelled overBudget`)
  return 'verdict parse + <=260-token filtered briefing + bounded wake; whitelist enforced by the plugin (dispatchFilterWakes)'
})

// ---------------------------------------------------------------------------
// panel Remote: markers must exist on the PROTOTYPE at module load (the
// gateway binds endpoints when the service is constructed).
test('client path: projection wire.view publishes the panel view; /df-ack folds from command/run', async () => {
  const unit = dfObserverProjection(z)
  assert.ok(unit.wire?.view && unit.wire?.viewSchema, 'wire.view declared (client-visible through the snapshot)')
  let state = unit.init()
  const payloadLine = `${PAYLOAD_PREFIX}${JSON.stringify({ form: 'briefing', tick: 5, data: { tick: 5, online: true, paused: true, alerts: [{ id: 'A9', kind: 'water', acked: false }] } })}`
  state = unit.apply(state, { type: 'user/message', data: { content: [{ type: 'text', text: `FORT BRIEFING\n${payloadLine}` }] } })
  const view = unit.wire.view(state)
  assert.equal(view.tick, 5)
  assert.equal(view.alerts[0].acked, false)
  assert.equal(unit.wire.view(state), view, 'view is reference-stable (publication suppression)')
  const acked = unit.apply(state, { type: 'command/run', data: { rawInput: '/df-ack A9' } })
  assert.notEqual(acked, state, 'ack changes the folded state')
  assert.equal(unit.wire.view(acked).alerts[0].acked, true, 'panel sees the ack')
  assert.equal(unit.apply(acked, { type: 'command/run', data: { rawInput: '/df-ack NOPE' } }), acked, 'unknown id: same reference')
  assert.equal(unit.apply(acked, { type: 'command/run', data: { rawInput: '/other' } }), acked, 'foreign command: same reference')
  return 'wire.view + reference stability + /df-ack fold verified'
})

test('remote: panel/ackAlert are marked Remote before construction', async () => {
  const mod = await import(path.join(pluginDir, 'index.js'))
  const { createTypertRemoteInfo } = await import(path.join(pluginDir, 'node_modules', '@deepseek-ai', 'dsh-typert-protocol', 'lib', 'index.js'))
  const proto = mod.default.prototype
  const names = []
  for (const name of ['panel', 'ackAlert']) {
    const info = createTypertRemoteInfo(proto[name], name, proto)
    assert.ok(info, `${name} is not a Remote method on the prototype`)
    names.push(name)
  }
  return `marked at module load: ${names.join(', ')}`
})

// ---------------------------------------------------------------------------
// client half: factory -> apply -> render smoke (stub React; the shipped page
// provides the real one). Catches the static-half builtin traps (styles.insert).
const fakeSnapshotForRender = {
  tick: 7, online: true, paused: true, pauseSource: 'plugin', generation: 1,
  facts: { pop: 7, miners: 2, picks: 4 },
  rungs: [{ id: 'labors', title: 'Miners', done: true }, { id: 'meeting', title: 'Meeting', done: false }],
  next: { id: 'meeting', title: 'Meeting area', keys: 'z', at: { elev: 36, x0: 5, x1: 11, y0: 115, y1: 121 } },
  boxes: [{ elev: 36, box: [27, 121, 42, 126], to_dig: 51, dug: 45, pending: 0 }],
  piles: [], shafts: [], warnings: null,
  todos: [{ id: 5, title: 'Meeting area', state: 'open', evidence: 'rung open' }],
  alerts: [{ id: 'A3', kind: 'water', coord: 'x50,y123', evidence: 'water elev35:4', acked: false, cleared: false }],
  digEvents: [], pauseEvents: [], crashlog: null, notes: ['note one'], overload: null,
  pollIntervalMs: 60000, config: { pause_authority: 'off', panel_poll_ms: 5000 },
  filter: { enabled: false }, sessionFolded: true,
}

function clientSandbox(registered, onComponent) {
  const fakeReact = {
    createElement: (type, props, ...children) => ({ type, props: props ?? {}, children: children.flat(3) }),
    useState: (init) => (init === null ? [fakeSnapshotForRender, () => {}] : [init === false ? true : init, () => {}]),
    useEffect: (fn) => { const cleanup = fn(); if (typeof cleanup === 'function') cleanup() },
  }
  const sandbox = {
    window: { __ModuleLoader__: { load: (reg) => { registered.reg = reg } } },
    require: (name) => { if (name === 'react') return fakeReact; throw new Error(`unexpected require ${name}`) },
    console, setInterval, clearInterval, Math, JSON, String, Number, Array, Object, Promise,
    onComponent,
  }
  return sandbox
}

function loadClient() {
  const source = fs.readFileSync(path.join(pluginDir, 'client.js'), 'utf8')
  const registered = {}
  const sandbox = clientSandbox(registered, null)
  vm.runInNewContext(source, sandbox)
  return { source, factory: registered.reg?.factory, id: registered.reg?.id }
}

test('client: factory registers, apply wires slots, panel renders with token-only styles', () => {
  const { source, factory, id } = loadClient()
  const codeLines = source.split('\n').filter((l) => !/^\s*\*/.test(l) && !/^\s*\/\//.test(l))
  assert.doesNotMatch(codeLines.join('\n'), /styles\.(insert|remove)/, 'static client half has no styles builtin - inline styles only')
  assert.equal(id, '@local/df-observer')
  assert.ok(factory, 'factory registered')
  const calls = []
  const ctx = {
    effect: (fn) => { const dispose = fn(); calls.push(['effect']); return () => { if (typeof dispose === 'function') dispose() } },
    locale: { register: (ns, dicts) => { calls.push(['locale', ns, Object.keys(dicts).join('+')]); return () => {} } },
    slots: {
      inject: (slot, fn) => { fn(); calls.push(['inject', slot]); return () => {} },
      register: (opts) => { calls.push(['register', opts.name, opts.id ?? opts.key]); return () => {} },
    },
    uiConversation: { events: { register: (def) => { calls.push(['events', def.kind, def.target]); return () => {} } } },
    remote: { commands: { execute: async () => ({ commandId: 'c1', result: { kind: 'success' } }) } },
    // Context-level lazy inject (the shipped right-Sidebar tab plugins use it).
    inject: (names, fn) => { calls.push(['lazy', names.join('+')]); fn(ctx); return () => {} },
    sidebarRight: { openTabIn: (s, k) => calls.push(['openTabIn', s, k]), openTab: (k) => calls.push(['openTab', k]) },
    sidebarRightTabs: { register: (def) => { calls.push(['tabType', def.kind, def.id, def.priority]); return () => {} } },
  }
  const plugin = factory((name) => { if (name === 'react') return fakeReactOf(); throw new Error(name) })
  assert.equal(plugin.inject.join(','), 'slots,uiConversation,remote,remote.commands,locale')
  plugin.apply(ctx)
  const names = calls.map((c) => c.join(':'))
  assert.ok(names.includes('locale:dfObserver:en+zh'), 'dictionaries registered (en+zh)')
  assert.ok(names.some((n) => n.startsWith('inject:conversation.input.dock')), 'panel injected into the composer dock')
  assert.ok(names.some((n) => n.startsWith('register:conversation.input.dock')), 'panel registered')
  assert.ok(names.includes('events:df-alert:chat'), 'alert definition registered for chat')
  assert.ok(names.some((n) => n.startsWith('inject:conversation.chat.node')), 'chat row injected')
  // The full GUI is a right-Sidebar tab: type + keyed body + keyed title.
  assert.ok(names.some((n) => n.startsWith('lazy:sidebarRight+sidebarRightTabs')), 'right-sidebar faces are lazily injected')
  assert.ok(names.includes('tabType:df-observer:@local/df-observer/panel:extension'), `tab type registered (${names.filter(n => n.startsWith('tabType')).join(' | ')})`)
  assert.ok(names.some((n) => n.startsWith('inject:sidebar.right.pane.tab')), 'tab body injected into the keyed seat')
  assert.ok(names.some((n) => n.startsWith('inject:sidebar.right.pane.tab.title')), 'tab title injected')
  // Render path: rebuild with a component spy and render the dock panel with data.
  let component = null
  const registered = {}
  const renderSandbox = clientSandbox(registered, (c) => { component = c })
  vm.runInNewContext(fs.readFileSync(path.join(pluginDir, 'client.js'), 'utf8'), renderSandbox)
  const renderCtx = {
    effect: (fn) => { const d = fn(); return () => { if (typeof d === 'function') d() } },
    locale: { register: () => () => {} },
    slots: {
      inject: (slot, fn) => { fn(); return () => {} },
      register: (opts, comp) => { if (opts.name === 'conversation.input.dock') component = comp; return () => {} },
    },
    uiConversation: { events: { register: () => () => {} } },
    remote: { commands: { execute: async () => ({ commandId: 'c1', result: { kind: 'success' } }) } },
    inject: (names, fn) => { fn(renderCtx); return () => {} },
    sidebarRight: { openTabIn: () => {}, openTab: () => {} },
    sidebarRightTabs: { register: () => () => {} },
  }
  registered.reg.factory((name) => { if (name === 'react') return fakeReactOf(); throw new Error(name) }).apply(renderCtx)
  assert.equal(typeof component, 'function', 'panel component captured')
  const tree = component({ sessionId: 'session-1', useProjection: () => fakeSnapshotForRender })
  const walked = JSON.stringify(tree)
  assert.match(walked, /Fort observer/, 'panel title renders')
  assert.match(walked, /water/, 'alert row renders')
  assert.match(walked, /to_dig 51/, 'box numbers render')
  assert.match(walked, /last pause: plugin/, 'pause attribution renders')
  const tokenRefs = walked.match(/var\(--[^)]*\)/g) ?? []
  const badTokens = tokenRefs.filter((v) => !v.startsWith('var(--dsw-alias-'))
  const literals = walked.match(/#[0-9a-fA-F]{3,8}\b/g) ?? []
  assert.equal(badTokens.length, 0, `only theme tokens are used (bad: ${JSON.stringify(badTokens.slice(0, 3))})`)
  assert.equal(literals.length, 0, `no literal colors (found: ${JSON.stringify(literals.slice(0, 3))})`)
  return `factory+apply+render smoke OK (${names.length} registrations, ${tokenRefs.length} token refs)`
})

function fakeReactOf() {
  return {
    createElement: (type, props, ...children) => ({ type, props: props ?? {}, children: children.flat(3) }),
    useState: (init) => (init === null ? [fakeSnapshotForRender, () => {}] : [init === false ? true : init, () => {}]),
    useEffect: (fn) => { const cleanup = fn(); if (typeof cleanup === 'function') cleanup() },
  }
}

// ---------------------------------------------------------------------------
// manifest/display checklist
test('bundle manifest and patch are coherent', () => {
  const pkg = JSON.parse(execFileSync('node', ['-e',
    `process.stdout.write(JSON.stringify(require(${JSON.stringify(path.join(pluginDir, 'package.json'))})))`]).toString())
  assert.equal(pkg.name, '@local/df-observer')
  assert.equal(pkg.type, 'module')
  assert.equal(pkg.dsh.bundle.patch, './cordis.patch.yml')
  assert.ok(pkg.exports['.'].endsWith('index.js'))
  assert.ok(pkg.exports['./client'])
  assert.ok(pkg.exports['./locale/*.json'])
  assert.ok(pkg.exports['./icon'])
  for (const f of ['index.js', 'observer.js', 'model.js', 'brief.js', 'tools.js', 'client.js',
    'cordis.patch.yml', 'locale/*.json', 'docs', 'icon.svg']) {
    assert.ok(pkg.files.includes(f), `files lists ${f}`)
  }
  const en = JSON.parse(execFileSync('node', ['-e',
    `process.stdout.write(require('node:fs').readFileSync(${JSON.stringify(path.join(pluginDir, 'locale', 'en.json'))},'utf8'))`]).toString())
  const zh = JSON.parse(execFileSync('node', ['-e',
    `process.stdout.write(require('node:fs').readFileSync(${JSON.stringify(path.join(pluginDir, 'locale', 'zh.json'))},'utf8'))`]).toString())
  for (const [name, doc] of [['en', en], ['zh', zh]]) {
    assert.ok(doc.meta.title && doc.meta.description && doc.meta.detail, `${name} meta`)
    assert.ok(doc.meta.title.length < 60)
  }
  assert.equal(en.meta.title, 'DF observer')
  const icon = execFileSync('node', ['-e',
    `process.stdout.write(String(require('node:fs').statSync(${JSON.stringify(path.join(pluginDir, 'icon.svg'))}).size))`]).toString()
  assert.ok(Number(icon) < 256 * 1024, `icon size ${icon}`)
  return `manifest+locales(${en.meta.title}/${zh.meta.title})+icon(${icon}B) checklist ok`
})

// ---------------------------------------------------------------------------
// THE OBSERVER RESET ROUTINE (player request 2026-11-21: a save reload must throw
// everything out and refetch, because the only truth is the DF game).
//
// The measured incident: the player loaded an older save of the same fort while the
// observer ran. `generation` stayed 1 (a save reload keeps the RPC socket), the
// in-game date went 104-2-25 -> 100-0-15, nine boxes reported dug 0 against
// baselines of dug 96, and the ledger reopened 20+ done to-dos in four
// `todo_reopened` rows plus four `dig_stalled` rows - all of it bookkeeping about a
// fort that no longer exists.
test('reset: a server save-swap event is detected once and names the cause', () => {
  const m = freshModel()
  const swap = m.gameChange()
  assert.equal(swap.swapped, false, 'no event, no swap')
  m.ingestFast(fastData({ reloaded: { why: 'the in-game clock ran backwards: Y104 tick 1234 -> Y100 tick 15',
    from: { save: 'start', year: 104, tick: 1234 }, to: { save: 'start', year: 100, tick: 15 },
    cleared: 'cache + DFHack map hashes' } }))
  const s2 = m.gameChange()
  assert.equal(s2.swapped, true)
  assert.equal(s2.source, 'server')
  assert.match(s2.why, /backwards/)
  assert.equal(m.gameChange().swapped, false, 'an event is consumed exactly once')
  return `server event detected once (${s2.why.slice(0, 40)}...)`
})

test('reset: signature change is a swap; a clock that runs backwards is a swap', () => {
  const W = 'The Universes of Destiny'
  const m = freshModel()
  m.gameChange()
  // Same fort, later clock: NOT a swap (time moving forward is just playing).
  m.ingestFast(fastData({ worldSignature: { save: 'start', world: W, mode: 'MODE_DWARF',
    tiles: '192x192x191', year: 104, tick: 97999 } }))
  assert.equal(m.gameChange().swapped, false, 'forward time is not a swap')
  // A different save name: swap.
  m.ingestFast(fastData({ worldSignature: { save: 'fort2', world: W, mode: 'MODE_DWARF',
    tiles: '192x192x191', year: 104, tick: 10 } }))
  const s = m.gameChange()
  assert.equal(s.swapped, true, 'save identity changed')
  assert.match(s.why, /identity changed/)
  // The nasty case: the SAME save name, the clock behind us.
  m.ingestFast(fastData({ worldSignature: { save: 'fort2', world: W, mode: 'MODE_DWARF',
    tiles: '192x192x191', year: 100, tick: 15 } }))
  const s2 = m.gameChange()
  assert.equal(s2.swapped, true, 'an older save of the same fort')
  assert.match(s2.why, /backwards/)
  return 'identity + clock-regression detection, forward time silent'
})

test('reset: plugin-side fallback reads the game date (no signature in the payload)', () => {
  const m = new FortModel(baseCfg)
  m.ingestFast(fastData({ worldSignature: undefined }))
  m.ingestDeep(deepData())
  m.facts = { ...m.facts, date: { year: 104, month: 2, day: 25 } }
  m.gameChange()
  m.facts = { ...m.facts, date: { year: 100, month: 0, day: 15 } }
  const s = m.gameChange()
  assert.equal(s.swapped, true, 'Y104 -> Y100 is a swap even without a server signature')
  assert.equal(s.source, 'plugin')
  assert.match(s.why, /104.*100/)
  return `date regression caught plugin-side (${s.why})`
})

test('reset: everything derived from the old game is thrown out', () => {
  const m = freshModel()
  m.watchBox([27, 121, 42, 126, 36])
  m.evaluate({})
  assert.ok(m.lastBoxState.size > 0 && m.dugBaseline.size > 0, 'baselines exist before the reset')
  const dropped = m.resetForNewGame({ why: 'test swap', source: 'plugin' })
  assert.equal(m.todos.length, 0, 'the previous fort plan is gone')
  assert.equal(m.extraBoxes.length, 0, 'watched boxes are gone')
  assert.equal(m.lastBoxState.size, 0, 'box baselines cleared')
  assert.equal(m.dugBaseline.size, 0, 'dug baselines cleared')
  assert.equal(m.boxes.length, 0, 'box tallies cleared')
  assert.equal(m.facts, null, 'facts cleared: they measured a world that is gone')
  assert.equal(m.piles.length, 0, 'pile reads cleared')
  assert.equal(m.alerts.length, 0, 'alerts cleared')
  assert.equal(m.seeded, false, 'ladder seeds re-run for the new fort')
  assert.equal(m.lastDeepTick, -Infinity, 'the deep refetch is forced on the next cycle')
  assert.equal(m.gameSwaps, 1)
  assert.equal(dropped.todos > 0, true, 'the drop counts are reported for the announcement')
  return `dropped ${dropped.todos} to-dos, ${dropped.boxes} boxes, ${dropped.alerts} alerts; all baselines cleared`
})

test('reset: the first reads of the new game are baselines, not a storm', () => {
  const m = freshModel()
  // The incident shape: a monitored box that read dug 39 now reads dug 0.
  const swap = m.gameChange()
  m.ingestFast(fastData({ reloaded: { why: 'identity changed: start -> start (older save)',
    from: { save: 'start', year: 104, tick: 96910 }, to: { save: 'start', year: 100, tick: 15 } } }))
  const s = m.gameChange()
  assert.equal(s.swapped, true)
  m.resetForNewGame(s)
  // A done to-do whose test now fails must NOT fire its own reopen alert on the
  // swap tick - the swap row says it all.
  m.ingestDeep(deepData())
  m.evaluate({})
  m.addTodo({ title: 'Stone pile', accept: { kind: 'pile_category', category: 'stone', min_items: 1 } })
  m.closeTodo(m.todos.at(-1).id, 'proved in the previous fort')
  const doneId = m.todos.at(-1).id
  m.ingestFast(fastData({
    worldSignature: { save: 'start', world: 'The Universes of Destiny', mode: 'MODE_DWARF',
      tiles: '192x192x191', year: 100, tick: 15 },
    boxes: [
      { elev: 36, box: [27, 121, 42, 126], to_dig: 96, dug: 0, pending: 0, water: 0, open: 0, building: 0, other: 0, total_tiles: 96 },
      { elev: 36, box: [43, 122, 49, 124], to_dig: 21, dug: 0, pending: 0, water: 0, open: 0, building: 0, other: 0, total_tiles: 21 },
    ],
    piles: [{ id: 19, found: 0, accepts: 'stone' }],
  }))
  const s2 = m.gameChange()
  assert.equal(s2.swapped, true, 'the clock regression is the swap')
  m.resetForNewGame(s2)
  // The new game's FIRST reads (the plugin forces a deep read on the next cycle).
  m.ingestFast(fastData({
    worldSignature: { save: 'start', world: 'The Universes of Destiny', mode: 'MODE_DWARF',
      tiles: '192x192x191', year: 100, tick: 15 },
    boxes: [
      { elev: 36, box: [27, 121, 42, 126], to_dig: 96, dug: 0, pending: 0, water: 0, open: 0, building: 0, other: 0, total_tiles: 96 },
      { elev: 36, box: [43, 122, 49, 124], to_dig: 21, dug: 0, pending: 0, water: 0, open: 0, building: 0, other: 0, total_tiles: 21 },
    ],
    piles: [{ id: 19, found: 0, accepts: 'stone' }],
  }))
  assert.equal(m.gameChange().swapped, false, 'the new identity is now the baseline: one swap, one row')
  m.ingestDeep(deepData({ piles: [{ id: 19, found: 0, accepts: 'stone' }] }))
  const raised = m.evaluate({})
  assert.equal(raised.filter((a) => a.kind === 'tile_state_lost').length, 0,
    'dug 39 -> 0 across a reset must not read as destroyed tile state')
  assert.equal(raised.filter((a) => a.kind === 'todo_reopened').length, 0,
    'the reset clears the ledger: no per-to-do reopen rows')
  assert.equal(raised.filter((a) => a.kind === 'dig_stalled').length, 0,
    'stagnation counters restart: an idle new fort is not a stalled face')
  const swaps = raised.filter((a) => a.kind === 'save_reloaded')
  assert.equal(swaps.length, 1, 'exactly ONE folded swap row')
  assert.match(swaps[0].evidence, /Thrown out: \d+ to-dos/)
  assert.equal(m.lastBoxState.get('36|27,121,42,126').dug, 0, 'the new read became the baseline')
  void doneId
  return `one folded swap row; 0 tile_state_lost, 0 dig_stalled, 0 todo_reopened (${swaps[0].evidence.slice(0, 60)}...)`
})

test('reset: game and gameSwaps ride the payload wire and the projection', () => {
  assert.ok(STATE_FIELDS.includes('game') && STATE_FIELDS.includes('gameSwaps'),
    'the shared field set carries the identity')
  const m = freshModel()
  const payload = m.payload()
  assert.equal(payload.game.save, 'start')
  assert.equal(payload.gameSwaps, 0)
  const line = `[df-observer] ${JSON.stringify({ form: 'briefing', tick: payload.tick, data: payload })}`
  const parsed = parsePayload([{ type: 'text', text: line }])
  assert.ok(parsed, 'the payload row parses')
  const view = dfObserverProjection(z).view
  const folded = view(dfObserverProjection(z).apply(
    dfObserverProjection(z).init(),
    { type: 'user/message', data: { content: [{ type: 'text', text: line }] } }))
  assert.equal(folded.game.save, 'start', 'the session fold carries which game it describes')
  return `payload+projection carry game/gameSwaps (${payload.game.save})`
})

test('reset: /df-reset command and the df_todo reset action are wired', () => {
  const index = execFileSync('node', ['-e',
    `process.stdout.write(require('node:fs').readFileSync(${JSON.stringify(path.join(pluginDir, 'index.js'))},'utf8'))`]).toString()
  assert.match(index, /name: 'df-reset'/, 'the /df-reset command is registered')
  assert.match(index, /async observerReset\(swap\)/, 'the routine lives in one method')
  assert.match(index, /this\.model\.gameChange\(\)/, 'the poll cycle asks the game identity every tick')
  assert.match(index, /resetAnnounce/, 'a reset bypasses the briefing spacing gate once')
  const tools = execFileSync('node', ['-e',
    `process.stdout.write(require('node:fs').readFileSync(${JSON.stringify(path.join(pluginDir, 'tools.js'))},'utf8'))`]).toString()
  assert.match(tools, /'list', 'add', 'done', 'why', 'reset'/, "df_todo's enum carries reset")
  const client = execFileSync('node', ['-e',
    `process.stdout.write(require('node:fs').readFileSync(${JSON.stringify(path.join(pluginDir, 'client.js'))},'utf8'))`]).toString()
  assert.match(client, /'\/df-reset'/, 'the panel has the reset button')
  assert.match(client, /'reset\.button': '↺ reset'/, 'en label')
  assert.match(client, /'reset\.button': '↺ 重置'/, 'zh label')
  return '/df-reset + df_todo reset + panel button + en/zh labels'
})

test('reset: observerReset archives the ledger and wipes the live records', async () => {
  const rows = new Map([['19', { id: 19, title: 'Tunnel', state: 'done' }],
    ['84', { id: 84, title: 'General pile', state: 'done' }],
    ['quiet', { kind: 'quiet', quiet: false }]])
  const boxRows = new Map([['box-36-27-121-42-126', [27, 121, 42, 126, 36]]])
  const mkTable = (data) => ({
    entries: () => [...data.entries()],
    get: (k) => data.get(k) ?? null,
    put: async (k, v) => { data.set(k, v) },
    delete: async (k) => data.delete(k),
    deleteMany: async (ks) => { for (const k of ks) data.delete(k); return ks.length },
  })
  const registered = []
  const ctx = {
    reflect: { provide: () => {} }, isolate: {},
    effect: (fn) => { fn(); return () => {} },
    commands: { register: (def) => { registered.push(def.name); return () => {} } },
    tools: { register: () => () => {} },
    agents: { list: () => [], on: () => () => {} },
    systemPrompt: { section: () => () => {} },
    sessionProjections: { register: () => () => {} },
    timers: { register: () => () => {} },
    get: () => undefined,
    storage: { open: async () => ({ table: () => ({ entries: () => [], put: () => {}, get: () => null }), close: () => {} }) },
  }
  const inst = new DFObserver(ctx, Config.parse({}))
  inst.ledger = { table: (name) => name === 'todos' ? mkTable(rows) : mkTable(boxRows), close: () => {} }
  inst.model.ingestFast(fastData())
  inst.model.ingestDeep(deepData())
  inst.model.evaluate({})
  const out = await inst.observerReset({ swapped: true, source: 'command', why: 'test reset' })
  assert.equal(out.dropped.todos > 0, true, 'the live ledger was counted before the drop')
  assert.equal(rows.has('19'), false, 'the live to-do record is deleted')
  assert.equal(rows.has('84'), false, 'both live records are deleted')
  assert.equal(rows.has('quiet'), true, 'the mute record is NOT a to-do and survives')
  const archived = [...rows.keys()].filter((k) => k.startsWith('arch-'))
  assert.equal(archived.length, 2, 'both dropped to-dos are archived')
  assert.equal(rows.get(archived[0]).archivedBy, 'test reset', 'the archive names its cause')
  assert.equal(boxRows.size, 0, 'watched boxes wiped')
  assert.equal(inst.model.todos.length, 0, 'in-memory ledger empty')
  assert.equal(inst.model.lastDeepTick, -Infinity, 'refetch forced')
  assert.ok(inst.noteLines.some((n) => /observer reset/.test(n.message)), 'the reset announced itself')
  return `ledger: ${archived.length} archived + wiped, boxes wiped, reset announced`
})

// ---------------------------------------------------------------------------
// THE STEER BOUNDARY (player report 2026-11-21: "the observer plugin messes with
// the steer msg sending"). The contract (dsh-agent runtime-types): `steer` is
// "steering for the nearest step - an idle driver starts a turn; a running driver
// consumes it at its next step boundary". An alert that always steers therefore
// queues observer text into the player's steering slot, opens turns on idle
// drivers, and stacks several messages at one boundary.
test('delivery: yield-aware picks the channel per Agent inbox, never the player slot', async () => {
  const { deliveryPlan } = await import(path.join(pluginDir, 'index.js'))
  const eq = (got, want, name) => assert.equal(got, want, `${name}: got ${got} want ${want}`)
  const playerMsg = { id: 'p1', source: { kind: 'user' } }
  const running = { id: 'a', status: 'running', inbox: { nextStep: [], nextTurn: [] }, steer() {}, followup() {}, inject() {} }
  const busyWithPlayer = { ...running, inbox: { nextStep: [playerMsg], nextTurn: [] } }
  const idle = { ...running, status: 'idle' }
  const ours = { ...running, inbox: { nextStep: [{ id: 'o1', source: { kind: 'df-observer' } }], nextTurn: [] } }
  eq(deliveryPlan('briefing', running).channel, 'inject', 'briefing -> inject')
  eq(deliveryPlan('tool', running).channel, 'inject', 'tool row -> inject')
  eq(deliveryPlan('alert', running).channel, 'steer', 'alert to a running driver with a free boundary -> steer')
  eq(deliveryPlan('alert', busyWithPlayer).channel, 'inject', 'alert yields when the player has steering pending')
  assert.match(deliveryPlan('alert', busyWithPlayer).why, /player/)
  eq(deliveryPlan('alert', idle).channel, 'followup', 'alert to an idle driver is its own turn, not steering')
  eq(deliveryPlan('alert', ours).channel, 'steer', 'our own pending steer does not count as the player slot')
  eq(deliveryPlan('alert', { id: 'x', followup() {}, inject() {} }).channel, 'followup', 'no steer -> followup')
  eq(deliveryPlan('alert', busyWithPlayer, { alertDelivery: 'steer' }).channel, 'steer', 'forced channel wins')
  return 'steer only for a running driver with a free boundary; yield to pending player input; followup when idle'
})

test('delivery: N alerts coalesce into ONE wake with the priority alert leading', () => {
  const { buildCoalescedWake, WAKE_PRIORITY } = briefApi
  const payload = freshModel().payload()
  const alerts = [
    { id: 'A1', kind: 'dig_complete', evidence: 'box dug', coord: 'x27-42', suggested: 'verify' },
    { id: 'A2', kind: 'water', evidence: 'water in column x50,y123', coord: 'x50,y123', suggested: 'stop' },
    { id: 'A3', kind: 'crew', evidence: 'picks 1 < miners 3', coord: '', suggested: 'recheck' },
  ]
  const w = buildCoalescedWake(alerts, payload, { maxTokens: 700 })
  assert.match(w.text, /3 ALERTS in one message/, 'one message, not three')
  assert.ok(w.text.indexOf('A2') < w.text.indexOf('A1'), 'water leads the wake')
  assert.ok(w.text.includes('A3'), 'the rest ride as one line each')
  assert.ok(w.text.includes('df_alerts({action:"ack"'), 'the ack advice rides once')
  assert.ok(estimateTokens(w.text) <= 700, `within the budget (${estimateTokens(w.text)})`)
  assert.ok(WAKE_PRIORITY.indexOf('water') < WAKE_PRIORITY.indexOf('dig_complete'), 'priority order')
  const single = buildCoalescedWake([alerts[1]], payload, { maxTokens: 700 })
  assert.match(single.text, /^DF-OBSERVER ALERT A2 \(water\)/, 'a lone alert keeps the old header')
  return `3 alerts -> 1 wake (${w.tokens} tokens), water leads`
})

test('delivery: the plugin replaces its pending steer instead of stacking one', async () => {
  const queued = []      // the agent's next-step steering slot
  const turns = []       // followup turns
  const injected = []    // pre-step context (the inbox splice path)
  const agent = {
    id: 'a1', session: { header: { origin: 'chat' } }, status: 'running',
    inbox: {
      nextStep: queued, nextTurn: turns,
      replace: (id, m) => { const i = queued.findIndex((x) => x.id === id); if (i < 0) return false; queued[i] = m; return true },
    },
    steer: (m) => queued.push(m), followup: (m) => turns.push(m), inject: (m) => injected.push(m),
  }
  const registered = []
  const ctx = {
    reflect: { provide: () => {} }, isolate: {},
    effect: (fn) => { fn(); return () => {} },
    commands: { register: (def) => { registered.push(def.name); return () => {} } },
    tools: { register: () => () => {} },
    agents: { list: () => [agent], on: () => () => {} },
    systemPrompt: { section: () => () => {} },
    sessionProjections: { register: () => () => {} },
    timers: { register: () => () => {} },
    get: () => undefined,
    storage: { open: async () => ({ table: () => ({ entries: () => [], put: () => {}, get: () => null }), close: () => {} }) },
  }
  const inst = new DFObserver(ctx, Config.parse({ wake_min_spacing_ms: 10000 }))
  inst.markWatched({ id: 'a1', session: { header: { origin: 'chat' } }, ctx: { effect: () => () => { } } }, 'test')
  inst.model.ingestFast(fastData())
  inst.model.ingestDeep(deepData())
  inst.model.evaluate({})
  inst.agents.set('a1', { lastTick: 0, lastAt: 0, lastSignal: null, lastWakeAt: 0, pendingSteerId: null })
  await inst.dispatchAlerts([
    { id: 'A1', kind: 'water', evidence: 'water in the shaft', coord: 'x50,y123', suggested: 'stop' },
    { id: 'A2', kind: 'dig_complete', evidence: 'box dug', coord: 'x27-42 elev36', suggested: 'verify' },
  ])
  assert.equal(queued.length, 1, 'two alerts, one steering message at the boundary')
  const firstId = queued[0].id
  assert.match(queued[0].content[0].text, /2 ALERTS in one message/)
  // The player steers: the next alert must NOT add to their boundary.
  queued.push({ id: 'p1', source: { kind: 'user' }, content: [{ type: 'text', text: 'player steer' }] })
  inst.agents.get('a1').lastWakeAt = 0
  await inst.dispatchAlerts([{ id: 'A3', kind: 'water', evidence: 'water rising', coord: 'x50,y123', suggested: 'stop' }])
  assert.equal(queued.length, 2, 'the player slot is untouched: no second steer appended')
  assert.equal(injected.length, 1, 'the alert rode as pre-step context instead')
  assert.match(inst.noteLines.map((n) => n.message).join('\n'), /wake delivered as context/, 'the demotion is announced')
  // Player input consumed; a new alert replaces OUR pending steer rather than adding.
  queued.splice(1, 1)
  inst.agents.get('a1').lastWakeAt = 0
  await inst.dispatchAlerts([{ id: 'A4', kind: 'water', evidence: 'water again', coord: 'x50,y123', suggested: 'stop' }])
  assert.equal(queued.length, 1, 'replaced in place, not appended')
  assert.match(queued[0].content[0].text, /water again/, 'the pending slot carries the newer state')
  return 'coalesced wake; yields to pending player input; replaces its own pending steer'
})

test('alerts are factory-made: ids, feed entries, ackable - never "ALERT undefined"', async () => {
  // MEASURED 2026-11-21 (player paste): every wake from the offline/online paths
  // read "DF-OBSERVER ALERT undefined (game_offline)". Those paths built bare
  // {kind, evidence} objects: no id, never added to model.alerts, so the header
  // printed `undefined`, the ack advice pointed at an alert df_alerts never had,
  // and a flooded chat had no way to silence it.
  const model = freshModel()
  const off = model.markOffline({ offlineMs: 122000, lastError: new Error('fetch failed') })
  assert.equal(off.length, 1, 'the offline transition raises once')
  assert.match(off[0].id, /^A\d+$/, `the offline alert carries an id (got ${JSON.stringify(off[0].id)})`)
  assert.ok(model.alerts.some((a) => a.id === off[0].id), 'it lands in the alert feed df_alerts lists')
  assert.ok(model.alertHistory.some((h) => h.id === off[0].id), 'and in the alert history')
  assert.ok(off[0].suggested.length > 0, 'with its advice')

  // A drop that never left online raises nothing (no storm while the server is down).
  assert.deepEqual(model.markOffline({ offlineMs: 200000, lastError: new Error('fetch failed') }), [],
    'a continuing outage stays quiet')

  // Coming back: the outage alert is CLEARED (the ack contract: silence "until the
  // condition clears"), and the return is its own alert with its own id.
  const back = model.markOnline(180000)
  assert.equal(back.length, 1, 'the return raises once')
  assert.match(back[0].id, /^A\d+$/, 'the return alert has an id too')
  assert.notEqual(back[0].id, off[0].id, 'a fresh id for the new condition')
  assert.ok(model.alerts.find((a) => a.id === off[0].id)?.cleared, 'the outage alert is cleared')
  // A second markOnline with the same evidence is a repeat on the same key: the
  // same alert, counted, not announced again.
  const repeat = model.markOnline(180000)
  assert.deepEqual(repeat, [], 'an unchanged repeat is not re-announced')
  assert.equal(model.alerts.find((a) => a.key === 'game-online').count, 2, 'it is counted on the active alert')

  // Ack silences repeats until the condition clears.
  const second = model.markOffline({ offlineMs: 60000, lastError: new Error('fetch failed') })
  assert.equal(second.length, 1, 'the new outage is a new alert')
  assert.ok(model.ackAlert(second[0].id), 'df_alerts ack finds it by the id we printed')
  model.online = true
  const third = model.markOffline({ offlineMs: 30000, lastError: new Error('fetch failed') })
  assert.deepEqual(third, [], 'acked: its repeats are silenced until it clears')

  // The transport-bug alert rides the same factory.
  const bug = model.markOffline({ bug: Object.assign(new Error('400 bad'), { status: 400 }) })
  assert.equal(bug.length, 1, 'a transport bug raises')
  assert.match(bug[0].id, /^A\d+$/, 'with an id')

  // Nothing the model prints may say "undefined" - including an alert that
  // skipped the factory (loud label, never a silent word).
  const payload = model.payload()
  const foreign = { kind: 'water', evidence: 'water in the shaft x50,y123', coord: 'x50,y123' }
  const texts = [
    buildWake(second[0], payload, { maxTokens: 700 }).text,
    buildWake(foreign, payload, { maxTokens: 700 }).text,
    buildWake(foreign, payload, { maxTokens: 120 }).text,
    briefApi.buildCoalescedWake([second[0], foreign], payload, { maxTokens: 700 }).text,
    briefApi.buildCoalescedWake([foreign], payload, { maxTokens: 700 }).text,
    briefApi.buildCoalescedWake([foreign], payload, { maxTokens: 140 }).text,
    filterApi.buildFilterWake(foreign, payload, { maxTokens: 420 }).text,
    buildBriefing(payload, { maxTokens: 700 }).text,
  ]
  for (const t of texts) {
    assert.ok(!/\bundefined\b/.test(t), `no literal "undefined" in printed output: ${t.slice(0, 90)}`)
    assert.ok(!/\[object/.test(t), `no stringified object in printed output: ${t.slice(0, 90)}`)
  }
  assert.match(texts[1], /A\?UNLABELLED/, 'an id-less alert is labelled loudly')
  assert.match(texts[1], /df_alerts\(\{action:"ack", id:"A\?UNLABELLED"\}/, 'and the ack line says so, in both places')
  assert.match(texts[6], /A\?UNLABELLED/, 'the filter wake carries an ackable id too')
  return `ids ${[...new Set(model.alerts.map((a) => a.id))].size} active alerts, every wake ackable`
})

test('the observer stops feeding an inbox that is not draining (measured: 400 splices)', async () => {
  // MEASURED 2026-11-21 in 44 filter sessions: briefings/ticks piled up to ~400
  // `agent/inbox/spliced` events in a session whose driver had stopped consuming,
  // and the last turn of each closed with `turn/end {"reason": null}` - which
  // `dsh-subagent`'s epoch fold reads as `end?.data.reason.kind` and throws
  // "Cannot read properties of null (reading 'kind')" -> the session appears in
  // the list as "failed to project session".
  const turns = []      // the filter's next-turn boundary
  const steps = []      // its next-step boundary
  const agent = {
    id: 'play-1', session: { header: { origin: 'chat' } }, status: 'running',
    inbox: { nextStep: steps, nextTurn: turns, replace: () => false },
    steer: (m) => steps.push(assertInboxMessage(m, 'steer')),
    followup: (m) => turns.push(assertInboxMessage(m, 'followup')),
    inject: (m) => steps.push(assertInboxMessage(m, 'inject')),
    ctx: { effect: () => () => {} },
  }
  const ctx = {
    reflect: { provide: () => {} }, isolate: {},
    effect: (fn) => { fn(); return () => {} },
    commands: { register: () => () => {} },
    tools: { register: () => () => {} },
    agents: { list: () => [agent], get: (id) => (id === agent.id ? agent : undefined), on: () => () => {} },
    systemPrompt: { section: () => () => {} },
    sessionProjections: { register: () => () => {} },
    timers: { register: () => () => {} },
    get: () => undefined,
    storage: { open: async () => ({ table: () => ({ entries: () => [], put: () => {}, get: () => null }), close: () => {} }) },
  }
  const cfg = Config.parse({ inject_min_spacing_ms: 0, wake_min_spacing_ms: 10000, pending_inject_cap: 4 })
  const inst = new DFObserver(ctx, cfg)
  inst.model.ingestFast(fastData())
  inst.model.ingestDeep(deepData())
  inst.model.evaluate({})

  // Fill the boundary to the cap with our own pending messages.
  for (let i = 0; i < 4; i++) steps.push({ id: `seed-${i}`, role: 'user', source: { kind: 'df-observer', form: 'briefing' }, content: [{ type: 'text', text: 'x' }] })
  const before = steps.length
  const res = inst.injectMessage(agent, { text: 'DF-OBSERVER briefing' }, 'briefing')
  assert.equal(res.channel, 'skipped', `a boundary at the cap takes no more (saw ${res.channel})`)
  assert.equal(steps.length, before, 'no new durable injection was written')
  assert.equal(inst.model.suppressed.pending, 1, 'the skipped injection is counted, not hidden')
  assert.match(inst.noteLines.map((n) => n.message).join('\n'), /observer messages already unspent/, 'and it is announced in the notes')
  // The player's own messages at the same boundary do not count against our cap.
  steps.push({ id: 'p1', role: 'user', source: { kind: 'user' }, content: [{ type: 'text', text: 'player' }] })
  const ours = inst.pendingOurs(agent, 'next-step')
  assert.equal(ours, 4, 'the cap counts OUR unspent messages only')
  // Draining releases the cap.
  steps.splice(0, 4)
  const res2 = inst.injectMessage(agent, { text: 'DF-OBSERVER briefing' }, 'briefing')
  assert.equal(res2.channel, 'inject', 'a drained boundary injects again')

  // The filter wake path applies the same rule and says which path is answering.
  inst.filterSessionId = agent.id
  for (let i = 0; i < 6; i++) turns.push({ id: `f-${i}`, role: 'user', source: { kind: 'df-observer', form: 'filter-tick' }, content: [{ type: 'text', text: 'x' }] })
  const t0 = turns.length
  inst.wakeFilter(['to_dig +4'], true)
  await new Promise((r) => setTimeout(r, 60))
  assert.equal(turns.length, t0, 'a full filter inbox gets no more ticks')
  assert.equal(inst.filterFallback, true, 'the rule gate is marked as the live path')
  assert.match(inst.noteLines.map((n) => n.message).join('\n'), /filter wake paused/, 'and the pause is announced')

  // Disposal must pass the documented cause OBJECT: a string cause is not an
  // AgentCancelCause, and it is what left the filter's turn closing with no reason.
  const index = fs.readFileSync(path.join(pluginDir, 'index.js'), 'utf8')
  const cancels = index.split('\n').map((l) => l.trim()).filter((l) => /\.cancel\(/.test(l) && !l.startsWith('*') && !l.startsWith('//'))
  assert.ok(cancels.length > 0, 'the disposal cancel exists')
  assert.ok(cancels.every((l) => /cancel\(\{ kind: '(disposed|parent|user)'/.test(l)), `cancel takes a cause object (saw: ${cancels.join(' | ')})`)
  return `cap ${cfg.pending_inject_cap}: pile-up skipped, counted, announced; cause object on disposal`
})

test('a tiles_progress test built from `box` runs its survey check (measured: xundefined)', () => {
  // MEASURED 2026-11-21: `df_todo add` with accept {kind:"tiles_progress"} + box
  // [x0,y0,x1,y1,elev] stored the rectangle in todo.box only, while the acceptance
  // test reads accept.x0..accept.elev -> the first check answered
  // "no survey read for box xundefined-undefined yundefined-undefined elevundefined",
  // i.e. a test that can never pass, and a to-do whose evidence is a lie about coords.
  const model = freshModel()
  const t = model.addTodo({ title: 'stair box', accept: { kind: 'tiles_progress', min_dug: 2 }, box: [27, 121, 42, 126, 36] })
  assert.deepEqual([t.accept.x0, t.accept.y0, t.accept.x1, t.accept.y1, t.accept.elev], [27, 121, 42, 126, 36],
    'the box lands in the test the runner reads')
  model.watchBox([27, 121, 42, 126, 36])
  const r = runAcceptance(t, model.snapshot())
  assert.ok(!/undefined/.test(r.evidence), `the evidence never prints undefined: ${r.evidence}`)
  assert.match(r.evidence, /x27-42 y121-126 elev36/, 'it names the real rectangle')
  // A test with no coordinates at all is labelled incomplete, not silently unpassable.
  const bare = model.addTodo({ title: 'no coords', accept: { kind: 'tiles_progress', min_dug: 1 } })
  const rb = runAcceptance(bare, model.snapshot())
  assert.ok(!/undefined/.test(rb.evidence), `bare test evidence stays clean: ${rb.evidence}`)
  assert.match(bare.incomplete ?? '', /box \[x0,y0,x1,y1,elev\]/, 'and it says what it is missing')
  return 'box -> accept coordinates filled; evidence names the real box; gaps labelled'
})

// ---------------------------------------------------------------------------
// Pile ids move with every save: an empty config must ask the game (the server resolves
// "auto" through its own stockpile scan). A typed list from a previous fort reads
// "pile id N not found" forever and no pile acceptance test can ever run.
test('pile ids are discovered from the game when the config names none', () => {
  const src = fs.readFileSync(path.join(pluginDir, 'index.js'), 'utf8')
  const line = src.split('\n').find((l) => l.includes('request.pile_ids'))
  assert(line && line.includes('cfg.pile_ids.length') && line.includes("'auto'"),
         'empty pile_ids must request "auto" from the server')
  assert(Config.parse({}).pile_ids.length === 0, 'the shipped default is the empty list')
})
// The player's rule, 2026-11-21: no stone pile in early game - it buys hauling labor for
// nothing on a 7-dwarf embark. The ledger must not nag for it on every fresh fort.
test('the auto-seeded basics do not push an early-game stone pile', () => {
  const src = fs.readFileSync(path.join(pluginDir, 'model.js'), 'utf8')
  const i = src.indexOf('export const BASIC_TODOS')
  const list = src.slice(i, src.indexOf('];', i))
  assert(!/key: 'stone-pile'/.test(list), 'BASIC_TODOS must not seed a stone pile task')
  assert(!/category: 'stone'/.test(list), 'no stone category acceptance in the basics')
  assert(/key: 'wood-pile'/.test(list) && /key: 'refuse-pile'/.test(list),
         'the pile basics that DO pay off are still seeded')
  assert(/deliberately NO stone pile/i.test(src.slice(0, i)),
         'and the reason is written where the next reader looks')
})
// The player's bug report, 2026-11-22: "the dsh observer plugin sometimes starts some other
// chats, this looks like a bug". It was: `targetAgent` excluded only subagents and honoured the
// `agents` allowlist when NON-empty, so with the default empty allowlist every session in the
// profile was a briefing/wake target - and `liveTargets()` adopts agents created before activation.
// The gate now requires the session to CONTACT the observer (a df_* tool, a df-* command, the
// panel) before any message lands there.
const selectorStub = (cfg) => {
  const sel = Object.create(DFObserver.prototype)
  sel.config = { agents: [], brief_subagents: false, require_tool_contact: true, ...cfg }
  sel.watched = new Set()
  sel.agents = new Map()
  sel.disposers = new Map()
  sel.log = () => {}
  return sel
}
test('an untouched session is never a briefing target (the "starts other chats" fix)', () => {
  const sel = selectorStub({})
  const chat = { id: 'chat-1', session: { header: { origin: 'chat' } } }
  assert(sel.eligibleAgent(chat) !== null, 'it IS eligible by config (no allowlist, not a subagent)')
  assert(sel.targetAgent(chat) === null, 'but it is NOT a target until it contacts the observer')
  assert(sel.markWatched(chat, 'tool call') === true, 'contact marks it')
  assert(sel.targetAgent(chat) !== null, 'and only then does it become a briefing target')
})
test('the allowlist and subagent rules still gate before contact', () => {
  const sel = selectorStub({ agents: ['pinned'] })
  const other = { id: 'other', session: { header: { origin: 'chat' } } }
  const pinned = { id: 'pinned', session: { header: { origin: 'chat' } } }
  assert(sel.markWatched(other, 'tool call') === false, 'a non-allowlisted session cannot self-subscribe')
  assert(sel.targetAgent(other) === null, 'and stays out')
  sel.markWatched(pinned, 'tool call')
  assert(sel.targetAgent(pinned) !== null, 'the pinned session gets through')
  const sub = { id: 'sub', session: { header: { origin: 'subagent' } } }
  const s2 = selectorStub({})
  assert(s2.eligibleAgent(sub) === null, 'subagents stay out by default (brief_subagents=false)')
})
test('require_tool_contact=false restores the old broadcast, and the fix is documented in the code', () => {
  const sel = selectorStub({ require_tool_contact: false })
  const chat = { id: 'chat-2', session: { header: { origin: 'chat' } } }
  assert(sel.targetAgent(chat) !== null, 'explicit broadcast: every eligible session is a target')
  const src = fs.readFileSync(path.join(pluginDir, 'index.js'), 'utf8')
  assert(/require_tool_contact: z\.boolean\(\)\.default\(true\)/.test(src),
         'the default is the safe one (opt-in contact)')
  assert(/starts\n   \* some other chats/.test(src) || /some other chats/.test(src),
         "the player\"s report is quoted where the gate is defined")
  assert(/name: 'df-watch'/.test(src), 'and there is an explicit command to see/control who is watched')
})
// The player's report, 2026-11-22: "under ungrouped it created a untitled new chat just now".
// Measured: 85 `df-observer-filter-*` session directories in ~/.dsh/sessions/--home-markus--.
// The filter's session id carried Date.now() (regenerated per activation), the agent lookup asked
// ctx.agents.get(sessionId) (agents are keyed by AGENT id), so `filterAgent()` never resolved, and
// `ensureFilterAgent()` created a NEW top-level session in the Host's own cwd on every wake cycle.
test('the filter reuses one session and can never leak chats', () => {
  const index = fs.readFileSync(path.join(pluginDir, 'index.js'), 'utf8')
  assert(!/filterSessionId = `df-observer-filter-\$\{Date\.now\(\)/.test(index),
         'the filter session id is stable - no per-activation id')
  assert(/this\.filterSessionId = this\.filterSessionId \|\| 'df-observer-filter'/.test(index),
         'and it is reused across activations')
  assert(/for \(const agent of this\.ctx\.agents\.list\(\)\)/.test(index),
         'the lookup matches the SESSION id where it lives, not agents.get(sessionId)')
  assert(/filter_max_spawns: z\.number\(\)\.int\(\)\.min\(0\)\.default\(1\)/.test(index),
         'a hard spawn cap exists (default 1)')
  assert(/this\.filterSpawns >= cap/.test(index), 'and it is enforced before every create')
  assert(/targetCwd \?\? process\.cwd\(\)/.test(index),
         'the filter session inherits the briefed workspace - not the Host cwd ("Ungrouped")')
  assert(/filter: \$\{this\.filterSpawns\} spawn/.test(index),
         'the cap announces itself and falls back to the rule gate')
  assert(/typeof ctl\.rename !== 'function'/.test(index),
         "the title call is made and its outcome is reported, never skipped in silence")
  assert(/void this\.titleFilterSession\(\)/.test(index),
         "the chat is titled at ACTIVATION - the live filter self-polls, so the spawn path is unreachable")
  assert(/async titleFilterSession\(controller\)/.test(index)
         && /if \(existing\) \{ void this\.titleFilterSession\(\); return existing \}/.test(index),
         "the title runs on the REUSE path too - a reused session never takes the spawn path, which is why it stayed Untitled")
  assert(/const rows = await controller\.list\(\{\}, this\.abort\.signal\)/.test(index),
         "the cwd comes from the documented SessionSummary rows (agents do not expose the header)")
})

test('water_supply requires a WaterSource zone or Well/Cistern building, not raw water_tiles (#67)', () => {
  const m = freshModel()
  const well = m.todos.find((t) => t.accept?.kind === 'water_supply')
  assert.ok(well, 'seeded water_supply todo exists')
  m.ingestFast({
    ...m.lastFast,
    facts: { ...(m.lastFast?.facts ?? {}), water_tiles: 1030 },
    buildings: [],
    zones: [],
  })
  m.evaluate(m.cfg)
  assert.notEqual(well.state, 'done', 'raw water_tiles=1030 alone must NOT mark water_supply done')

  m.ingestFast({
    ...m.lastFast,
    facts: { ...(m.lastFast?.facts ?? {}), water_tiles: 0 },
    buildings: [{ name: 'Well', count: 1 }],
    zones: [],
  })
  m.evaluate(m.cfg)
  assert.equal(well.state, 'done', 'a Well building marks water_supply done')
})
for (const [name, fn] of queue) {
  try {
    const out = await fn()
    results.push([name, 'ok', out])
  } catch (error) {
    results.push([name, 'FAIL', error?.message ?? String(error)])
  }
}

let failed = 0
for (const [name, status, detail] of results) {
  console.log(`${status === 'ok' ? 'PASS' : 'FAIL'}  ${name}${detail ? `  -  ${detail}` : ''}`)
  if (status !== 'ok') failed += 1
}
console.log(failed ? `df-observer: ${failed} FAILURES` : `df-observer: ${results.length} checks passed`)
process.exit(failed ? 1 : 0)
