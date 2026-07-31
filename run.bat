@echo off
chcp 65001 >nul
echo 启动风速监控系统...
cd /d "%~dp0web"
..\venv\Scripts\python realtime_wind_monitor.py
pause
