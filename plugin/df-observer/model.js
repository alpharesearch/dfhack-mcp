/**
 * df-observer - the fort model: merged observation state, the to-do ledger with
 * plugin-run acceptance tests, alerts with evidence, pause attribution, and the
 * delta gate that decides when a briefing is worth the model's context.
 *
 * Two roles live here:
 *
 *  1. `FortModel` - the live state the poller feeds (Host singleton per plugin
 *     fiber). It computes alerts, status flips, pause attribution, overload.
 *  2. the projection unit (`dfObserverProjection`) - the SESSION copy of that
 *     state, folded from committed events only (`apply(state, event)` pure and
 *     synchronous, `view()` reference-stable). Everything the model sees is
 *     reconstructable from the session log: the injected briefing/alert messages
 *     carry the COMPLETE post-change state as a JSON line, so a fork/resume
 *     refolds the same view. Plugin memory is a derived cache, never the truth.
 *
 * The reading rules this code enforces (they are the lessons this repo paid for,
 * see skills/df-dig §6 and PLAN-MODEL.md):
 *   - progress is POSITIVE tile state: to_dig = rock+hidden, dug = floor+stairs+ramp;
 *     complete means to_dig == 0 AND dug > 0, never "nothing seen";
 *   - every number carries its box/elevation and the window it came from;
 *   - "no data" is labelled as such - absence is never a fact;
 *   - elevations are IN-GAME panel numbers (z = elev + 129).
 *
 * @module @local/df-observer/model
 */

export const STATE_VERSION = 1
export const PAYLOAD_PREFIX = '[df-observer] '

/** The state field set shared by the live model and the session projection. */
export const STATE_FIELDS = [
  'tick', 'online', 'observedAt', 'paused', 'pauseSource', 'pauseAtTick', 'pluginPauseTick',
  'generation', 'poll', 'pauseAuthority', 'filter',
  'facts', 'buildings', 'boxes', 'shafts', 'piles', 'warnings', 'rungs', 'next',
  'reports', 'crashlog', 'notes', 'alerts', 'alertHistory', 'todos', 'digEvents', 'pauseEvents',
  'overload', 'briefings',
]

