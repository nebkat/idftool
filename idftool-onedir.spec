# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = []
datas += collect_data_files('esptool')
# The eFuse definitions of each chip, for the espefuse command.
datas += collect_data_files('espefuse')


a = Analysis(
    ['idftool.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    # idftool.fs and its backends are imported lazily inside the fs commands, to keep them
    # off the startup path; name them so the analysis can't miss them.
    # bitstring (under espefuse) picks its backend module at runtime.
    hiddenimports=['idftool.fs', 'idftool.fs.fatfs', 'idftool.fs.littlefs', 'idftool.fs.spiffs']
    + collect_submodules('bitstring'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='idftool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    contents_directory='idftool.internal',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='idftool',
)
