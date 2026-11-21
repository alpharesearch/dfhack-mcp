import path from 'node:path'
const dir = '/home/markus/github/dfhack-mcp/plugin/df-observer'
const { z } = await import(path.join(dir, 'node_modules/zod/index.js'))
const { FortModel, dfObserverProjection } = await import(path.join(dir, 'model.js'))
const { Config } = await import(path.join(dir, 'index.js'))
const { buildBriefing } = await import(path.join(dir, 'brief.js'))
const req = { boxes: [[45,121,66,122,35],[38,113,48,122,41]], shafts: [[50,123]], warnings: true, facts: true, rungs: true, reports: 3, pause: true, survey: true, status_probe: true, elev0: 22, elev1: 45, designations: 12, pile_ids: [18,19,20,24] }
const data = await (await fetch('http://127.0.0.1:8000/observe?' + new URLSearchParams({ q: JSON.stringify(req) }))).json()
const cfg = Config.parse({})
const m = new FortModel(cfg); m.ingestFast(data); m.ingestDeep(data); m.evaluate(cfg)
const b = buildBriefing(m.payload(), { maxTokens: 700 })
const line = b.text.split('\n').find(l => l.startsWith('[df-observer] '))
const wire = JSON.parse(line.slice('[df-observer] '.length)).data
console.log('level', b.payloadLevel, 'tokens', b.tokens)
console.log('wire keys:', Object.keys(wire).join(','))
console.log('wire.designations:', JSON.stringify(wire.designations)?.slice(0, 60))
const proj = dfObserverProjection(z)
const view = proj.apply(proj.init(), { type: 'user/message', data: { content: line } })
console.log('view.designations:', JSON.stringify(view.designations)?.slice(0, 60))
console.log('view.rungs:', Array.isArray(view.rungs) ? view.rungs.length : String(view.rungs), '| view.boxes:', Array.isArray(view.boxes) ? view.boxes.length : String(view.boxes))
