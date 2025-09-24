# scripts/dev.ps1
$ErrorActionPreference = "Stop"

function Set-EnvVar([string]$name, [string]$val) {
  if (($null -ne $name) -and ($name -ne "")) {
    Set-Item -Path ("Env:{0}" -f $name) -Value $val
  }
}

# ensure venv + deps
$venv = ".\.venv\Scripts\Activate.ps1"
if (!(Test-Path $venv)) { python -m venv .venv }
& $venv
if (Test-Path "backend\requirements.txt")     { pip install -q -r backend\requirements.txt }
if (Test-Path "backend\requirements-dev.txt") { pip install -q -r backend\requirements-dev.txt }

# Load backend\.env if present
$envFile = Join-Path $PSScriptRoot "..\backend\.env"
if (Test-Path $envFile) {
  Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*#') { return }
    if ($_ -match '^\s*$') { return }
    $pair = $_ -split '=', 2
    if ($pair.Count -eq 2) {
      $name = $pair[0].Trim()
      $val  = $pair[1].Trim().Trim('"')
      Set-EnvVar $name $val
    }
  }
}

# Dev defaults / overrides
Set-EnvVar "DEV_MODE" "1"
if (-not $env:FRONTEND_ORIGIN) { Set-EnvVar "FRONTEND_ORIGIN" "http://localhost:5173" }
if (-not $env:OCR_PROVIDER)    { Set-EnvVar "OCR_PROVIDER" "mock" }

Write-Host "[dev] DEV_MODE=$($env:DEV_MODE) OCR_PROVIDER=$($env:OCR_PROVIDER)"
Write-Host "[dev] FRONTEND_ORIGIN=$($env:FRONTEND_ORIGIN)"
Write-Host "[dev] Starting uvicorn on http://127.0.0.1:8000 ..."

# Run backend
python -m uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
