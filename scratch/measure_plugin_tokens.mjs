// First-turn token cost of the df-observer plugin, measured from the real
// strings the plugin injects (system-prompt section + the first briefing line
// as injected on a fresh session). chars/4 estimator, as brief.js uses.
import { STANDING_RULES } from '../plugin/df-observer/brief.js'
import { Config } from '../plugin/df-observer/index.js'

const tokens = (s) => Math.ceil(s.length / 4)

// The live briefing injected into a new session at tick 1 (captured from the
// running game, 2026-11-21 11:33, paused, level-7 payload fold):
const brief = [
  'FORT BRIEFING (in-game elevations) | PAUSED | pop 95 | miners 2 of ? (probe has no data) picks | food 13 | drink 0 | wood 0 | stone 136 | metal 0 | pending dig 1',
  'LADDER: 6 of 21 done [labors entrance stairwell stockpile barrels depot]',
  'NEXT:   meeting: Meeting area inside the fort, 5x5-7x7, on the 2nd level below ground at x65-71 y113-119 elev38 (region A, 8899 walkable tiles)',
  '        keys: z -> Meeting Area -> draw rect filling the room -> Accept',
  '        why:  keeps idle dwarves inside; dig the room out FIRST, zones need mined space',
  'DIG (tile categories; complete = to_dig 0 && dug >0):',
  '  elev36 x27-42 y121-126: to_dig 51 | dug 45 | pending 0',
  '  elev36 x43-49 y122-124: to_dig 8 | dug 13 | pending 0',
  'TODOS: 13 open, 5 done',
  '  #5 Meeting area inside the fort, 5x5-7x7, on the 2nd level below ground (ladder)',
  '      accept: ladder rung meeting satisfied | last: rung open: keeps idle dwarves inside; dig the room out…',
  'ALERTS: none active',
  'Rules: progress = positive tile state, never absence; every number names its box/elev; NO DATA is labelled; each to-do carries its acceptance test; know-how: skills df-dig / df-stockpile.',
  '[df-observer] {"form":"briefing","tick":1,"data":{"tick":1,"online":true,"paused":true,"todos":[{"id":1,"state":"done","evidence":"rung predicate satisfied on the ladder read"},{"id":2,"state":"done","evidence":"rung predicate satisfied on the ladder read"},{"id":3,"state":"done","evidence":"rung predicate satisfied on the ladder read"},{"id":4,"state":"done","evidence":"rung predicate satisfied on the ladder read"},{"id":5,"state":"open","evidence":"rung open: keeps idle dwarves inside; dig the room out FIRST"},{"id":6,"state":"open","evidence":"rung open: refuse inside the fort rots and miasma kills"},{"id":7,"state":"open","evidence":"rung open: a plot planted in one season idles 3/4 of the yea"},{"id":8,"state":"open","evidence":"rung open: 3 logs also come from deconstructing the wagon: c"},{"id":9,"state":"open","evidence":"rung open: no alcohol means slow, unhappy dwarves"},{"id":10,"state":"done","evidence":"rung predicate satisfied on the ladder read"},{"id":11,"state":"open","evidence":"not checked yet"},{"id":12,"state":"open","evidence":"not checked yet"},{"id":13,"state":"open","evidence":"not checked yet"},{"id":14,"state":"open","evidence":"not checked yet"},{"id":15,"state":"open","evidence":"not checked yet"},{"id":16,"state":"open","evidence":"not checked yet"},{"id":17,"state":"open","evidence":"not checked yet"},{"id":18,"state":"open","evidence":"not checked yet"}],"alerts":[],"digEvents":[]}}',
].join('\n')

const cfg = Config.parse({})
console.log('system-prompt standing-rules section:', tokens(STANDING_RULES), 'tokens')
console.log('first injected briefing (real, tick 1):', tokens(brief), 'tokens')
console.log('combined first-turn delta:', tokens(STANDING_RULES) + tokens(brief), 'tokens')
console.log('cfg: briefing_max_tokens', cfg.briefing_max_tokens,
  '| inject spacing min/max ms', cfg.inject_min_spacing_ms, cfg.inject_max_spacing_ms,
  '| filter cap', cfg.filter_briefing_max_tokens)
console.log('steady-state: rules section every assembly + briefing every <=',
  Math.round(cfg.inject_max_spacing_ms / 1000), 's while playing')
