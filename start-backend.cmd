@echo off
cd /d "%~dp0backend"
echo Starting plagiarism analyzer backend at http://127.0.0.1:8000
.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
