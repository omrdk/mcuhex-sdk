"""Version check and self-update against GitHub Releases."""

import glob as _glob
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
import zipfile
from typing import NamedTuple

from desktop.config import (
    APP_NAME,
    APP_SUPPORT_DIR,
    ASSET_SUFFIX,
    RELEASES_API,
    RELEASES_URL,
    UPDATE_CHECK_INTERVAL,
    VERSION,
)

LOG = logging.getLogger(__name__)


class UpdateInfo(NamedTuple):
    version: str
    html_url: str
    asset_url: str | None


def _parse_version(tag: str) -> tuple:
    """Parse 'v1.2.3' or '1.2.3' into a comparable tuple."""
    tag = tag.lstrip("v")
    parts = []
    for p in tag.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _find_zip_asset(release_data: dict) -> str | None:
    """Find the platform-appropriate zip asset URL from release data."""
    for asset in release_data.get("assets", []):
        name = asset.get("name", "")
        if name.endswith(ASSET_SUFFIX) and name.startswith("MCUHex-"):
            return asset.get("browser_download_url")
    return None


def check_for_update() -> UpdateInfo | None:
    """Check GitHub for a newer release.

    Returns UpdateInfo if an update is available, else None.
    """
    try:
        req = urllib.request.Request(
            RELEASES_API,
            headers={"Accept": "application/vnd.github.v3+json",
                     "User-Agent": "MCUHex-Updater"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        tag = data.get("tag_name", "")
        remote = _parse_version(tag)
        local = _parse_version(VERSION)

        if remote > local:
            html_url = data.get("html_url", RELEASES_URL)
            asset_url = _find_zip_asset(data)
            LOG.info("Update available: %s (current: v%s)", tag, VERSION)
            return UpdateInfo(tag.lstrip("v"), html_url, asset_url)

        LOG.info("Up to date: v%s", VERSION)
        return None

    except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
        LOG.warning("Update check failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Auto-check cooldown
# ---------------------------------------------------------------------------

_LAST_CHECK_FILE = os.path.join(APP_SUPPORT_DIR, "last_update_check")


def _record_check() -> None:
    os.makedirs(APP_SUPPORT_DIR, exist_ok=True)
    with open(_LAST_CHECK_FILE, "w") as f:
        f.write(str(time.time()))


def should_auto_check() -> bool:
    """Return True if enough time has passed since the last update check."""
    try:
        with open(_LAST_CHECK_FILE) as f:
            last = float(f.read().strip())
        return (time.time() - last) >= UPDATE_CHECK_INTERVAL
    except (FileNotFoundError, ValueError):
        return True


def auto_check_for_update() -> UpdateInfo | None:
    """Check for updates if the cooldown has elapsed. Records the check time."""
    if not should_auto_check():
        return None
    _record_check()
    return check_for_update()


# ---------------------------------------------------------------------------
# Self-replacing update (frozen builds only)
# ---------------------------------------------------------------------------

def _get_current_app_path() -> str | None:
    """Return the path to the running .app bundle, or None in dev mode."""
    if not getattr(sys, "frozen", False):
        return None
    # sys.executable is e.g. /Applications/MCUHex.app/Contents/MacOS/MCUHex
    macos_dir = os.path.dirname(sys.executable)          # .../Contents/MacOS
    contents_dir = os.path.dirname(macos_dir)             # .../Contents
    app_dir = os.path.dirname(contents_dir)               # .../MCUHex.app
    if app_dir.endswith(".app"):
        return app_dir
    return None


def download_and_apply_update(asset_url: str, notify_cb=None) -> None:
    """Download a zip release asset, replace the current install, and relaunch.

    Platform dispatch:
      - Windows: spawns a detached .bat helper (see updater_win.py)
      - macOS: swaps the .app bundle in-place (below)

    Args:
        asset_url: Direct download URL for the .zip asset.
        notify_cb: Optional callback(message, title) for user notifications.
    """
    if sys.platform == "win32":
        from desktop.updater_win import apply_update_windows
        apply_update_windows(asset_url, notify_cb)
        return

    current_app = _get_current_app_path()
    if current_app is None:
        LOG.warning("Cannot self-update: not running as a frozen .app bundle")
        if notify_cb:
            notify_cb("Self-update is only available in the packaged app.", APP_NAME)
        return

    tmp_dir = tempfile.mkdtemp(prefix="mcuhex_update_")
    backup_path = current_app + ".bak"

    try:
        # Download
        if notify_cb:
            notify_cb("Downloading update...", APP_NAME)
        zip_path = os.path.join(tmp_dir, "update.zip")
        LOG.info("Downloading update from %s", asset_url)
        urllib.request.urlretrieve(asset_url, zip_path)

        # Extract
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_dir)

        # Find the .app in extracted contents
        apps = _glob.glob(os.path.join(tmp_dir, "**", "*.app"), recursive=True)
        if not apps:
            raise RuntimeError("No .app found in downloaded archive")
        new_app = apps[0]

        # Replace: backup current, move new into place
        if os.path.exists(backup_path):
            shutil.rmtree(backup_path)
        LOG.info("Backing up %s -> %s", current_app, backup_path)
        shutil.move(current_app, backup_path)

        LOG.info("Installing %s -> %s", new_app, current_app)
        shutil.move(new_app, current_app)

        # Ensure the main binary is executable
        binary = os.path.join(current_app, "Contents", "MacOS", "MCUHex")
        if os.path.exists(binary):
            os.chmod(binary, 0o755)

        if notify_cb:
            notify_cb("Update installed. Restarting...", APP_NAME)

        # Relaunch and exit
        subprocess.Popen(["open", current_app])
        sys.exit(0)

    except PermissionError:
        LOG.error("Permission denied updating %s", current_app)
        # Rollback
        if os.path.exists(backup_path) and not os.path.exists(current_app):
            shutil.move(backup_path, current_app)
        if notify_cb:
            notify_cb(
                "Update failed: permission denied. "
                "Try moving the app to a location you own.",
                APP_NAME,
            )
    except Exception as e:
        LOG.error("Update failed: %s", e, exc_info=True)
        # Rollback
        if os.path.exists(backup_path) and not os.path.exists(current_app):
            shutil.move(backup_path, current_app)
        if notify_cb:
            notify_cb(f"Update failed: {e}", APP_NAME)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
