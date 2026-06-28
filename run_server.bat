@echo off
chcp 65001 > nul
title Srooyesh Lead Monitor (Server)
:: ======================================================================
::  اجرای دائم روی ویندوز (VPS یا PC همیشه‌روشن)
::  اگر برنامه کرش کرد یا بسته شد، خودکار دوباره اجرا می‌شود.
::  توقف: پنجره را ببند یا Ctrl+C بزن.
:: ======================================================================
cd /d "%~dp0"

:: استفاده از venv در صورت وجود
if exist "venv\Scripts\python.exe" (
    set "PY=venv\Scripts\python.exe"
) else (
    set "PY=python"
)

:loop
echo [%date% %time%] شروع برنامه...
%PY% main.py
echo [%date% %time%] برنامه متوقف شد. ۱۰ ثانیه دیگر دوباره اجرا می‌شود...
timeout /t 10 /nobreak > nul
goto loop
