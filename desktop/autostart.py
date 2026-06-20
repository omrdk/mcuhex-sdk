"""Cross-platform autostart management for MCUHex tray app.

macOS: LaunchAgent plist in ~/Library/LaunchAgents/
Windows: HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run registry value
"""

import logging
import os
import sys

from desktop.config import APP_NAME, BUNDLE_ID

LOG = logging.getLogger(__name__)


def _get_executable_path() -> str:
    if getattr(sys, 'frozen', False):
        return sys.executable
    return os.path.abspath(sys.argv[0])


# ---------------------------------------------------------------------------
# macOS — LaunchAgent plist
# ---------------------------------------------------------------------------

def _mac_plist_path() -> str:
    return os.path.expanduser(f"~/Library/LaunchAgents/{BUNDLE_ID}.plist")


def _mac_is_enabled() -> bool:
    return os.path.exists(_mac_plist_path())


def _mac_set(enabled: bool) -> None:
    path = _mac_plist_path()
    if enabled:
        exe = _get_executable_path()
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{BUNDLE_ID}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(plist)
        LOG.info(f"Autostart enabled: {path}")
    else:
        if os.path.exists(path):
            os.remove(path)
            LOG.info(f"Autostart disabled: removed {path}")


# ---------------------------------------------------------------------------
# Windows — HKCU Run registry value
# ---------------------------------------------------------------------------

_WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _win_is_enabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except FileNotFoundError:
        return False


def _win_set(enabled: bool) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            exe = _get_executable_path()
            # Quote the path — registry Run values are command-line strings; paths may contain spaces
            value = f'"{exe}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, value)
            LOG.info(f"Autostart enabled: HKCU\\{_WIN_RUN_KEY}\\{APP_NAME} = {value}")
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
                LOG.info(f"Autostart disabled: removed HKCU\\{_WIN_RUN_KEY}\\{APP_NAME}")
            except FileNotFoundError:
                pass


def _win_stored_path() -> str | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
        return value.strip('"')
    except FileNotFoundError:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_autostart_enabled() -> bool:
    if sys.platform == "win32":
        return _win_is_enabled()
    return _mac_is_enabled()


def set_autostart(enabled: bool) -> None:
    if sys.platform == "win32":
        _win_set(enabled)
    else:
        _mac_set(enabled)


def self_heal_autostart_path() -> None:
    """If autostart is enabled but points to a stale path, rewrite it.

    Handles the case where the user moved/renamed the app folder after
    enabling autostart. Safe to call on every startup.
    """
    if not is_autostart_enabled():
        return
    current = _get_executable_path()
    if sys.platform == "win32":
        stored = _win_stored_path()
        if stored and os.path.normcase(stored) != os.path.normcase(current):
            LOG.info(f"Autostart path stale (was {stored}, now {current}); updating")
            _win_set(True)
    else:
        # macOS plist is regenerated from scratch on every set_autostart;
        # rewrite it with the current exe path.
        _mac_set(True)
