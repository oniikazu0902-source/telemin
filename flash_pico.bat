@echo off
cd /d "%~dp0"
echo ===================================================
echo   Raspberry Pi Pico ファームウェア書き込みツール
echo ===================================================
echo.
echo [手順]
echo 1. Raspberry Pi Pico 本体の白い「BOOTSEL」ボタンを押したまま、
echo    USBケーブルをパソコンに差し込んでください。
echo 2. エクスプローラーに「RPI-RP2」というドライブが現れます。
echo.
echo ドライブを自動検出中...
echo.

python scratch\watch_and_flash.py

echo.
pause
