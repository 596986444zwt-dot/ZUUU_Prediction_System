@echo off
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
set PYTHONIOENCODING=utf-8
"C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe" -B scripts\t0_experimental.py status
