"""Windows self-update: download zip, spawn detached .bat helper, exit.

The helper waits for MCUHex.exe to exit, swaps the folder with robocopy,
relaunches the new exe, and deletes itself.
"""

import glob as _glob
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from desktop.config import APP_NAME, APP_SUPPORT_DIR

LOG = logging.getLogger(__name__)

# Windows process creation flags — fully detach the helper so it survives parent exit
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_DETACHED_PROCESS = 0x00000008
_CREATE_NO_WINDOW = 0x08000000


def _get_install_dir() -> str | None:
    """Return the folder containing MCUHex.exe, or None in dev mode."""
    if not getattr(sys, "frozen", False):
        return None
    # PyInstaller onedir: sys.executable = <install>\MCUHex\MCUHex.exe
    return os.path.dirname(sys.executable)


def apply_update_windows(asset_url: str, notify_cb=None) -> None:
    install_dir = _get_install_dir()
    if install_dir is None:
        LOG.warning("Cannot self-update: not running as a frozen build")
        if notify_cb:
            notify_cb("Self-update is only available in the packaged app.", APP_NAME)
        return

    # Refuse if install dir is read-only (e.g., extracted into Program Files without admin)
    if not os.access(install_dir, os.W_OK):
        LOG.error("Install dir %s is not writable; aborting update", install_dir)
        if notify_cb:
            notify_cb(
                "Update failed: MCUHex is installed in a read-only location. "
                "Move the MCUHex folder to a user-writable path "
                "(e.g. %LOCALAPPDATA%\\Programs\\MCUHex) and retry.",
                APP_NAME,
            )
        return

    staging_root = os.path.join(APP_SUPPORT_DIR, "update")
    os.makedirs(staging_root, exist_ok=True)

    # Clear any previous staging (failed prior updates, etc.)
    for old in _glob.glob(os.path.join(staging_root, "*")):
        try:
            if os.path.isdir(old):
                shutil.rmtree(old, ignore_errors=True)
            else:
                os.remove(old)
        except OSError:
            pass

    try:
        if notify_cb:
            notify_cb("Downloading update...", APP_NAME)
        zip_path = os.path.join(staging_root, "update.zip")
        LOG.info("Downloading update from %s", asset_url)
        urllib.request.urlretrieve(asset_url, zip_path)

        extract_dir = tempfile.mkdtemp(prefix="mcuhex_", dir=staging_root)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)

        # Find MCUHex.exe anywhere in the extracted tree — its parent is the folder to promote
        exes = _glob.glob(os.path.join(extract_dir, "**", "MCUHex.exe"), recursive=True)
        if not exes:
            raise RuntimeError("No MCUHex.exe found in downloaded archive")
        new_dir = os.path.dirname(exes[0])

        bat_path = os.path.join(APP_SUPPORT_DIR, "apply_update.bat")
        # @chcp 65001 switches CMD to UTF-8 so non-ASCII paths work (e.g., C:\Users\Ömer\...)
        bat_contents = (
            "@echo off\r\n"
            "@chcp 65001 > nul\r\n"
            ":wait\r\n"
            'tasklist /FI "IMAGENAME eq MCUHex.exe" 2>nul | find /I "MCUHex.exe" > nul\r\n'
            "if not errorlevel 1 (\r\n"
            "    timeout /t 1 /nobreak > nul\r\n"
            "    goto wait\r\n"
            ")\r\n"
            f'robocopy "{new_dir}" "{install_dir}" /MIR /R:3 /W:2 /NFL /NDL /NJH /NJS /NC /NS > nul\r\n'
            f'start "" "{install_dir}\\MCUHex.exe"\r\n'
            f'rmdir /S /Q "{extract_dir}" 2> nul\r\n'
            'del "%~f0"\r\n'
        )
        with open(bat_path, "w", encoding="utf-8") as f:
            f.write(bat_contents)

        if notify_cb:
            notify_cb("Update staged. Restarting...", APP_NAME)

        # Fully detach so the helper survives our exit
        subprocess.Popen(
            ["cmd.exe", "/c", bat_path],
            creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW,
            close_fds=True,
        )
        sys.exit(0)

    except Exception as e:
        LOG.error("Update failed: %s", e, exc_info=True)
        if notify_cb:
            notify_cb(f"Update failed: {e}", APP_NAME)
