@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYW="
if exist "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\Launcher\pyw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Launcher\pyw.exe"
if not defined PYW if exist "C:\Users\36211\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe" set "PYW=C:\Users\36211\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe"

if not defined PYW (
    where pythonw >nul 2>nul
    if not errorlevel 1 set "PYW=pythonw"
)
if not defined PYW (
    where python >nul 2>nul
    if not errorlevel 1 set "PYW=python"
)
if not defined PYW (
    echo [ERROR] Python 3.10+ not found.
    echo Please install Python from https://www.python.org/downloads/
    echo and make sure "Add python.exe to PATH" is checked.
    pause
    exit /b 1
)

start "" "%PYW%" "%~dp0main.py"
exit /b 0
