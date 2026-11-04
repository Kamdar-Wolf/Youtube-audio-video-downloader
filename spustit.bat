@echo off
chcp 65001 >nul
title YouTube ^& YT Music Downloader (MP3 / MP4)
cd /d "%~dp0"
echo ============================================================
echo   Kontrola zavislosti a spousteni YouTube Downloader...
echo ============================================================
python -c "import fastapi, uvicorn, yt_dlp, mutagen, PIL, static_ffmpeg, webview" 2>nul
if errorlevel 1 (
    echo Instaluji potrebne knihovny pri prvnim spusteni...
    python -m pip install --upgrade -r requirements.txt
)
echo.
echo   Oteviram okno aplikace YouTube ^& YT Music Downloader...
echo   Po zavreni okna aplikace se automaticky uzavre i tento terminal.
echo ============================================================
python app.py
if errorlevel 1 pause
exit
