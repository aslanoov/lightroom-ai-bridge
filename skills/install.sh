#!/bin/sh
# Install the Lightroom Bridge skills for Claude Code.
#
#   ./skills/install.sh            # symlink into ~/.claude/skills (stays in sync with git pull)
#   ./skills/install.sh --copy     # copy instead of symlink
#   ./skills/install.sh --project /path/to/project   # install into that project's .claude/skills
#
# Skills installed: lightroom-bridge, lightroom-bridge-pro

set -e

SRC="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$SRC")"
MODE=link
DEST="$HOME/.claude/skills"

while [ $# -gt 0 ]; do
  case "$1" in
    --copy)    MODE=copy; shift ;;
    --project) DEST="$2/.claude/skills"; shift 2 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)         echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$DEST"

for skill in lightroom-bridge lightroom-bridge-pro; do
  target="$DEST/$skill"
  if [ -e "$target" ] || [ -L "$target" ]; then
    echo "skip   $target (already exists; remove it first to reinstall)"
    continue
  fi
  if [ "$MODE" = link ]; then
    ln -s "$SRC/$skill" "$target"
    echo "linked $target -> $SRC/$skill"
  else
    cp -R "$SRC/$skill" "$target"
    echo "copied $target"
  fi
done

cat <<NOTE

Done. The skills look for the bridge in this order:
  1. an 'lrc' console script on PATH (pip / uvx install), then
  2. \$LIGHTROOM_BRIDGE_HOME, then
  3. ~/lightroom-ai-bridge

This checkout is at:
  $REPO

If that is not one of the above, add this to your shell profile:
  export LIGHTROOM_BRIDGE_HOME="$REPO"

Then start Claude Code and type /lightroom-bridge or /lightroom-bridge-pro.
NOTE
