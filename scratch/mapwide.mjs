const q = { facts: true, buildings: true, rungs: true, elev0: 22, elev1: 45 }
const t0 = Date.now()
const data = await (await fetch('http://127.0.0.1:8000/observe?' + new URLSearchParams({ q: JSON.stringify(q) }))).json()
const ms = Date.now() - t0
console.log('latency:', ms, 'ms | scope:', data.buildings_scope, '| notes:', JSON.stringify(data.notes ?? []))
console.log('map-wide:', (data.buildings ?? []).map(b => `${b.name}x${b.count}`).join(', '))
console.log('band-only:', (data.buildings_band_only ?? []).map(b => `${b.name}x${b.count}`).join(', '))
const farm = (data.rungs ?? []).find(r => r.id === 'farm')
console.log('farm rung done:', farm?.done, '| next:', JSON.stringify(data.next?.id))
