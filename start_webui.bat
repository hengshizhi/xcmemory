@echo off
chcp 65001 >nul
title XCMemory WebUI
echo Starting XCMemory WebUI...
echo.
o:\project\xcmemory_interest\venv\Scripts\python.exe o:\project\xcmemory_interest\start_server.py --gradio
pause
