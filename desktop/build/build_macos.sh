#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DESKTOP_DIR="$PROJECT_ROOT/desktop"
BUILD_DIR="$SCRIPT_DIR"
DIST_DIR="$BUILD_DIR/dist"

echo "=== MCUHex macOS Build ==="

# Clean previous build
rm -rf "$BUILD_DIR/build" "$DIST_DIR"

# Use project venv
VENV_DIR="$PROJECT_ROOT/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "Error: Project venv not found at $VENV_DIR"
    echo "Run: cd $PROJECT_ROOT && python3 -m venv .venv && pip install -r requirements.txt"
    exit 1
fi
source "$VENV_DIR/bin/activate"

pip install "$PROJECT_ROOT" --no-deps --force-reinstall -q
pip install -r "$DESKTOP_DIR/requirements-desktop.txt"

# Run PyInstaller
cd "$BUILD_DIR"
pyinstaller mcuhex.spec --noconfirm

echo "Build complete: $DIST_DIR/MCUHex.app"

# Code signing (optional — set DEVELOPER_ID env var)
if [ -n "${DEVELOPER_ID:-}" ]; then
    codesign --deep --force \
        --sign "$DEVELOPER_ID" \
        --entitlements "$BUILD_DIR/entitlements.plist" \
        --options runtime \
        "$DIST_DIR/MCUHex.app"
    echo "Signed with: $DEVELOPER_ID"
fi

# Read version from config
VERSION=$(python3 -c "from desktop.config import VERSION; print(VERSION)")

# Create zip (ditto preserves macOS metadata and code signatures)
cd "$DIST_DIR"
ditto -c -k --keepParent "MCUHex.app" "MCUHex-${VERSION}-macos-arm64.zip"

# Clean up intermediate build artifacts — only the zip is needed
rm -rf "$DIST_DIR/MCUHex" "$DIST_DIR/MCUHex.app"

echo "ZIP created: $DIST_DIR/MCUHex-${VERSION}-macos-arm64.zip"

# Notarization (optional — set APPLE_ID, TEAM_ID, APP_PASSWORD env vars)
if [ -n "${APPLE_ID:-}" ] && [ -n "${TEAM_ID:-}" ] && [ -n "${APP_PASSWORD:-}" ]; then
    xcrun notarytool submit "$DIST_DIR/MCUHex-${VERSION}-macos-arm64.zip" \
        --apple-id "$APPLE_ID" \
        --team-id "$TEAM_ID" \
        --password "$APP_PASSWORD" \
        --wait
    xcrun stapler staple "$DIST_DIR/MCUHex.app"
    echo "Notarization complete"
fi

echo "=== Done ==="
