/**
 * df-observer - DSH plugin (Host half).
 *
 * One-sentence job (PLAN.md): watch the game through the DF MCP server and tell
 * the model what matters, UNPROMPTED - status, alerts, and the live to-do list
 * with proof - so a local model can play Dwarf Fortress without burning its
 * context polling tools.
 *
 * Mechanisms (verified against the installed 0.2.2-rc.5):
 *   - `apply(ctx, config)` + `Config` (schemastery) validate the row's config;
 *   - the poller is a plugin-owned async loop disposed with the fiber;
 *   - per-agent behaviour is registered on `agent.ctx` in `agent/created`,
 *     wrapped in `agent.ctx.effect()`, with the disposer ALSO kept keyed by
 *     agent in this plugin's own effect;
 *   - `agent.inject()` feeds the briefing (logged as inbox splice, enters the
 *     next admitted step, never wakes); `agent.followup()` wakes on Config
 *     whitelisted alerts only;
 *   - `ctx.systemPrompt.section()` carries the standing reading rules once;
 *   - `ctx.sessionProjections` unit folds everything the model sees from
 *     committed session events (pure synchronous apply, reference-stable view);
 *   - `ctx.tools.register()` adds df_status / df_todo / df_alerts;
 *   - `@Remote` methods serve the panel (client half) read-only.
 *
 * Hard boundaries (PLAN.md §5): read-only towards the game. Never designates,
 * builds, sets labours, runs commands, sends keys, digs, blueprints, or writes
 * game-dir files. The single contemplated write - pause - is Config-gated and
 * DEFAULT OFF (`pause_authority: off`).
 *
 * @module @local/df-observer
 */

import { z } from 'zod'
import { Service } from '@deepseek-ai/cordis'
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { createUserMessage } from '@deepseek-ai/dsh-llm'

import { ObserverTransport } from './observer.js'
import { FortModel, STATE_VERSION, significantChange, dfObserverProjection } from './model.js'
import { buildBriefing, buildWake, standingRules } from './brief.js'
import { toolDefinitions, jsonSafe } from './tools.js'
import { FILTER_SYSTEM_PROMPT, STATE_PREFIX, buildFilteredBriefing, buildFilterWake, parseVerdict } from './filter.js'

