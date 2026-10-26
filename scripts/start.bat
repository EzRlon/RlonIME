@echo off
REM ============================================================
REM  智语 · 工程领域智能中文输入法  启动脚本
REM ============================================================
chcp 65001 >nul
setlocal

set PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe
set APP=%~dp0..\src\main.py

if not exist "%PY%" (
    echo [错误] 未找到 Python 环境: %PY%
    echo 请先运行: scripts\install_deps.py
    pause
    exit /b 1
)

echo 正在启动 智语输入法 ...
start "" "%PY%" "%APP%"
exit /b 0
