@echo off
:: Move to this script's folder so it works from shortcuts / other directories
cd /d "%~dp0"
echo ==========================================
echo  tElemin Launcher (Internal Synthesizer)
echo ==========================================

:: 1. Activate Python Virtual Environment
echo [1/2] Activating Python virtual environment...

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else if exist "myenv\Scripts\activate.bat" (
    call myenv\Scripts\activate.bat
) else (
    echo [WARNING] Virtual environment not found. Trying default system Python.
)

:: 2. Run Script
echo [2/2] Starting tElemin...
python telemin.py

echo.
echo tElemin has terminated.
pause
