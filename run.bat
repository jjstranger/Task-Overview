@echo off
rem ==========================================
rem  项目看板 - 双击启动（无黑色控制台窗口）
rem  启动失败会弹错误框，并在 data\launch_error.log 留下完整堆栈
rem  排查用：改跑 run_debug.bat
rem ==========================================
setlocal

set PY=%USERPROFILE%\.workbuddy\binaries\python\envs\board\Scripts\pythonw.exe
set APP=%~dp0app\_boot.py

if not exist "%PY%" goto nopython
if not exist "%APP%" goto noapp

rem _boot 负责：崩溃兜底、孤儿锁清理、唤醒已有实例
start "" "%PY%" "%APP%"
exit /b 0

:nopython
echo [ERR] Python not found: %PY%
echo Please check venv: %USERPROFILE%\.workbuddy\binaries\python\envs\board
pause
exit /b 1

:noapp
echo [ERR] entry script not found: %APP%
pause
exit /b 1
