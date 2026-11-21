const req = { facts: true, buildings: true, elev0: 0, elev1: 128 }
const data = await (await fetch('http://127.0.0.1:8000/observe?' + new URLSearchParams({ q: JSON.stringify(req) }))).json()
const names = (data.buildings ?? []).map(b => `${b.name} x${b.count}`)
console.log('band', data.elev0, '-', data.elev1, '| buildings:', names.join(', '))
const farm = (data.buildings ?? []).filter(b => /farm|plot|garden/i.test(b.name))
console.log('farm matches:', farm.length ? JSON.stringify(farm) : 'NONE in the whole map scan')
