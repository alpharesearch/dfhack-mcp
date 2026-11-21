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
import { STANDING_RULES, buildBriefing, buildWake } from './brief.js'
import { toolDefinitions } from './tools.js'

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
  alert_wakes: z.array(z.string()).default(['water', 'dig_complete', 'game_crashed', 'game_offline']),
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
  overload_todos: z.number().int().min(1).default(12),
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
  /** Panel refresh cadence the client half should use. */
  panel_poll_ms: z.number().int().min(2000).default(5000),
}).prefault({})


const BRIEF_SECTION_ORDER = 55

/**
 * Mark one prototype method as a direct Remote endpoint without decorator
 * syntax: applies the protocol's `Remote(options)` decorator with a
 * ClassMethodDecoratorContext whose initializer runs on the instance being
 * constructed. Requires: called from inside the constructor (`this` = instance).
 */
function markDirectRemote(instance, method, options) {
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
  initializer.call(instance)
}

/**
 * The plugin service: owns the poller, the live model, the session projection,
 * the per-agent briefing/wake state, the tools, and the panel Remote methods.
 */
export default class DFObserver extends TypertRemoteService {
  static inject = ['tools', 'agents', 'systemPrompt', 'sessionProjections', 'timer']
  static Config = Config

  constructor(ctx, config = Config.parse({})) {
    super(ctx, 'dfObserver')
    this.config = config
    this.model = new FortModel(config)
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
    // Remote endpoints, marked imperatively (Node runs this module without a
    // decorator transform; the marker path below is exactly the @Remote form).
    markDirectRemote(this, 'panel', { name: 'panel' })
    markDirectRemote(this, 'ackAlert', { name: 'ackAlert' })
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
      text: STANDING_RULES,
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
    const request = {
      boxes: cfg.monitored_boxes,
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
    this.model.ingestFast(data)
    if (isDeep) this.model.ingestDeep(data)
    const raised = this.model.evaluate({})
    await this.dispatchAlerts(raised)
    await this.dispatchBriefing()
    await this.maybePauseFor(raised)
    await this.maybeAutoResume()
  }

  // ---------------------------------------------------------------------
  // Briefing (inject, delta-gated, bounded) and wakes (followup, whitelisted)
  // ---------------------------------------------------------------------

  async dispatchBriefing() {
    if (this.config.briefing_mode === 'off') return
    const payload = this.model.payload()
    const now = Date.now()
    for (const agent of this.liveTargets()) {
      const entry = this.agents.get(agent.id)
      if (!entry) continue
      const changed = this.config.briefing_mode === 'always'
        ? ['mode-always']
        : significantChange(entry.lastSignal, payload, { min_to_dig_delta: this.config.min_to_dig_delta })
      const spaced = now - entry.lastAt >= this.config.inject_min_spacing_ms
      const overdue = now - entry.lastAt >= this.config.inject_max_spacing_ms
      if (!changed.length && !overdue) continue
      if (!spaced && !overdue) continue
      const brief = buildBriefing(payload, { maxTokens: this.config.briefing_max_tokens })
      this.injectMessage(agent, brief, 'briefing')
      entry.lastSignal = payload
      entry.lastAt = now
      entry.lastTick = payload.tick
      this.model.briefings += 1
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
    try {
      agent.inject(createUserMessage({
        source: { kind: 'df-observer', form, summary: form === 'alert' ? 'DF observer alert' : 'DF observer briefing' },
        content: [{ type: 'text', text }],
      }))
    } catch (error) {
      this.note(`inject failed for ${agent.id}: ${error?.message ?? error}`)
    }
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
      config: {
        pause_authority: this.config.pause_authority,
        alert_wakes: this.config.alert_wakes,
        briefing_mode: this.config.briefing_mode,
        briefing_max_tokens: this.config.briefing_max_tokens,
        poll_running_ms: this.config.poll_running_ms,
        poll_paused_ms: this.config.poll_paused_ms,
        panel_poll_ms: this.config.panel_poll_ms,
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
