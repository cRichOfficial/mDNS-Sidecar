# NeonBeam Discovery Sidecar — Windows launch script
# Run from the repo root:  .\discovery_sidecar\start_sidecar.ps1
# Or from inside discovery_sidecar\:  .\start_sidecar.ps1

param(
    [int]$Port = 3001,
    [string]$BindHost = "0.0.0.0"   # NOTE: $Host is a reserved PowerShell variable; use $BindHost
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host ""
Write-Host "  NeonBeam Discovery Sidecar" -ForegroundColor Cyan
Write-Host "  --------------------------" -ForegroundColor DarkGray
Write-Host "  Setting up virtual environment..." -ForegroundColor DarkGray

# Create venv if it doesn't exist
if (-not (Test-Path ".venv")) {
    Write-Host "  Creating .venv..." -ForegroundColor Yellow
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: python -m venv failed. Is Python 3.11+ installed?" -ForegroundColor Red
        exit 1
    }
}

# Activate and install deps
& ".venv\Scripts\Activate.ps1"

Write-Host "  Installing / updating dependencies..." -ForegroundColor DarkGray
pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERROR: pip install failed." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "  Starting sidecar on http://${BindHost}:${Port}" -ForegroundColor Green
Write-Host "  Press Ctrl-C to stop." -ForegroundColor DarkGray
Write-Host ""

$env:DISCOVERY_PORT = $Port
$env:DISCOVERY_HOST = $BindHost

# Kill any process already listening on the target port so the new instance
# can bind cleanly.  Without this, a stale process keeps the port and the
# new uvicorn silently exits (it shows "startup complete" then immediately
# shuts down — no error, very confusing).
$existing = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
    Where-Object State -eq 'Listen' |
    Select-Object -ExpandProperty OwningProcess -Unique
if ($existing) {
    foreach ($procId in $existing) {
        $pname = (Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName
        Write-Host "  Releasing port ${Port}: killing PID $procId ($pname)" -ForegroundColor Yellow
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 500   # let the OS reclaim the port
}

python run.py --host $BindHost --port $Port
