#!/bin/sh
set -eu

APP_HOME="${SAMPLEWATCH_HOME:-$HOME/.local/share/samplewatch}"
VENV="$APP_HOME/venv"
BIN_DIR="${SAMPLEWATCH_BIN_DIR:-$HOME/bin}"
LINK="$BIN_DIR/samplewatch"
TARGET="$VENV/bin/samplewatch"

if [ -x "$LINK" ]; then
  "$LINK" stop >/dev/null 2>&1 || true
fi

if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$TARGET" ]; then
  rm "$LINK"
  echo "Removed $LINK"
elif [ -e "$LINK" ]; then
  echo "Leaving $LINK in place; it is not the samplewatch symlink created by this installer."
fi

if [ -d "$VENV" ]; then
  rm -rf "$VENV"
  echo "Removed $VENV"
fi

rmdir "$APP_HOME" 2>/dev/null || true

echo "Preserved:"
echo "  $HOME/.samplewatch.toml"
echo "  $HOME/.samplewatch.log"
