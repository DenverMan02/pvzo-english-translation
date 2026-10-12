@echo off
chcp 65001 >nul
setlocal
rem Use the bundled portable entry before any system Python fallback.
if exist "%~dp0..\一键启动游戏.exe" (
    start "" "%~dp0..\一键启动游戏.exe"
    exit /b 0
)
if exist "%~dp0运行环境\PvZRuntime.exe" (
    start "" /min "%~dp0运行环境\PvZRuntime.exe"
    exit /b 0
)
set "PYW=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
if exist "%PYW%" goto run
set "PYW=pythonw"
where pythonw >nul 2>nul
if not errorlevel 1 goto run
set "PYW=python"
where python >nul 2>nul
if not errorlevel 1 goto run
echo Python runtime was not found. Please extract the complete game package.
pause
exit /b 1
:run
start "" "%PYW%" "%~dp0launcher.py"
exit /b 0
