@echo off
setlocal
set "PYW=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
if exist "%PYW%" goto run
set "PYW=pythonw"
where pythonw >nul 2>nul
if not errorlevel 1 goto run
set "PYW=python"
where python >nul 2>nul
if not errorlevel 1 goto run
echo.
echo Python was not found. Install Python 3.13 and add it to PATH.
echo.
pause
exit /b 1
:run
start "" "%PYW%" "%~dp0launcher.py"
exit /b 0
