const q = { facts: true, buildings: true, rungs: true, elev0: 22, elev1: 45 }
for (const [label, port] of [['live 8000 (band-only code)', 8000], ['scratch 8006 (map-wide code)', 8006]]) {
  const t0 = Date.now()
  const data = await (await fetch(`http://127.0.0.1:${port}/observe?` + new URLSearchParams({ q: JSON.stringify(q) }))).json()
  const ms = Date.now() - t0
  const farm = (data.rungs ?? []).find(r => r.id === 'farm')
  console.log(label.padEnd(30), String(ms).padStart(5), 'ms | farm rung:', farm?.done, '| scope:', data.buildings_scope ?? '(n/a)')
}
