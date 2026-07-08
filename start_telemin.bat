@echo off
echo ==========================================
echo  tElemin Launcher
echo ==========================================

:: 1. Launch Vital
set VITAL_PATH="C:\Program Files\Vital\Vital.exe"

if exist %VITAL_PATH% (
    echo [1/3] Launching Vital...
    start "" %VITAL_PATH%
    echo [2/3] Waiting 2 seconds for MIDI port...
    timeout /t 2 /nobreak > nul
) else (
    echo [WARNING] Vital.exe was not found at %VITAL_PATH%
    echo Please start Vital manually.
    timeout /t 3 /nobreak > nul
)

:: 2. Activate Python Virtual Environment
echo [3/3] Activating virtual environment and running tElemin...

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else if exist "myenv\Scripts\activate.bat" (
    call myenv\Scripts\activate.bat
) else (
    echo [WARNING] Virtual environment not found. Trying default system Python.
)

:: 3. Run Script
python telemin.py

echo.
echo tElemin has terminated.
pause
