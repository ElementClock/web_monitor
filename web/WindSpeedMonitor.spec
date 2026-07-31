# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['realtime_wind_monitor.py'],
    pathex=[],
    binaries=[],
    datas=[('templates', 'templates'), ('modules', 'modules')],
    hiddenimports=['engineio.async_drivers.threading', 'markupsafe', 'jinja2.ext', 'serial', 'serial.urlhandler', 'serial.tools.list_ports', 'serial.tools.list_ports_common', 'serial.tools.list_ports_windows', 'flask_socketio', 'socketio', 'engineio', 'simple_websocket', 'wsproto'],
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
    name='WindSpeedMonitor',
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='WindSpeedMonitor',
)
