@echo off
chcp 65001 >nul
title YouTube ^& YT Music Downloader (MP3 / MP4)
cd /d "%~dp0"
echo ============================================================
echo   Spoustim YouTube ^& YT Music Downloader...
echo   Prohlizec se za okamzik automaticky otevre.
echo   Pro ukonceni programu zavrete toto okno.
echo ============================================================
python app.py
pause
