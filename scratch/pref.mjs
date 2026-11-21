const dir = '/home/markus/github/dfhack-mcp/plugin/df-observer'
const { FortModel } = await import(dir + '/model.js')
const { Config } = await import(dir + '/index.js')
const { buildBriefing, setTitleBudget } = await import(dir + '/brief.js')
const { buildFilteredBriefing } = await import(dir + '/filter.js')
const cfg = Config.parse({})
setTitleBudget({ rows: 10, chars: 130 })
const m = new FortModel(cfg)
m.ingestFast({ paused: false, generation: 1,
  facts: { pop: 108, dwarves: 21, miners: 3, picks: 3, food: 78, drink: 0, wood: 46, stone: 136, metal: 0, barrels: 0, pending_dig: 18, water_tiles: 1030, magma_tiles: 0 },
  buildings: [{ name: 'Well', count: 1 }],
  boxes: [{ elev: 35, box: [45, 121, 66, 122], to_dig: 20, dug: 24, pending: 0 }],
  shafts: [[50, 123]], warnings: { designated_tiles: 18, damp_tiles: 12, hot_tiles: 0 }, notes: [] })
const rungs = []
for (let i = 0; i < 14; i += 1) {
  rungs.push({ id: 'r' + i, title: 'A quickstart rung with a long descriptive title the player wants to read in full, number ' + i, done: i < 4 })
}
m.ingestDeep({ rungs, notes: [], piles: [], designations: { designated_total: 18, top: [{ elev: 22, x0: 43, y0: 121, x1: 44, y1: 122, tiles: 4 }] } })
m.evaluate(cfg)
const p = m.payload()
const longest = Math.max(...p.todos.map((t) => (t.title ?? '').length))
console.log('open rows:', p.todos.filter((t) => t.state !== 'done').length, '| longest title:', longest)
const v = { headline: 'Miner death report unacked; four faces idle 39 reads; drink 0 with 21 dwarves', significance: 'high', changes: ['game says: "Aban Fikodatzul, Miner has been found dead."', 'elev41 x38-48 y113-122: dug 16->17, to_dig 16'], notes: [] }
for (const cap of [480, 600, 700, 800, 900]) {
  const b = buildFilteredBriefing(v, p, { maxTokens: cap })
  const wire = JSON.parse(b.text.split('\n').find((l) => l.startsWith('[df-observer] ')).slice('[df-observer] '.length)).data
  const open = wire.todos.filter((t) => t.state !== 'done')
  const titled = open.filter((t) => t.title)
  const avg = titled.length ? Math.round(titled.reduce((s, t) => s + t.title.length, 0) / titled.length) : 0
  console.log('cap', String(cap).padEnd(5), '| tokens', String(b.tokens).padEnd(4), b.overBudget ? 'OVER' : 'ok  ',
    '| level', String(b.payloadLevel).padEnd(2), '| titled', (titled.length + '/' + open.length).padEnd(6), '| avg', String(avg).padEnd(4),
    '| boxes', Array.isArray(wire.boxes) ? wire.boxes.length : '-')
}
