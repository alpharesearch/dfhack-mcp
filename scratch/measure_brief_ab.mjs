// Measured A/B of the two briefing paths against the CURRENT live game state:
//   - the rule-gated briefing (brief.js, capped by Config.briefing_max_tokens)
//   - the filter-composed briefing (filter.js, capped by Config.filter_briefing_max_tokens)
// The verdict here is written by this script (a stand-in for the filter agent's
// answer) so the SIZE is measured; the filter's own judgment is measured live
// when Config.filter_enabled=true.
import { Config } from '../plugin/df-observer/index.js'
import { FortModel } from '../plugin/df-observer/model.js'
import { buildBriefing, estimateTokens } from '../plugin/df-observer/brief.js'
import { buildFilteredBriefing } from '../plugin/df-observer/filter.js'

const BASE = 'http://127.0.0.1:8000'
const q = {
  elev0: 33, elev1: 40,
  boxes: [[27, 121, 42, 126, 36], [43, 122, 49, 124, 36]],
  shafts: [[50, 123], [43, 121]],
  pile_ids: [15, 17, 18, 19, 20, 24],
  picks: true, rungs: true, facts: true, warnings: true, reports: true,
}
const url = `${BASE}/observe?q=${encodeURIComponent(JSON.stringify(q))}`
const data = JSON.parse(await (await fetch(url)).text())

const cfg = Config.parse({})
const model = new FortModel(cfg)
model.pollMs = 8000
model.ingestFast(data)
model.ingestDeep(data)
model.evaluate({})
const payload = model.payload()

const rule = buildBriefing(payload, { maxTokens: cfg.briefing_max_tokens })
const verdict = {
  tick: payload.tick,
  significance: 'low',
  headline: 'Fort quiet: tunnel 51 tiles from done, no work pending at the face',
  changes: [
    'elev36 x27-42 y121-126: to_dig 51, dug 45, pending 0',
    'elev36 x43-49 y122-124: to_dig 8, dug 13, pending 0',
    'drink 0 with 95 pop: no alcohol production yet',
  ],
  next: payload.next ? { id: payload.next.id, title: payload.next.title, keys: payload.next.keys, at: payload.next.at } : null,
  wake: [],
  notes: [],
}
const filtered = buildFilteredBriefing(verdict, payload, { maxTokens: cfg.filter_briefing_max_tokens })
const filtered260 = buildFilteredBriefing(verdict, payload, { maxTokens: 260 })

console.log('live payload (full, tokens):', estimateTokens(JSON.stringify(payload)))
console.log('rule briefing      :', rule.tokens, 'tokens  payloadLevel', rule.payloadLevel)
console.log('filtered @260      :', filtered260.tokens, 'tokens  payloadLevel', filtered260.payloadLevel ?? 'leanest (body trimmed)')
console.log('filtered @'+cfg.filter_briefing_max_tokens+'    :', filtered.tokens, 'tokens  payloadLevel', filtered.payloadLevel ?? 'leanest (body trimmed)')
console.log('saving per injection:', rule.tokens - filtered.tokens, 'tokens')
console.log('--- filtered text ---')
console.log(filtered.text)
