# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MCUHex SDK tray application."""

import os
import platform

block_cipher = None
_system = platform.system()

# Resolve absolute paths from spec file location
_spec_dir = os.path.dirname(os.path.abspath(SPEC))
_desktop_dir = os.path.normpath(os.path.join(_spec_dir, '..'))
_project_root = os.path.normpath(os.path.join(_spec_dir, '..', '..'))

# Add project root to sys.path so PyInstaller can find local packages (probe, server)
import sys
sys.path.insert(0, _project_root)
sys.path.insert(0, _desktop_dir)

# Collect local packages that aren't pip-installed
from PyInstaller.utils.hooks import collect_submodules
_local_modules = collect_submodules('probe') + ['server']


# ---------------------------------------------------------------------------
# Only add hiddenimports for packages that are actually installed.
# Prior builds crashed because PyInstaller's hooks tried to process
# modules (numpy, aioserial, cmsis_pack_manager) that weren't present
# or whose hook was incompatible with the installed version.
# ---------------------------------------------------------------------------
def _module_exists(name):
    """Return True if a top-level module can be imported in the build env."""
    import importlib
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def _safe_collect(name):
    """collect_submodules only if the package exists; empty list otherwise."""
    if _module_exists(name):
        return collect_submodules(name)
    print(f"  [spec] Skipping collect_submodules('{name}') — not installed")
    return []


_serial_modules = _safe_collect('serial')

_optional_hidden = []
for _mod in ['aioserial', 'cmsis_pack_manager', 'importlib_metadata']:
    if _module_exists(_mod):
        _optional_hidden.append(_mod)
    else:
        print(f"  [spec] Skipping hiddenimport '{_mod}' — not installed")

# cmsis_pack_manager has a sub-module with the native FFI lib
if _module_exists('cmsis_pack_manager'):
    _optional_hidden.append('cmsis_pack_manager.cmsis_pack_manager')

# pyocd probe plugins — only if pyocd is installed
_pyocd_hidden = []
if _module_exists('pyocd'):
    _pyocd_hidden = [
        'pyocd.probe.stlink_probe',
        'pyocd.probe.stlink',
        'pyocd.probe.jlink_probe',
        'pyocd.probe.cmsis_dap_probe',
        'pyocd.probe.picoprobe',
        'pyocd.probe.tcp_client_probe',
        'pyocd.target.builtin',
        'pyocd.target.pack.cmsis_pack',
        'pyocd.target.pack.pack_target',
    ]
else:
    print("  [spec] Skipping pyocd hiddenimports — not installed")


# Platform-specific hidden imports, icon, and UPX flag
if _system == 'Windows':
    _platform_hidden = ['pystray._win32', 'serial.tools.list_ports_windows']
    _icon_file = 'icon.ico'
    _upx = False  # UPX + unsigned Windows exe triggers SmartScreen/AV false positives
elif _system == 'Darwin':
    _platform_hidden = ['pystray._darwin', 'serial.tools.list_ports_posix']
    _icon_file = 'icon.icns'
    _upx = True
else:
    _platform_hidden = ['pystray._xorg', 'serial.tools.list_ports_linux']
    _icon_file = 'icon.png'
    _upx = True

a = Analysis(
    [os.path.join(_desktop_dir, 'tray_app.py')],
    pathex=[_desktop_dir, _project_root],
    binaries=[],
    datas=[
        (os.path.join(_desktop_dir, 'resources'), 'resources'),
    ],
    hiddenimports=_local_modules + _serial_modules + _platform_hidden + _pyocd_hidden + _optional_hidden + [
        'serial',
        'serial.tools',
        'serial.tools.list_ports',
    ],
    hookspath=['hooks/'] if os.path.isdir(os.path.join(_spec_dir, 'hooks')) else [],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'scipy',
        'numpy',           # built-in hook needs _PyInstaller_hooks_0_numpy which is often
                            # missing or version-incompatible; only used by DummyProbe
                            # (demo waveforms) — real probes don't need it
        'PIL.ImageTk',
        'test',
        'unittest',
        'xmlrpc',
        'doctest',
        # 'pdb',  # needed by pyelftools (via pyocd)
    ],
    noarchive=False,
    optimize=0,
)

# PyInstaller's module finder can't resolve our local 'probe' package submodules.
# Force-inject them into the module table, replacing any broken entries.
_probe_dir = os.path.join(_project_root, 'probe')
for _fname in os.listdir(_probe_dir):
    if _fname.endswith('.py'):
        _modname = 'probe.' + _fname[:-3] if _fname != '__init__.py' else 'probe'
        _fpath = os.path.join(_probe_dir, _fname)
        # Remove existing broken entry if present
        a.pure[:] = [(n, p, t) for (n, p, t) in a.pure if n != _modname]
        a.pure.append((_modname, _fpath, 'PYMODULE'))

_server_path = os.path.join(_project_root, 'server.py')
a.pure[:] = [(n, p, t) for (n, p, t) in a.pure if n != 'server']
a.pure.append(('server', _server_path, 'PYMODULE'))

# Debug: verify probe modules in a.pure before PYZ
_probe_entries = [(n, p) for (n, p, t) in a.pure if n.startswith('probe')]
print(f"=== probe entries in a.pure ({len(_probe_entries)}) ===")
for n, p in _probe_entries:
    _size = os.path.getsize(p) if os.path.exists(p) else -1
    print(f"  {n}: {p} ({_size} bytes)")

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

_icon_path = os.path.join(_desktop_dir, 'resources', _icon_file)
_icon = _icon_path if os.path.exists(_icon_path) else None

# Windows PE version resource (Properties → Details fields on MCUHex.exe)
_version_file = os.path.join(_spec_dir, 'version_info.txt')
_win_version = _version_file if (_system == 'Windows' and os.path.exists(_version_file)) else None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # onedir mode — binaries go into COLLECT
    name='MCUHex',
    icon=_icon,
    version=_win_version,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=_upx,
    console=False,           # No terminal window (tray app)
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=_upx,
    upx_exclude=[],
    name='MCUHex',
)

# macOS .app bundle
if _system == 'Darwin':
    _icns_path = os.path.join(_desktop_dir, 'resources', 'icon.icns')
    app = BUNDLE(
        coll,
        name='MCUHex.app',
        icon=_icns_path if os.path.exists(_icns_path) else _icon,
        bundle_identifier='com.mcuhex.sdk',
        info_plist={
            'CFBundleDisplayName': 'MCUHex',
            'CFBundleShortVersionString': '0.0.1',
            'LSUIElement': True,  # Hide from Dock (tray-only app)
            'NSHighResolutionCapable': True,
        },
    )
