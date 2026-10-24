@echo off
rem Starts JARVIS in the background (no console). Logs: %LOCALAPPDATA%\Jarvis\jarvis.log
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" main.py
