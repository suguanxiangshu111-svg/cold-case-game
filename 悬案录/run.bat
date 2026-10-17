@echo off
setlocal
cd /d "%~dp0"

if not exist ".env" (
    if exist ".env.example" copy /y ".env.example" ".env" >nul
    echo Created .env from .env.example.
    echo Open .env and fill in DEEPSEEK_API_KEY, then run run.bat again.
    notepad .env
    pause
    exit /b 1
)

rem Prefer a local venv, then the shared parent venv, then global python.
set "PY=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%~dp0.venv\Scripts\python.exe" if exist "%~dp0..\.venv\Scripts\python.exe" set "PY=%~dp0..\.venv\Scripts\python.exe"

"%PY%" -c "import fastapi, uvicorn, httpx" >nul 2>nul
if errorlevel 1 (
    echo Installing dependencies with "%PY%" ...
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Failed. Use a Python with FastAPI installed, or run:
        echo   "%PY%" -m pip install -r requirements.txt
        pause
        exit /b 1
    )
)

echo Starting Xuan An Lu (FastAPI) ...
echo Game:      http://127.0.0.1:8787
echo API docs:  http://127.0.0.1:8787/docs
"%PY%" main.py
pause
