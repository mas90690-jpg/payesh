@echo off
chcp 65001 >nul
cd /d "F:\payesh plat"
call venv\Scripts\activate.bat
python panel.py
pause