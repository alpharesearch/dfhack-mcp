#!/usr/bin/env bash
# Deploy the df-observer plugin: check everything, then install through the
# supported path. The profile's plugin registry is pnpm, and `dsh plugin`
# forwards to it (see `dsh plugin --help`), so this script can do the whole
# round trip from the shell.
#
#   ./plugin/df-observer/deploy.sh check     syntax + manifest + locales + tests (default)
#   ./plugin/df-observer/deploy.sh install   check, then dsh plugin --profile $PROFILE add <dir>
#   ./plugin/df-observer/deploy.sh update    check, then remove + add (fresh module generation)
#   ./plugin/df-observer/deploy.sh status    is the bundle in the profile's node_modules?
#   ./plugin/df-observer/deploy.sh verify    live state of the plugin (mount, poll counters, storage)
#   ./plugin/df-observer/deploy.sh server    restart the DF MCP server so /observe matches main.py
#
# After install/update: a NEW session in the running Harness exercises the
# plugin; a live Host applies the change without a restart (plugin_manager
# reports `application: applied`). Inside this workspace the bundle is a pnpm
# link, so edits to the JS are picked up by the next activation.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
PKG="@local/df-observer"
PROFILE="${DSH_PROFILE:-web}"
JS=(index.js observer.js model.js brief.js tools.js filter.js client.js)

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

check() {
  local missing=0
  for f in "${JS[@]}"; do
    [[ -f "$DIR/$f" ]] || { say "missing $f"; missing=1; continue; }
    node --check "$DIR/$f" || fail "syntax: $f"
  done
  [[ $missing -eq 0 ]] || fail "files missing"
  say "JS syntax: ${#JS[@]} files OK (client.js checked with node --check; factory body parses as script)"

  node -e "
    const fs = require('node:fs')
    const DIR = process.argv[1]
    const pkg = JSON.parse(fs.readFileSync(DIR + '/package.json', 'utf8'))
    for (const k of ['name','version','type','exports','files','dsh']) {
      if (!(k in pkg)) throw new Error('package.json missing '+k)
    }
    if (pkg.dsh.bundle.patch !== './cordis.patch.yml') throw new Error('dsh.bundle.patch wrong')
    if (!pkg.dsh.client) throw new Error('dsh.client missing (panel needs the client half)')
    for (const f of pkg.files) {
      if (f.includes('*')) {
        const dir = DIR + '/' + f.slice(0, f.lastIndexOf('/'))
        const want = f.slice(f.lastIndexOf('/') + 1).replace('*', '')
        const hits = fs.readdirSync(dir).filter((n) => n.endsWith(want))
        if (hits.length === 0) throw new Error('files glob matches nothing: '+f)
        continue
      }
      if (!fs.existsSync(DIR + '/' + f)) throw new Error('files entry missing on disk: '+f)
    }
    for (const loc of ['en','zh']) {
      const doc = JSON.parse(fs.readFileSync(DIR + '/locale/' + loc + '.json', 'utf8'))
      if (!doc.meta?.title || !doc.meta?.description || !doc.meta?.detail) throw new Error('locale '+loc+' meta incomplete')
      const detail = DIR + '/' + doc.meta.detail
      if (!fs.existsSync(detail)) throw new Error('meta.detail missing: '+doc.meta.detail)
      if (fs.statSync(detail).size > 1024*1024) throw new Error('meta.detail exceeds 1 MiB')
    }
    const icon = fs.statSync(DIR+'/icon.svg')
    if (icon.size > 256*1024) throw new Error('icon exceeds 256 KiB')
    if (!fs.readFileSync(DIR+'/icon.svg','utf8').trimStart().startsWith('<svg')) throw new Error('icon.svg is not an SVG')
  " "$DIR" || fail "manifest / locales / icon checklist"
  say "manifest + locales + detail + icon checklist OK"

  python3 - "$DIR/cordis.patch.yml" <<'PY' || fail "patch YAML"
import sys
try:
    import yaml
except ImportError:
    sys.exit(0)
doc = yaml.safe_load(open(sys.argv[1]))
assert isinstance(doc, list) and doc[0].get("insert"), "expected a top-level insert list"
row = doc[0]["insert"][0]
assert row["id"] == "df-observer" and row["name"] == "@local/df-observer", row
assert isinstance(row["config"], dict) and "pause_authority" in row["config"], "config keys"
print("patch YAML OK: row df-observer with %d config keys" % len(row["config"]))
PY

  say "unit tests (offline model/projection/briefing/transport/manifest)"
  node "$REPO/tests/test_df_observer.mjs" || fail "plugin unit tests"
  say "observe endpoint tests"
  "$REPO/.venv/bin/python" "$REPO/tests/test_observe.py" 2>/dev/null | grep -v RuntimeWarning | tail -1 \
    || fail "observe tests"
}

