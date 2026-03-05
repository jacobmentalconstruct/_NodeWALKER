# -*- mode: python ; coding: utf-8 -*-
# Node Walker - PyInstaller Spec File

block_cipher = None

a = Analysis(
    ['src/app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'src.walker',
        'src.walker.types',
        'src.walker.db',
        'src.walker.manifest',
        'src.walker.cas',
        'src.walker.structure',
        'src.walker.chunks',
        'src.walker.graph',
        'src.walker.scoring',
        'src.walker.walker',
        'src.walker.policy',
        'src.walker.notes',
        'src.walker.signature',
        'src.walker.antidata',
        'src.ui',
        'src.ui.theme',
        'src.ui.main_window',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='NodeWalker',
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
    icon='assets/icons/app.ico',
)
