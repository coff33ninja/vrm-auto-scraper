# VRM Auto-Scraper - Run All Services
# PowerShell version with better process management

$ErrorActionPreference = "Stop"

Write-Host "========================================"
Write-Host "  VRM Auto-Scraper - Starting Services"
Write-Host "========================================" 
Write-Host ""

# Check venv
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "ERROR: Virtual environment not found!" -ForegroundColor Red
    Write-Host "Run: python -m venv .venv"
    Write-Host "Then: pip install -r requirements.txt"
    exit 1
}

$pythonPath = ".\.venv\Scripts\python.exe"

# Store process IDs for cleanup
$processes = @()

try {
    # Start Sketchfab crawler
    Write-Host "[1/4] Starting Sketchfab crawler..." -ForegroundColor Cyan
    $sketchfab = Start-Process -FilePath $pythonPath -ArgumentList "src/cli.py", "crawl-continuous", "--sources", "sketchfab", "--batch", "20", "--interval", "60" -PassThru -WindowStyle Minimized
    $processes += $sketchfab
    Start-Sleep -Seconds 2

    # Start VRoid Hub crawler
    Write-Host "[2/4] Starting VRoid Hub crawler..." -ForegroundColor Cyan
    $vroid = Start-Process -FilePath $pythonPath -ArgumentList "src/cli.py", "crawl-continuous", "--sources", "vroid_hub", "--batch", "20", "--interval", "60" -PassThru -WindowStyle Minimized
    $processes += $vroid
    Start-Sleep -Seconds 2

    # Start DeviantArt crawler
    Write-Host "[3/4] Starting DeviantArt crawler..." -ForegroundColor Cyan
    $deviantart = Start-Process -FilePath $pythonPath -ArgumentList "src/cli.py", "crawl-continuous", "--sources", "deviantart", "--batch", "20", "--interval", "60" -PassThru -WindowStyle Minimized
    $processes += $deviantart
    Start-Sleep -Seconds 2

    # Start web server
    Write-Host "[4/4] Starting Web Viewer..." -ForegroundColor Cyan
    Write-Host ""
    Write-Host "========================================"
    Write-Host "  All services started!" -ForegroundColor Green
    Write-Host "========================================"
    Write-Host ""
    Write-Host "  Web Viewer:  " -NoNewline; Write-Host "http://localhost:8080" -ForegroundColor Yellow
    Write-Host "  Sketchfab:   Running (PID: $($sketchfab.Id))"
    Write-Host "  VRoid Hub:   Running (PID: $($vroid.Id))"
    Write-Host "  DeviantArt:  Running (PID: $($deviantart.Id))"
    Write-Host ""
    Write-Host "  Press Ctrl+C to stop all services"
    Write-Host "========================================"
    Write-Host ""

    # Run web server in foreground
    & $pythonPath src/cli.py web

} finally {
    # Cleanup: stop background processes
    Write-Host ""
    Write-Host "Stopping background services..." -ForegroundColor Yellow
    foreach ($proc in $processes) {
        if (-not $proc.HasExited) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            Write-Host "  Stopped PID: $($proc.Id)"
        }
    }
    Write-Host "All services stopped." -ForegroundColor Green
}
