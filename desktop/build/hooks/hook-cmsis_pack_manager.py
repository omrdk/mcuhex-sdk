"""PyInstaller hook for cmsis_pack_manager.

Collects the native Rust-based FFI library used by pyocd for CMSIS pack management.
Safe to skip if cmsis_pack_manager is not installed.
"""

try:
    from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files
    binaries = collect_dynamic_libs('cmsis_pack_manager')
    datas = collect_data_files('cmsis_pack_manager')
    hiddenimports = ['cmsis_pack_manager']
except Exception:
    binaries = []
    datas = []
    hiddenimports = []
