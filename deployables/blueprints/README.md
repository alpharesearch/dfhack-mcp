# deployables/blueprints - quickfort blueprints, deployed by copy

Source of truth for every blueprint the MCP authors: `apply_blueprint` writes here
(git-tracked), then copies to `<gamedir>/dfhack-config/blueprints/`, because quickfort
resolves blueprint names only against its own directory.

`./scripts/deploy.sh check` compares this tree with the game directory; `deploy` copies.
Modelines set the mode (`#dig`, `#build`); build keys are only valid in `#build`.
