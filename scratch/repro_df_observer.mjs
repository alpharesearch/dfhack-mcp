/* Reproduce the activation failure of @local/df-observer in isolation. */
const N = '/home/markus/.npm/_npx/1e7f6d9597241db0/node_modules/@deepseek-ai'
const { Context, Service } = await import(`${N}/cordis/lib/index.js`)
const mod = await import('/home/markus/github/dfhack-mcp/plugin/df-observer/index.js')

const ctx = new Context()
const stubs = {
  tools: { register: () => () => {}, restrict: () => () => {}, guard: () => () => {} },
  agents: { list: () => [], get: () => undefined, register: () => () => {}, turnBoundaryOf: () => ({ lastTurn: 0 }) },
  systemPrompt: { section: () => () => {}, context: () => () => {} },
  sessionProjections: {
    register: async (def) => ({
      stateOf: () => def.init({}, 0),
      readerFor: () => () => def.init({}, 0),
      dispose: async () => {},
    }),
    onChanged: () => () => {},
  },
  timer: { timeout: (fn, ms) => { const t = setTimeout(fn, ms); return () => clearTimeout(t) }, interval: (fn, ms) => { const t = setInterval(fn, ms); return () => clearInterval(t) } },
}
for (const [name, value] of Object.entries(stubs)) ctx.provide(name, value)
const config = mod.Config.parse({})
try {
  const fiber = await ctx.plugin(mod.default, { config })
  console.log('activated, state:', fiber.state)
  await new Promise((r) => setTimeout(r, 500))
  console.log('after 500ms state:', fiber.state)
} catch (error) {
  console.log('ACTIVATION FAILED:', error?.message)
  console.log((error?.stack ?? '').split('\n').slice(0, 12).join('\n'))
}
await ctx.fiber.dispose()
