#!/bin/bash
#
# Double-click this file to install Move-In Triage.
#
# It builds a self-contained Move-In Triage.app -- Python environment and all --
# and puts it in your Applications folder. Nothing is installed system-wide and
# nothing outside the app bundle is touched.
#
# Deliberately NOT using py2app. py2app freezes the interpreter, which means
# hunting down hidden imports and debugging a bootstrapper when something is
# missing. Creating a normal virtualenv inside the bundle is far more predictable:
# if `pip install` works, the app works. The bundle is a few MB larger. That is a
# good trade for something you need to actually run rather than admire.

set -euo pipefail

APP_NAME="Move-In Triage"
BUNDLE_ID="com.yujiyoshida.moveintriage"
VERSION="1.0.0"

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT="$HOME/Library/Application Support/MoveInTriage"

# Keep the window open long enough to read if something fails.
trap 'echo; echo "Install did not finish. The message above says why."; echo "Press return to close."; read -r' ERR

echo
echo "  Move-In Triage — installer"
echo "  =========================="
echo

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

if [[ "$(uname)" != "Darwin" ]]; then
  echo "  This installs a macOS app and only runs on macOS." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "  Python 3 is not installed." >&2
  echo "  Run this in Terminal to get it, then try again:" >&2
  echo "      xcode-select --install" >&2
  exit 1
fi

PYVER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "  Using Python $PYVER at $(command -v python3)"

# 3.10+ for the `X | None` type syntax used throughout.
python3 - <<'PYCHECK'
import sys
if sys.version_info < (3, 10):
    sys.exit(
        "  Python 3.10 or newer is required (found %d.%d)."
        % sys.version_info[:2]
    )
PYCHECK

# ---------------------------------------------------------------------------
# Where to install
# ---------------------------------------------------------------------------

# Prefer /Applications, but fall back to ~/Applications rather than demanding a
# password. A per-user install works identically.
if [[ -w /Applications ]]; then
  DEST="/Applications"
else
  DEST="$HOME/Applications"
  mkdir -p "$DEST"
  echo "  /Applications is not writable — installing to ~/Applications instead."
fi

APP="$DEST/$APP_NAME.app"
echo "  Installing to $APP"
echo

# ---------------------------------------------------------------------------
# Build the bundle
# ---------------------------------------------------------------------------

# Stop any running copy first, or the old process keeps its status item and you
# end up with two.
if pgrep -f "Move-In Triage.app/Contents/Resources/app/menubar.py" >/dev/null 2>&1; then
  echo "==> Stopping running copy"
  pkill -f "Move-In Triage.app/Contents/Resources/app/menubar.py" || true
  sleep 1
fi

# Reuse the Python environment if one is already there. Building it takes several
# minutes; replacing the code takes a second. Reinstalling to pick up a code
# change should not cost the former.
REUSE_VENV=0
if [[ -x "$APP/Contents/Resources/venv/bin/python" ]]; then
  REUSE_VENV=1
  echo "==> Reusing existing Python environment"
  rm -rf "$APP/Contents/Resources/app" "$APP/Contents/MacOS"
elif [[ -d "$APP" ]]; then
  echo "==> Removing previous version"
  rm -rf "$APP"
fi

echo "==> Creating app bundle"
mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources/app"

