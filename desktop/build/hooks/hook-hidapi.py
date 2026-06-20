"""PyInstaller hook for hidapi.

Collects the platform-specific native shared library that hidapi wraps.
Safe to skip if hidapi is not installed.
"""

try:
    from PyInstaller.utils.hooks import collect_dynamic_libs
    binaries = collect_dynamic_libs('hid')
    hiddenimports = ['hid']
except Exception:
    binaries = []
    hiddenimports = []