function num(value, fallback = 0) {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

function boxKey(box) {
  // Payload shape is {elev, box:[x0,y0,x1,y1]}; accept the flat form too.
  const [x0, y0, x1, y1] = Array.isArray(box.box) ? box.box : [box.x0, box.y0, box.x1, box.y1]
  return `${box.elev}|${x0},${y0},${x1},${y1}`
}

/** Compact, comparable fingerprint of the parts of state the model cares about. */
export function signalOf(state) {
  return {
    online: state.online !== false,
    paused: state.paused === true,
    pauseSource: state.pauseSource ?? null,
    generation: state.generation ?? null,
    rungsDone: (state.rungs ?? []).filter((r) => r.done).map((r) => r.id).join(','),
    next: state.next ? state.next.id : null,
    todos: (state.todos ?? []).map((t) => `${t.id}:${t.state}`).join(','),
    alerts: (state.alerts ?? []).map((a) => `${a.id}:${a.acked ? 'a' : 'n'}`).join(','),
    boxes: (state.boxes ?? []).map((b) => `${boxKey(b)}:${b.to_dig}/${b.dug}/${b.pending}`).join(','),
    water: (state.shafts ?? []).flatMap((s) => (s.water_elevs ?? []).map((e) => `${s.x},${s.y}@${e}`)).join(','),
    damp: num(state.warnings?.damp_tiles), hot: num(state.warnings?.hot_tiles),
    pending_dig: num(state.facts?.pending_dig), miners: num(state.facts?.miners), picks: num(state.facts?.picks),
    pop: num(state.facts?.pop), food: num(state.facts?.food), drink: num(state.facts?.drink), wood: num(state.facts?.wood),
    piles: (state.piles ?? []).map((p) => `${p.id}:${p.found}/${p.total ?? 0}`).join(','),
    crash: (state.crashlog?.files ?? [0]).map((f) => `${f.name}:${f.bytes}`).join(','),
    overload: state.overload ? 1 : 0,
  }
}

const SIGNAL_KEYS = Object.keys(signalOf({}))

/**
 * Which parts changed between two states, with magnitudes - the delta gate.
 * `min_to_dig_delta` keeps tiny survey wiggles from costing the model a briefing.
 */
export function significantChange(prev, next, cfg) {
  if (prev == null) return ['first']
  const changed = []
  const a = signalOf(prev)
  const b = signalOf(next)
  for (const key of SIGNAL_KEYS) {
    if (a[key] === b[key]) continue
    if (key === 'boxes') {
      const moved = boxDeltas(a.boxes, b.boxes)
      if (moved.some((d) => Math.abs(d.toDig) >= cfg.min_to_dig_delta || Math.abs(d.dug) >= cfg.min_to_dig_delta)) {
        changed.push('boxes')
      }
      continue
    }
    changed.push(key)
  }
  return changed
}

function boxDeltas(aStr, bStr) {
  const toMap = (s) => new Map(String(s).split(',').filter(Boolean).map((part) => {
    const [key, counts] = part.split(':')
    const [toDig, dug, pending] = String(counts).split('/').map(Number)
    return [key, { toDig, dug, pending }]
  }))
  const a = toMap(aStr)
  const b = toMap(bStr)
  const out = []
  for (const [key, bv] of b) {
    const av = a.get(key) ?? { toDig: bv.toDig, dug: bv.dug, pending: bv.pending }
    out.push({ key, toDig: bv.toDig - av.toDig, dug: bv.dug - av.dug })
  }
  for (const [key, av] of a) if (!b.has(key)) out.push({ key, toDig: -av.toDig, dug: -av.dug })
  return out
}

// ---------------------------------------------------------------------------
// The basics the plan says the plugin must auto-create from ladder gaps
// (PLAN.md §9.3). Each carries the acceptance test the plugin itself runs
// against the MCP reads; the label names it as auto-created.
// ---------------------------------------------------------------------------

export const BASIC_TODOS = [
  { key: 'general-pile', title: 'General pile near the wagon, excluding stone/wood/corpses/refuse/gems',
    accept: { kind: 'pile_general' }, cite: 'df-stockpile §1' },
  { key: 'stone-pile', title: 'Stone pile for the dig face (accepts stone, wheelbarrows on)',
    accept: { kind: 'pile_category', category: 'stone', min_items: 1 }, cite: 'df-stockpile §1,§4' },
  { key: 'wood-pile', title: 'Wood pile near the entrance/road (accepts wood only, no bins/barrels)',
    accept: { kind: 'pile_category', category: 'wood', min_items: 1 }, cite: 'df-stockpile §1' },
  { key: 'refuse-pile', title: 'Refuse/corpse pile OUTDOORS (corpses+refuse, no containers)',
    accept: { kind: 'pile_categories', categories: ['corpses', 'refuse'], min_items: 0 }, cite: 'df-stockpile §1' },
  { key: 'meeting-area', title: 'Meeting area inside the fort (5x5-7x7 on the 2nd level below ground)',
    accept: { kind: 'rung', rung: 'meeting' }, cite: 'df-dig §16b' },
  { key: 'farm-plot', title: 'Farm plot: 3x3 in a 5x5 soil room, plump helmets in ALL seasons',
    accept: { kind: 'rung', rung: 'farm' }, cite: 'fort ladder (farm)' },
  { key: 'well', title: 'Well (or cistern plan) for the water supply',
    accept: { kind: 'buildings_kw', keyword: 'well', min: 1 }, cite: 'fort ladder (water)' },
  { key: 'doors', title: 'Doors on ordered openings (count against openings you designated)',
    accept: { kind: 'buildings_kw', keyword: 'door', min: 1 }, cite: 'df-dig §16b (openings)' },
]

/** Identity of a to-do for dedupe: everything the acceptance test proves,
 * canonically ordered - not its title text (measured: auto pile tasks carry
 * exclude lists, and a key-by-a-few-fields signature let them seed twice). */
export function todoSignature(todo) {
  const a = todo?.accept ?? {}
  const canon = (v) => {
    if (v == null) return 'null'
    if (Array.isArray(v)) return `[${v.map(canon).sort().join(',')}]`
    if (typeof v === 'object') return `{${Object.keys(v).sort().map((k) => `${k}:${canon(v[k])}`).join(',')}}`
    return String(v)
  }
  return canon(a)
}

/** Fields the status probe owns; they survive fast ticks (facts is replaced). */
export const STATUS_KEYS = ['picks', 'site_id', 'seeds', 'date', 'alcohol', 'meals', 'raw_food']

const LADDER_TODO_KEYS = new Set(['labors', 'entrance', 'stairwell', 'stockpile', 'meeting', 'refuse', 'farm', 'wood', 'drinks', 'depot'])

/** Seed to-dos: the quickstart ladder (in order) plus the auto-created basics. */
export function seedTodos(rungs, cfg) {
  const todos = []
  let id = 1
  for (const rung of rungs ?? []) {
    if (!LADDER_TODO_KEYS.has(rung.id)) continue
    todos.push({
      id: id++, title: rung.title, origin: 'ladder', state: rung.done ? 'done' : 'open',
      accept: { kind: 'rung', rung: rung.id }, evidence: rung.done
        ? `rung predicate satisfied on the ladder read` : `rung open: ${rung.why}`,
      statusTick: 0, cite: rung.keys,
    })
  }
  for (const basic of BASIC_TODOS) {
    todos.push({
      id: id++, title: basic.title, origin: 'auto', state: 'open',
      accept: basic.accept, evidence: 'not checked yet', statusTick: 0, cite: basic.cite,
    })
  }
  void cfg
  return todos
}

// ---------------------------------------------------------------------------
// Acceptance tests: each is an MCP-read predicate the plugin runs on its poll
// cycle. A status flip must quote the evidence, so every test returns text.
// ---------------------------------------------------------------------------

export function runAcceptance(todo, snapshot) {
  const accept = todo.accept ?? {}
  const facts = snapshot.facts ?? {}
  const piles = snapshot.piles ?? []
  const buildings = snapshot.buildings ?? []
  const boxes = snapshot.boxes ?? []
  const rungs = snapshot.rungs ?? []
  switch (accept.kind) {
    case 'tiles_progress': {
      // Payload boxes arrive as {elev, box:[x0,y0,x1,y1]}; accept names the
      // corners. Compare the coordinates, not the shape they came in.
      const coords = (b) => Array.isArray(b.box)
        ? { x0: b.box[0], y0: b.box[1], x1: b.box[2], y1: b.box[3] }
        : { x0: b.x0, y0: b.y0, x1: b.x1, y1: b.y1 }
      const box = boxes.find((b) => {
        if (b.elev !== accept.elev) return false
        const c = coords(b)
        return c.x0 === accept.x0 && c.y0 === accept.y0 && c.x1 === accept.x1 && c.y1 === accept.y1
      })
      if (!box) return { ok: false, noData: true, evidence: `no survey read for box x${accept.x0}-${accept.x1} y${accept.y0}-${accept.y1} elev${accept.elev} (no data, not a fact)` }
      if (box.to_dig == null || box.dug == null) {
        return { ok: false, noData: true, evidence: `box x${accept.x0}-${accept.x1} y${accept.y0}-${accept.y1} elev${accept.elev}: NO DATA yet (watched, awaiting its first survey read - not a fact)` }
      }
      const minDug = num(accept.min_dug, 1)
      const ok = box.to_dig === 0 && box.dug >= minDug
      // The clause must state THIS test (min_dug), not the generic completion rule:
      // measured - `why` quoted "dug>0" on a task that requires 44.
      return { ok, evidence: `x${box.box[0]}-${box.box[2]} y${box.box[1]}-${box.box[3]} elev${box.elev}: to_dig ${box.to_dig}, dug ${box.dug}, pending ${box.pending}${box.hidden ? `, hidden ${box.hidden}` : ''} (accept: to_dig==0 && dug>=${minDug})` }
    }
    case 'pile_category': {
      const pile = piles.find((p) => p.found === 1 && String(p.accepts ?? '').split(',').includes(accept.category))
      if (!pile) return { ok: false, noData: !piles.length, evidence: piles.length
        ? `no pile accepts ${accept.category} (probe covered ids ${piles.map((p) => p.id).join(',')})`
        : `no pile probe rows this tick (probe pending): ${accept.category} UNKNOWN, not a fact` }
      const items = num(pile.total)
      const ok = items >= num(accept.min_items, 1)
      return { ok, evidence: `pile ${pile.id} x${pile.x0}-${pile.x1} y${pile.y0}-${pile.y1} elev${pile.elev} accepts ${pile.accepts}, ${items} items on it (accept: >= ${accept.min_items ?? 1})` }
    }
    case 'pile_categories': {
      const need = accept.categories ?? []
      const pile = piles.find((p) => p.found === 1 && need.every((c) => String(p.accepts ?? '').split(',').includes(c)))
      if (!pile) return { ok: false, noData: !piles.length, evidence: piles.length
        ? `no pile accepts all of ${need.join('+')}`
        : `no pile probe rows this tick (probe pending): ${need.join('+')} UNKNOWN, not a fact` }
      const items = num(pile.total)
      const ok = items >= num(accept.min_items, 0)
      return { ok, evidence: `pile ${pile.id} elev${pile.elev} accepts ${pile.accepts}, ${items} items on it` }
    }
    case 'pile_general': {
      const pile = piles.find((p) => {
        if (p.found !== 1) return false
        const cats = String(p.accepts ?? '').split(',').filter(Boolean)
        return cats.length >= 8 && !cats.includes('stone') && !cats.includes('wood')
      })
      if (!pile) return { ok: false, noData: !piles.length, evidence: piles.length
        ? 'no pile taking >=8 categories while excluding stone+wood'
        : 'no pile probe rows this tick (probe pending): general pile UNKNOWN, not a fact' }
      return { ok: true, evidence: `pile ${pile.id} elev${pile.elev} accepts ${pile.accepts} (excludes stone/wood)` }
    }
    case 'rung': {
      const rung = rungs.find((r) => r.id === accept.rung)
      if (!rung) return { ok: false, noData: true, evidence: `ladder rung ${accept.rung} not in the read (no data, not a fact)` }
      return { ok: !!rung.done, evidence: `rung ${rung.id} ${rung.done ? 'satisfied' : 'open'}: ${rung.title}` }
    }
    case 'buildings_kw': {
      const kw = String(accept.keyword).toLowerCase()
      const hits = buildings.filter((b) => String(b.name).toLowerCase().includes(kw))
      const ok = hits.length >= num(accept.min, 1)
      return { ok, evidence: `${hits.length} building(s) named *${kw}* in band (accept: >= ${accept.min ?? 1}): ${hits.map((h) => h.name).slice(0, 6).join(', ') || 'none'}` }
    }
    case 'crew_picks': {
      const miners = num(facts.miners), picks = num(facts.picks)
      return { ok: miners > 0 && picks >= miners, evidence: `miners ${miners}, picks ${picks} (accept: picks >= designated miners; df-dig §9)` }
    }
    case 'manual': {
      return { ok: todo.state === 'done', evidence: todo.evidence ?? 'closed by the model with evidence' }
    }
    default:
      return { ok: false, evidence: `acceptance test "${accept.kind}" is not runnable by the plugin (labelled, never assumed)` }
  }
}

// ---------------------------------------------------------------------------
// Alerts
// ---------------------------------------------------------------------------

/** kind -> does it wake the agent (Config.alert_wakes is applied by the caller). */
export const SUGGESTIONS = {
  water: 'stop the shaft/dig at this column: water follows the shaft down (df-dig §4, §12); drain first',
  damp_hot: 'these designations breach an aquifer/magma: clear them with NO_DIG before any dwarf digs (df-dig §1)',
  dig_complete: 'box is dug (to_dig 0, dug > 0): verify the ceiling with cross-section, re-survey before extending (df-dig §6)',
  tile_state_lost: 'dug tiles DISAPPEARED: a designation went over existing floors/stairs - rebuild (stairs: up-down stair at the level below), then designate rock only',
  dig_stalled: 'designations drained with no dug change = the game cancelled the rest (no access/site): read dig_reachable, order middle-first (df-dig §5)',
  pile_full: 'pile at capacity: add tiles/bins or a feeder pile linked to the consumer (df-stockpile §5)',
  pile_holes: 'pile has container/hole problems or 0 accepted categories: run the checklist (df-stockpile §7)',
  pile_none_accepting: 'a watched pile accepts nothing: configure it (df-stockpile §3)',
  crew: 'crew/pick/food limit: re-check labours and the pick supply before ordering more digging (df-dig §9)',
  game_offline: 'the game (:5000) is closed; the observer keeps tolerating it - verify the fort after relaunch (df-dig §7)',
  game_crashed: 'a NEW crash log appeared (name/size only): relaunch, then re-survey the fort and re-issue lost designations as to-dos',
  save_reloaded: 'generation changed (save reload): list what survived via the box dig events before extending the plan',
  todo_reopened: 'a done to-do failed its acceptance test again: reopen with the quoted evidence',
  overload: 'alert/todo backlog past thresholds: work the queue in order, one item at a time',
  transport: 'the observer hit a 4xx from the MCP server = OUR bug: report it, do not retry blindly',
}

/** The advice must match what the acceptance test actually proved (measured: a
 * pile to-do woke the agent with "box is dug - verify the ceiling"). */
export function todoAdvice(todo) {
  const kind = todo?.accept?.kind
  if (kind === 'tiles_progress') return SUGGESTIONS.dig_complete
  if (kind === 'pile_category' || kind === 'pile_categories' || kind === 'pile_general') {
    return 'pile acceptance verified by the live pile read: keep its categories and container limits as the build grows (df-stockpile §3, §5)'
  }
  if (kind === 'buildings_kw') return 'building count proven by the live building read: check access and materials before relying on it (df-stockpile §2)'
  if (kind === 'rung') return 'ladder rung satisfied: take the NEXT rung from fort_progress, in order (PLAN §4)'
  return 'to-do acceptance passed on the live read: cite the evidence, do not re-probe blindly'
}

let alertSeq = 0

export function makeAlert({ kind, key, evidence, coord = '', suggested, nowMs }) {
  return {
    id: `A${++alertSeq}`, kind, key, evidence, coord,
    suggested: suggested ?? SUGGESTIONS[kind] ?? '',
    firstTick: null, lastTick: null, count: 1, acked: false, wakeSent: false,
    at: nowMs ?? Date.now(), cleared: false,
  }
}

// ---------------------------------------------------------------------------
// The live model
// ---------------------------------------------------------------------------

export class FortModel {
  constructor(cfg) {
    this.cfg = cfg
    this.tick = 0
    this.online = null
    this.observedAt = null
    this.paused = null
    this.pauseSource = null       // 'plugin' | 'game-auto-or-player' | 'player-resume' | null
    this.pauseAtTick = null
    this.pluginPauseTick = null   // tick at which WE issued the pause (attribution key)
    this.generation = null
    this.facts = null
    this.buildings = []
    this.boxes = []
    this.shafts = []
    this.piles = []
    this.warnings = null
    this.rungs = []
    this.next = null
    this.reports = []
    this.crashlog = null
    this.notes = []
    this.alerts = []              // active, newest first
    this.alertHistory = []
    this.todos = []
    this.digEvents = []
    this.pauseEvents = []
    this.overload = null
    this.briefings = 0
    this.lastBoxState = new Map()  // boxKey -> {to_dig, dug, pending, stagnant}
    this.crashSeen = new Set()
    this.seeded = false
    this.lastDeepTick = -Infinity
    this.statusFacts = {}
    /** Last known `dug` per monitored box: a decrease = destroyed tiles. */
    this.dugBaseline = new Map()
    /** Boxes the user registered via to-dos; they join every survey request. */
    this.extraBoxes = []
    /** Plugin-side diagnostics (transport, ledger, filter) merged into payloads. */
    this.notesSource = null
    this.pluginNotes = () => (this.notesSource ? this.notesSource() : [])
    /** Next free to-do id (shared by seeds and user adds). */
    this.nextTodoId = 1
    /** Set by the plugin: called on every ledger mutation so the record persists. */
    this.onLedgerWrite = null
    this.onBoxWatch = null
    this.lastDwarves = 0
    this.lastDwarvesTick = null
    this.dwarvesKnown = false
    this.popKnown = false
  }

  /** Fast tick: facts, boxes, warnings, pause, generation (the 5-60 s cadence). */
  ingestFast(data) {
    const changed = []
    this.tick += 1
    this.online = true
    this.observedAt = Date.now()
    if (data.paused !== null && data.paused !== undefined) {
      const prev = this.paused
      this.paused = data.paused === true
      if (prev === true && this.paused === false) {
        changed.push('unpause')
        this.pauseEvents.push({ tick: this.tick, paused: false, source: this.pauseSource ?? 'unknown' })
        this.pauseSource = 'player-resume'
        this.pauseAtTick = this.tick
        this.trim('pauseEvents', 30)
      } else if (prev === false && this.paused === true) {
        changed.push('pause')
        const byPlugin = this.pluginPauseTick != null && this.tick - this.pluginPauseTick <= 12
        this.pauseSource = byPlugin ? 'plugin' : 'game-auto-or-player'
        this.pauseAtTick = this.tick
        if (byPlugin) this.pluginPauseTick = null
        this.pauseEvents.push({ tick: this.tick, paused: true, source: this.pauseSource })
        this.trim('pauseEvents', 30)
      }
    }
    if (data.generation !== null && data.generation !== undefined) {
      if (this.generation !== null && this.generation !== data.generation) changed.push('generation')
      this.generation = data.generation
    }
    if (data.facts) {
      // The status-bar fields come from the deep probe; a fast tick replaces
      // `facts` with the server's own object, so re-attach what we probed.
      const carried = {}
      for (const key of STATUS_KEYS) {
        if (this.statusFacts[key] != null) carried[key] = this.statusFacts[key]
      }
      this.facts = Object.keys(carried).length ? { ...data.facts, ...carried } : data.facts
      changed.push('facts')
    }
    if (data.buildings) this.buildings = data.buildings
    if (Array.isArray(data.boxes)) { this.boxes = data.boxes; changed.push('boxes') }
    if (Array.isArray(data.shafts)) { this.shafts = data.shafts; changed.push('shafts') }
    if (data.warnings !== undefined) { this.warnings = data.warnings; changed.push('warnings') }
    return { changed, tick: this.tick }
  }

  /** Deep tick: rungs, piles, reports, crashlog (the 60 s cadence). */
  ingestDeep(data) {
    this.lastDeepTick = this.tick
    // The status probe (one Lua round-trip, deep ticks only): picks, site id,
    // date, seeds. An absent probe leaves the fields null - never 0.
    const st = data.status ?? (data.picks != null ? { picks: data.picks } : null)
    if (st) {
      // The probe rides the deep tick; remember it so the next FAST tick (which
      // replaces `facts` wholesale) does not erase the status bar.
      for (const key of STATUS_KEYS) {
        if (st[key] != null) this.statusFacts[key] = st[key]
      }
      if (this.facts) {
        for (const key of STATUS_KEYS) {
          if (this.statusFacts[key] != null) this.facts[key] = this.statusFacts[key]
        }
      }
    }
    if (Array.isArray(data.rungs)) { this.rungs = data.rungs }
    if (data.next !== undefined) this.next = data.next
    // The server keys pile rows by id ({"18": {...}}); accept both shapes -
    // an Array.isArray() guard alone dropped every pile row (measured: the piles
    // scope read NO DATA while curl showed rows).
    if (Array.isArray(data.piles)) this.piles = data.piles
    else if (data.piles && typeof data.piles === 'object') this.piles = Object.values(data.piles)
    if (Array.isArray(data.reports)) this.reports = data.reports
    if (data.crashlog !== undefined) this.crashlog = data.crashlog
    if (Array.isArray(data.notes)) this.notes = data.notes
    if (!this.seeded && this.rungs.length) {
      const seeded = seedTodos(this.rungs, this.cfg)
      // Re-key seeds past anything already registered (a restore can beat the
      // first ladder read).
      const offset = Math.max(0, ...this.todos.map((t) => t.id))
      // Seeds must not duplicate a restored to-do (measured: reloads created a
      // second general/stone/wood pile task, and the backlog tripped the
      // overload alert on our own bookkeeping).
      const taken = new Set(this.todos.map((t) => todoSignature(t)))
      const fresh = seeded.filter((t) => !taken.has(todoSignature(t)))
        .map((t) => ({ ...t, id: t.id + offset }))
      this.todos = [...this.todos, ...fresh]
      this.nextTodoId = Math.max(this.nextTodoId, ...this.todos.map((t) => t.id)) + 1
      this.trim('todos', 80)
      this.seeded = true
    }
  }

  /** The game went offline longer than the grace window, or the poll failed. */
  markOffline({ offlineMs, lastError, bug }) {
    this.tick += 1
    if (bug) {
      return [{ kind: 'transport', key: 'transport-bug',
        evidence: `transport reports OUR bug (4xx): ${bug.message}`, suggested: 'plugin bug: report it, fix it (4xx = our bug, not the game)' }]
    }
    const wasOnline = this.online !== false
    this.online = false
    if (wasOnline) {
      return [{ kind: 'game_offline', key: `offline-${Date.now()}`,
        evidence: `game offline for ${Math.round((offlineMs ?? 0) / 1000)}s: ${lastError?.message ?? lastError}`,
        suggested: SUGGESTIONS.game_offline }]
    }
    return []
  }

  markOnline(offlineMs) {
    this.online = true
    return [{ kind: 'game_offline', key: `offline-return-${Date.now()}`,
      evidence: `game back online after ${Math.round((offlineMs ?? 0) / 1000)}s offline`,
      suggested: 're-survey the fort (world-cache-reset is the server side; read boxes before extending)' }]
  }

  /**
   * Evaluate everything the poll cycle produced: dig flips, water, damp/hot,
   * pile health, crew, crash logs, save reload, acceptance tests, overload.
   * Returns the alert list raised by this tick (deduped against active alerts).
   */
  evaluate({ nowMs = Date.now() } = {}) {
    const raised = []
    const cfg = this.cfg
    const raise = (kind, key, evidence, coord, advice = null) => {
      const active = this.alerts.find((a) => a.key === key && !a.cleared)
      if (active) {
        if (active.acked) return null      // acked: silence repeats until the condition clears
        const changed = evidence !== active.evidence
        active.lastTick = this.tick
        active.count += 1
        active.evidence = evidence
        if (changed) raised.push(active)
        return changed ? active : null
      }
      const alert = makeAlert({ kind, key, evidence, coord, suggested: advice ?? SUGGESTIONS[kind], nowMs })
      alert.firstTick = this.tick
      alert.lastTick = this.tick
      raised.push(alert)
      this.alerts.unshift(alert)
      this.trim('alerts', 60)
      this.alertHistory.push({ id: alert.id, kind, key, evidence, coord, tick: this.tick })
      this.trim('alertHistory', 200)
      return alert
    }

    // ---- dig progress per monitored box: positive tile state only ----
    for (const box of this.boxes) {
      const key = boxKey(box)
      const known = this.lastBoxState.get(key)
      if (known === undefined) {
        // First read of a box is a BASELINE, not a transition: no complete/stall
        // claim without a previous state to have left.
        this.lastBoxState.set(key, { to_dig: box.to_dig, dug: box.dug, pending: box.pending, stagnant: 0, lastDug: box.dug })
        continue
      }
      const prev = known
      if (box.to_dig === 0 && box.dug > 0 && (prev.to_dig > 0 || prev.dug < box.dug)) {
        const ev = `x${box.box[0]}-${box.box[2]} y${box.box[1]}-${box.box[3]} elev${box.elev}: to_dig ${box.to_dig}, dug ${box.dug}`
        this.digEvents.push({ tick: this.tick, kind: 'complete', box: box.box, elev: box.elev, evidence: ev })
        this.trim('digEvents', 40)
        raise('dig_complete', `dig-${key}`, `${ev} (accept: to_dig==0 && dug>0 MET)`, `x${box.box[0]}-${box.box[2]} y${box.box[1]}-${box.box[3]} elev${box.elev}`)
      }
      // Stagnation, one writer per box (measured bug: the drained-else reset
      // `stagnant` to 0 on every non-drained read, so an idle face never counted).
      // Two kinds of frozen face:
      //   drained: designations were queued, drained to 0, nothing dug;
      //   idle:    nothing queued, nothing dug for stall_ticks reads (the filter
      //            kept reporting "Ten game days, zero tiles dug, pending 0").
      const unchanged = box.to_dig === prev.to_dig && box.dug === prev.dug
      const drained = prev.pending > 0 && box.pending === 0 && unchanged
      const idle = box.pending === 0 && box.to_dig > 0 && unchanged
      if (drained || idle) {
        const stagnant = (prev.stagnant ?? 0) + 1
        if (stagnant >= cfg.stall_ticks) {
          const coord = `x${box.box[0]}-${box.box[2]} y${box.box[1]}-${box.box[3]} elev${box.elev}`
          const ev = drained
            ? `${coord}: pending 0, to_dig ${box.to_dig} unchanged ${stagnant} reads, dug ${box.dug} unchanged - designations drained with no dug change`
            : `${coord}: to_dig ${box.to_dig}, pending 0, dug ${box.dug} unchanged for ${stagnant} reads - nothing queued and nothing dug (the plan is idle at this face)`
          this.digEvents.push({ tick: this.tick, kind: 'stalled', box: box.box, elev: box.elev, evidence: ev })
          this.trim('digEvents', 40)
          raise('dig_stalled', `stall-${key}`, ev, coord,
            drained
              ? 'no access to the rest: read dig_reachable from a dwarf-occupied tile, order the band middle-first (df-dig §5)'
              : 'queue the next face: survey it, designate ROCK tiles only (never over floors/stairs), middle-first so each dug tile is the next access (df-dig §5)')
        }
        this.lastBoxState.set(key, { ...prev, to_dig: box.to_dig, dug: box.dug, pending: box.pending, stagnant })
      } else {
        this.lastBoxState.set(key, { to_dig: box.to_dig, dug: box.dug, pending: box.pending, stagnant: 0, lastDug: box.dug })
      }
    }

    // ---- water GROWING in the monitored band (an aquifer breach floods) ----
    // Measured: the designations at elev22-24 breached the aquifer and the band's
    // water tiles went 1030 -> 1091 (+61). Shaft columns can stay dry while the
    // fort floods elsewhere, so the band total gets its own alert.
    const waterNow = num(this.facts.water_tiles)
    const waterPrev = this.lastWaterTiles
    this.lastWaterTiles = waterNow
    if (waterPrev !== undefined && waterNow - waterPrev >= cfg.water_growth_min) {
      raise('water', 'water-growth',
        `water in the monitored band rose ${waterPrev} -> ${waterNow} (+${waterNow - waterPrev} tiles): an aquifer breach is flooding the works`,
        `elev${cfg.elev0}-${cfg.elev1}`,
        'stop digging into the aquifer: clear the DAMP designations (NO_DIG), and drain/pump before continuing (df-dig §4)')
      this.lastWaterGrowth = this.tick
    } else if (waterPrev !== undefined && this.lastWaterGrowth !== undefined && this.tick - this.lastWaterGrowth >= 2) {
      const active = this.alerts.find((a) => a.key === 'water-growth' && !a.cleared)
      if (active) { active.cleared = true; active.lastTick = this.tick }
    }

    // ---- water in a monitored shaft column (water = stop, df-dig §4) ----
    for (const shaft of this.shafts) {
      const waters = shaft.water_elevs ?? []
      if (waters.length) {
        const levels = Object.entries(shaft.water_levels ?? {}).map(([e, l]) => `elev${e}:${l}`).join(' ')
        raise('water', `water-${shaft.x},${shaft.y}`,
          `water in monitored shaft column x${shaft.x},y${shaft.y}: ${levels} (aquifer elevs ${(shaft.aquifer_elevs ?? []).join(',') || 'none'})`,
          `x${shaft.x},y${shaft.y}`)
      } else {
        const active = this.alerts.find((a) => a.key === `water-${shaft.x},${shaft.y}` && !a.cleared)
        if (active) { active.cleared = true; active.lastTick = this.tick }
      }
      const magmas = shaft.magma_elevs ?? []
      if (magmas.length) {
        raise('damp_hot', `magma-${shaft.x},${shaft.y}`,
          `magma in monitored shaft column x${shaft.x},y${shaft.y} at elevs ${magmas.join(',')}`, `x${shaft.x},y${shaft.y}`)
      }
    }

    // ---- damp/hot among live designations ----
    const warnings = this.warnings
    if (warnings && num(warnings.damp_tiles) > 0) {
      const bx = (warnings.damp_boxes ?? [])[0]
      raise('damp_hot', `damp-${bx ? boxKey({ elev: bx.elev0, x0: bx.x0, y0: bx.y0, x1: bx.x1, y1: bx.y1 }) : 'band'}`,
        `warnings: ${warnings.damp_tiles} DAMP designations (boxes ${(warnings.damp_boxes ?? []).map((b) => `x${b.x0}-${b.x1} y${b.y0}-${b.y1} elev${b.elev0}-${b.elev1}`).join(' | ') || 'n/a'})`,
        bx ? `x${bx.x0}-${bx.x1} y${bx.y0}-${bx.y1} elev${bx.elev0}-${bx.elev1}` : '')
    }
    if (warnings && num(warnings.hot_tiles) > 0) {
      raise('damp_hot', `hot-band`, `warnings: ${warnings.hot_tiles} HOT designations (magma below)`, '')
    }

    // ---- pile health ----
    for (const pile of this.piles) {
      if (pile.found !== 1) {
        raise('pile_holes', `pile-missing-${pile.id}`, `watched pile id ${pile.id} not found in the game (renumbered after reload?)`, '')
        continue
      }
      const cats = String(pile.accepts ?? '').split(',').filter(Boolean)
      const area = (pile.x1 - pile.x0 + 1) * (pile.y1 - pile.y0 + 1)
      if (cats.length === 0) {
        raise('pile_none_accepting', `pile-none-${pile.id}`, `pile ${pile.id} elev${pile.elev} accepts NOTHING (no categories)`, `pile ${pile.id}`)
      } else if (num(pile.total) >= cfg.pile_full_items && area <= 9) {
        raise('pile_full', `pile-full-${pile.id}`, `pile ${pile.id} elev${pile.elev} holds ${pile.total} items on ${area} tiles`, `pile ${pile.id}`)
      } else {
        const active = this.alerts.find((a) => a.key === `pile-none-${pile.id}` && !a.cleared)
        if (active) active.cleared = true
      }
    }

    // ---- crew: picks vs designated miners, food/drink zero ----
    const facts = this.facts ?? {}
    if (facts.picks !== null && facts.picks !== undefined
      && num(facts.miners) > num(facts.picks) && num(facts.pending_dig) > 0) {
      raise('crew', 'crew-picks', `picks ${facts.picks} < designated miners ${facts.miners} with pending dig ${facts.pending_dig} -> the extra miners starve at the face (df-dig §9)`, '')
    }
    // Supply alerts follow the player's ranking (measured request 2026-11-21):
    // alcohol > prepared meals > raw food. These are the FORT-WIDE item counts
    // from the status probe - not the pile counts, which ride in the pile lines.
    const dwarvesN = facts.dwarves == null ? null : num(facts.dwarves)
    if (dwarvesN > 0) {
      if (facts.alcohol != null && num(facts.alcohol) === 0) {
        raise('crew', 'crew-no-drink',
          `alcohol 0 with ${dwarvesN} dwarves: drink outranks meals and raw food - build the still (barrels to store it)`,
          'y -> Craftsdwarf (Still); b -> craftsdwarf workshop')
      } else if (facts.meals != null && num(facts.meals) === 0) {
        raise('crew', 'crew-no-meals',
          `no prepared meals (meals 0, raw ${num(facts.raw_food)}, ${dwarvesN} dwarves): meals outrank raw food - cook it`,
          'b -> Craftsdwarf / Kitchen -> butcher + kitchen near the farm')
      } else if (facts.raw_food != null && num(facts.raw_food) === 0 && num(facts.seeds) === 0) {
        raise('crew', 'crew-no-raw',
          `no raw food and no seeds (${dwarvesN} dwarves): the farm plot is the supply base`,
          'dig a 5x5 soil room, 3x3 farm plot, irrigate')
      }
    }
    // ---- dwarf deaths: `pop` counts pets/livestock too, so the alert reads the
    // dwarf number (measured: a 96->81 unit drop with the fort at 21 dwarves is
    // vermin churn, not a massacre). Unknown dwarves = no alert, never a guess.
    const dwarves = facts.dwarves == null ? null : num(facts.dwarves)
    if (dwarves != null && dwarves > 0) {
      if (this.dwarvesKnown && this.lastDwarves - dwarves >= cfg.pop_drop_min) {
        const lost = this.lastDwarves - dwarves
        raise('pop_drop', 'dwarf-pop-drop',
          `dwarves ${this.lastDwarves} -> ${dwarves} (-${lost}) between tick ${this.lastDwarvesTick} and ${this.tick}`
          + `; drink ${num(facts.drink)} food ${num(facts.food)}`,
          'find the cause: df_alerts, then the game reports - an empty report list is not evidence of none')
      }
      if (!this.dwarvesKnown) this.lastDwarvesTick = this.tick
      this.lastDwarves = dwarves
      this.dwarvesKnown = true
    }
    void this.popKnown
    if (num(facts.pop) > 0 && num(facts.drink) === 0 && num(facts.barrels) === 0) {
      // drink zero is the normal early state; only alert once per session for it
      raise('crew', 'crew-drink-early', `drink 0 (no barrels yet): the still rung stays open`, '')
    }

    // ---- dug state must never shrink: a DECREASE means tiles were destroyed
    // (measured 2026-11-21: a whole-box DEFAULT_DIG designation went over floors
    // and two stairs; the box's dug count is the thing that reveals it).
    for (const b of this.boxes) {
      if (!b || b.dug == null) continue
      const key = boxKey(b)
      const prev = this.dugBaseline.get(key)
      if (prev === undefined) { this.dugBaseline.set(key, num(b.dug)); continue }
      if (num(b.dug) < prev) {
        const box = b.box ?? []
        raise('tile_state_lost', `tile-loss-${key}`,
          `box x${box[0]}-${box[2]} y${box[1]}-${box[3]} elev${b.elev}: dug ${prev} -> ${num(b.dug)} (-${prev - num(b.dug)}) - positive tile state was REMOVED (stairs/floors destroyed by a designation over existing stuff?)`,
          `x${box[0]}-${box[2]} y${box[1]}-${box[3]} elev${b.elev}`,
          'rebuild what was lost (stairs: designate up-down stairs at the level BELOW the floor); from now on designate rock tiles only - survey first (df-dig §5)')
        this.dugBaseline.set(key, num(b.dug))
      } else if (num(b.dug) > prev) {
        this.dugBaseline.set(key, num(b.dug))
      }
    }

    // ---- the game's own hints: quote them, do not paraphrase ----
    // "Urdim ... cancels Eat: Item inaccessible" is the game saying dwarves
    // cannot eat; that outranks any of our derived supply math.
    const eatHints = (this.reports ?? []).filter((r) => /cancels Eat|Out of food|no food|Out of drink|get drunk/i.test(String(r.text ?? '')))
    if (eatHints.length) {
      const last = eatHints.at(-1)
      raise('crew', 'game-says-supply',
        `the game's own reports (${eatHints.length} of the last ${this.reports.length} reads): "${String(last.text).slice(0, 110)}"`,
        last.z != null ? `elev${last.z}` : '')
    }

    // ---- the game's own reports as alerts (its hints outrank derived math) ----
    // Newest-first over the reports we have not seen yet; only noteworthy
    // patterns raise, each quote is verbatim, and the sweep is bounded so a
    // busy announcement stream cannot flood the ledger.
    const notable = /batter|wound|kill|murder|attack|ambush|siege|breach|flood|lava|magma|volcano|necro|forgotten|vampire|were|demon|gollm|out of food|Out of food|no food|Out of drink|cancels Eat|cancels Drink|starv|trade carav|migrant/i
    const seen = this.reportSeen ?? new Set()
    this.reportSeen = seen
    const fresh = (this.reports ?? []).filter((r) => r?.text && notable.test(String(r.text)))
    let raisedNow = 0
    for (const r of fresh.slice(-6).reverse()) {
      const key = `game-says-${String(r.text).slice(0, 80)}`
      if (seen.has(key)) continue
      seen.add(key)
      raise('game_says', key,
        `the game says (Y${r.year ?? '?'} @time ${r.time ?? '?'}): "${String(r.text).slice(0, 140)}"`,
        r.z != null ? `elev${r.z}` : '')
      if (++raisedNow >= 3) break
    }

    // ---- crash log: NAME/SIZE only (contents are out of scope) ----
    const crashFiles = this.crashlog?.files ?? []
    if (this.crashlog && this.crashlog.dir) {
      const known = new Set(this.crashSeen)
      for (const file of crashFiles.slice(0, cfg.crashlog_files)) {
        const signature = `${file.name}:${file.bytes}`
        if (!known.has(signature) && !known.has(file.name)) {
          this.crashSeen.add(signature)
          this.crashSeen.add(file.name)
          raise('game_crashed', `crash-${file.name}`,
            `new crash log ${file.name} (${file.bytes} bytes, mtime ${new Date(file.mtime * 1000).toISOString()}) - contents NOT read (out of scope)`, file.name)
        }
      }
      if (crashFiles.length === 0 && this.crashSeen.size === 0) {
        // explicitly: no crash logs seen
      }
    }

    // ---- save reload: generation changed ----
    if (this.generation !== null && this.lastGeneration !== undefined && this.lastGeneration !== null
      && this.generation !== this.lastGeneration) {
      raise('save_reloaded', `gen-${this.generation}`,
        `generation ${this.lastGeneration} -> ${this.generation} (save reload): dug-state differences follow in the dig events`, '')
    }
    this.lastGeneration = this.generation

    // ---- acceptance tests on the ledger (plugin-run) ----
    const snapshot = { facts, piles: this.piles, buildings: this.buildings, boxes: this.boxes, rungs: this.rungs }
    for (const todo of this.todos) {
      if (todo.accept.kind === 'manual') continue
      const { ok, evidence, noData = false } = runAcceptance(todo, snapshot)
      todo.lastCheck = { tick: this.tick, ok: noData ? null : ok, evidence, noData }
      // Absence is never a fact (player-measured flap A11-A14 -> A15-A22): an
      // empty probe read must not reopen a completed to-do, and must not close
      // an open one. Only a real reading moves the state.
      if (noData) continue
      const wasDone = todo.state === 'done'
      const nextState = ok ? 'done' : (todo.state === 'done' ? 'open' : todo.state)
      if (nextState !== todo.state) {
        todo.state = nextState
        todo.statusTick = this.tick
        todo.evidence = evidence
        this.onLedgerWrite?.(todo)
        if (nextState === 'done') {
          this.digEvents.push({ tick: this.tick, kind: 'todo_done', todo: todo.id, title: todo.title, evidence })
          this.trim('digEvents', 40)
          raise('dig_complete', `todo-${todo.id}`, `to-do #${todo.id} "${todo.title}" -> done: ${evidence}`, todo.box ? `x${todo.box[0]}-${todo.box[2]} y${todo.box[1]}-${todo.box[3]} elev${todo.box[4]}` : '', todoAdvice(todo))
        } else if (wasDone) {
          raise('todo_reopened', `todo-reopen-${todo.id}`, `to-do #${todo.id} "${todo.title}" reopened: ${evidence}`, '')
        }
      }
    }

    // ---- overload (plan §9.2: tell the model to work the queue) ----
    const openAlerts = this.alerts.filter((a) => !a.acked && !a.cleared).length
    const openTodos = this.todos.filter((t) => t.state === 'open').length
    const overAlerts = openAlerts > cfg.overload_alerts
    const overTodos = openTodos > cfg.overload_todos
    this.overload = (overAlerts || overTodos)
      ? { alerts: openAlerts, todos: openTodos, overAlerts, overTodos }
      : null

    return raised.filter(Boolean)
  }

  trim(field, max) {
    if (this[field].length > max) this[field] = this[field].slice(0, max)
  }

  /** Re-apply stored records after a reload; seeds fill the gaps. */
  restoreTodos(stored) {
    const known = new Map(this.todos.map((t) => [t.id, t]))
    for (const rec of stored ?? []) {
      if (!rec || typeof rec.id !== 'number') continue
      known.set(rec.id, { ...rec })
    }
    this.todos = [...known.values()].sort((a, b) => a.id - b.id)
    this.nextTodoId = Math.max(this.nextTodoId, ...this.todos.map((t) => t.id)) + 1
    this.trim('todos', 80)
  }

  restoreBoxes(stored) {
    for (const box of stored ?? []) {
      if (!Array.isArray(box) || box.length < 5) continue
      const has = this.extraBoxes.some((b) => b[0] === box[0] && b[1] === box[1]
        && b[2] === box[2] && b[3] === box[3] && b[4] === box[4])
      if (!has) this.extraBoxes.push([...box])
    }
  }

  addTodo({ title, accept, box }) {
    // A counter shared with seeding: ids never collide, even when a to-do is
    // added before the first ladder read (measured: that gave id 1, clashing
    // with the seeded ledger).
    const id = Math.max(this.nextTodoId, this.todos.length ? Math.max(...this.todos.map((t) => t.id)) + 1 : 1)
    this.nextTodoId = id + 1
    const todo = { id, title, origin: 'model', state: 'open', accept: accept ?? { kind: 'manual' }, evidence: 'not checked yet', statusTick: this.tick }
    if (box) todo.box = box
    if (accept?.kind === 'tiles_progress' && box) todo.box = box
    this.todos.push(todo)
    this.trim('todos', 80)
    this.onLedgerWrite?.(todo)
    return todo
  }

  closeTodo(id, evidence) {
    const todo = this.todos.find((t) => t.id === Number(id))
    if (!todo) return null
    todo.state = 'done'
    todo.statusTick = this.tick
    this.onLedgerWrite?.(todo)
    todo.evidence = evidence ?? 'closed by the model (manual acceptance)'
    todo.accept = { kind: 'manual' }
    return todo
  }

  ackAlert(id) {
    const alert = this.alerts.find((a) => a.id === id)
    if (!alert) return null
    alert.acked = true
    alert.ackedAt = Date.now()
    return alert
  }

  /** Register a box for survey watching (from df_todo add with tiles_progress). */
  watchBox(box) {
    const [x0, y0, x1, y1, elev] = box.map(Number)
    const key = boxKey({ elev, x0, y0, x1, y1 })
    // Watching must enter the SURVEY list, not just the display: a box that is
    // never requested stays NO DATA forever (measured).
    if (!this.extraBoxes.some((b) => b[0] === x0 && b[1] === y0 && b[2] === x1 && b[3] === y1 && b[4] === elev)) {
      this.extraBoxes.push([x0, y0, x1, y1, elev])
      this.extraBoxes = this.extraBoxes.slice(-20)
      this.onBoxWatch?.([x0, y0, x1, y1, elev])
    }
    if (!this.boxes.some((b) => boxKey(b) === key)) {
      this.boxes.push({ elev, box: [x0, y0, x1, y1], to_dig: null, dug: null, pending: null, notes: 'awaiting first survey' })
      this.boxes = this.boxes.slice(-16)
    }
    return key
  }

  /** The live state as plain JSON (same field set the projection carries). */
  snapshot() {
    return this.payload()
  }

  /** Record that WE paused the game (attribution + never auto-resume a player pause). */
  recordPluginPause(tick) {
    this.pluginPauseTick = tick
    this.pauseSource = 'plugin'
    this.pauseAtTick = tick
  }

  /** A pause this plugin may issue (Config-gated; default OFF via pause_authority). */
  mayPause(kind, nowMs) {
    const cfg = this.cfg
    if (cfg.pause_authority !== 'events') return { ok: false, why: `pause_authority=${cfg.pause_authority}` }
    if (!cfg.pause_on.includes(kind)) return { ok: false, why: `${kind} not in Config.pause_on` }
    if (this.paused === true) return { ok: false, why: 'the game is already paused' }
    if (this.pluginPauseTick != null) return { ok: false, why: 'the plugin pause is still active' }
    const moving = this.boxes.some((b) => b.pending > 0) || num(this.facts?.pending_dig) > 0
    const dugRecently = this.digEvents.some((e) => e.tick >= this.tick - cfg.moving_window_ticks)
    if (!moving && !dugRecently) return { ok: false, why: 'hard guard: nothing designated and nothing moving - pausing an idle fort is the player\'s business' }
    return { ok: true }
  }

  /** The complete post-change state the model sees, as plain JSON. */
  payload() {
    const compactAlerts = this.alerts.filter((a) => !a.cleared).slice(0, 20).map((a) => ({
      id: a.id, kind: a.kind, evidence: a.evidence, coord: a.coord, suggested: a.suggested,
      acked: !!a.acked, firstTick: a.firstTick, lastTick: a.lastTick, count: a.count,
    }))
    return {
      tick: this.tick, t: Date.now(), online: this.online !== false,
      // How old the newest successful game read is: an agent must know the age of
      // the numbers it is planning with (the probe asked for exactly this).
      observedAt: this.observedAt ?? null,
      age_s: this.observedAt ? Math.round((Date.now() - this.observedAt) / 1000) : null,
      paused: this.paused, pauseSource: this.pauseSource, pauseAtTick: this.pauseAtTick,
      generation: this.generation,
      poll: this.pollMs ?? null,
      pauseAuthority: this.cfg.pause_authority,
      filter: this.filterState ?? null,
      facts: this.facts ? {
        pop: num(this.facts.pop), dwarves: this.facts.dwarves == null ? null : num(this.facts.dwarves),
        seeds: this.facts.seeds == null ? null : num(this.facts.seeds),
        alcohol: this.facts.alcohol == null ? null : num(this.facts.alcohol),
        meals: this.facts.meals == null ? null : num(this.facts.meals),
        raw_food: this.facts.raw_food == null ? null : num(this.facts.raw_food),
        site_id: this.facts.site_id ?? null, date: this.facts.date ?? null,
        miners: num(this.facts.miners),
        picks: this.facts.picks === null || this.facts.picks === undefined ? null : num(this.facts.picks),
        food: num(this.facts.food), drink: num(this.facts.drink), wood: num(this.facts.wood),
        stone: num(this.facts.stone), metal: num(this.facts.metal), barrels: num(this.facts.barrels),
        pending_dig: num(this.facts.pending_dig), underground_floors: num(this.facts.underground_floors),
        water_tiles: num(this.facts.water_tiles), magma_tiles: num(this.facts.magma_tiles),
      } : null,
      buildings: this.buildings.slice(0, 40),
      boxes: this.boxes.slice(0, 12),
      shafts: this.shafts.slice(0, 12),
      piles: this.piles.slice(0, 12),
      warnings: this.warnings ? {
        designated_tiles: num(this.warnings.designated_tiles), damp_tiles: num(this.warnings.damp_tiles),
        hot_tiles: num(this.warnings.hot_tiles),
        damp_boxes: (this.warnings.damp_boxes ?? []).slice(0, 4), hot_boxes: (this.warnings.hot_boxes ?? []).slice(0, 4),
      } : null,
      rungs: this.rungs.map((r) => ({ id: r.id, title: r.title, done: !!r.done, keys: r.keys, why: r.why })),
      next: this.next,
      alerts: compactAlerts,
      todos: this.todos.slice(0, 40).map((t) => ({
        id: t.id, title: t.title, origin: t.origin, state: t.state, accept: t.accept,
        evidence: t.evidence, box: t.box, cite: t.cite,
        // `lastCheck` was missing here, so `df_todo why` could only ever answer
        // "last check: none" - the acceptance runs, the payload dropped it.
        lastCheck: t.lastCheck ? {
          tick: t.lastCheck.tick, ok: t.lastCheck.ok, noData: t.lastCheck.noData === true,
          evidence: String(t.lastCheck.evidence ?? '').slice(0, 170),
        } : null,
      })),
      digEvents: this.digEvents.slice(0, 24),
      pauseEvents: this.pauseEvents.slice(0, 12),
      overload: this.overload,
      // The game's own announcements (its hints: "cancels Eat: Item inaccessible")
      // - measured gap: payload() never carried them, so no briefing, panel row or
      // projection fold could show them.
      reports: this.reports.slice(-6),
      crashlog: this.crashlog ?? null,
      notes: [...this.notes.slice(0, 12), ...this.pluginNotes()].slice(0, 16),
    }
  }
}

// ---------------------------------------------------------------------------
// The session projection: the log-derived copy of that state.
// ---------------------------------------------------------------------------

/** Collect every text string a message's content carries (tool results nest JSON deeper). */
function collectText(content, out = [], depth = 0) {
  if (depth > 4 || content == null) return out
  if (typeof content === 'string') {
    out.push(content)
    return out
  }
  if (Array.isArray(content)) {
    for (const item of content) collectText(item, out, depth + 1)
    return out
  }
  if (typeof content === 'object') {
    for (const key of ['text', 'output', 'content', 'value']) {
      if (content[key] !== undefined) collectText(content[key], out, depth + 1)
    }
  }
  return out
}

/** Extract the payload from a message's content, if it is an observer payload. */
export function parsePayload(content) {
  for (const text of collectText(content)) {
    const line = text.split('\n').find((l) => l.startsWith(PAYLOAD_PREFIX))
    if (!line) continue
    try {
      const payload = JSON.parse(line.slice(PAYLOAD_PREFIX.length))
      return payload.form === 'briefing' || payload.form === 'alert' || payload.form === 'tool'
        ? payload
        : null
    } catch {
      return null
    }
  }
  return null
}

/** The projection view: stable per state reference (unchanged state costs nothing). */
export function viewOf(state) {
  if (state._view === undefined) {
    state._view = {
      tick: state.tick, online: state.online, observedAt: state.observedAt,
      poll: state.poll ?? null, pauseAuthority: state.pauseAuthority ?? null,
      filter: state.filter ?? null,
      paused: state.paused, pauseSource: state.pauseSource, pauseAtTick: state.pauseAtTick,
      generation: state.generation, facts: state.facts, buildings: state.buildings,
      boxes: state.boxes, shafts: state.shafts, piles: state.piles, warnings: state.warnings,
      rungs: state.rungs, next: state.next, reports: state.reports, crashlog: state.crashlog,
      notes: state.notes, alerts: state.alerts, alertHistory: state.alertHistory,
      todos: state.todos, digEvents: state.digEvents, pauseEvents: state.pauseEvents,
      overload: state.overload, briefings: state.briefings,
    }
  }
  return state._view
}

function mergePayload(state, payload) {
  const base = { ...state }
  base._view = undefined
  const p = payload.data ?? {}
  for (const field of STATE_FIELDS) {
    if (p[field] !== undefined) base[field] = p[field]
  }
  return base
}

/**
 * Pure, synchronous fold over committed events. It ignores everything that is
 * not an observer payload, returning the SAME state reference (zero downstream
 * cost). A state-carrying log event carries the complete post-change state,
 * never a bare delta - so fork/resume refolds identically.
 */
export const dfObserverProjection = (z) => ({
  key: 'dfObserver',
  stateVersion: STATE_VERSION,
  // `wire.view` publishes the folded view to the Client (practices.md: the
  // panel reads the value the Host already computed; the Client folds nothing).
  wire: {
    viewSchema: z.json(),
    view: (state) => viewOf(state),
  },
  stateSchema: z.object({
    tick: z.number().default(0),
    online: z.boolean().nullable().default(null),
    observedAt: z.number().nullable().default(null),
    paused: z.boolean().nullable().default(null),
    pauseSource: z.string().nullable().default(null),
    pauseAtTick: z.number().nullable().default(null),
    generation: z.number().nullable().default(null),
    // The panel chips read these; omitting them from the schema silently made
    // them '?' in the UI (measured) and hid the filter state entirely.
    poll: z.number().nullable().default(null),
    pauseAuthority: z.string().nullable().default(null),
    filter: z.any().default(null),
    facts: z.any().default(null),
    buildings: z.array(z.any()).default([]),
    boxes: z.array(z.any()).default([]),
    shafts: z.array(z.any()).default([]),
    piles: z.array(z.any()).default([]),
    warnings: z.any().default(null),
    rungs: z.array(z.any()).default([]),
    next: z.any().default(null),
    reports: z.array(z.any()).default([]),
    crashlog: z.any().default(null),
    notes: z.array(z.any()).default([]),
    alerts: z.array(z.any()).default([]),
    alertHistory: z.array(z.any()).default([]),
    todos: z.array(z.any()).default([]),
    digEvents: z.array(z.any()).default([]),
    pauseEvents: z.array(z.any()).default([]),
    overload: z.any().default(null),
    briefings: z.number().default(0),
  }),
  init: () => ({
    tick: 0, online: null, observedAt: null, paused: null, pauseSource: null, pauseAtTick: null,
    generation: null, poll: null, pauseAuthority: null, filter: null,
    facts: null, buildings: [], boxes: [], shafts: [], piles: [], warnings: null,
    rungs: [], next: null, reports: [], crashlog: null, notes: [], alerts: [], alertHistory: [],
    todos: [], digEvents: [], pauseEvents: [], overload: null, briefings: 0,
  }),
  apply: (state, event) => {
    switch (event.type) {
      case 'user/message': {
        const payload = parsePayload(event.data.content)
        if (!payload) return state
        return mergePayload(state, payload)
      }
      case 'tool/result': {
        // Only our tools embed an observer payload; anything else is ignored by content.
        const payload = parsePayload(event.data.message?.content)
        if (!payload || payload.form !== 'tool') return state
        return mergePayload(state, payload)
      }
      case 'command/run': {
        // `/df-ack <id>` (the panel's Ack, and the same words a human may type):
        // fold the ack into THIS session's view - log-truth, no side channel.
        const line = typeof event.data?.rawInput === 'string' ? event.data.rawInput.trim() : ''
        const match = /^\/df-ack\s+(\S+)/.exec(line)
        if (!match) return state
        const id = match[1]
        let changed = false
        const alerts = (state.alerts ?? []).map((a) => {
          if (!a || a.id !== id || a.acked) return a
          changed = true
          return { ...a, acked: true }
        })
        if (!changed) return state
        const next = { ...state, alerts }
        delete next._view
        return next
      }
      default:
        return state
    }
  },
  view: viewOf,
})
