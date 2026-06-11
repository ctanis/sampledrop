#!/bin/sh
set -eu

REQUESTED_COMMAND=${1:-status}
COMMAND=$REQUESTED_COMMAND
if [ "$REQUESTED_COMMAND" = "start" ]; then
  COMMAND="--detach"
fi

PATH="$HOME/bin:$HOME/.local/share/samplewatch/venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export PATH

if command -v samplewatch >/dev/null 2>&1; then
  SAMPLEWATCH=$(command -v samplewatch)
elif [ -x "$HOME/.local/share/samplewatch/venv/bin/samplewatch" ]; then
  SAMPLEWATCH="$HOME/.local/share/samplewatch/venv/bin/samplewatch"
else
  echo "samplewatch command not found. Run scripts/install.sh first."
  exit 1
fi

OUTPUT=$("$SAMPLEWATCH" "$COMMAND" 2>&1 || true)

if [ -z "$OUTPUT" ]; then
  OUTPUT="Done: samplewatch $COMMAND"
fi

printf '%s\n' "$OUTPUT"