/** Every tunable lives here so the player edits `cordis.patch.yml`, not code. */
export const Config = z.object({
  /** DF MCP server base URL. */
  server_url: z.string().default('http://127.0.0.1:8000'),
  /** Fort elevation band (in-game panel numbers). 0 = auto from the server's fort band. */
  elev0: z.number().int().default(0),
  elev1: z.number().int().default(0),
  /** Monitored survey boxes: [x0, y0, x1, y1, elev]; elev 0 = band middle. */
  monitored_boxes: z.array(z.array(z.number()).length(5)).default([]),
  /** Watched stockpile ids. */
  pile_ids: z.array(z.number()).default([]),
  /** Watched shaft columns: [x, y] - water in these columns is the tripwire. */
  shafts: z.array(z.array(z.number()).length(2)).default([]),
  /** Report lines folded into the deep read (0 = off). */
  reports: z.number().int().min(0).default(4),
  /** Crash-log file signals to list (names/sizes only; 0 = off). */
  crashlog_files: z.number().int().min(0).default(6),
  /** Game directory for the crash-log watch (server env is used when empty). */
  game_dir: z.string().default(''),

  /** Adaptive poll: game RUNNING interval (player decision: 5-15 s). */
  poll_running_ms: z.number().int().min(5000).max(15000).default(8000),
  /** Adaptive poll: game PAUSED interval (player decision: 60 s). */
  poll_paused_ms: z.number().int().min(10000).default(60000),
  /** Every N fast ticks also runs the deep read (ladder, piles, reports, crash log). */
  deep_every: z.number().int().min(1).default(4),
  /** Transport: grace while the game is offline (long game-off is normal). */
  offline_grace_ms: z.number().int().min(1000).default(3600000),
  /** Transport: first/maximum retry nap. */
  nap_min_ms: z.number().int().min(200).default(2000),
  nap_max_ms: z.number().int().min(1000).default(60000),
  /** Announce "game offline" at most this often. */
  announce_every_ms: z.number().int().min(1000).default(120000),

  /** Hard cap on one briefing message (tokens, ~chars/4). */
  briefing_max_tokens: z.number().int().min(120).default(700),
  /** 'on': inject when meaningful state changes; 'always': every deep read; 'off': never. */
  briefing_mode: z.enum(['on', 'always', 'off']).default('on'),
  /** Minimum spacing between briefings for one agent. */
  inject_min_spacing_ms: z.number().int().min(0).default(30000),
  /** Force a refresh briefing after this long without one (state is stale by then). */
  inject_max_spacing_ms: z.number().int().min(60000).default(600000),
  /** A box to_dig shift smaller than this does not cost the model a briefing. */
  min_to_dig_delta: z.number().int().min(1).default(3),

  /** Alert kinds that WAKE the agent (player decision 2026-11-21: water, dig_complete, crash). */
  alert_wakes: z.array(z.string()).default(['water', 'dig_complete', 'tile_state_lost', 'game_crashed', 'game_offline']),
  /** Minimum spacing between wakes for one agent. */
  wake_min_spacing_ms: z.number().int().min(10000).default(60000),

  /** Pause authority, DEMOTED per PLAN §5a: off | events. Default OFF. */
  pause_authority: z.enum(['off', 'events']).default('off'),
  /** Which alert kinds may trigger a plugin pause (opt-in per event). */
  pause_on: z.array(z.string()).default(['water', 'overload']),
  /** Plugin pauses only, never unpauses (default). true = auto-resume plugin-made pauses. */
  auto_resume: z.boolean().default(false),
  auto_resume_after_ms: z.number().int().min(10000).default(120000),
  /** Attribution window: a pause within N ticks of our call is attributed to the plugin. */
  plugin_pause_attribution_ticks: z.number().int().min(1).default(12),
  /** Hard guard window: "nothing moving" lookback in ticks. */
  moving_window_ticks: z.number().int().min(1).default(8),

  /** Overload signalling thresholds (plan §9.2). */
  overload_alerts: z.number().int().min(1).default(6),
  overload_todos: z.number().int().min(1).default(20),
  /** Stall: designations drained with no dug change for N consecutive ticks. */
  stall_ticks: z.number().int().min(2).default(4),
  /** A pile this full on a small area is reported as full. */
  pile_full_items: z.number().int().min(10).default(150),

  /** Agent ids to brief/wake (empty = every live root agent). */
  agents: z.array(z.string()).default([]),
  /** Also brief/wake subagent sessions (they inherit the briefing in their log). */
  brief_subagents: z.boolean().default(false),
  /** Quiet switches (player asks for a silent agent). */
  wake_enabled: z.boolean().default(true),
  /** Rules section budget (measured: the full text costs ~316 tokens on EVERY
   * assembly): full | compact (default: only what a briefing line cannot say) | off. */
  rules_mode: z.enum(['full', 'compact', 'off']).default('compact'),
  /** full = the long tool how-to text; compact = terse labels (measured 962 -> ~520 tokens per request). */
  tool_docs: z.enum(['full', 'compact']).default('compact'),
  /** Alert when the population falls by this many dwarves between reads. The
   * observer's own rule: absence of reports is not absence of a cause. */
  pop_drop_min: z.number().int().min(1).default(5),
  /** Filter subagent (plan §11): a plugin-owned analyst session that reads the
   * full observation payload in ITS context and answers with one compact
   * verdict; play sessions get the filtered briefing. Default OFF - measure,
   * then flip. The wake whitelist stays Config-enforced over the filter. */
  filter_enabled: z.boolean().default(false),
  /** Preset the filter session runs (empty = profile default). */
  filter_agent_preset: z.string().default(''),
  /** Optional explicit model route for the filter ({provider, model}). */
  filter_provider: z.string().default(''),
  filter_model: z.string().default(''),
  /** Cap on the filtered briefing injected into play sessions. */
  filter_briefing_max_tokens: z.number().int().min(80).default(340),
  /** Fall back to the rule-gated briefing if no verdict arrives this long
   * after a filter wake (the filter session is not the game's gatekeeper). */
  filter_timeout_ms: z.number().int().min(10000).default(120000),
  /** Max size of the state payload pushed to the filter (its own context). */
  filter_state_max_tokens: z.number().int().min(400).default(4000),
  /** Push the FULL payload every N significant ticks; the rest are gate lines
   * only. The filter's session log is its history, so full pushes can be sparse. */
  filter_full_every: z.number().int().min(1).default(2),
}).prefault({})


const BRIEF_SECTION_ORDER = 55

/**
 * Mark one prototype method as a direct Remote endpoint without decorator
 * syntax: applies the protocol's `Remote(options)` decorator with a
 * ClassMethodDecoratorContext whose initializer runs on the instance being
 * constructed. Requires: called from inside the constructor (`this` = instance).
 */
/**
 * Apply the `@Remote` marker to the PROTOTYPE at module load - the Node runtime
 * here has no decorator syntax, and the gateway's binding is read when the
 * service is CONSTRUCTED (`bindTypertRemote` in the base constructor), so a
 * marker applied later (in `[Service.init]`) is invisible to the Client.
 * We fabricate the standard ClassMethodDecoratorContext, take the initializer
 * the decorator schedules, and run it with `this` bound to an object whose
 * prototype is the class prototype - exactly what the real decorator does.
 */
function markRemotePrototype(Class, method, options) {
  const decorator = Remote(options)
  let initializer = null
  decorator(function () {}, {
    kind: 'method',
    name: method,
    static: false,
    private: false,
    addInitializer(fn) { initializer = fn },
  })
  if (initializer === null) throw new Error(`df-observer: Remote marker for ${method} produced no initializer`)
  initializer.call(Object.create(Class.prototype))
}

/**
 * The plugin service: owns the poller, the live model, the session projection,
 * the per-agent briefing/wake state, the tools, and the panel Remote methods.
 */
