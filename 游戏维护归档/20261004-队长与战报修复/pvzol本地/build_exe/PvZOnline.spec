# -*- mode: python ; coding: utf-8 -*-
# pathex uses absolute paths so gm / player_store / admin_server are always found.
import os

_SRV = os.path.normpath(os.path.join(SPECPATH, "..", "PvZ-Server-PoC"))
_ENTRY = os.path.normpath(os.path.join(SPECPATH, "..", "launcher.py"))

a = Analysis(
    [_ENTRY],
    pathex=[_SRV],
    binaries=[],
    datas=[],
    hiddenimports=['gm', 'player_store', 'admin_server'],
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
    name='PvZOnline',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
