@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
echo ===================================================
echo   Raspberry Pi Pico ファームウェア書き込みツール
echo ===================================================
echo.

set CLI="C:\Users\hp\AppData\Local\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"
set SKETCH=controller\firmware\MIDIUSB_buzzer
set FQBN=rp2040:rp2040:rpipico:usbstack=tinyusb

if not exist %CLI% (
    echo [ERROR] arduino-cli.exe が見つかりません: %CLI%
    echo Arduino IDE のインストール先を確認してください。
    pause
    exit /b 1
)

if not "%~1"=="" (
    set PORT=%~1
    goto :compile_and_upload
)

echo 接続中のボードを確認しています...
%CLI% board list
echo.
set /p PORT=書き込む COM ポートを入力してください (例: COM5): 
if "%PORT%"=="" (
    echo [ERROR] ポートが指定されませんでした。
    pause
    exit /b 1
)

:compile_and_upload
echo.
echo [1/2] ファームウェアをコンパイル中...
%CLI% compile -b %FQBN% "%SKETCH%"
if errorlevel 1 (
    echo [ERROR] コンパイルに失敗しました。
    pause
    exit /b 1
)

echo.
echo [2/2] %PORT% へ書き込み中...
%CLI% upload -p %PORT% -b %FQBN% "%SKETCH%"
if errorlevel 1 (
    echo [ERROR] 書き込みに失敗しました。Pico を BOOTSEL ボタンを押しながら再接続して試してください。
    pause
    exit /b 1
)

echo.
echo 書き込みが完了しました！
pause
