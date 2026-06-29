@echo off
REM Bot Control Panel - double-click to open
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" panel.py
) else (
    python panel.py
)
