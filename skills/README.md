# skills/ - the play-knowledge library

Skills are **procedure and judgment**; the MCP tools are the instruments. This folder keeps
that knowledge in the repo - versioned, reviewable, and portable - instead of welding it
into one harness install.

## Library
| Skill | Covers |
|---|---|
| [`df-dig`](df-dig/SKILL.md) | tunnels, rooms, stair shafts: depth/overburden, damp & hot hazards, designating into fog, labour, band ordering, proving the dig, background-job waits, this world's geometry |

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