/**
 * Which inbox boundary a message uses (player-measured: a `followup` wake on a
 * busy agent sits above the composer as a queued turn until it is pressed).
 *   alert   -> steer  (idle agent: starts a turn; busy agent: consumes it at
 *            its next step boundary, so the play agent sees it mid-task)
 *   else    -> inject (context for the next pre-step, never wakes)
 * Falls back to followup when this Agent has no steer.
 */
/** Pull the [df-verdict] out of one `session/event` assistant event (both the
 * log shape `data.message.content` and a bare `data.content`). */
export function verdictFromEvent(event) {
  if (event?.type !== 'assistant/message') return null
  const data = event.data ?? {}
  return parseVerdict(data.message?.content ?? data.content ?? event.content)
}

export function deliveryOf(form, agent) {
  if (form !== 'alert') return 'inject'
  return typeof agent?.steer === 'function' ? 'steer' : 'followup'
}

export default class DFObserver extends TypertRemoteService {
  static inject = ['tools', 'agents', 'systemPrompt', 'sessionProjections', 'timer', 'commands', 'storageDomain']
  static Config = Config

  constructor(ctx, config = Config.parse({})) {
    super(ctx, 'dfObserver')
    this.config = config
    this.model = new FortModel(config)
    this.model.notesSource = () => this.noteLines.map((n) => `plugin: ${n.message}`)
    this.transport = new ObserverTransport({
      base: config.server_url, timeoutMs: 30000,
      offlineGraceMs: config.offline_grace_ms,
      napMinMs: config.nap_min_ms, napMaxMs: config.nap_max_ms,
      announceEveryMs: config.announce_every_ms,
    }, (message) => this.note(message))
    /** Per-agent briefing/wake bookkeeping: {lastTick, lastAt, lastSignal, lastWakeAt}. */
    this.agents = new Map()
    this.disposers = new Map()
    this.projection = null
    this.stopped = false
    this.abort = new AbortController()
    this.lastWakeTick = 0
    this.noteLines = []
    this.filterSessionId = null
    this.filterVerdict = null
    this.filterWokeAt = null
    this.filterPushTick = 0
    this.filterFallback = false
    this.filterPromptInstalled = false
    this.modelPollAnnounced = 0
    // Remote endpoints, marked imperatively (Node runs this module without a
    // decorator transform; the marker path below is exactly the @Remote form).
    // markers live at module scope (see markRemotePrototype) - the gateway
    // reads the prototype descriptor at construction time.
  }

  note(message) {
    this.noteLines.push({ at: Date.now(), message })
    if (this.noteLines.length > 40) this.noteLines.shift()
    try {
      this.ctx.logger?.('df-observer')?.info(message)
    } catch {
      // no logger in this composition: the notes stay in `this.noteLines`
    }
  }

  async [Service.init]() {
    const { ctx, config } = this

    // ---- session projection: everything the model sees, refoldable from the log ----
    this.projection = await ctx.sessionProjections.register(dfObserverProjection(z))

    // ---- standing reading rules, once per assembly ----
    ctx.effect(() => ctx.systemPrompt.section({
      name: 'df-observer',
      order: BRIEF_SECTION_ORDER,
      text: standingRules(config.rules_mode),
    }), 'df-observer: prompt section')

    // ---- the pull tools ----
    const sessionFilter = (exec) => this.targetAgent(exec.agent) !== null
    for (const definition of toolDefinitions({
      model: this.model, projection: this.projection, config, sessionFilter,
    })) {
      ctx.effect(() => ctx.tools.register(definition, {
        when: ({ agent }) => this.targetAgent(agent) !== null,
      }), `df-observer: tool ${definition.name}`)
    }

    // ---- per-agent behaviour: registered on agent.ctx, disposed both ways ----
    ctx.on('agent/created', ({ agent }) => {
      if (this.targetAgent(agent) === null) return
      const entry = { lastTick: 0, lastAt: 0, lastSignal: null, lastWakeAt: 0 }
      this.agents.set(agent.id, entry)
      const dispose = agent.ctx.effect(() => () => {
        this.agents.delete(agent.id)
        this.disposers.delete(agent.id)
      }, 'df-observer: agent bookkeeping')
      this.disposers.set(agent.id, dispose)
    })
    ctx.on('agent/disposed', ({ agent }) => {
      this.agents.delete(agent.id)
      const dispose = this.disposers.get(agent.id)
      this.disposers.delete(agent.id)
      dispose?.()
    })
    ctx.effect(() => () => {
      for (const dispose of this.disposers.values()) dispose()
      this.disposers.clear()
      this.agents.clear()
    }, 'df-observer: agent disposers')

    // ---- durable ledger: the to-do list survives plugin reloads ----
    // (measured gap: a reinstall wiped a user-added to-do; plugin memory is a
    // derived cache, so anything the user registered must be stored.
    // Measured the hard way: `ctx.effect(() => this.openLedger())` never opens
    // the domain - an async body is not a disposer, and the promise is dropped.
    // Await the open HERE, in activation, then register the returned close.)
    const ledgerDispose = await this.openLedger()
    // ctx.effect calls its callback as the CONSTRUCTOR and uses what it returns
    // as the disposer. Passing the disposer itself runs it at activation - the
    // domain closed immediately and every write failed ("domain is closed").
    if (ledgerDispose) ctx.effect(() => ledgerDispose, 'df-observer: ledger storage')

    // ---- the panel's Ack, and the human's words, as a session command ----
    // (practices.md/user-actions.md: one Host operation, called by the UI through
    // `ctx.remote.commands.execute(sessionId, line, [])` and by the agent through
    // `df_alerts({action:"ack"})` - both land on model.ackAlert.)
    ctx.effect(() => ctx.commands.register({
      name: 'df-ack',
      description: 'Silence repeats of one DF observer alert until its condition clears: /df-ack <alert-id>',
      input: { hint: '<alert-id> (from df_alerts / the observer panel)' },
      handler: ({ agent, rawInput }) => {
        const id = String(rawInput ?? '').trim().split(/\s+/)[0]
        if (!id) return { kind: 'error', text: 'df-ack: give an alert id, e.g. /df-ack A12' }
        const alert = this.model.ackAlert(id)
        if (!alert) {
          const known = this.model.alerts.map((a) => a.id).join(', ')
          return { kind: 'error', text: `df-ack: no alert "${id}". Active ids: ${known || 'none'}` }
        }
        void agent
        return { kind: 'success', text: `df-ack: ${alert.id} (${alert.kind}) silenced until its condition clears.` }
      },
    }), 'df-observer: /df-ack command')

    // ---- filter subagent surface (Config-gated; OFF by default) ----
    if (config.filter_enabled) this.installFilterSurface()

    // ---- the poll loop ----
    ctx.effect(() => {
      void this.pollLoop()
      return () => {
        this.stopped = true
        this.abort.abort(new Error('disposed'))
      }
    }, 'df-observer: poll loop')
  }

