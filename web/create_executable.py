"""
PyInstaller打包脚本
用于将实时风速监控系统打包成独立的可执行文件
"""


import os
import subprocess
import sys
from pathlib import Path

def install_pyinstaller():
    """安装PyInstaller"""
    try:
        import PyInstaller
        print("PyInstaller已安装")
    except ImportError:
        print("正在安装PyInstaller...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        print("PyInstaller安装完成")

def create_spec_file():
    """如果spec文件不存在则创建（已有则跳过，避免覆盖修复版本）"""
    if os.path.exists('wind_monitor_new.spec'):
        print("使用已存在的 spec 文件: wind_monitor_new.spec")
        return

    print("创建新的 spec 文件...")
    spec_content = """# -*- mode: python ; coding: utf-8 -*-

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
"""
    
    with open('wind_monitor_new.spec', 'w', encoding='utf-8') as f:
        f.write(spec_content)
    
    print("PyInstaller规范文件已创建: wind_monitor_new.spec")

def run_pyinstaller():
    """运行PyInstaller进行打包"""
    try:
        print("开始打包程序...")
        # 使用新spec文件进行打包
        result = subprocess.run([
            sys.executable, "-m", "PyInstaller", 
            "wind_monitor_new.spec",
            "--clean"
        ], check=True, capture_output=True, text=True)
        
        print("打包成功完成！")
        print(result.stdout)
        
    except subprocess.CalledProcessError as e:
        print(f"打包过程中出现错误: {e}")
        print(f"错误输出: {e.stderr}")
        # 尝试不使用spec文件直接打包
        print("尝试使用直接命令打包...")
        try:
            subprocess.run([
                sys.executable, "-m", "PyInstaller",
                "realtime_wind_monitor.py",
                "--name=WindSpeedMonitor",
                "--add-data=templates;templates",
                "--add-data=modules;modules",
                "--hidden-import=engineio.async_drivers.threading",
                "--hidden-import=markupsafe",
                "--hidden-import=jinja2.ext",
                "--hidden-import=serial",
                "--hidden-import=serial.urlhandler",
                "--hidden-import=serial.tools.list_ports",
                "--hidden-import=serial.tools.list_ports_common",
                "--hidden-import=serial.tools.list_ports_windows",
                "--hidden-import=flask_socketio",
                "--hidden-import=socketio",
                "--hidden-import=engineio",
                "--console",
                "--clean"
            ], check=True)
            print("使用直接命令打包成功！")
        except subprocess.CalledProcessError as e2:
            print(f"直接命令打包也失败了: {e2}")
            raise

def main():
    """主函数"""
    print("实时风速监控系统打包工具")
    print("="*50)
    
    # 检查并安装PyInstaller
    install_pyinstaller()
    
    # 创建spec文件
    create_spec_file()
    
    # 执行打包
    run_pyinstaller()
    
    print("="*50)
    print("打包完成！")
    print("可执行文件位于 dist/WindSpeedMonitor 目录中")
    print("将整个WindSpeedMonitor文件夹复制到其他电脑即可运行")

if __name__ == "__main__":
    main()