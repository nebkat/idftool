# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

datas = []
datas += collect_data_files('esptool')


a = Analysis(
    ['idftool.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    # idftool.fs and its backends are imported lazily inside the fs commands, to keep them
    # off the startup path; name them so the analysis can't miss them.
    hiddenimports=['idftool.fs', 'idftool.fs.fatfs', 'idftool.fs.littlefs', 'idftool.fs.spiffs'],
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
    a.binaries,
    a.datas,
    [],
    name='idftool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