  // ---------------------------------------------------------------------
  // Targeting
  // ---------------------------------------------------------------------

  targetAgent(agent) {
    if (!agent) return null
    const origin = agent.session?.header?.origin
    if (origin === 'subagent' && !this.config.brief_subagents) return null
    if (this.config.agents.length && !this.config.agents.includes(String(agent.id))) return null
    return agent
  }

  liveTargets() {
    const out = []
    for (const agent of this.ctx.agents.list()) {
      if (this.targetAgent(agent) === null) continue
      // Agents created before this plugin activated never emitted `agent/created`
      // for us: adopt them lazily so a live session is not briefing-blind.
      if (!this.agents.has(agent.id)) {
        this.agents.set(agent.id, { lastTick: 0, lastAt: 0, lastSignal: null, lastWakeAt: 0 })
        this.disposers.set(agent.id, agent.ctx.effect(() => () => {
          this.agents.delete(agent.id)
          this.disposers.delete(agent.id)
        }, 'df-observer: agent bookkeeping (adopted)'))
      }
      out.push(agent)
    }
    return out
  }

  // ---------------------------------------------------------------------
  // The poll cycle: adaptive interval from the player's decision
  // (game paused 60 s, game running 5-15 s).
  // ---------------------------------------------------------------------

  currentIntervalMs() {
    return this.model.paused === true ? this.config.poll_paused_ms : this.config.poll_running_ms
  }

  async pollLoop() {
    let tickIndex = 0
    while (!this.stopped) {
      const started = Date.now()
      try {
        await this.cycle(tickIndex)
        tickIndex += 1
      } catch (error) {
        if (!this.stopped) this.note(`poll cycle failed: ${error?.message ?? error}`)
      }
      if (this.stopped) break
      const wait = Math.max(1000, this.currentIntervalMs() - (Date.now() - started))
      await this.sleepCancellable(wait)
    }
  }

  sleepCancellable(ms) {
    return new Promise((resolve) => {
      const timer = setTimeout(resolve, ms)
      const onAbort = () => {
        clearTimeout(timer)
        resolve()
      }
      this.abort.signal.addEventListener('abort', onAbort, { once: true })
      if (this.abort.signal.aborted) onAbort()
    })
  }

