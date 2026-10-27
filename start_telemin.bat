@echo off
:: Move to this script's folder so it works from shortcuts / other directories
cd /d "%~dp0"
echo ==========================================
echo  tElemin Launcher
echo ==========================================

:: 1. Launch Vital (optional)
:: tElemin now has a built-in synth (synth.py) that plays through the PC audio output,
:: so Vital is not required. Set LAUNCH_VITAL=1 to also start Vital.
set LAUNCH_VITAL=0
set VITAL_PATH="C:\Program Files\Vital\Vital.exe"

if "%LAUNCH_VITAL%"=="1" (
    if exist %VITAL_PATH% (
        echo [1/3] Launching Vital...
        start "" %VITAL_PATH%
        echo [2/3] Waiting 2 seconds for MIDI port...
        timeout /t 2 /nobreak > nul
    ) else (
        echo [WARNING] Vital.exe was not found at %VITAL_PATH%
    )
) else (
    echo [1/3] Using built-in synth - Vital is not launched.
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
