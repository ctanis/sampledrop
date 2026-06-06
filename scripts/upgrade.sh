#!/bin/sh
set -eu

APP_HOME="${SAMPLEWATCH_HOME:-$HOME/.local/share/samplewatch}"
VENV="$APP_HOME/venv"
BIN_DIR="${SAMPLEWATCH_BIN_DIR:-$HOME/bin}"
LINK="$BIN_DIR/samplewatch"

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ ! -x "$VENV/bin/python" ]; then
  echo "No samplewatch venv found at $VENV"
  echo "Run scripts/install.sh first."
  exit 1
fi

"$VENV/bin/python" -m pip install --upgrade "$REPO_ROOT"
mkdir -p "$BIN_DIR"
ln -sfn "$VENV/bin/samplewatch" "$LINK"

echo "Upgraded samplewatch:"
echo "  $LINK -> $VENV/bin/samplewatch"