  async cycle(index) {
    const cfg = this.config
    const isDeep = index % cfg.deep_every === 0
    const seen = new Set()
    const boxes = [...cfg.monitored_boxes, ...(this.model?.extraBoxes ?? [])]
      .filter((b) => {
        const k = b.join(',')
        if (seen.has(k)) return false
        seen.add(k)
        return true
      })
      .slice(0, 16)
    const request = {
      boxes,
      shafts: cfg.shafts,
      warnings: true, facts: true, rungs: false,
      reports: 0, crashlog_files: 0, pause: true, survey: true,
    }
    if (cfg.elev0 > 0 && cfg.elev1 > 0) {
      request.elev0 = cfg.elev0
      request.elev1 = cfg.elev1
    }
    if (isDeep) {
      request.rungs = true
      // The status probe (picks/site/date/seeds) costs a Lua round-trip: deep ticks only.
      request.status_probe = true
      request.pile_ids = cfg.pile_ids
      request.reports = cfg.reports
      request.crashlog_files = cfg.crashlog_files
      if (cfg.game_dir) request.crashlog_dir = cfg.game_dir
    }

    const outcome = await this.transport.withRetry(() => this.transport.observe(request))
    if (outcome.ok === false) {
      if (outcome.bug) {
        this.note(`transport bug (4xx = our bug): ${outcome.bug.message}`)
        const alerts = this.model.markOffline({ bug: outcome.bug })
        await this.dispatchAlerts(alerts)
      } else if (outcome.offline) {
        const raised = this.model.markOffline({ offlineMs: outcome.offlineMs, lastError: outcome.lastError })
        await this.dispatchAlerts(raised)
      }
      return
    }
    const { data, wasOffline, offlineMs } = outcome
    if (wasOffline) {
      await this.dispatchAlerts(this.model.markOnline(offlineMs))
    }
    this.model.pollMs = this.currentIntervalMs()
    this.model.filterState = this.filterEnabled() ? {
      enabled: true, live: !!this.filterAgent(), verdictTick: this.filterVerdict?.tick ?? null,
    } : null
    this.model.ingestFast(data)
    if (isDeep) this.model.ingestDeep(data)
    const raised = this.model.evaluate({})
    await this.dispatchAlerts(raised)
    const gateNotes = this.filterEnabled()
      ? significantChange(this.lastGateSignal, this.model.payload(), { min_to_dig_delta: this.config.min_to_dig_delta })
      : []
    if (this.filterEnabled() && (isDeep || gateNotes.length)) {
      this.lastGateSignal = this.model.payload()
      this.wakeFilter(gateNotes, isDeep)
    }
    await this.dispatchBriefing()
    await this.dispatchFilterWakes()
    await this.maybePauseFor(raised)
    await this.maybeAutoResume()
  }

  // ---------------------------------------------------------------------
  // Briefing (inject, delta-gated, bounded) and wakes (followup, whitelisted)
  // ---------------------------------------------------------------------

  async openLedger() {
    const storage = this.ctx.get('storageDomain')
    if (storage === undefined) {
      this.note('no storageDomain in this composition: the ledger lives only in memory this run (labelled)')
      return null
    }
    let domain
    try {
      domain = await storage.open({
      name: 'df_observer', // UNIT_NAME_RE ^[a-z][a-z0-9_]*$ - camelCase is rejected
      version: 1,
      layout: 'per-record',
        tables: {
          todos: { valueSchema: z.json() },
          boxes: { valueSchema: z.json() },
        },
      })
    } catch (error) {
      this.note(`ledger open failed (in-memory this run, labelled): ${error?.message ?? error}`)
      return null
    }
    this.ledger = domain
    // Load what was stored, then let the model's seeds fill the gaps.
    try {
      const stored = [...domain.table('todos').entries()].map(([, v]) => v)
      if (stored.length) this.model.restoreTodos(stored)
      const boxes = [...domain.table('boxes').entries()].map(([, v]) => v)
      if (boxes.length) this.model.restoreBoxes(boxes)
      this.note(`ledger restored: ${stored.length} to-dos, ${boxes.length} watched boxes`)
    } catch (error) {
      this.note(`ledger load failed (starting fresh, labelled): ${error?.message ?? error}`)
    }
    // Every ledger mutation lands durably (writes are queued by the domain).
    this.model.onLedgerWrite = (todo) => {
      void domain.table('todos').put(String(todo.id), JSON.parse(JSON.stringify(todo)))
        .catch((e) => this.note(`ledger write failed: ${e?.message ?? e}`))
    }
    this.model.onBoxWatch = (box) => {
      const key = `${box[4]}:${box[0]},${box[1]},${box[2]},${box[3]}`
      void domain.table('boxes').put(key, JSON.parse(JSON.stringify(box)))
        .catch((e) => this.note(`box write failed: ${e?.message ?? e}`))
    }
    return () => { void domain.close() }
  }

  filterEnabled() {
    return this.config.filter_enabled === true
  }

  /** The verdict is usable only for the tick (or later) it was drawn from. */
  freshVerdict(payload) {
    const verdict = this.filterVerdict
    if (!verdict) return null
    if (verdict.tick < payload.tick - this.config.deep_every * 2) return null
    return verdict
  }

  async dispatchBriefing() {
    if (this.config.briefing_mode === 'off') return
    const payload = this.model.payload()
    const now = Date.now()
    const verdict = this.filterEnabled() ? this.freshVerdict(payload) : null
    const timedOut = this.filterEnabled() && this.filterTimedOut()
    for (const agent of this.liveTargets()) {
      const entry = this.agents.get(agent.id)
      if (!entry) continue
      const changed = this.config.briefing_mode === 'always'
        ? ['mode-always']
        : significantChange(entry.lastSignal, payload, { min_to_dig_delta: this.config.min_to_dig_delta })
      const spaced = now - entry.lastAt >= this.config.inject_min_spacing_ms
      const overdue = now - entry.lastAt >= this.config.inject_max_spacing_ms
      let brief = null
      if (verdict) {
        // The filter spoke: significance decides, the plugin composes the text.
        if (verdict.significance === 'noise' && !overdue) continue
        if (verdict.significance !== 'high' && !spaced && !overdue) continue
        brief = buildFilteredBriefing(verdict, payload, {
          maxTokens: this.config.filter_briefing_max_tokens,
        })
      } else {
        if (this.filterEnabled() && !timedOut) continue   // verdict pending; do not pre-empt it
        if (!changed.length && !overdue) continue
        if (!spaced && !overdue) continue
        brief = buildBriefing(payload, { maxTokens: this.config.briefing_max_tokens })
        if (timedOut && !this.filterFallback) {
          this.filterFallback = true
          this.model.notes = ['filter silent past Config.filter_timeout_ms: rule-gate briefing resumed',
            ...this.model.notes].slice(0, 12)
        }
      }
      this.injectMessage(agent, brief, 'briefing')
      entry.lastSignal = payload
      entry.lastAt = now
      entry.lastTick = payload.tick
      this.model.briefings += 1
    }
  }