installed() {
  [[ -e "$HOME/.dsh/profiles/$PROFILE/node_modules/$PKG" ]]
}

verify() {
  check
  say "profile dependency"
  grep -q "@local/df-observer" "$HOME/.dsh/profiles/$PROFILE/package.json" \
    && echo "  in package.json: yes" || echo "  in package.json: NO - the plugin is NOT mounted (dsh plugin --profile $PROFILE add $DIR)"
  say "live mount (Config entries for @local/df-observer): inspect via the Host Config provider, entry include:df-observer"
  say "server + poll counters"
  curl -s --max-time 10 "http://127.0.0.1:8000/get-status" | head -c 200; echo
  curl -s --max-time 10 "http://127.0.0.1:8000/observe-stats" | head -c 160; echo
  say "live cost knobs (from the profile row)"
  PROFILE_FILE="$HOME/.dsh/profiles/web/cordis.patch.yml" python3 - <<'PY'
import os, re
lines = open(os.environ["PROFILE_FILE"]).read().split("\n")
start = next((i for i, l in enumerate(lines) if l.strip() == "- id: df-observer"), None)
if start is None:
    print("  no df-observer row in the profile")
else:
    keys = ("briefing_max_tokens", "filter_briefing_max_tokens", "filter_enabled",
            "panel_full_briefing", "tool_docs", "rules_mode", "poll_running_ms", "poll_paused_ms")
    body = lines[start:start + 60]
    found = {}
    for l in body:
        m = re.match(r"^\s+([a-z_]+):\s*(.*)$", l)
        if m and m.group(1) in keys:
            found[m.group(1)] = m.group(2)
    for k in keys:
        print(f"  {k}: {found.get(k, '(Config default)')}")
    print("  measured: 340 -> ids-only rows, 480 -> titles + scan on the filtered wire;")
    print("            panel_full_briefing adds ~622 tokens/briefing for the panel sections")
PY

  say "storage domain (durable ledger)"
  ls -la "$HOME/.dsh/storages/" 2>/dev/null | grep -i "observer" || echo "  no df_observer storage file yet (created on the first ledger write)"
  say "NOTE: for a linked bundle, a Host restart (the running 'dsh web') is the dependable way to load new code"
}

status() {
  if installed; then
    say "$PKG is in the $PROFILE profile: $(readlink -f "$HOME/.dsh/profiles/$PROFILE/node_modules/$PKG")"
  else
    say "$PKG is NOT in the $PROFILE profile"
  fi
}

install() {
  check
  say "installing into profile '$PROFILE' (a live profile applies without a restart)"
  dsh plugin --profile "$PROFILE" add "$DIR"
  installed || fail "install did not land in the profile"
  say "installed. Verify: Plugins shows the row (enabled), and a NEW session shows briefings."
}

update() {
  check
  local current installed_version
  current="$(node -e "process.stdout.write(JSON.parse(require('node:fs').readFileSync('$DIR/package.json','utf8')).version)")"
  installed_version="$(node -e "
    const fs = require('node:fs')
    try { process.stdout.write(JSON.parse(fs.readFileSync('$HOME/.dsh/profiles/$PROFILE/node_modules/$PKG/package.json','utf8')).version) }
    catch { process.stdout.write('none') }" 2>/dev/null || echo none)"
  if [[ "$installed_version" == "$current" ]]; then
    say "NOTE: a live profile REUSES the loaded module when the version is unchanged ($current)."
    say "      Bump 'version' in package.json before installing code changes, then re-run update."
  fi
  if installed; then
    say "removing the installed copy (a fresh module generation needs this)"
    dsh plugin --profile "$PROFILE" remove "$PKG" || true
  fi
  install
}

