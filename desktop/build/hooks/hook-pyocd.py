"""PyInstaller hook for pyocd.

Collects:
- Package metadata (entry_points.txt) so importlib_metadata can find pyocd
- Built-in target definitions (loaded dynamically by pyocd)
- Data files (board IDs, SVD files, etc.)

Safe to skip if pyocd is not installed.
"""

try:
    from PyInstaller.utils.hooks import (
        collect_data_files,
        collect_submodules,
        copy_metadata,
    )

    datas = copy_metadata('pyocd')
    datas += collect_data_files('pyocd')
    hiddenimports = collect_submodules('pyocd')
except Exception:
    datas = []
    hiddenimports = []
