/**
 * df-observer - the filter subagent (opt-in, Config-gated).
 *
 * The briefing budget (`briefing_max_tokens`) is a STARTING POINT, not a
 * verdict: the rule-gate in model.js decides significance from numeric deltas,
 * which cannot notice "two tunnels stalled and the pick supply is the reason"
 * without spending the play agent's context on the raw state.
 *
 * So the observer can own a dedicated filter agent: a persistent session the
 * plugin wakes with the FULL observation payload (its own context absorbs the
 * cost, not the play sessions'), and which answers with one compact verdict
 * line. The plugin then composes the play-session briefing from the verdict -
 * short by construction - and enforces every boundary the filter must not
 * cross (alert wake whitelist, read-only, evidence rules). The filter proposes;
 * the plugin decides.
 *
 * Verdict contract (one JSON line, `[df-verdict] {..}`):
 *   { tick, significance: "high"|"low"|"noise", headline, changes[], next,
 *     wake: [{kind, evidence}], notes[] }
 * - `wake[].kind` is matched against Config.alert_wakes by the plugin; a kind
 *   outside the whitelist is demoted to briefing text, never to a wake.
 * - Evidence must quote tile categories/coordinates; NO DATA is never a fact.
 *
 * The filter's context is deliberately generous (>262k on current routes): the
 * plugin PUSHES the full observation payload into the filter's inbox every
 * `filter_full_every` ticks, and a short gate line in between - the filter's
 * own session log is its tick history, so it can notice trends (a stall
 * deepening, water creeping) that a stateless per-tick glance cannot. It never
 * pays for that history from a play session's context.
 *
 * Failure path: no verdict within `filter_timeout_ms` -> the plugin falls back
 * to the rule-gated direct briefing, with the fallback recorded in its notes.
 * The filter agent is disposed with the plugin fiber.
 *
 * @module @local/df-observer/filter
 */

export const VERDICT_PREFIX = '[df-verdict] '
export const STATE_PREFIX = '[df-state] '

export const FILTER_SYSTEM_PROMPT = [
  'You are the DF-OBSERVER FILTER: a read-only analyst of one Dwarf Fortress game, watched through the df-observer plugin.',
  'You receive observation ticks. Call the `df_observe_state` tool to read the full state payload, then answer with EXACTLY ONE line:',
  '  [df-verdict] {"tick":N,"significance":"high|low|noise","headline":"...","changes":["..."],"next":{"id","title","keys","at"},"wake":[{"kind","evidence"}],"notes":["..."]}',
  'Rules you enforce:',
  '- Progress facts are POSITIVE tile state (to_dig = rock+hidden, dug = floor+stairs+ramp at a named box/elevation); completion requires to_dig==0 AND dug>0; absence, painted glyphs, truncation, designation counts are never facts.',
  '- NO DATA / unknown fields are labelled as such and never treated as facts.',
  '- Elevations are IN-GAME panel numbers; every number names its box/column.',
  '- significance=high only when the play agent should act now (water entering a monitored shaft, a dig completed, designations drained with no dug change, crew/pick exhaustion, crash/offline).',
  '- wake[] entries carry the alert kind (water, dig_complete, dig_stalled, crew, game_offline, game_crashed, damp_hot, pile_full, save_reloaded, todo_reopened, overload) and the evidence sentence with its numbers.',
  '- The headline is <= 25 words; changes <= 6 bullets of <= 15 words each; carry the evidence numbers, not adjectives.',
  '- The payload `reports` holds the game\'s OWN announcements and hints ("cancels Eat: Item inaccessible", caravan arrivals, "Out of food!"). Quote them verbatim in `changes` when they matter and attribute them to the game; a hint about supplies outranks your derived math.',
  '- You are READ-ONLY: never propose designations, builds, labours, commands, unpausing; you only report and rank.',
  '- If nothing meaningful changed, answer significance=noise with a one-line headline; do not pad.',
].join('\n')

/** Parse the verdict line out of an assistant message's content. */
export function parseVerdict(content) {
  const texts = []
  const walk = (value, depth = 0) => {
    if (depth > 4 || value == null) return
    if (typeof value === 'string') { texts.push(value); return }
    if (Array.isArray(value)) { for (const item of value) walk(item, depth + 1); return }
    if (typeof value === 'object') {
      for (const key of ['text', 'output', 'content', 'value']) {
        if (value[key] !== undefined) walk(value[key], depth + 1)
      }
    }
  }
  walk(content)
  for (const text of texts) {
    const line = text.split('\n').find((l) => l.startsWith(VERDICT_PREFIX))
    if (!line) continue
    try {
      const v = JSON.parse(line.slice(VERDICT_PREFIX.length))
      if (v && typeof v === 'object' && typeof v.tick === 'number') return v
    } catch {
      /* not our line */
    }
  }
  return null
}