  /** Filter-proposed wakes are plugin-approved: kind must be in Config.alert_wakes. */
  async dispatchFilterWakes() {
    if (!this.filterEnabled()) return
    const verdict = this.filterVerdict
    if (!verdict?.wake?.length) return
    if (verdict.tick !== this.model.tick) return
    const payload = this.model.payload()
    const now = Date.now()
    for (const proposed of verdict.wake) {
      if (!this.config.alert_wakes.includes(proposed.kind)) continue
      const alert = this.model.alerts.find((a) => a.kind === proposed.kind && !a.cleared && !a.acked)
        ?? { id: `F${this.model.tick}`, kind: proposed.kind, evidence: proposed.evidence,
             coord: '', suggested: '', acked: false }
      for (const agent of this.liveTargets()) {
        const entry = this.agents.get(agent.id)
        if (!entry || alert.wakeSent) continue
        if (now - entry.lastWakeAt < this.config.wake_min_spacing_ms) continue
        const wake = buildFilterWake({ ...alert, evidence: proposed.evidence || alert.evidence }, payload)
        this.injectMessage(agent, wake, 'alert')
        alert.wakeSent = true
        entry.lastWakeAt = now
      }
    }
  }

  async dispatchAlerts(alerts) {
    if (!alerts?.length) return
    const payload = this.model.payload()
    const now = Date.now()
    for (const alert of alerts) {
      if (!this.config.wake_enabled) continue
      if (!this.config.alert_wakes.includes(alert.kind)) continue
      for (const agent of this.liveTargets()) {
        const entry = this.agents.get(agent.id)
        if (!entry) continue
        if (alert.wakeSent) continue
        if (now - entry.lastWakeAt < this.config.wake_min_spacing_ms) continue
        const wake = buildWake(alert, payload, { maxTokens: this.config.briefing_max_tokens })
        this.injectMessage(agent, wake, 'alert')
        alert.wakeSent = true
        entry.lastWakeAt = now
      }
    }
  }

  injectMessage(agent, { text }, form) {
    const wake = form === 'alert'
    try {
      // Alerts must WAKE the agent: agent.inject() parks a message in the inbox
      // until other input arrives (practices.md), which is right for briefings
      // (state, not a request) and wrong for the wake conditions the plan pays
      // for - water, a drained face, a crash, the game going offline.
      const message = createUserMessage({
        source: { kind: 'df-observer', form, summary: form === 'alert' ? 'DF observer alert' : 'DF observer briefing' },
        content: [{ type: 'text', text }],
      })
      if (deliveryOf(form, agent) === 'steer') {
        agent.steer(message)
      } else if (wake) {
        // An alert must reach a BUSY agent too. Measured (player report): via
        // `followup` the wake sat above the composer as a queued turn until the
        // player pressed it. `steer` is the right boundary - "an idle driver
        // starts a turn; a running driver consumes it at its next step
        // boundary" - so steer first, and fall back to followup only if this
        // Agent has no steer.
        if (typeof agent.steer === 'function') agent.steer(message)
        else agent.followup(message)
      } else {
        // Briefings are state, not a request: inject (never wakes the driver).
        agent.inject(message)
      }
    } catch (error) {
      this.note(`inject failed for ${agent.id}: ${error?.message ?? error}`)
    }
  }

  // ---------------------------------------------------------------------
  // Filter subagent: spawn once, brief through its verdict, fall back to the
  // rule gate when it is silent. The plugin keeps every boundary enforced.
  // ---------------------------------------------------------------------

