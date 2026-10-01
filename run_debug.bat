@echo off
rem ==========================================
rem  项目看板 - 排查模式（保留控制台 + 实时日志）
rem  双击失败的真正原因看这里，不要去猜
rem ==========================================
setlocal

set PY=%USERPROFILE%\.workbuddy\binaries\python\envs\board\Scripts\python.exe
set APP=%~dp0app\_boot.py
set LOG=%~dp0data\launch.log

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
    echo crashdump: %~dp0data\launch_error.log
    echo.
    pause
)
exit /b 0

:nopython
echo [ERR] Python not found: %PY%
pause
exit /b 1
