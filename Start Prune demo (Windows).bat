@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py prune.py --demo) || (python prune.py --demo)
if errorlevel 1 (echo. & echo Prune needs Python 3. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH". & pause)