  installFilterSurface() {
    const { ctx, config } = this
    this.filterSessionId = `df-observer-filter-${Date.now().toString(36)}`
    // The filter's standing rules: scoped to its own session only.
    ctx.effect(() => ctx.systemPrompt.section({
      name: 'df-observer-filter',
      order: BRIEF_SECTION_ORDER + 1,
      text: (assemble) => (assemble?.session?.id === this.filterSessionId || assemble?.agent?.id === this.filterSessionId)
        ? FILTER_SYSTEM_PROMPT : '',
    }), 'df-observer: filter prompt')
    // The filter's only read: the full observation payload (its context pays).
    ctx.effect(() => ctx.tools.register({
      name: 'df_observe_state',
      description: 'Read the observer\'s complete current state payload (facts, boxes by tile categories, piles, shafts, warnings, rungs, ledger, alerts). Only the df-observer filter session calls this; answer with one [df-verdict] line as your instructions require.',
      parameters: {
        type: 'object',
        properties: {
          focus: { type: 'string', description: 'Optional focus word (boxes|piles|shafts|ledger|alerts|ladder) to trim the answer toward; everything is always present in full at default.' },
        },
        required: [],
        additionalProperties: false,
      },
      output: {
        schema: { type: 'object', additionalProperties: true },
        render: (args, value) => [{ type: 'text', text: typeof value?.text === 'string' ? value.text : JSON.stringify(value) }],
      },
      async execute() {
        // The Host rejects non-lossless tool output; the payload carries nulls
        // and nested rows, so pass it through the same guard the other tools use.
        const payload = jsonSafe(this.model.payload())
        const text = JSON.stringify(payload)
        const cap = this.config.filter_state_max_tokens * 4
        return { text: text.length > cap ? `${text.slice(0, cap)}…(truncated at the state cap: labelled, never a fact)` : text }
      },
    }, { when: ({ agent }) => agent?.id === this.filterSessionId }), 'df-observer: filter tool')
    // Verdict capture: the filter session's assistant answers fold into the
    // plugin's live state; the filter session's own log keeps them durably.
    // Measured: `assistant/message` is NOT a bus event - the durable append feed
    // is `session/event`. The old listener could never fire, so the filter ran
    // (its log holds verdicts) and the plugin stayed on rule briefings with
    // `verdict tick ?` forever.
    ctx.on('session/event', (session, event) => {
      const sessionId = session?.id ?? (typeof session === 'string' ? session : undefined)
      if (sessionId !== this.filterSessionId) return
      if (event?.type !== 'assistant/message') return
      const verdict = verdictFromEvent(event)
      if (verdict) this.acceptVerdict(verdict)
    })
    ctx.effect(() => () => {
      const agent = this.filterAgent()
      if (agent) { try { agent.cancel('df-observer: plugin disposed') } catch { /* already gone */ } }
    }, 'df-observer: filter disposal')
  }

  filterAgent() {
    return this.filterSessionId ? this.ctx.agents.get(this.filterSessionId) : undefined
  }

