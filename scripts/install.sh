#!/bin/sh
set -eu

APP_HOME="${SAMPLEWATCH_HOME:-$HOME/.local/share/samplewatch}"
VENV="$APP_HOME/venv"
ASSET_DIR="$APP_HOME/assets"
DROPZONE_ASSET="$ASSET_DIR/dropzone-target-finder.png"
BIN_DIR="${SAMPLEWATCH_BIN_DIR:-$HOME/bin}"
LINK="$BIN_DIR/samplewatch"
PYTHON="${PYTHON:-python3}"

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

configure_dropzone_image() {
  CONFIG_FILE="$HOME/.samplewatch.toml"
  if grep -Eq '^[[:space:]]*finder_background_image[[:space:]]*=.*dropzone-target[.]png' "$CONFIG_FILE"; then
    TMP_CONFIG="$CONFIG_FILE.tmp"
    awk -v image="$DROPZONE_ASSET" '
      /^[[:space:]]*finder_background_image[[:space:]]*=.*dropzone-target[.]png/ {
        print "finder_background_image = \"" image "\""
        next
      }
      { print }
    ' "$CONFIG_FILE" > "$TMP_CONFIG"
    mv "$TMP_CONFIG" "$CONFIG_FILE"
    echo "Updated Finder dropzone image in $CONFIG_FILE"
    return
  fi
  if grep -Eq '^[[:space:]]*finder_background_image[[:space:]]*=' "$CONFIG_FILE"; then
    return
  fi

  TMP_CONFIG="$CONFIG_FILE.tmp"
  awk -v image="$DROPZONE_ASSET" '
    BEGIN { in_launch = 0; saw_launch = 0; inserted = 0 }
    /^\[launch\][[:space:]]*$/ {
      in_launch = 1
      saw_launch = 1
      print
      next
    }
    /^\[/ && in_launch {
      if (!inserted) {
        print "finder_background_image = \"" image "\""
        inserted = 1
      }
      in_launch = 0
    }
    in_launch && /^[[:space:]]*#[[:space:]]*finder_background_image[[:space:]]*=/ {
      print "finder_background_image = \"" image "\""
      inserted = 1
      next
    }
    { print }
    END {
      if (in_launch && !inserted) {
        print "finder_background_image = \"" image "\""
        inserted = 1
      }
      if (!saw_launch) {
        print ""
        print "[launch]"
        print "finder_background_image = \"" image "\""
      }
    }
  ' "$CONFIG_FILE" > "$TMP_CONFIG"
  mv "$TMP_CONFIG" "$CONFIG_FILE"
  echo "Configured Finder dropzone image in $CONFIG_FILE"
}

mkdir -p "$APP_HOME" "$ASSET_DIR" "$BIN_DIR"
cp "$REPO_ROOT/assets/dropzone-target-finder.png" "$DROPZONE_ASSET"

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
configure_dropzone_image

echo "Installed samplewatch:"
echo "  $LINK -> $VENV/bin/samplewatch"
echo "  dropzone image: $DROPZONE_ASSET"

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
