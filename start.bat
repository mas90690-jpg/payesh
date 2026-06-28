@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo  TEST RUN - Showing errors
echo ============================================================
echo.

set "VENV_DIR=%~dp0venv"

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [ERROR] venv not found at: %VENV_DIR%
    echo.
    echo Please run: python -m venv venv
    pause
    exit /b 1
)

echo [OK] venv found.
echo.

echo [RUN] %VENV_DIR%\Scripts\python.exe main.py
echo.
echo ============================================================
"%VENV_DIR%\Scripts\python.exe" main.py

echo.
echo ============================================================
echo  Bot stopped. Exit code: %errorlevel%
echo ============================================================
pause