const req = { facts: true, rungs: true, elev0: 22, elev1: 45, survey: true, warnings: true, pause: true, buildings: true }
const data = await (await fetch('http://127.0.0.1:8000/observe?' + new URLSearchParams({ q: JSON.stringify(req) }))).json()
console.log('keys:', Object.keys(data).join(','))
const farm = (data.rungs ?? []).find(r => /farm/i.test(String(r.title ?? '')))
console.log('farm rung:', JSON.stringify(farm))
const bl = data.buildings ?? []
const lines = bl.map(x => [x.type, x.kind, x.name, x.custom, x.items?.built?.length].filter(v => v !== undefined && v !== null && v !== '').join('/'))
console.log('buildings:', lines.length)
console.log(lines.filter(l => /farm|plot|garden/i.test(l)).join('\n') || '(no building line mentions farm/plot/garden)')
