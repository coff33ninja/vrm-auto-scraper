@echo off
title VRM Auto-Scraper - All Services
echo ========================================
echo   VRM Auto-Scraper - Starting Services
echo ========================================
echo.

:: Check if venv exists
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Virtual environment not found!
    echo Run: python -m venv .venv
    echo Then: pip install -r requirements.txt
    pause
    exit /b 1
)

:: Activate venv and start all services
echo Starting services...
echo.

:: Start Sketchfab crawler in background
echo [1/4] Starting Sketchfab crawler...
start "Sketchfab Crawler" /min cmd /c ".venv\Scripts\python src/cli.py crawl-continuous --sources sketchfab --batch 20 --interval 60"

:: Small delay between starts
timeout /t 2 /nobreak >nul

:: Start VRoid Hub crawler in background
echo [2/4] Starting VRoid Hub crawler...
start "VRoid Hub Crawler" /min cmd /c ".venv\Scripts\python src/cli.py crawl-continuous --sources vroid_hub --batch 20 --interval 60"

:: Small delay
timeout /t 2 /nobreak >nul

:: Start DeviantArt crawler in background (if configured)
echo [3/4] Starting DeviantArt crawler...
start "DeviantArt Crawler" /min cmd /c ".venv\Scripts\python src/cli.py crawl-continuous --sources deviantart --batch 20 --interval 60"

:: Small delay
timeout /t 2 /nobreak >nul

:: Start web server (this one stays in foreground)
echo [4/4] Starting Web Viewer...
echo.
echo ========================================
echo   All services started!
echo ========================================
echo.
echo   Web Viewer:  http://localhost:8080
echo   Sketchfab:   Running in background (minimized)
echo   VRoid Hub:   Running in background (minimized)
echo   DeviantArt:  Running in background (minimized)
echo.
echo   Press Ctrl+C to stop the web server
echo   Close the minimized windows to stop crawlers
echo ========================================
echo.

.venv\Scripts\python src/cli.py web