const num = (v) => Number(v ?? 0)

/**
 * Compose the play-session briefing FROM the verdict: short by construction.
 * The state payload line is appended unchanged - the session projection folds
 * the log, so the filter's opinion never replaces the facts.
 */
import { PAYLOAD_PREFIX } from './model.js'
import { shrinkPayloadLevel, SHRINK_MAX, estimateTokens } from './brief.js'

export function buildFilteredBriefing(verdict, payload, { maxTokens }) {
  const lines = [`DF-OBSERVER (filtered) tick ${payload.tick}${payload.paused ? ' | PAUSED' : ''}`]
  if (verdict.headline) lines.push(`HEADLINE: ${verdict.headline}`)
  for (const change of (verdict.changes ?? []).slice(0, 6)) lines.push(`  - ${change}`)
  const next = verdict.next ?? payload.next
  if (next) {
    const at = next.at ? ` (x${next.at.x0}-${next.at.x1} y${next.at.y0}-${next.at.y1} elev${next.at.elev})` : ''
    lines.push(`NEXT: ${next.id ?? ''} ${next.title ?? ''}${at}${next.keys ? ` | keys: ${next.keys}` : ''}`)
  }
  for (const note of (verdict.notes ?? []).slice(0, 3)) lines.push(`NOTE: ${note}`)
  const boxes = (payload.boxes ?? []).slice(0, 3).map((b) =>
    `elev${b.elev} x${b.box[0]}-${b.box[2]} y${b.box[1]}-${b.box[3]}: to_dig ${b.to_dig} | dug ${b.dug} | pending ${b.pending}`)
  if (boxes.length) lines.push(`DIG: ${boxes.join(' ; ')}`)
  lines.push('Detail: df_status / df_todo / df_alerts. Filter is the plugin\'s analyst; wake whitelist stays Config-enforced.')
  const text = lines.join('\n')
  for (let level = 0; level <= SHRINK_MAX; level += 1) {
    const data = shrinkPayloadLevel(payload, level)
    const stateLine = `${PAYLOAD_PREFIX}${JSON.stringify({ form: 'briefing', tick: payload.tick, data })}`
    const out = `${text}\n${stateLine}`
    const tokens = estimateTokens(out)
    if (tokens <= maxTokens) return { text: out, tokens, payloadLevel: level }
  }
  const data = shrinkPayloadLevel(payload, SHRINK_MAX)
  const stateLine = `${PAYLOAD_PREFIX}${JSON.stringify({ form: 'briefing', tick: payload.tick, data })}`
  const shortText = lines.slice(0, 2).join('\n')
  const out = `${shortText}\n${stateLine}`
  return { text: out, tokens: estimateTokens(out), overBudget: estimateTokens(out) > maxTokens }
}

/** Wake text from a filter-proposed, plugin-approved alert. */
export function buildFilterWake(alert, payload, { maxTokens = 420 } = {}) {
  const lines = [
    `DF-OBSERVER ALERT (filter, tick ${payload.tick}) ${alert.kind}${alert.coord ? ` at ${alert.coord}` : ''}:`,
    alert.evidence,
    alert.suggested ? `-> ${alert.suggested}` : '',
    `Detail: df_status({scope:"summary"}); silence repeats: df_alerts({action:"ack"}).`,
  ].filter(Boolean)
  const text = lines.join('\n')
  for (let level = 0; level <= SHRINK_MAX; level += 1) {
    const data = shrinkPayloadLevel(payload, level)
    const stateLine = `${PAYLOAD_PREFIX}${JSON.stringify({ form: 'alert', tick: payload.tick, data })}`
    const out = `${text}\n${stateLine}`
    const tokens = estimateTokens(out)
    if (tokens <= maxTokens) return { text: out, tokens, payloadLevel: level }
  }
  const data = shrinkPayloadLevel(payload, SHRINK_MAX)
  const out = `${lines.slice(0, 2).join('\n')}\n${PAYLOAD_PREFIX}${JSON.stringify({ form: 'alert', tick: payload.tick, data })}`
  return { text: out, tokens: estimateTokens(out), overBudget: estimateTokens(out) > maxTokens }
  void num
}
