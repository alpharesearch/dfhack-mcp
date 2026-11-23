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
 *     whitelisted alerts only. Every inbox boundary takes a MESSAGE OBJECT:
 *     the object is written verbatim into `agent/inbox/spliced.inserted`, so a
 *     raw string there makes the stored session unloadable. All plugin output
 *     goes through `DFObserver.observerMessage()` (see `injectMessage`).
 *   - `ctx.systemPrompt.section()` carries the standing reading rules once;
 *   - `ctx.sessionProjections` unit folds everything the model sees from
 *     committed session events (pure synchronous apply, reference-stable view);
 *   - `ctx.tools.register()` adds df_status / df_todo / df_alerts;
 *   - the panel (client half) reads the projection wire: a third-party bundle
 *     CANNOT reach an `@Remote` surface (`remote.dfObserver` never resolves), and
 *     the Context-level lazy inject is the only safe way to touch optional faces.
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
// TypertRemoteService only: no @Remote endpoints are exposed - a third-party
// bundle's client half cannot resolve them (measured).
import { TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import { createUserMessage } from '@deepseek-ai/dsh-llm'

import { ObserverTransport } from './observer.js'
import { FortModel, STATE_VERSION, significantChange, dfObserverProjection } from './model.js'
import { buildBriefing, buildWake, buildCoalescedWake, standingRules, compactDesignations, setTitleBudget, alertLabel } from './brief.js'
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
  /** How many observer messages may sit UNSPENT at one inbox boundary before the
   * plugin stops adding to that boundary. An injection is a durable event:
   * measured 2026-11-21, a filter session took ~400 injections while its driver
   * was not consuming - hundreds of splices in one log, and its last turn closed
   * with `reason: null`, which the harness's subagent projection reads unguarded
   * ("failed to project session"). A boundary that is not draining is a boundary
   * to stop feeding. 0 disables the cap. */
  pending_inject_cap: z.number().int().min(0).default(8),
  /** A box to_dig shift smaller than this does not cost the model a briefing. */
  min_to_dig_delta: z.number().int().min(1).default(3),

  /** Alert kinds that WAKE the agent (player decision 2026-11-21: water, dig_complete, crash). */
  alert_wakes: z.array(z.string()).default(['water', 'dig_complete', 'dig_stalled', 'tile_state_lost', 'game_says', 'game_crashed', 'game_offline', 'save_reloaded']),
  /** How an alert reaches the Agent. `yield-aware` (default) never takes the
   * steering slot the player queued: an alert goes to `steer` only for a RUNNING
   * driver with nothing else pending at that boundary, becomes its own turn
   * (`followup`) for an idle one, and `inject`s when the player's steering is
   * already pending. `steer`/`followup`/`inject` force one channel for everything. */
  alert_delivery: z.enum(['yield-aware', 'steer', 'followup', 'inject']).default('yield-aware'),
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
  /** Tile growth that counts as flooding: the aquifer breach measured 1030 -> 1091. */
  water_growth_min: z.number().int().min(1).default(25),
  /** A designation cluster this big, outside every watched box, gets named: work
   * nobody is measuring. 0 disables the alert. */
  unwatched_min_tiles: z.number().int().min(0).default(20),
  /** Ship briefings with the compact panel sections (boxes/piles/rungs/notes/scan)
   * even when the ledger leaves no room: costs ~400 extra tokens per briefing, and
   * is the only way the right-Sidebar panel sees those sections (measured: the
   * `/df-panel` promise never settles for the client half). */
  panel_full_briefing: z.boolean().default(false),
  /** The panel lists the first `panel_title_rows` OPEN to-dos: how many of them
   * carry a title on the wire, and how many characters each title gets. Titles
   * cost ~5 tokens per row at 24 chars, ~10 at 44 (measured). */
  panel_title_rows: z.number().int().min(0).max(30).default(10),
  panel_title_chars: z.number().int().min(8).max(200).default(44),
  /** Max height (px) of the composer dock's expanded body; 0 = no cap (measured:
   * the Sidebar page fills its pane with no scroll, the dock is the one capped
   * surface - 340 px scrolls a 14-row ledger at 495 px). */
  dock_max_height: z.number().int().min(0).max(4000).default(340),
  /** Show the panel's mute button (pauses THIS PLUGIN's injections - briefings and
   * wakes - never the game; the game pause stays behind `pause_authority`). */
  panel_pause_button: z.boolean().default(true),
  /** A pile this full on a small area is reported as full. */
  pile_full_items: z.number().int().min(10).default(150),

  /** Agent ids to brief/wake (empty = every live root agent). */
  agents: z.array(z.string()).default([]),
  /** Also brief/wake subagent sessions (they inherit the briefing in their log). */
  brief_subagents: z.boolean().default(false),
  /** Require a session to CONTACT the observer (a df_* tool call, a df-* session
   * command, the panel) before it becomes a briefing/wake target. Default true.
   * Measured 2026-11-22 (player report: "the dsh observer plugin sometimes starts
   * some other chats, this looks like a bug"): `targetAgent` excluded only subagents and
   * honoured the `agents` allowlist when NON-empty, so with the default empty allowlist
   * EVERY session in the profile was a wake target - `liveTargets()` even adopts agents
   * created before activation, so any chat opened in this profile gets DF briefings
   * injected and wakes. Session contact is the honest signal of interest; set false to
   * restore the old broadcast. */
  require_tool_contact: z.boolean().default(true),
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
/** Measured: the compact panel fill costs ~400 tokens at the lean levels. */
const PANEL_FILL_ALLOWANCE = 420



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

/**
 * Where an alert may be delivered, decided against THIS Agent's inbox.
 *
 * The contract (dsh-agent runtime-types): `steer` is "steering for the nearest
 * step - an idle driver starts a turn; a running driver consumes it at its next
 * step boundary"; `followup` is "an ordinary follow-up turn ... the sole
 * ordinary message of its own turn"; `inject` is "context for the next pre-step
 * without waking". So an alert that always steers does three things the player
 * feels: it queues the observer's text INTO THE PLAYER'S steering slot, it opens
 * a turn on an idle driver, and stacking several alerts fills the same boundary
 * with several messages. `yield-aware` refuses all three.
 */
export function deliveryPlan(form, agent, { alertDelivery = 'yield-aware' } = {}) {
  if (form !== 'alert') return { channel: 'inject', why: 'state, not a request' }
  const canSteer = typeof agent?.steer === 'function'
  if (alertDelivery === 'steer' && canSteer) return { channel: 'steer', why: 'alert_delivery=steer' }
  if (alertDelivery === 'followup') return { channel: 'followup', why: 'alert_delivery=followup' }
  if (alertDelivery === 'inject') return { channel: 'inject', why: 'alert_delivery=inject' }
  if (!canSteer) return { channel: 'followup', why: 'this Agent has no steer' }
  const pending = [...(agent?.inbox?.nextStep ?? []), ...(agent?.inbox?.nextTurn ?? [])]
  const foreign = pending.filter((m) => m?.source?.kind !== 'df-observer')
  if (foreign.length) {
    return { channel: 'inject',
      why: `${foreign.length} message(s) are already pending at this boundary (the player's own input): yield the slot, deliver as context` }
  }
  if (agent?.status === 'idle') {
    return { channel: 'followup', why: 'idle driver: steering would open the turn - an idle wake is an ordinary turn of its own' }
  }
  return { channel: 'steer', why: 'running driver with a free boundary: consumed at the next step' }
}

export function deliveryOf(form, agent) {
  return deliveryPlan(form, agent).channel
}

export default class DFObserver extends TypertRemoteService {
  static inject = ['tools', 'agents', 'systemPrompt', 'sessionProjections', 'timer', 'commands', 'storageDomain']
  static Config = Config

  constructor(ctx, config = Config.parse({})) {
    super(ctx, 'dfObserver')
    this.config = config
    // Mute state (panel button): suppresses briefings/wakes only. Restored from
    // storage at activation so a reload does not un-mute the player's choice.
    this.quiet = false
    // The counters live on the model (its payload is the wire the panel
    // folds) - keeping a second copy here is how they drifted apart (measured).
    // The panel's word budget is Config-driven (measured: 24-char titles read as
    // clipped nonsense, and title-less rows left the panel listing bare ids).
    setTitleBudget({ rows: config.panel_title_rows, chars: config.panel_title_chars })
    this.model = new FortModel(config)
    this.model.notesSource = () => this.noteLines.map((n) => `plugin: ${n.message}`)
    this.transport = new ObserverTransport({
      base: config.server_url, timeoutMs: 30000,
      offlineGraceMs: config.offline_grace_ms,
      napMinMs: config.nap_min_ms, napMaxMs: config.nap_max_ms,
      announceEveryMs: config.announce_every_ms,
    }, (message) => this.note(message))
    /** Per-agent briefing/wake bookkeeping: {lastTick, lastAt, lastSignal, lastWakeAt}. */
    this.watched = new Set()   // sessions that CONTACTED the observer (see require_tool_contact)
    this.agents = new Map()
    this.disposers = new Map()
    this.projection = null
    this.stopped = false
    this.abort = new AbortController()
    this.lastWakeTick = 0
    this.noteLines = []
    /** Set by a game swap: the next briefing bypasses the spacing gate once. */
    this.resetAnnounce = false
    this.filterSessionId = null
    this.filterVerdict = null
    this.filterWokeAt = null
    this.filterPushTick = 0
    this.filterFallback = false
    this.filterPromptInstalled = false
    this.modelPollAnnounced = 0
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
    // Tool EXECUTION is the opt-in: a session that calls a df_* tool asked for this data.
    // `when` keeps tools visible to eligible sessions (visibility must not require contact
    // first - that would make the opt-in unreachable); delivery requires contact.
    const sessionFilter = (exec) => { this.markWatched(exec.agent, 'tool call'); return this.eligibleAgent(exec.agent) !== null }
    for (const definition of toolDefinitions({
      model: this.model, projection: this.projection, config, sessionFilter,
      onReset: (why) => this.observerReset({ swapped: true, source: 'tool', why: why || 'reset requested via df_todo' }),
    })) {
      ctx.effect(() => ctx.tools.register(definition, {
        when: ({ agent }) => this.eligibleAgent(agent) !== null,
      }), `df-observer: tool ${definition.name}`)
    }

    // ---- per-agent behaviour: registered on agent.ctx, disposed both ways ----
    ctx.on('agent/created', ({ agent }) => {
      if (this.targetAgent(agent) === null) return
      const entry = { lastTick: 0, lastAt: 0, lastSignal: null, lastWakeAt: 0, pendingSteerId: null }
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
        this.markWatched(agent, '/df-ack')
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

    // Mute the observer's own injections (the panel's pause button). The game is
    // NOT touched: pausing the game stays behind `pause_authority` (default off).
    ctx.effect(() => ctx.commands.register({
      name: 'df-quiet',
      description: 'Pause (or resume) the DF observer\'s context injections - briefings and alert wakes. The game keeps running: /df-quiet [on|off]',
      input: { hint: '[on|off] (toggles when omitted)' },
      handler: ({ agent, rawInput }) => {
        this.markWatched(agent, '/df-quiet')
        const arg = String(rawInput ?? '').trim().toLowerCase().split(/\s+/)[0]
        const next = arg === 'on' ? true : arg === 'off' ? false : !this.quiet
        const changed = next !== this.quiet
        this.setQuiet(next, changed ? 'command' : 'command (no change)')
        void agent
        return { kind: 'success', text: `df-quiet: injections ${next ? 'PAUSED' : 'running'}`
          + ` (${this.model.suppressed.briefings} briefings and ${this.model.suppressed.wakes} wakes suppressed so far)`
          + (changed ? '' : ' - no change') }
      },
    }), 'df-observer: /df-quiet command')

    // The panel's own data pull. The briefing wire is ledger-first (measured: at
    // the Config cap of 700 there is no room for boxes/piles/rungs on top of a
    // 30-row ledger), and a third-party client cannot reach an @Remote surface -
    // so the full read goes through the SAME session-command path the Ack uses.
    ctx.effect(() => ctx.commands.register({
      name: 'df-reset',
      description: 'Throw out everything the DF observer derived from the current game (to-dos, watched boxes, baselines, alerts, facts - archived, not deleted) and re-read the fort in full. Use it after loading a different save. /df-reset [why]',
      input: { hint: '[reason, optional]' },
      handler: async ({ agent, rawInput }) => {
        this.markWatched(agent, '/df-reset')
        const why = String(rawInput ?? '').trim() || 'reset requested by the player'
        const out = await this.observerReset({ swapped: true, source: 'command', why })
        void agent
        const d = out.dropped ?? {}
        return { kind: 'success', text: `df-reset: observer state thrown out (${d.todos} to-dos archived, `
          + `${d.boxes} watched boxes, ${d.piles} pile reads, ${d.alerts} alerts) - the next tick re-reads the fort from the game` }
      },
    }), 'df-observer: /df-reset command')

    ctx.effect(() => ctx.commands.register({
      name: 'df-panel',
      description: 'Read the observer\'s full current panel payload (ladder, boxes, piles, shafts, ledger, alerts, notes, designation scan) as JSON.',
      input: { hint: '(no arguments)' },
      handler: ({ agent }) => {
        this.markWatched(agent, '/df-panel')
        const sessionId = agent?.session ?? agent?.id
        const payload = this.panelPayload(typeof sessionId === 'string' ? sessionId : undefined)
        return { kind: 'success', text: JSON.stringify(payload) }
      },
    }), 'df-observer: /df-panel command')

    // ---- explicit watch control: who may be briefed ----
    ctx.effect(() => ctx.commands.register({
      name: 'df-watch',
      description: 'Which sessions the DF observer briefs. /df-watch list | on (this session) | off (this session). Sessions become targets by CONTACTING the observer (a df_* tool call, a df-* command, the panel) - before that they are never woken, so unrelated chats stay clean (player-measured bug, 2026-11-22).',
      input: { hint: '[list|on|off] (default: list)' },
      handler: ({ agent, rawInput }) => {
        const arg = String(rawInput ?? '').trim().toLowerCase().split(/\s+/)[0] || 'list'
        const id = agent ? String(agent.id) : null
        if (arg === 'on') {
          if (!this.markWatched(agent, '/df-watch on'))
            return { kind: 'error', text: 'df-watch: this session is not an eligible target (allowlist/subagent rule)' }
          return { kind: 'success', text: `df-watch: session ${id} is now a briefing target` }
        }
        if (arg === 'off') {
          this.watched.delete(id)
          this.agents.delete(id)
          const dispose = this.disposers.get(id)
          this.disposers.delete(id)
          dispose?.()
          return { kind: 'success', text: `df-watch: session ${id} is NO LONGER a briefing target (it re-subscribes by calling a df_* tool)` }
        }
        return { kind: 'success', text: `df-watch: require_tool_contact=${this.config.require_tool_contact}; watched=[${[...this.watched].join(', ') || 'none'}]; briefing targets now=${this.liveTargets().length}` }
      },
    }), 'df-observer: /df-watch command')

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

  /** Who the observer MAY talk to: config allowlist + the subagent rule. */
  eligibleAgent(agent) {
    if (!agent) return null
    const origin = agent.session?.header?.origin
    if (origin === 'subagent' && !this.config.brief_subagents) return null
    if (this.config.agents.length && !this.config.agents.includes(String(agent.id))) return null
    return agent
  }

  /** Mark a session as interested (it used a df_* tool, a df-* command, or the panel). */
  markWatched(agent, why = 'contact') {
    const a = this.eligibleAgent(agent)
    if (!a) return false
    const id = String(a.id)
    if (!this.watched.has(id)) this.log?.('info', `df-observer: session ${id} is now a briefing target (${why})`)
    this.watched.add(id)
    return true
  }

  /** Who the observer DOES talk to (briefings, wakes, panel data, tools' `when`). */
  targetAgent(agent) {
    const a = this.eligibleAgent(agent)
    if (!a) return null
    // THE FIX (player-measured 2026-11-22): with an empty allowlist the old code returned
    // every session in the profile, so unrelated chats got DF wakes. Contact is required
    // unless the operator explicitly asks for the broadcast.
    if (this.config.require_tool_contact && !this.watched.has(String(a.id))) return null
    return a
  }

  liveTargets() {
    const out = []
    for (const agent of this.ctx.agents.list()) {
      if (this.targetAgent(agent) === null) continue
      // Agents created before this plugin activated never emitted `agent/created`
      // for us: adopt them lazily so a live session is not briefing-blind.
      if (!this.agents.has(agent.id)) {
        this.agents.set(agent.id, { lastTick: 0, lastAt: 0, lastSignal: null, lastWakeAt: 0, pendingSteerId: null })
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
    // The ladder's NEXT box joins the survey list: a suggestion whose tiles are
    // never read is advice the agent cannot verify (measured: "unproven").
    const nextBox = this.model?.nextBox ?? null
    const boxes = [...cfg.monitored_boxes, ...(this.model?.extraBoxes ?? []), ...(nextBox ? [nextBox] : [])]
      .filter((b, i, arr) => arr.findIndex((o) => o[0] === b[0] && o[1] === b[1] && o[2] === b[2] && o[3] === b[3] && o[4] === b[4]) === i)
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
      // Where the game's designations physically are (deep ticks only - the scan
      // is block-granularity server-side). Absent on a server without it: the
      // plugin labels the gap instead of guessing.
      request.designations = 12
      request.rungs = true
      // The status probe (picks/site/date/seeds) costs a Lua round-trip: deep ticks only.
      request.status_probe = true
      // Pile ids move with every save, so an empty config means ASK THE GAME: the
      // server resolves "auto" through its own stockpile scan. A hand-typed list from a
      // previous fort reads "pile id N not found" forever and every pile acceptance test
      // is unrunnable - exactly what happened after the save swaps on this fort.
      request.pile_ids = (cfg.pile_ids && cfg.pile_ids.length) ? cfg.pile_ids : 'auto'
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
    // A different game is loaded -> THE RESET ROUTINE, before any rule compares the
    // new numbers against the old fort's baselines. Order matters: after a save swap
    // every box/pile/fact read is a FIRST read, so the dig rules treat it as a
    // baseline, not as a transition - which is precisely what the tile_state_lost,
    // dig_stalled and todo_reopened storm lacked (measured 2026-11-21).
    const swap = this.model.gameChange()
    if (swap.swapped) {
      await this.observerReset(swap)
      // A reset must be ANNOUNCED, not folded into the next routine briefing at the
      // judge's convenience: the agent has to know the numbers just changed source.
      this.resetAnnounce = true
    }
    this.persistSignature()
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

  /** Flip the mute and remember it (storage + notes + the panel wire). */
  /** One message with everything the mute swallowed: the changes, then a full
   * snapshot briefing. Nothing is replayed as N separate injections. */
  deliverCatchUp(why = 'panel') {
    const from = Number.isFinite(this.mutedFromTick) ? this.mutedFromTick : 0
    const p = this.model.payload()
    const facts0 = this.mutedFacts ?? {}
    const lines = [`DF-OBSERVER CATCH-UP (injections were PAUSED from tick ${from}, by ${why}):`,
      `  ${this.model.suppressed.briefings} briefings and ${this.model.suppressed.wakes} wakes were suppressed over ${Math.max(0, p.tick - from)} ticks.`]
    const alerts = (this.model.alertHistory ?? []).filter((a) => (a.tick ?? 0) >= from)
    const byId = new Map(alerts.map((a) => [a.id, a]))
    if (byId.size) {
      lines.push('  ALERTS RAISED WHILE PAUSED:')
      for (const a of [...byId.values()].slice(-8)) lines.push(`    !${alertLabel(a)} ${a.kind}${a.coord ? ` [${a.coord}]` : ''}: ${String(a.evidence ?? '').slice(0, 110)}`)
    }
    const digs = (this.model.digEvents ?? []).filter((e) => (e.tick ?? 0) >= from)
    if (digs.length) {
      lines.push('  DIG / LEDGER CHANGES:')
      for (const e of digs.slice(-8)) lines.push(`    @${e.tick} ${e.kind}${e.todo ? ` #${e.todo}` : ''}${e.title ? ` ${String(e.title).slice(0, 40)}` : ''}${e.evidence ? ` - ${String(e.evidence).slice(0, 70)}` : ''}`)
    }
    const pauses = (this.model.pauseEvents ?? []).filter((e) => (e.tick ?? 0) >= from)
    if (pauses.length) lines.push(`  PAUSE: ${pauses.map((e) => `${e.paused ? 'paused' : 'resumed'} at ${e.tick} (${e.source ?? '?'})`).join('; ')}`)
    const delta = (k, label) => {
      const a = facts0?.[k], b = p.facts?.[k]
      if (a == null || b == null || a === b) return null
      return `${label} ${a}->${b}`
    }
    const deltas = ['food', 'drink', 'alcohol', 'meals', 'raw_food', 'seeds', 'wood', 'stone', 'barrels', 'miners', 'dwarves', 'pending_dig', 'water_tiles']
      .map((k) => delta(k, k)).filter(Boolean)
    lines.push(`  FACTS: ${deltas.length ? deltas.join(', ') : 'no fact moved while paused'}` +
      `${p.facts?.date ? ` | date Y${p.facts.date.year} M${p.facts.date.month} d${p.facts.date.day}` : ''}`)
    const brief = buildBriefing(p, { maxTokens: this.config.briefing_max_tokens + PANEL_FILL_ALLOWANCE })
    const text = `${lines.join('\n')}\n\n${brief.text}`
    for (const agent of this.liveTargets()) {
      this.injectMessage(agent, { text }, 'catch-up')
    }
    this.note(`catch-up delivered: ${lines.length - 2} summary lines + a ${brief.tokens}-token snapshot (budget ${this.config.briefing_max_tokens + PANEL_FILL_ALLOWANCE}${brief.overBudget ? ', LABELLED overBudget' : ''})`)
    this.mutedFromTick = null
    this.mutedFacts = null
    this.model.suppressed = { briefings: 0, wakes: 0 }
    return { injected: true, lines: lines.length, tokens: brief.tokens }
  }

  setQuiet(on, why = 'panel', announce = true) {
    const next = Boolean(on)
    const changed = next !== this.quiet
    this.quiet = next
    this.model.setQuiet(next, this.model.suppressed)
    if (changed) this.note(next
      ? `injections PAUSED (${why}): briefings and wakes are suppressed; the game is untouched`
      : `injections resumed (${why}): ${this.model.suppressed.briefings} briefings and ${this.model.suppressed.wakes} wakes were suppressed while paused`)
    try {
      // Measured: a `meta` table never reached disk (writes are queued, and a
      // remove/install within seconds dropped them), so the mute died with the
      // instance. The ledger table is the proven-durable path - `todos/<id>.json`
      // files exist after reloads - and restoreTodos skips non-numeric ids, so a
      // `quiet` key there is storage, not a to-do.
      void this.ledger?.table?.('todos')?.put?.('quiet', { kind: 'quiet', quiet: next, why, at: Date.now() })
      this.ledger?.table?.('meta')?.put?.('quiet', { quiet: next, why, at: Date.now() })
    } catch (error) {
      // Durability must announce itself: a mute that cannot be stored is a mute a
      // reload will silently undo.
      this.note(`mute state NOT stored (this session only): ${error?.message ?? error}`)
    }
    // The panel folds the SESSION LOG, and muting stops the very rows it reads - so
    // the toggle itself must land as one compact folded row, otherwise the page
    // freezes at the last briefing and the button never flips. (Measured: after a
    // click the transcript said PAUSED while the panel still read "pause briefings".)
    if (changed) {
      if (next) {
        // Remember where the mute began: the resume must report EVERYTHING that
        // was suppressed (the player's request: "after a start it should send
        // everything at once").
        this.mutedFromTick = this.model.tick
        this.mutedFacts = { ...(this.model.payload().facts ?? {}) }
      } else {
        this.deliverCatchUp(why)
      }
    }
    if (changed && announce) {
      const p = this.model.payload()
      const line = `DF-OBSERVER injections ${next ? 'PAUSED' : 'RESUMED'} (${why}): `
        + `${p.suppressed.briefings} briefings and ${p.suppressed.wakes} wakes suppressed so far. The game is untouched; the panel keeps its data.`
        + `\n[df-observer] ${JSON.stringify({ form: 'briefing', tick: p.tick,
          data: { tick: p.tick, online: p.online, paused: p.paused, poll: p.poll, generation: p.generation,
            pauseSource: p.pauseSource, pauseAuthority: p.pauseAuthority, quiet: next,
            suppressed: { ...p.suppressed }, facts: p.facts, todos: (p.todos ?? []).slice(0, 12),
            alerts: (p.alerts ?? []).slice(0, 4).map((a) => ({ id: a.id, kind: a.kind, coord: a.coord, acked: !!a.acked })) } })}`
      for (const agent of this.liveTargets()) { this.injectMessage(agent, { text: line }, 'briefing') }
    }
    return { quiet: next, changed, suppressed: { ...this.model.suppressed } }
  }

  /** The identity of the loaded save, as one storable key (game = the only truth). */
  signatureKey() {
    const g = this.model?.game
    if (!g) return null
    return [g.save, g.world, g.mode, g.tiles].join('|')
  }

  /** Remember which game the ledger belongs to, so a swap while we were DOWN is
   * still detected: at activation we have no previous identity to compare with. */
  persistSignature() {
    const key = this.signatureKey()
    if (!key || !this.ledger?.table?.('todos')) return
    const sig = { key, ...(this.model.game ?? {}), at: Date.now(), tick: this.model.tick }
    if (this.lastPersistedSignature === key) return
    this.lastPersistedSignature = key
    void this.ledger.table('todos').put('game', sig)
      .catch((e) => this.note(`signature write failed: ${e?.message ?? e}`))
  }

  /**
   * THE OBSERVER RESET ROUTINE (player request 2026-11-21: "when a different game is
   * detected, even by just reloading a different save, throw everything out and refetch
   * everything once again").
   *
   * Model side: every baseline, tally, event, alert, fact and to-do is dropped
   * (model.resetForNewGame) and the deep read is forced on the next cycle.
   * Ledger side: the dropped to-dos are ARCHIVED under non-numeric keys (restoreTodos
   * skips non-numeric ids - measured as the proven-durable path), then the numeric
   * records and the watched-box records are deleted, so a restart of the plugin cannot
   * restore the previous fort's plan into this one.
   */
  async observerReset(swap) {
    const dropped = this.model.resetForNewGame(swap)
    let archived = 0
    const table = this.ledger?.table?.('todos')
    if (table) {
      const keys = [...table.entries()].map(([k]) => k).filter((k) => /^\d+$/.test(String(k)))
      const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')
      for (const k of keys) {
        const rec = table.get(k)
        if (!rec) continue
        void table.put(`arch-${stamp}-${k}`, JSON.parse(JSON.stringify({
          ...rec, archivedBy: swap.why, archivedAt: Date.now(), archivedSwap: this.model.gameSwaps,
        }))).catch((e) => this.note(`ledger archive failed: ${e?.message ?? e}`))
        archived += 1
      }
      // Delete the live records: an archive that is still readable as a to-do is
      // the reopen storm again.
      try {
        if (typeof table.deleteMany === 'function') await table.deleteMany(keys)
        else for (const k of keys) await table.delete(k)
      } catch (error) {
        this.note(`ledger wipe failed (the archive holds ${archived} rows; in-memory state is clean): ${error?.message ?? error}`)
      }
      const boxTable = this.ledger?.table?.('boxes')
      if (boxTable) {
        const boxKeys = [...boxTable.entries()].map(([k]) => k)
        try {
          if (typeof boxTable.deleteMany === 'function') await boxTable.deleteMany(boxKeys)
          else for (const k of boxKeys) await boxTable.delete(k)
        } catch (error) {
          this.note(`watched-box wipe failed: ${error?.message ?? error}`)
        }
      }
      this.lastPersistedSignature = null   // force a signature re-write after the wipe
    }
    this.note(`observer reset (${swap.source}): ${swap.why} - dropped ${dropped.todos} to-dos `
      + `(archived ${archived}), ${dropped.boxes} watched boxes, ${dropped.piles} pile reads, `
      + `${dropped.alerts} alerts; re-seeded from the ladder, re-reading the fort in full`)
    return { ...swap, dropped, archived }
  }

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
      const meta = domain.table('todos').get('quiet') ?? domain.table('meta').get('quiet')
      if (meta?.quiet) {
        // Restoring a mute must ANNOUNCE (one folded row): the panel reads the
        // session log, and muting suppresses the rows it would otherwise fold -
        // measured: after a reload the page read "pause briefings" at a frozen tick
        // while the plugin was in fact muted.
        this.setQuiet(true, 'storage', true)
      }
      // Seed the swap detector with the game THESE records belong to. Without this,
      // a save swap that happened while the observer was down is invisible on the
      // first poll (there is no previous identity to compare against), and the
      // previous fort's plan would be restored into a fort that never had it.
      const storedGame = domain.table('todos').get('game')
      if (storedGame?.key) {
        this.model.lastGame = storedGame
        this.lastPersistedSignature = storedGame.key
      }
      this.note(`ledger restored: ${stored.length} to-dos, ${boxes.length} watched boxes`
        + (storedGame?.key ? ` (plan of game ${storedGame.key}${storedGame.year ? ` Y${storedGame.year}` : ''})`
          : (stored.length
            // The swap detector has nothing to compare against for a ledger written
            // before the game signature was stored: say so, do not silently trust it.
            ? ' (NO GAME STAMP: written before signatures were stored - if the loaded save is not the fort these to-dos describe, run /df-reset)'
            : '')))
    } catch (error) {
      this.note(`ledger load failed (starting fresh, labelled): ${error?.message ?? error}`)
    }
    // Every ledger mutation lands durably (writes are queued by the domain), and
    // carries the game it was written for: a to-do is a claim about ONE fort.
    this.model.onLedgerWrite = (todo) => {
      const game = this.signatureKey()
      void domain.table('todos').put(String(todo.id), JSON.parse(JSON.stringify(game ? { ...todo, game } : todo)))
        .catch((e) => this.note(`ledger write failed: ${e?.message ?? e}`))
    }
    this.model.onBoxWatch = (box) => {
      // Storage keys must match /^[a-zA-Z0-9_-]+$/ (measured: the ':'/',' key was
      // rejected, so watched boxes were never persisted - "0 watched boxes").
      const key = `box-${box[4]}-${box[0]}-${box[1]}-${box[2]}-${box[3]}`
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
    // The panel's mute button (player request): pause THIS plugin's context
    // injections - briefings and wakes - without touching the game. Counted, so the
    // mute is visible, never a silent hole in the log.
    if (this.quiet) { this.model.suppressed.briefings += 1; return }
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
      if (this.resetAnnounce) changed.push(`observer reset (${this.model.gameSwaps})`)
      const spaced = this.resetAnnounce || now - entry.lastAt >= this.config.inject_min_spacing_ms
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
        brief = buildBriefing(payload, {
          // The panel's sections ride the wire only if the budget pays for them, so
          // the Config knob widens the budget when the player asks for the full panel.
          maxTokens: this.config.briefing_max_tokens + (this.config.panel_full_briefing ? PANEL_FILL_ALLOWANCE : 0),
        })
        if (timedOut && !this.filterFallback) {
          this.filterFallback = true
          this.model.notes = ['filter silent past Config.filter_timeout_ms: rule-gate briefing resumed',
            ...this.model.notes].slice(0, 12)
        }
      }
      this.injectMessage(agent, brief, 'briefing')
      this.resetAnnounce = false
      entry.lastSignal = payload
      entry.lastAt = now
      entry.lastTick = payload.tick
      this.model.briefings += 1
    }
  }

  /** Filter-proposed wakes are plugin-approved: kind must be in Config.alert_wakes. */
  async dispatchFilterWakes() {
    if (this.quiet) return
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
    if (this.quiet) { this.model.suppressed.wakes += alerts.length; return }
    const payload = this.model.payload()
    const now = Date.now()
    const candidates = alerts.filter((a) =>
      this.config.wake_enabled && this.config.alert_wakes.includes(a.kind) && !a.wakeSent)
    if (!candidates.length) return
    // ONE wake per agent per boundary: N alerts on one tick are one message, not N
    // steering messages competing for the player's step boundary.
    for (const agent of this.liveTargets()) {
      const entry = this.agents.get(agent.id)
      if (!entry) continue
      if (now - entry.lastWakeAt < this.config.wake_min_spacing_ms) continue
      const wake = buildCoalescedWake(candidates, payload, { maxTokens: this.config.briefing_max_tokens })
      if (!wake?.text) continue
      const delivered = this.injectMessage(agent, wake, 'alert')
      for (const a of candidates) a.wakeSent = true
      entry.lastWakeAt = now
      if (delivered?.channel === 'inject') {
        // A wake demoted to context is a wake that did not wake: count it, never hide it.
        const s = (this.model.suppressed ??= { briefings: 0, wakes: 0 })
        s.steers = (s.steers ?? 0) + 1
        this.note(`wake delivered as context (not steering): ${delivered.why}`)
      }
    }
  }

  /**
   * The ONLY producer of model-facing messages in this plugin.
   *
   * `agent.inject/steer/followup` take a message OBJECT, and the object is
   * written verbatim into the durable log as `agent/inbox/spliced.inserted`.
   * A raw string therefore corrupts the session beyond repair: the loader
   * rejects it (`agent/inbox/spliced requires message objects`) and the whole
   * history becomes unloadable. Measured 2026-11-21 on a play session: two
   * announce paths passed a string and the session died. So nothing reaches an
   * inbox boundary except through here, and here refuses to pass a non-message.
   */
  observerMessage(text, form, summary) {
    const body = typeof text === 'string' ? text : String(text ?? '')
    const message = createUserMessage({
      source: {
        kind: 'df-observer',
        form,
        summary: summary ?? (form === 'alert' ? 'DF observer alert'
          : form === 'catch-up' ? 'DF observer catch-up'
            : 'DF observer briefing'),
      },
      content: [{ type: 'text', text: body }],
    })
    if (message == null || typeof message !== 'object' || Array.isArray(message)
      || message.role !== 'user' || !Array.isArray(message.content)
      || typeof message.id !== 'string' || message.id.length === 0) {
      throw new Error('df-observer: refusing to deliver a non-message payload')
    }
    return message
  }

  /** Our own messages still unspent at one inbox boundary. */
  pendingOurs(agent, target) {
    const list = target === 'next-turn' ? (agent?.inbox?.nextTurn ?? []) : (agent?.inbox?.nextStep ?? [])
    return [...list].filter((m) => m?.source?.kind === 'df-observer').length
  }

  /** The boundary an inject channel writes to. */
  static targetOf(channel) {
    return channel === 'followup' ? 'next-turn' : 'next-step'
  }

  injectMessage(agent, { text }, form) {
    const plan = deliveryPlan(form, agent, { alertDelivery: this.config.alert_delivery })
    try {
      // Alerts must WAKE the agent; briefings are state and park in the inbox
      // (practices.md). Which channel an alert may use is decided per Agent
      // against its live inbox - see `deliveryPlan` for the boundary contract.
      const cap = this.config.pending_inject_cap
      if (cap > 0 && plan.channel !== 'steer') {
        const target = DFObserver.targetOf(plan.channel)
        const pending = this.pendingOurs(agent, target)
        if (pending >= cap) {
          // Honest bookkeeping: a dropped injection is counted, never hidden.
          const s = (this.model.suppressed ??= { briefings: 0, wakes: 0 })
          s.pending = (s.pending ?? 0) + 1
          this.note(`injection skipped for ${agent.id}: ${pending} observer messages already unspent at ${target} (cap ${cap}) - the driver is not consuming`)
          return { channel: 'skipped', why: `${pending} unspent at ${target} (cap ${cap})` }
        }
      }
      const message = this.observerMessage(text, form)
      const entry = this.agents.get(agent.id)
      if (plan.channel === 'steer') {
        // Never STACK: if our own observer steer is still pending at this
        // boundary, replace it with the newer state instead of adding a second
        // steering message the player has to sit through.
        const pendingId = entry?.pendingSteerId
        const pendingList = agent.inbox?.nextStep ?? []
        if (pendingId && [...pendingList].some((m) => m?.id === pendingId) && typeof agent.inbox?.replace === 'function') {
          if (agent.inbox.replace(pendingId, message)) {
            // Same slot, newer state: the id stays pending, the text is the fresh one.
            return { channel: 'replace', why: 'replaced the pending observer steer at this boundary' }
          }
        }
        agent.steer(message)
        if (entry) entry.pendingSteerId = message.id
        return { channel: 'steer', why: plan.why }
      }
      if (plan.channel === 'followup') {
        agent.followup(message)
        if (entry) entry.pendingSteerId = null
        return { channel: 'followup', why: plan.why }
      }
      agent.inject(message)
      return { channel: 'inject', why: plan.why }
    } catch (error) {
      this.note(`inject failed for ${agent.id}: ${error?.message ?? error}`)
      return { channel: 'failed', why: String(error?.message ?? error) }
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
      // `cancel` takes an AgentCancelCause OBJECT ({kind:'user'|'parent'|'hook'|
      // 'disposed'}); a bare string is not one, and the loop's cause reader
      // switches on `cause.kind`. Measured: our string cause left the filter's
      // last turn closing as `reason: null`, which the harness's subagent
      // projection reads unguarded -> "failed to project session".
      if (agent) { try { agent.cancel({ kind: 'disposed' }) } catch { /* already gone */ } }
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
      return this.observerMessage(
        `Observation tick ${tick}.${gate} No full payload this tick - decide from your log history and df_observe_state if you need it. ${instruction}`,
        'filter-tick', `DF observer filter tick ${tick}`)
    }
    let body = JSON.stringify(payload)
    const cap = this.config.filter_state_max_tokens * 4
    if (body.length > cap) body = `${body.slice(0, cap)}…(truncated: labelled, never a fact)`
    return this.observerMessage(
      `Observation tick ${tick}.${gate} Full state payload follows; compare with your earlier ticks and rank what changed. ${instruction}\n${STATE_PREFIX}${body}`,
      'filter-tick', `DF observer filter tick ${tick} (full)`)
  }

  wakeFilter(gateNotes, isDeep) {
    const agent = this.filterAgent() ?? null
    void (async () => {
      const live = agent ?? await this.ensureFilterAgent()
      if (!live) return
      // The measured runaway: followups to a filter driver that is not consuming
      // piled ~400 durable injections into its log. Its verdict is already
      // missing, so the rule gate is the live path - say so once, do not stack.
      const cap = this.config.pending_inject_cap
      if (cap > 0) {
        const pending = this.pendingOurs(live, 'next-turn') + this.pendingOurs(live, 'next-step')
        if (pending >= cap) {
          this.note(`filter wake paused: ${pending} observer messages unspent in its inbox (cap ${cap}); the rule gate is answering`)
          this.filterFallback = true
          void isDeep
          return
        }
      }
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
  /** Everything the panel renders, from the live model (served by /df-panel). */
  panelPayload(sessionId) {
    const live = this.model.payload()
    let state = null
    if (sessionId && this.projection) {
      try {
        state = this.projection.stateOf(this.ctx.agents.get(sessionId)?.session ?? sessionId)
      } catch {
        state = null
      }
    }
    return {
      tick: live.tick, observedAt: live.observedAt, online: live.online,
      paused: live.paused, pauseSource: live.pauseSource, generation: live.generation,
      poll: live.poll, pauseAuthority: live.pauseAuthority, filter: live.filter,
      facts: live.facts, rungs: live.rungs, next: live.next, designations: live.designations,
      boxes: live.boxes, piles: live.piles, shafts: live.shafts, warnings: live.warnings,
      todos: (state?.todos ?? live.todos).slice(0, 14),
      alerts: (state?.alerts ?? live.alerts).filter((a) => !a.cleared).slice(0, 10),
      digEvents: (state?.digEvents ?? live.digEvents).slice(0, 10),
      pauseEvents: (state?.pauseEvents ?? live.pauseEvents).slice(0, 8),
      reports: live.reports, crashlog: live.crashlog,
      notes: [...(live.notes ?? []), ...this.noteLines.slice(-4).map((n) => n.message)].slice(0, 8),
      overload: live.overload, sessionFolded: !!state,
    }
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
