@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM --- Check panel.py exists ---
if not exist "panel.py" (
    echo.
    echo [X] panel.py not found in: %CD%
    echo.
    pause
    exit /b 1
)

REM --- Prefer venv if available ---
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" panel.py
) else (
    python panel.py
)

REM --- Pause at the end ---
pause