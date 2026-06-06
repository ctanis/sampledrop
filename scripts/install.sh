#!/bin/sh
set -eu

APP_HOME="${SAMPLEWATCH_HOME:-$HOME/.local/share/samplewatch}"
VENV="$APP_HOME/venv"
BIN_DIR="${SAMPLEWATCH_BIN_DIR:-$HOME/bin}"
LINK="$BIN_DIR/samplewatch"
PYTHON="${PYTHON:-python3}"

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

mkdir -p "$APP_HOME" "$BIN_DIR"

if [ ! -x "$VENV/bin/python" ]; then
  "$PYTHON" -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --upgrade "$REPO_ROOT"

ln -sfn "$VENV/bin/samplewatch" "$LINK"

if [ ! -f "$HOME/.samplewatch.toml" ]; then
  cp "$REPO_ROOT/samplewatch.example.toml" "$HOME/.samplewatch.toml"
  echo "Created $HOME/.samplewatch.toml"
else
  echo "Keeping existing $HOME/.samplewatch.toml"
fi

echo "Installed samplewatch:"
echo "  $LINK -> $VENV/bin/samplewatch"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    echo
    echo "Add this to your shell profile if samplewatch is not found:"
    echo "  export PATH=\"$BIN_DIR:\$PATH\""
    ;;
esac

echo
echo "Try:"
echo "  samplewatch --detach"
echo "  samplewatch status"