# Copy the source.
#
# Every .py in the folder except the test suite, rather than a hand-maintained
# list. The list version silently shipped a broken app the first time a new
# module was added: triggers.py was left behind, the app failed to import it, and
# the only symptom was an error glyph in the menu bar. A glob cannot forget.
shopt -s nullglob
copied=0
for f in "$SRC"/*.py; do
  base="$(basename "$f")"
  [[ "$base" == test_*.py ]] && continue
  [[ "$base" == setup_app.py ]] && continue
  cp "$f" "$APP/Contents/Resources/app/"
  copied=$((copied + 1))
done
shopt -u nullglob

if [[ ! -f "$APP/Contents/Resources/app/menubar.py" ]]; then
  echo "  menubar.py not found in $SRC" >&2
  echo "  Run this installer from inside the movein-triage folder." >&2
  exit 1
fi
echo "    copied $copied modules"

if [[ "$REUSE_VENV" == "0" ]]; then
  echo "==> Creating Python environment (this takes a few minutes)"
  # Created directly at its final location: virtualenvs bake in absolute paths,
  # so building elsewhere and copying would produce a subtly broken environment.
  python3 -m venv "$APP/Contents/Resources/venv"
fi

VENV_PY="$APP/Contents/Resources/venv/bin/python"

echo "==> Installing dependencies"
"$VENV_PY" -m pip install --quiet --upgrade pip
# No rumps: it silently fails to create a status item on Python 3.14 -- the
# process runs but nothing appears in the menu bar. menubar.py talks to
# NSStatusBar through pyobjc directly instead, which needs only Cocoa and WebKit.
"$VENV_PY" -m pip install --quiet \
  "msal>=1.28,<2" \
  "requests>=2.31,<3" \
  "pyobjc-framework-Cocoa>=9.0" \
  "pyobjc-framework-WebKit>=9.0"

# ---------------------------------------------------------------------------
# Info.plist
# ---------------------------------------------------------------------------

echo "==> Writing bundle metadata"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>              <string>$APP_NAME</string>
    <key>CFBundleDisplayName</key>       <string>$APP_NAME</string>
    <key>CFBundleIdentifier</key>        <string>$BUNDLE_ID</string>
    <key>CFBundleVersion</key>           <string>$VERSION</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundlePackageType</key>       <string>APPL</string>
    <key>CFBundleExecutable</key>        <string>MoveInTriage</string>

    <!-- LSUIElement is deliberately FALSE.
         menubar.py calls setActivationPolicy_(Accessory) at startup, which has
         the same effect: menu bar only, no Dock icon. Doing it at runtime rather
         than here is not redundancy, it is the difference between working and
         not. With LSUIElement true, a copy launched through LaunchServices (i.e.
         double-clicked, the normal way) runs happily and never draws its status
         item, while the identical binary launched directly from a shell draws it
         fine. Setting the policy in code covers both launch paths. -->
    <key>LSUIElement</key>               <false/>

    <key>NSHighResolutionCapable</key>   <true/>
    <key>LSMinimumSystemVersion</key>    <string>11.0</string>
</dict>
</plist>
PLIST

# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------

cat > "$APP/Contents/MacOS/MoveInTriage" <<'LAUNCHER'
#!/bin/bash
# Bundle launcher. Runs the menu bar app from the embedded environment.
HERE="$(cd "$(dirname "$0")/.." && pwd)"
RES="$HERE/Resources"

# Credentials, if configured. Absent on a fresh install, which is fine -- the app
# falls back to demo mode below rather than failing at a sign-in prompt.
CONF="$HOME/Library/Application Support/MoveInTriage/env"
if [ -f "$CONF" ]; then
  set -a
  . "$CONF"
  set +a
fi

if [ -n "${MOVEIN_CLIENT_ID:-}" ] && [ -n "${MOVEIN_TENANT_ID:-}" ]; then
  exec "$RES/venv/bin/python" "$RES/app/menubar.py"
else
  exec "$RES/venv/bin/python" "$RES/app/menubar.py" --demo
fi
LAUNCHER

chmod +x "$APP/Contents/MacOS/MoveInTriage"

# ---------------------------------------------------------------------------
# Credentials template
# ---------------------------------------------------------------------------

mkdir -p "$SUPPORT"
if [[ ! -f "$SUPPORT/env" ]]; then
  cat > "$SUPPORT/env" <<'ENVFILE'
# Move-In Triage — credentials.
#
# While these are blank the app runs in DEMO mode against sample data.
# Fill both in and restart the app to point it at the live Master Sheet.
#
# Neither value is a secret: they identify an Azure app registration and a
# tenant. This app uses a public-client device-code flow and has no client
# secret. See .env.example in the source folder for how to obtain them.

MOVEIN_CLIENT_ID=
MOVEIN_TENANT_ID=
ENVFILE
  chmod 600 "$SUPPORT/env"
fi

# Built locally, so it was never quarantined -- but strip the attribute if some
# earlier copy of the source carried one, so Gatekeeper stays quiet.
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Launch agent
# ---------------------------------------------------------------------------
#
# The app is started by a LaunchAgent rather than by double-clicking the bundle.
# That is not a workaround dressed up as a design: a menu bar utility is exactly
# what LaunchAgents are for, and it means the thing starts itself at login
# instead of waiting to be remembered.
#
# It also happens to be the launch path that works. On macOS 26, a copy started
# through LaunchServices -- double-clicked, the ordinary way -- runs perfectly
# and never draws its status item. The identical code started as a plain process
# draws it immediately. Hours of evidence behind that sentence; the LaunchAgent
# starts it as a plain process.

echo "==> Installing launch agent (starts at login)"

AGENT_DIR="$HOME/Library/LaunchAgents"
AGENT="$AGENT_DIR/$BUNDLE_ID.plist"
mkdir -p "$AGENT_DIR"

# Unload any previous version before rewriting, or launchd keeps the old one.
launchctl unload "$AGENT" 2>/dev/null || true

cat > "$AGENT" <<AGENTPLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$BUNDLE_ID</string>

    <key>ProgramArguments</key>
    <array>
        <string>$APP/Contents/MacOS/MoveInTriage</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <!-- Bring it back if it dies, but not in a tight loop if it dies at once. -->
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>30</integer>

    <!-- Somewhere to look when something goes wrong. -->
    <key>StandardOutPath</key>
    <string>$SUPPORT/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$SUPPORT/stderr.log</string>
</dict>
</plist>
AGENTPLIST

mkdir -p "$SUPPORT"

# Stop anything already running before launchd starts its own copy, so there is
# exactly one status item rather than two.
pkill -f "Resources/app/menubar.py" 2>/dev/null || true
sleep 1

launchctl load "$AGENT" 2>&1 || echo "  (launchctl load reported an issue — see above)"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

trap - ERR

echo
echo "  Installed: $APP"
echo
echo "  It is running in DEMO mode with sample data — you will see two units"
echo "  flagged as halted, which is what the sample contains."
echo
echo "  To point it at the real Master Sheet, fill in the two values here:"
echo "      $SUPPORT/env"
echo "  then quit and reopen the app."
echo
echo "  Look for the menu bar icon (top right), not the Dock — this is a menu"
echo "  bar utility and has no Dock icon by design."
echo

# Already started by launchctl above -- no need to open the bundle, and opening
# it would start a second copy.

sleep 2
if pgrep -f "Resources/app/menubar.py" >/dev/null 2>&1; then
  echo "  Running — look for the checklist icon in the menu bar (top right)."
else
  echo "  WARNING: it does not appear to be running. Check:"
  echo "      $SUPPORT/stderr.log"
fi

echo
echo "  It starts automatically at login from now on."
echo "  To stop it permanently:  launchctl unload ~/Library/LaunchAgents/$BUNDLE_ID.plist"
echo
echo "  You can close this window."
echo
