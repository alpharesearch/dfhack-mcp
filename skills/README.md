# skills/ - the play-knowledge library

Skills are **procedure and judgment**; the MCP tools are the instruments. This folder keeps
that knowledge in the repo - versioned, reviewable, and portable - instead of welding it
into one harness install.

## Library
| Skill | Covers |
|---|---|
| [`df-quickstart`](df-quickstart/SKILL.md) | full Quickstart Dependency Graph & Execution Playbook (from `FlowchartDF.png` & the DF Wiki Quickstart Guide), mapping every resource, workshop, stockpile, zone, noble, and goal node to exact `dfhack-mcp` tools and prerequisite checks |
| [`df-dig`](df-dig/SKILL.md) | tunnels, rooms, stair shafts (`box`, `boxes`, `middle_first`, `stair_shaft`): depth/overburden, damp & hot hazards, designating into fog, labour, band ordering, proving the dig (`transitions`), background-job waits, this world's geometry |
| [`df-placement`](df-placement/SKILL.md) | workshops, furniture, trade depots, farm plots (`configure_farmplot`), stockpiles (`create_stockpile` + category/container config), zones (`create_zone`), building status & stall diagnostics (`building_status`) |

Planned (the ladder in play order, one skill per discipline):
`df-build` (stockpiles, zones, furniture, material-attached construction, depot + wagon path),
`df-farm` (soil rooms, plump helmets, irrigation), `df-industry` (worksites, molten, shafts),
`df-military` (squad, rooms, drafting), `df-alerts` (caravan/wave/miasma/stress reading),
`df-plan` (survey -> probe -> designate -> watch -> re-survey loop, fallbacks, fog discipline).

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
