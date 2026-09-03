#!/usr/bin/env bash
#
# Build Move-In Triage.app and wrap it in a distributable .dmg.
#
#     ./build_dmg.sh
#
# Uses hdiutil, which ships with macOS -- no Homebrew, no extra tooling. The
# result is dist/Move-In-Triage-<version>.dmg containing the app plus the
# customary symlink to /Applications, so installing is a drag from left to right.
#
# What this does NOT do is sign or notarize. See the note at the bottom.

set -euo pipefail

APP_NAME="Move-In Triage"
VERSION="1.0.0"
VOLUME_NAME="Move-In Triage"
DMG_NAME="Move-In-Triage-${VERSION}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# --------------------------------------------------------------------------
# Preflight
# --------------------------------------------------------------------------

if [[ "$(uname)" != "Darwin" ]]; then
  echo "error: this builds a macOS app bundle and only runs on macOS." >&2
  exit 1
fi

if ! python3 -c "import py2app" 2>/dev/null; then
  echo "error: py2app is not installed." >&2
  echo "       pip install -r requirements-mac.txt" >&2
  exit 1
fi

# --------------------------------------------------------------------------
# 1. Build the .app
# --------------------------------------------------------------------------

echo "==> Cleaning previous build"
# Guarded rather than a bare rm -rf on a variable, so a mistyped path cannot
# take out something else.
[[ -d build ]] && rm -rf build
[[ -d dist ]] && rm -rf dist

echo "==> Building ${APP_NAME}.app"
python3 setup_app.py py2app

APP_PATH="dist/${APP_NAME}.app"
if [[ ! -d "$APP_PATH" ]]; then
  echo "error: expected $APP_PATH but py2app did not produce it." >&2
  exit 1
fi

# --------------------------------------------------------------------------
# 2. Stage the disk image contents
# --------------------------------------------------------------------------

echo "==> Staging disk image contents"
STAGING="$(mktemp -d)"
# Clean up the staging directory however this script exits, including on error.
trap 'rm -rf "$STAGING"' EXIT

cp -R "$APP_PATH" "$STAGING/"

# The /Applications symlink is what makes the familiar drag-to-install window
# work. Without it the user has to know to copy the app somewhere themselves.
ln -s /Applications "$STAGING/Applications"

# --------------------------------------------------------------------------
# 3. Create the compressed image
# --------------------------------------------------------------------------

echo "==> Creating ${DMG_NAME}.dmg"
DMG_PATH="dist/${DMG_NAME}.dmg"

# UDZO is the standard compressed read-only format: smaller download, and the
# contents cannot be modified in place, which is what you want for a release.
hdiutil create \
  -volname "$VOLUME_NAME" \
  -srcfolder "$STAGING" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

SIZE="$(du -h "$DMG_PATH" | cut -f1)"

echo
echo "Built $DMG_PATH ($SIZE)"
echo

# --------------------------------------------------------------------------
# Signing -- read this before sending the file to anyone
# --------------------------------------------------------------------------
cat <<'NOTE'
This build is unsigned and un-notarized.

On your own machine it runs fine: right-click the app the first time and choose
Open, and macOS remembers the decision.

On someone else's machine, if they receive it by download, macOS attaches a
quarantine flag and Gatekeeper will refuse it with "cannot be opened because the
developer cannot be verified". They can still right-click -> Open, but you should
warn them, because the default reaction to that dialog is to assume the file is
malware.

To remove the warning entirely you need an Apple Developer account (99 USD/year)
and these two steps after building:

    codesign --deep --force --options runtime \
      --sign "Developer ID Application: YOUR NAME (TEAMID)" \
      "dist/Move-In Triage.app"

    xcrun notarytool submit "dist/Move-In-Triage-1.0.0.dmg" \
      --apple-id you@example.com --team-id TEAMID --wait
    xcrun stapler staple "dist/Move-In-Triage-1.0.0.dmg"

Worth deciding before you promise anyone a copy: for one or two colleagues,
a warning plus a heads-up is usually fine. For wider distribution it is not.
NOTE
