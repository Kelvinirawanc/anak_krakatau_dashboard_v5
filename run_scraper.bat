@echo off
cd /d "%~dp0"
title Anak Krakatau 2026 - Scraper

echo =================================================
echo   ANAK KRAKATAU 2026 DATA SCRAPER
echo =================================================
echo.

python scraper\scrape_anak_krakatau_2026.py

if errorlevel 1 (
    echo.
    echo SCRAPER FAILED.
    pause
    exit /b 1
)

echo.
echo Scraper finished successfully.
echo.
pause
