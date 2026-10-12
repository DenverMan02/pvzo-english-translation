@echo off
rem ============================================================
rem  Plants vs. Zombies Online - double-click to launch.
rem  Opens the graphical launcher (Start button + game).
rem ============================================================
cd /d "%~dp0"

rem Prefer pythonw (no black console window); fall back to python.
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0launcher.py"
    exit /b
)
where python >nul 2>nul
if %errorlevel%==0 (
    python "%~dp0launcher.py"
    exit /b
)

echo.
echo   Python is not installed (or not on PATH).
echo   Install it from  https://www.python.org/downloads/
echo   and tick "Add python.exe to PATH" during setup, then run this again.
echo.
pause
