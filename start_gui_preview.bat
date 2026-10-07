@echo off
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
"C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe" -B -m src.gui.app --preview
