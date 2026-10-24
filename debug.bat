@echo off
rem Runs JARVIS with a console showing the live log.
cd /d "%~dp0"
".venv\Scripts\python.exe" main.py
pause