  async ensureFilterAgent() {
    if (!this.config.filter_enabled || this.filterAgent()) return this.filterAgent()
    const controller = this.ctx.get('sessionController')
    if (controller === undefined) {
      this.note('filter enabled but no sessionController in this composition: filter inactive (rule gate continues)')
      this.config = { ...this.config, filter_enabled: false }
      return undefined
    }
    try {
      await controller.create({
        sessionId: this.filterSessionId,
        cwd: process.cwd(),
        ...(this.config.filter_agent_preset ? { agentPreset: this.config.filter_agent_preset } : {}),
      })
      for (let i = 0; i < 20 && !this.filterAgent(); i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 250))
      }
      const agent = this.filterAgent()
      if (agent && this.config.filter_provider && this.config.filter_model) {
        try { agent.model.set({ provider: this.config.filter_provider, model: this.config.filter_model }) } catch (e) {
          this.note(`filter model route rejected: ${e?.message ?? e}`)
        }
      }
      this.note(agent ? `filter session live: ${this.filterSessionId}` : 'filter session created but not live yet')
      return agent
    } catch (error) {
      this.note(`filter spawn failed: ${error?.message ?? error}`)
      return undefined
    }
  }

  acceptVerdict(verdict) {
    this.filterVerdict = verdict
    this.filterVerdictAt = Date.now()
    this.filterFallback = false
  }

  filterWakeMessage({ tick, gateNotes, payload, full }) {
    const gate = gateNotes.length ? ` Numeric gate: ${gateNotes.join(', ')}.` : ''
    const instruction = 'Answer with exactly one [df-verdict] line per your instructions.'
    if (!full) {
      return createUserMessage({
        source: { kind: 'df-observer', form: 'filter-tick', summary: `DF observer filter tick ${tick}` },
        content: [{ type: 'text', text: `Observation tick ${tick}.${gate} No full payload this tick - decide from your log history and df_observe_state if you need it. ${instruction}` }],
      })
    }
    let body = JSON.stringify(payload)
    const cap = this.config.filter_state_max_tokens * 4
    if (body.length > cap) body = `${body.slice(0, cap)}…(truncated: labelled, never a fact)`
    return createUserMessage({
      source: { kind: 'df-observer', form: 'filter-tick', summary: `DF observer filter tick ${tick} (full)` },
      content: [{ type: 'text', text: `Observation tick ${tick}.${gate} Full state payload follows; compare with your earlier ticks and rank what changed. ${instruction}\n${STATE_PREFIX}${body}` }],
    })
  }

  wakeFilter(gateNotes, isDeep) {
    const agent = this.filterAgent() ?? null
    void (async () => {
      const live = agent ?? await this.ensureFilterAgent()
      if (!live) return
      this.filterWokeAt = Date.now()
      const full = this.filterPushTick % this.config.filter_full_every === 0
      this.filterPushTick += 1
      try {
        live.followup(this.filterWakeMessage({
          tick: this.model.tick, gateNotes, payload: this.model.payload(), full,
        }))
      } catch (e) {
        this.note(`filter wake failed: ${e?.message ?? e}`)
      }
      void isDeep
    })()
  }

  filterTimedOut() {
    if (this.filterWokeAt === null) return false
    if (this.filterVerdictAt && this.filterVerdictAt >= this.filterWokeAt) return false
    return Date.now() - this.filterWokeAt > this.config.filter_timeout_ms
  }

  // ---------------------------------------------------------------------
  // Pause: attribution is always on; ISSUANCE is Config-gated, default OFF.
  // ---------------------------------------------------------------------

  async maybePauseFor(raised) {
    const cfg = this.config
    if (cfg.pause_authority !== 'events') return
    for (const alert of raised ?? []) {
      if (!cfg.pause_on.includes(alert.kind)) continue
      const gate = this.model.mayPause(alert.kind, Date.now())
      if (!gate.ok) continue
      try {
        const answer = await this.transport.setPause(true)
        this.model.recordPluginPause(this.model.tick)
        const note = `PLUGIN PAUSE (Config pause_on event "${alert.kind}"): ${alert.evidence}`
        this.model.notes = [note, ...this.model.notes].slice(0, 12)
        this.model.pauseEvents.push({ tick: this.model.tick, paused: true, source: 'plugin', alert: alert.id })
        this.note(note)
        void answer
      } catch (error) {
        this.note(`plugin pause refused by the server: ${error?.message ?? error}`)
      }
    }
  }

  /** Auto-resume: default OFF; only plugin-made pauses, never over a player pause. */
  async maybeAutoResume() {
    const cfg = this.config
    if (!cfg.auto_resume) return
    if (this.model.paused !== true) return
    if (this.model.pauseSource !== 'plugin') return          // a player/game pause: the player's business
    if (this.model.overload) return                          // queue not drained
    if (this.model.pluginPauseTick == null && this.model.pauseAtTick == null) return
    const since = Date.now() - (this.model.pluginPauseAtMs ?? Date.now())
    if (since < cfg.auto_resume_after_ms) return
    try {
      await this.transport.setPause(false)
      this.note('plugin auto-resume: queue drained, the plugin made this pause')
    } catch (error) {
      this.note(`auto-resume failed: ${error?.message ?? error}`)
    }
  }

  // ---------------------------------------------------------------------
  // Panel Remote surface (read-only)
  // ---------------------------------------------------------------------

  panelPayload(sessionId) {
    const live = this.model.payload()
    const agent = sessionId ? this.ctx.agents.get(sessionId) : undefined
    let state = null
    if (agent && this.projection) {
      try {
        state = this.projection.stateOf(agent.session)
      } catch {
        state = null
      }
    }
    const notes = this.noteLines.slice(-6).map((n) => n.message)
    return {
      tick: live.tick, observedAt: live.observedAt, online: live.online,
      paused: live.paused, pauseSource: live.pauseSource, generation: live.generation,
      facts: live.facts, rungs: live.rungs, next: live.next,
      boxes: live.boxes, piles: live.piles, shafts: live.shafts, warnings: live.warnings,
      todos: (state?.todos ?? live.todos).slice(0, 14),
      alerts: (state?.alerts ?? live.alerts).filter((a) => !a.cleared).slice(0, 10),
      digEvents: (state?.digEvents ?? live.digEvents).slice(0, 10),
      pauseEvents: (state?.pauseEvents ?? live.pauseEvents).slice(0, 8),
      crashlog: live.crashlog, notes: [...(live.notes ?? []), ...notes].slice(0, 10),
      overload: live.overload,
      pollIntervalMs: this.currentIntervalMs(),
      filter: {
        enabled: this.filterEnabled(),
        session: this.filterSessionId,
        live: !!this.filterAgent(),
        verdictTick: this.filterVerdict?.tick ?? null,
        significance: this.filterVerdict?.significance ?? null,
        fallback: this.filterFallback,
      },
      config: {
        pause_authority: this.config.pause_authority,
        alert_wakes: this.config.alert_wakes,
        briefing_mode: this.config.briefing_mode,
        briefing_max_tokens: this.config.briefing_max_tokens,
        poll_running_ms: this.config.poll_running_ms,
        poll_paused_ms: this.config.poll_paused_ms,
        stateVersion: STATE_VERSION,
      },
      sessionFolded: !!state,
    }
  }

  panel(sessionId) {
    return this.panelPayload(typeof sessionId === 'string' ? sessionId : undefined)
  }

  ackAlert(id) {
    const alert = this.model.ackAlert(String(id))
    return { ok: !!alert, id, tick: this.model.tick }
  }
}
// The panel reads the projection's `wire.view` (client-visible through the
// session snapshot), and its Ack runs the `/df-ack` session command through the
// shipped `remote.commands` namespace. A third-party bundle CANNOT add its own
// namespace to the web client's `ctx.remote` face: that aggregate is compiled
// from fixed imports (measured: `remote.dfObserver` never resolves, and a deep
// inject path that cannot resolve aborts the whole client module row).
