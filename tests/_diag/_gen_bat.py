# -*- coding: utf-8 -*-
"""生成 GBK + CRLF 编码的 .bat（cmd.exe 按本地代码页解析，不能用 UTF-8 + LF）。"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RUN = """@echo off
rem ==========================================
rem  项目看板 - 双击启动（无黑色控制台窗口）
rem  启动失败会弹错误框，并在 data\\launch_error.log 留下完整堆栈
rem  排查用：改跑 run_debug.bat
rem ==========================================
setlocal

set PY=%USERPROFILE%\\.workbuddy\\binaries\\python\\envs\\board\\Scripts\\pythonw.exe
set APP=%~dp0app\\_boot.py

if not exist "%PY%" goto nopython
if not exist "%APP%" goto noapp

rem _boot 负责：崩溃兜底、孤儿锁清理、唤醒已有实例
start "" "%PY%" "%APP%"
exit /b 0

:nopython
echo [ERR] Python not found: %PY%
echo Please check venv: %USERPROFILE%\\.workbuddy\\binaries\\python\\envs\\board
pause
exit /b 1

:noapp
echo [ERR] entry script not found: %APP%
pause
exit /b 1
"""

DEBUG = """@echo off
rem ==========================================
rem  项目看板 - 排查模式（保留控制台 + 实时日志）
rem  双击失败的真正原因看这里，不要去猜
rem ==========================================
setlocal

set PY=%USERPROFILE%\\.workbuddy\\binaries\\python\\envs\\board\\Scripts\\python.exe
set APP=%~dp0app\\_boot.py
set LOG=%~dp0data\\launch.log

if not exist "%~dp0data" mkdir "%~dp0data"
if not exist "%PY%" goto nopython

echo [%date% %time%] start >> "%LOG%"
"%PY%" -X utf8 "%APP%" >> "%LOG%" 2>&1

if errorlevel 1 (
    echo.
    echo ==== LAUNCH FAILED, log tail ====
    type "%LOG%"
    echo.
    echo log:      %LOG%
    echo crashdump: %~dp0data\\launch_error.log
    echo.
    pause
)
exit /b 0

:nopython
echo [ERR] Python not found: %PY%
pause
exit /b 1
"""


def write(name, text):
    p = os.path.join(ROOT, name)
    with open(p, "w", encoding="gbk", newline="\r\n") as f:
        f.write(text)
    print("wrote", p, os.path.getsize(p), "bytes")


write("run.bat", RUN)
write("run_debug.bat", DEBUG)
