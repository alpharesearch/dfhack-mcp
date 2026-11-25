# skills/ - the play-knowledge library

Skills are **procedure and judgment**; the MCP tools are the instruments. This folder keeps
that knowledge in the repo - versioned, reviewable, and portable - instead of welding it
into one harness install.

## Library
| Skill | Covers |
|---|---|
| [`df-quickstart`](df-quickstart/SKILL.md) | full Quickstart Dependency Graph & Execution Playbook (from `FlowchartDF.png` & the DF Wiki Quickstart Guide), mapping every resource, workshop, stockpile, zone, noble, and goal node to exact `dfhack-mcp` tools and prerequisite checks |
| [`df-access`](df-access/SKILL.md) | access-first build order (staircase, connection tunnel, room), work-detail staffing, `fort_strata` + `fort_plan` CAD (`bootstrap_camp`, `find_site`, topological commit), and two-stage Bootstrap -> DFHack Quickfort scale-up |
| [`df-dig`](df-dig/SKILL.md) | tunnels, rooms, stair shafts (`box`, `boxes`, `middle_first`, `stair_shaft`), `fort_strata` + `fort_plan`: depth/overburden, damp & hot hazards, designating into fog, labour, band ordering, proving the dig |
| [`df-placement`](df-placement/SKILL.md) | workshops, crafted furniture (`workshop_job`), trade depots (`trade_depot`), farm plots (`configure_farmplot`), zones (`create_zone`, `assign_zone_owner`), `fort_plan(action="find_site")`, `building_status`, and `dfhack_automation` |
| [`df-stockpile`](df-stockpile/SKILL.md) | general, wood, and refuse stockpiles (`create_stockpile`, `configure_stockpile`, `list_stockpiles`), container/wheelbarrow limits, and `workshop_job(action="stock")` |
| [`df-trade`](df-trade/SKILL.md) | Trade Depot 3-wide wagon access, Broker appointment/summoning, marking & hauling trade goods, and autonomous barter (`open_trade`, `barter_status`, `barter_select`, `barter_confirm`, `close_trade`) |
| [`df-industry`](df-industry/SKILL.md) | `workshop_job` vs Manager `work_order`, fuel/metal and cloth/hospital chains, skill-based staffing (`unit_profile`), Bookkeeper stock scope, enclosed noble rooms, and `prioritize` |
| [`df-crisis`](df-crisis/SKILL.md) | emergency triage (`alerts_and_wellbeing`, `building_status`, `unit_profile`), cavern/wildlife seals, wounded miners & hospital setup, drink/seed recovery, aquifer cancellations, and strange moods |
| [`df-farm`](df-farm/SKILL.md) | soil & dig-to-soil planning (`fort_strata`, `fort_plan`), 3x3 `Farm Plot` setup, `autofarm`/`seedwatch`/`ban-cooking`, Still + barrels brewing, Kitchen meals, and surface grass pastures |

Planned:
`df-military` (squads, drafting, patrols — once the squad/draft MCP tool `#73` is built).

## Format (DSH filesystem provider contract)
- A skill is `skills/<name>/SKILL.md` (directory bundle) or flat `<name>.md`.
- Discovery is **one level deep**: nested `**/SKILL.md` trees are deliberately not found.
- YAML frontmatter: **`name`** (kebab-case, must match the directory) and **`description`**
  required; `whenToUse`, `metadata`, `disable-model-invocation`, `user-invocable` optional.
- Keep it bounded: a skill is loaded on demand, so every line must earn its tokens.
  Long reference material goes in sibling files the skill points at, not in the body.

## Live here with no profile change (verified)
`/home/markus/github/dfhack-mcp/.dsh/skills -> ../skills` (a symlink, git-tracked). The
filesystem provider scans **`<projectRoot>/.dsh/skills`** at rank 100, where the project root
is the nearest `.git` ancestor - so authoring in `skills/` is discovered and watched live in
any session whose working directory is inside this repo. Proof: `df-dig` appeared in the
session skill catalog and loaded, with its base directory reported as
`.dsh/skills/df-dig`.

## Making a harness use this library (pick one, all non-destructive)
1. **Point the provider at the repo** (no copying; edits are watched live):
   ```yaml
   - name: '@deepseek-ai/dsh-skill'
   - name: '@deepseek-ai/dsh-skill-filesystem'
     config:
       customSkillDirs: ["/home/markus/github/dfhack-mcp/skills"]
   ```
2. **Deploy copies** to the harness skills root (`$DSH_HOME/skills`, default `~/.dsh/skills`):
   ```sh
   ./scripts/deploy.sh skills          # copies skills/* into $DSH_HOME/skills
   ./scripts/deploy.sh skills-check    # diff only, copies nothing
   ```
3. **Project roots** are scanned automatically when the session's working directory is this
   repo, so `skills/` here is discoverable in-repo with the default provider config.

MCP bundles are configuration-only and supply **tools, resources and literal server
instructions** - they cannot supply skills. Skills come from skill providers registered on
`ctx.skills`; the registry merges every provider's catalog. So this library is the right
place for play knowledge, and the MCP server stays the instrument panel.

`df-access` - the access-first build order (staircase, connection tunnel, room), work-detail staffing, and the reads that prove a dig is real. Generic: no coordinates, any map.

See [`docs/tool-notes.md`](../docs/tool-notes.md) for historical playtest findings, DF 53.17-r1 crash postmortems, and the HTTP-only diagnostic/operator endpoint reference (the MCP surface is pruned to 33 gameplay tools including `fort_strata`, `fort_plan`, `workshop_job`, `dfhack_automation`, `trade_depot`, and `unit_profile`).
