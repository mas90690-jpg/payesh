@echo off
REM اجرای سریع تست‌ها
REM usage: tests\run_platform.bat bale
REM        tests\run_platform.bat all

setlocal
set PLATFORM=%1
if "%PLATFORM%"=="" set PLATFORM=all

cd /d "%~dp0\.."
if "%PLATFORM%"=="all" (
    python tests\run_all_tests.py
) else (
    python tests\test_%PLATFORM%_full.py
)