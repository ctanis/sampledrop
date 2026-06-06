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

if [ "$REQUESTED_COMMAND" = "status" ] && command -v osascript >/dev/null 2>&1; then
  STATUS_MESSAGE=$(
    printf '%s\n' "$OUTPUT" |
      awk -F': ' '/^(Backend|Project|Trim|Normalize|Notifications): / {printf "%s=%s ", tolower($1), $2}'
  )
  if [ -z "$STATUS_MESSAGE" ]; then
    STATUS_MESSAGE=$(printf '%s\n' "$OUTPUT" | awk 'NF {print; count++} count == 3 {exit}')
  fi
  osascript \
    -e 'on run argv' \
    -e 'display notification (item 1 of argv) with title "Samplewatch Status"' \
    -e 'end run' \
    "$STATUS_MESSAGE" >/dev/null 2>&1 || true
fi

printf '%s\n' "$OUTPUT"
