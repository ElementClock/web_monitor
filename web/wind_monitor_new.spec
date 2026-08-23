# -*- mode: python ; coding: utf-8 -*-

import sys
import os

# 获取项目根目录 - 使用当前脚本所在目录
basedir = os.path.abspath('.')

block_cipher = None

a = Analysis(
    ['realtime_wind_monitor.py'],
    pathex=[basedir],
    binaries=[],
    datas=[
        # 包含templates目录 - 确保路径存在
        ('templates', 'templates'),
        # 包含modules目录 - 确保路径存在
        ('modules', 'modules'),
        # P2-10: 包含本地前端静态库（离线可用，CDN兜底）
        ('static', 'static'),
        # P1-11: 包含串口配置文件，打包后用户配置可保留
        ('serial_configs.json', '.'),
    ],
    hiddenimports=[
        # 添加可能的隐藏导入
        'engineio.async_drivers.threading',
        'markupsafe',
        'jinja2.ext',
        'serial',
        'serial.urlhandler',
        'serial.tools.list_ports',
        'serial.tools.list_ports_common',
        'serial.tools.list_ports_windows',
        'flask_socketio',
        'socketio',
        'engineio',
        'simple_websocket',
        'wsproto',
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
    name='WindSpeedMonitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # 保持控制台窗口以便查看日志
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # 可以添加图标文件路径
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='WindSpeedMonitor',
)