# Fresh-embark play test (PLAYTEST.md): swap the live profile row to a
# no-assumptions config (auto band, nothing watched), print what the plugin is
# EXPECTED to do on a virgin fort, and keep a backup so `embark-restore` puts the
# measured fort's config back. It never touches the game.
embark() {
  say "switching the profile row to fresh-embark config (backup: $DIR/.embark-backup.txt)"
  PROFILE_FILE="$HOME/.dsh/profiles/web/cordis.patch.yml" BACKUP="$DIR/.embark-backup.txt" python3 - <<'PY'
import os, re
path = os.environ["PROFILE_FILE"]; backup = os.environ["BACKUP"]
lines = open(path).read().split("\n")
start = next((i for i, l in enumerate(lines) if l.strip() == "- id: df-observer"), None)
if start is None:
    raise SystemExit("df-observer row not found in the profile")
end = start + 1
while end < len(lines) and not lines[end].startswith("- "):
    end += 1
row = "\n".join(lines[start:end])
open(backup, "w").write(row)
new_row = row
for key, val in (("elev0", "0"), ("elev1", "0")):
    new_row = re.sub(rf"^(\s*{key}: )\d+$", rf"\g<1>{val}", new_row, flags=re.M)
for key in ("monitored_boxes", "pile_ids", "shafts"):
    new_row = re.sub(rf"^(\s*{key}: ).*$", rf"\g<1>[]", new_row, flags=re.M)
open(path, "w").write(src.replace(row, new_row, 1))
print("row switched; backup written")
print("watched lists cleared, band set to auto (elev0/elev1 = 0)")
PY
  say "expected first-briefing shape on a virgin fort (PLAYTEST §5)"
  cat <<'TXT'
  - FORT BRIEFING header: date, pause state, pop by profession, NO DATA labelled
  - the Quickstart ladder seeded as to-dos, each with its acceptance test
  - NO watched boxes (Config empty) => "pending dig N (watched boxes 0 ...)" is HONEST,
    and the designation scan names where the queued work physically is
  - dig_complete / dig_stalled / tile_state_lost stay quiet until real tiles move
  - water wakes fire only when the band's water tiles grow >= water_growth_min
  - the panel: chips live, sections fill as the wire affords them
  Read it with:  curl -sG "http://127.0.0.1:8000/observe" --data-urlencode 'q={"facts":true,"warnings":true,"survey":true,"designations":12,"rungs":true}'
TXT
}

embark_restore() {
  PROFILE_FILE="$HOME/.dsh/profiles/web/cordis.patch.yml" BACKUP="$DIR/.embark-backup.txt" python3 - <<'PY'
import os
path = os.environ["PROFILE_FILE"]; backup = os.environ["BACKUP"]
if not os.path.exists(backup):
    raise SystemExit("no backup at " + backup + " - nothing to restore")
row = open(backup).read()
lines = open(path).read().split("\n")
start = next((i for i, l in enumerate(lines) if l.strip() == "- id: df-observer"), None)
if start is None:
    raise SystemExit("df-observer row not found in the profile")
end = start + 1
while end < len(lines) and not lines[end].startswith("- "):
    end += 1
lines[start:end] = row.split("\n")
open(path, "w").write("\n".join(lines))
print("restored the measured fort config from the backup")
PY
}

server() {
  say "restarting the DF MCP server so /observe matches main.py"
  "$REPO/start.sh" restart
}

case "${1:-check}" in
  embark)          embark ;;
  embark-restore) embark_restore ;;
  check)   check ;;
  install) install ;;
  update)  update ;;
  status)  status ;;
  verify)  verify ;;
  channel)
    # Channel diagnosis (measured failure mode): the plugin's own poll path is
    # plain HTTP GET /observe; the MCP tools go through POST /mcp, which 404s
    # after the DFHack server restarts until the client's MCP session rebinds.
    base="${DFHACK_MCP_URL:-http://127.0.0.1:8000}"
    poll=$(curl -sG --max-time 10 -o /dev/null -w '%{http_code}' "$base/observe-stats" || echo 000)
    obs=$(curl -sG --max-time 15 -o /dev/null -w '%{http_code}' "$base/observe" --data-urlencode 'q={"elev0":22,"elev1":45}' || echo 000)
    mcp=$(curl -s --max-time 10 -o /dev/null -w '%{http_code}' -X POST "$base/mcp" -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' || echo 000)
    echo "poll GET /observe-stats : $poll"
    echo "read GET /observe       : $obs   (the plugin's channel)"
    echo "mcp  POST /mcp          : $mcp   (the mcp__dfhack__ tools' channel)"
    # 404 = the measured dead-session binding; 406/400 = endpoint alive but our bare
    # ping lacks the session headers (normal for a raw curl); 000 = nothing there.
    if [ "$poll" = 200 ] && [ "$obs" = 200 ] && [ "$mcp" = 404 ]; then
      echo "DIAGNOSIS: the plugin is reading the game fine, but MCP tool calls are bound to a"
      echo "           dead session (measured: -32600 Session not found after a server restart)."
      echo "           Fix by rebinding the MCP connection (restart/reconnect the dfhack MCP client/proxy);"
      echo "           the observer needs no restart - its df_* tools answer from the live model."
    elif [ "$poll" = 200 ] && [ "$mcp" = 406 -o "$mcp" = 400 -o "$mcp" = 200 ] ; then
      echo "channel: healthy (poll 200, MCP endpoint answering; the client binds its session)"
    elif [ "$poll" = 200 ]; then
      echo "channel: poll healthy; MCP endpoint status $mcp - not a dead session, watch for -32600 in tool calls"
    else
      echo "channel: the DFHack MCP server is not answering - start it (deploy.sh server) and re-run."
    fi ;;
  server)  server ;;
  *) echo "usage: $0 [check|install|update|status|verify|server|channel]"; exit 1 ;;
esac
