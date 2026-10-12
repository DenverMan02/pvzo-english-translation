# -*- mode: python ; coding: utf-8 -*-
# pathex uses absolute paths so admin_server and its deps are bundled.
import os

_SRV = os.path.normpath(os.path.join(SPECPATH, "..", "PvZ-Server-PoC"))
_ENTRY = os.path.normpath(os.path.join(SPECPATH, "..", "PvZ-Server-PoC", "run_all.py"))

a = Analysis(
    [_ENTRY],
    pathex=[_SRV],
    binaries=[],
    datas=[],
    hiddenimports=['admin_server', 'gm', 'player_store'],
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
    name='PvZServer',
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
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PvZServer',
)
