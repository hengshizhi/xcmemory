@echo off
chcp 65001 >nul
title XCMemory WebUI
echo Starting XCMemory WebUI...
echo.
"%~dp0venv\Scripts\python.exe" "%~dp0start_server.py" --gradio
pause
