param([string]$PythonPath = '')

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

# A server inherits its launch account's network restrictions for its entire life.
if ([Environment]::UserName -like 'CodexSandbox*') {
    throw 'Start TRINETRA from your normal Windows PowerShell, or an approved unsandboxed launch. A Codex sandbox server can block TronGrid even when the browser has internet access.'
}

if (Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue) {
    throw 'Port 8000 is already in use. Stop the existing TRINETRA server before launching; this script will not terminate an unknown process.'
}

if (-not $PythonPath) {
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $venvPython) {
        $PythonPath = $venvPython
    } elseif (Test-Path -LiteralPath $bundledPython) {
        $PythonPath = $bundledPython
    } else {
        $PythonPath = (Get-Command python -ErrorAction Stop).Source
    }
}

Push-Location $projectRoot
try {
    # Load configuration through the application; never display secrets or exception URLs.
    $preflight = @'
import sys
import requests
from app.services.feature_flags import feature_flags
from app.settings import settings
from engine.adapters.tron import config_from_env
if not (3, 11) <= sys.version_info[:2] < (3, 13):
    print('TRINETRA requires Python 3.11 or 3.12.')
    sys.exit(1)
if settings.mode != 'fixture' and not settings.session_secret:
    print('SESSION_SECRET must be configured privately in .env before starting live mode.')
    sys.exit(1)
if feature_flags()['live_tron_trace']['enabled']:
    try:
        response = requests.get(config_from_env().base_url, timeout=10)
        response.close()
    except requests.RequestException as exc:
        print('TronGrid connection check failed (' + type(exc).__name__ + '). Check network access before starting the live server.')
        sys.exit(1)
    print('TronGrid connection check passed. This checks connectivity, not trace or custody results.')
'@
    & $PythonPath -c $preflight
    if ($LASTEXITCODE -ne 0) { throw 'TRINETRA startup preflight failed.' }

    $logDirectory = Join-Path $projectRoot 'var'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $logStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $errorLog = Join-Path $logDirectory "uvicorn-$logStamp.err.log"
    $server = Start-Process -FilePath $PythonPath -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000', '--workers', '1') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDirectory "uvicorn-$logStamp.out.log") -RedirectStandardError $errorLog -PassThru
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        if ($server.HasExited) { throw "TRINETRA exited during startup. Inspect $errorLog" }
        try {
            $page = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/login' -UseBasicParsing -TimeoutSec 2
            if ($page.StatusCode -eq 200) {
                Write-Output "TRINETRA running at http://127.0.0.1:8000 (PID $($server.Id)). Logs: $errorLog"
                return
            }
        } catch {
            # Wait for Uvicorn startup without spawning another worker.
        }
    }
    throw "TRINETRA startup has not completed; inspect $errorLog (PID $($server.Id)) before retrying."
} finally {
    Pop-Location
}
