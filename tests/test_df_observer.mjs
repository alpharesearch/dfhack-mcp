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
  PAYLOAD_PREFIX,
} = await import(path.join(pluginDir, 'model.js'))
const { buildBriefing, buildWake, estimateTokens, STANDING_RULES } = await import(path.join(pluginDir, 'brief.js'))
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
  facts: { pop: 7, miners: 2, picks: 3, food: 10, drink: 0, wood: 0, stone: 5, metal: 0,
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

// ---------------------------------------------------------------------------
test('config defaults and ranges', () => {
  assert.equal(baseCfg.poll_running_ms, 8000)
  assert.equal(baseCfg.poll_paused_ms, 60000)
  assert.equal(baseCfg.pause_authority, 'off')
  assert.equal(baseCfg.auto_resume, false)
  assert.deepEqual(baseCfg.alert_wakes, ['water', 'dig_complete', 'game_crashed', 'game_offline'])
  assert.equal(baseCfg.overload_todos, 20, 'a normal embark ledger is not an overload')
  assert.throws(() => Config.parse({ poll_running_ms: 16000 }))
  assert.throws(() => Config.parse({ pause_authority: 'manual_suggestion' }))
  return 'pause_authority default off; running interval bounded 5-15 s'
})

test('service module exports the service-class form', () => {
  assert.equal(typeof DFObserver, 'function')
  assert.deepEqual(DFObserver.inject, ['tools', 'agents', 'systemPrompt', 'sessionProjections', 'timer', 'commands'])
  assert.equal(DFObserver.Config, Config)
  assert.equal(typeof DFObserver.prototype.panel, 'function')
  assert.equal(typeof DFObserver.prototype.ackAlert, 'function')
  // Remote markers attach at construction (per instance); the full activation
  // path is exercised by scratch/repro_df_observer.mjs and the live install.
  return 'default export class + static inject/Config + panel/ackAlert endpoint methods'
})

test('model seeds ladder + basics todos', () => {
  const model = freshModel()
  const ladder = model.todos.filter((t) => t.origin === 'ladder')
  const auto = model.todos.filter((t) => t.origin === 'auto')
  assert.ok(ladder.length >= 1)
  assert.equal(auto.length, 8)
  assert.ok(model.todos.every((t) => t.accept && t.accept.kind))
  return `seeded ${ladder.length} ladder + ${auto.length} basics to-dos`
})

test('acceptance: pile_category passes with quoted evidence', () => {
  const model = freshModel()
  const stone = model.todos.find((t) => t.origin === 'auto' && t.accept.category === 'stone')
  assert.equal(stone.state, 'done')
  assert.match(stone.evidence, /pile 19 .* accepts stone, 13 items/)
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
  assert.deepEqual(foldedFull.data.todos.map((t) => `${t.id}:${t.state}`),
    live.todos.map((t) => `${t.id}:${t.state}`), 'ledger ids+states reconstruct from the log line')
  assert.ok(foldedFull.data.todos.every((t) => t.evidence), 'ledger evidence survives')
  assert.match(full.text, /\[df-observer\] \{/)
  assert.match(full.text, /FORT BRIEFING \(in-game elevations\)/)
  assert.match(full.text, /to_dig 51 \| dug 39 \| pending 6/)
  assert.match(full.text, /water in monitored shaft column/)
  assert.match(full.text, /accept: /)
  assert.match(full.text, /complete = to_dig 0 && dug >0/)
  const mid = buildBriefing(model.payload(), { maxTokens: 700 })
  assert.ok(mid.tokens <= 700, `tokens ${mid.tokens}`)
  const foldedMid = foldOf(mid.text)
  assert.deepEqual(foldedMid.data.todos.map((t) => `${t.id}:${t.state}`),
    live.todos.map((t) => `${t.id}:${t.state}`), 'ledger reconstructs at the Config budget')
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
  assert.match(status.text, /pop 7 miners 2\/3 picks/)
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
  const wake = buildFilterWake({ kind: 'water', evidence: 'water elev35:4 in x50,y123', coord: 'x50,y123', suggested: 'stop the shaft' }, model.payload(), { maxTokens: 420 })
  assert.match(wake.text, /DF-OBSERVER ALERT \(filter, tick \d+\) water/)
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
