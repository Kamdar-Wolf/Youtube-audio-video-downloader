@echo off
chcp 65001 >nul
title YouTube ^& YT Music Downloader (MP3 / MP4)
cd /d "%~dp0"
echo ============================================================
echo   Kontrola zavislosti a spousteni YouTube Downloader...
echo ============================================================
python -c "import fastapi, uvicorn, yt_dlp, mutagen, PIL, static_ffmpeg" 2>nul
if errorlevel 1 (
    echo Instaluji potrebne knihovny pri prvnim spusteni...
    python -m pip install --upgrade -r requirements.txt
)
echo.
echo   Spoustim webovy server na http://127.0.0.1:8765
echo   Prohlizec se za okamzik automaticky otevre.
echo   Pro ukonceni programu zavrete toto okno.
echo ============================================================
python app.py
pause
