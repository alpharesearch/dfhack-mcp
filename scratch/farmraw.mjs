const req = { facts: true, buildings: true, elev0: 22, elev1: 45 }
const data = await (await fetch('http://127.0.0.1:8000/observe?' + new URLSearchParams({ q: JSON.stringify(req) }))).json()
console.log(JSON.stringify(data.buildings, null, 1).slice(0, 1500))
console.log('--- facts.food:', data.facts?.food, '| raw_food:', data.facts?.raw_food, '| meals:', data.facts?.meals, '| seeds:', data.facts?.seeds)
