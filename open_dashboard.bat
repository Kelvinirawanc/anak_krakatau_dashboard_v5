@echo off
cd /d "%~dp0"
title Anak Krakatau 2026 - Dashboard

where code >nul 2>nul
if errorlevel 1 (
    echo VS Code was not found in PATH. Open the project manually in VS Code.
) else (
    start "" code .
)

echo.
echo Open this folder with VS Code Live Server.
echo Then open index.html using Live Server.
pause
