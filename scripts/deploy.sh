#!/usr/bin/env bash
# Deploy the repo's deployables into a Dwarf Fortress game directory.
#
# Direction of truth: the REPO is the source; the game dir is a copy target, because
# DFHack only reads blueprints/scripts from its own tree. Authoring stays under git with
# plain workspace permissions; only this copy step touches the game directory.
#
#   ./scripts/deploy.sh            # check (report differences, change nothing)
#   ./scripts/deploy.sh deploy     # copy repo -> game
#   ./scripts/deploy.sh list       # show the mapping
#   DF_GAMEDIR=/x/df ./scripts/deploy.sh deploy
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GAMEDIR="${DF_GAMEDIR:-/mnt/DATA/SteamLibrary/steamapps/common/Dwarf Fortress}"
SRC_BP="$REPO/deployables/blueprints"
SRC_SC="$REPO/deployables/scripts"
MODE="${1:-check}"

dst_bp() { echo "$GAMEDIR/dfhack-config/blueprints"; }
dst_sc() { echo "$GAMEDIR/hack/scripts"; }

list_map() {
    echo "repo -> game (gamedir: $GAMEDIR)"
    echo "  $SRC_BP/*.csv"
    echo "      -> $(dst_bp)/"
    echo "  $SRC_SC/**/*.lua"
    echo "      -> $(dst_sc)/  (subdirectories mirrored)"
}

# content compare: 0 same, 1 differs, 2 missing target
state() {
    local src="$1" dst="$2"
    [ -e "$dst" ] || { echo 2; return; }
    cmp -s "$src" "$dst" && echo 0 || echo 1
}

copy_pair() {
    local src="$1" dst="$2"
    mkdir -p "$(dirname "$dst")"
    cp -p "$src" "$dst"
}

run() {
    local verb="$1"
    local added=0 changed=0 same=0 missing_dir=0
    local srcs=() dsts=()
    if [ -d "$SRC_BP" ]; then
        for f in "$SRC_BP"/*.csv; do
            [ -e "$f" ] || continue
            srcs+=("$f"); dsts+=("$(dst_bp)/$(basename "$f")")
        done
    fi
    if [ -d "$SRC_SC" ]; then
        while read -r f; do
            rel="${f#$SRC_SC/}"
            srcs+=("$f"); dsts+=("$(dst_sc)/$rel")
        done < <(find "$SRC_SC" -name '*.lua' -type f 2>/dev/null)
    fi
    if [ "${#srcs[@]}" -eq 0 ]; then
        echo "nothing to deploy (no .csv in deployables/blueprints, no .lua in deployables/scripts)"
        return 0
    fi
    for i in "${!srcs[@]}"; do
        local s="${srcs[$i]}" d="${dsts[$i]}"
        local st; st="$(state "$s" "$d")"
        case "$st" in
            0) same=$((same+1));;
            1) changed=$((changed+1)); [ "$verb" = deploy ] && copy_pair "$s" "$d";;
            2) added=$((added+1));    [ "$verb" = deploy ] && copy_pair "$s" "$d";;
        esac
        printf '  %-7s %s\n' "$( [ "$st" = 0 ] && echo same || { [ "$st" = 1 ] && echo differs || echo new; } )" "${s#$REPO/}"
    done
    echo ""
    if [ "$verb" = deploy ]; then
        echo "deployed: $added new, $changed updated, $same unchanged"
        echo "game dir: $GAMEDIR"
        echo "restart the game (or the RPC server) if you deployed lua scripts - DFHack loads them at start."
    else
        echo "check: $added new, $changed differing, $same identical (nothing copied)"
        echo "run './scripts/deploy.sh deploy' to copy. This needs write access to the game dir."
    fi
    return 0
}

case "$MODE" in
    list) list_map;;
    check) run check;;
    deploy) run deploy;;
    skills-check|skills)
        # The play-knowledge library: copy skills/<name>/SKILL.md bundles into the harness
        # skills root. Explicit only - nothing lands in a harness without being asked.
        DEST="${DSH_HOME:-$HOME/.dsh}/skills"
        [ "$1" = skills-check ] && MODE=check || MODE=deploy
        echo "skills -> $DEST ($MODE)"
        n=0
        for d in "$REPO"/skills/*/; do
            [ -f "$d/SKILL.md" ] || { echo "  skip $(basename "$d"): no SKILL.md"; continue; }
            if [ "$MODE" = check ]; then
                if [ -f "$DEST/$(basename "$d")/SKILL.md" ]; then
                    cmp -s "$d/SKILL.md" "$DEST/$(basename "$d")/SKILL.md" \
                        && echo "  same $(basename "$d")" || echo "  DIFFERS $(basename "$d")"
                else
                    echo "  missing $(basename "$d")"
                fi
            else
                mkdir -p "$DEST/$(basename "$d")"
                cp "$d/SKILL.md" "$DEST/$(basename "$d")/SKILL.md"
                echo "  copied $(basename "$d")"
            fi
            n=$((n+1))
        done
        echo "skills: $n ($MODE) -> $DEST"
        ;;
    *) echo "usage: $0 [check|deploy|list|skills-check|skills]   (default check)"; exit 2;;
esac
