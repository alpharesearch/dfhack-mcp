# deployables/scripts - DFHack lua scripts, deployed by copy

Anything here (including subdirectories, e.g. `gui/`) is copied to
`<gamedir>/hack/scripts/` by `./scripts/deploy.sh`. DFHack loads scripts at game start,
so a deploy needs a restart to take effect.

No scripts ship yet; this file exists so the directory survives a clone (git does not
track empty directories).
