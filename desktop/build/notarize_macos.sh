#!/bin/bash
set -euo pipefail

# Usage: ./notarize_macos.sh <path-to-dmg>
#
# Required env vars:
#   DEVELOPER_ID  - signing identity (e.g. "Developer ID Application: Name (TEAMID)")
#   APPLE_ID      - Apple ID email
#   TEAM_ID       - Apple Developer Team ID
#   APP_PASSWORD  - app-specific password for notarytool

DMG_PATH="${1:?Usage: $0 <path-to-dmg>}"

if [ ! -f "$DMG_PATH" ]; then
    echo "Error: DMG not found at $DMG_PATH"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== MCUHex macOS Notarization ==="

# Code sign the DMG
if [ -n "${DEVELOPER_ID:-}" ]; then
    codesign --deep --force \
        --sign "$DEVELOPER_ID" \
        --entitlements "$SCRIPT_DIR/entitlements.plist" \
        --options runtime \
        "$DMG_PATH"
    echo "Signed: $DMG_PATH"
else
    echo "Warning: DEVELOPER_ID not set, skipping code signing"
fi

# Notarize
echo "Submitting for notarization..."
xcrun notarytool submit "$DMG_PATH" \
    --apple-id "$APPLE_ID" \
    --team-id "$TEAM_ID" \
    --password "$APP_PASSWORD" \
    --wait

# Staple
xcrun stapler staple "$DMG_PATH"

echo "=== Notarization complete: $DMG_PATH ==="
