@echo off
chcp 65001 > nul
echo ==========================================
# 空間MIDIコントローラー「tElemin」自動起動スクリプト
echo ==========================================

:: 1. Vital (シンセサイザー) の起動確認と実行
set VITAL_PATH="C:\Program Files\Vital\Vital.exe"

if exist %VITAL_PATH% (
    echo [1/3] Vital を起動しています...
    start "" %VITAL_PATH%
    echo [2/3] MIDIポート占有を防ぐため、2秒間待機します...
    timeout /t 2 /nobreak > nul
) else (
    echo [警告] Vital.exe が見つかりませんでした (%VITAL_PATH%)。
    echo Vital は手動で起動してください。
    timeout /t 3 /nobreak > nul
)

:: 2. Python 仮想環境の有効化とスクリプト実行
echo [3/3] Python仮想環境を有効化して tElemin を実行します...

if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else if exist "myenv\Scripts\activate.bat" (
    call myenv\Scripts\activate.bat
) else (
    echo [警告] 仮想環境 (activate.bat) が見つかりません。システムのデフォルトPythonで実行を試みます。
)

:: 3. スクリプトの実行
python telemin.py

echo.
echo tElemin が終了しました。
pause
