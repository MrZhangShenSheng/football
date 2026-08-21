# update.ps1 - Football KB update (run after git pull, or standalone)
# Differs from install: no junction/env-var setup, adds test regression
# Idempotent: safe to re-run

$ErrorActionPreference = "Stop"
$HOME_DIR = $PSScriptRoot
$env:FOOTBALL_HOME = $HOME_DIR

Write-Host "=== Football KB Update ===" -ForegroundColor Cyan
Write-Host "Root: $HOME_DIR`n"

# 1. git pull (skill + scripts + tests may have changed)
Write-Host "[1/4] git pull..." -ForegroundColor Yellow
Push-Location $HOME_DIR
git pull --ff-only
if ($LASTEXITCODE -ne 0) { Write-Host "    git pull failed (check local changes)" -ForegroundColor DarkYellow }
Pop-Location

# 2. pip deps sync (only installs if changed)
Write-Host "[2/4] Sync Python deps..." -ForegroundColor Yellow
python -m pip install -r "$HOME_DIR\engine\requirements.txt" --quiet --disable-pip-version-check
Write-Host "    OK" -ForegroundColor Green

# 3. Data refresh (odds + league profiles + DC refit with --auto)
Write-Host "[3/4] Data refresh (run.py all)..." -ForegroundColor Yellow
Push-Location "$HOME_DIR\engine\scripts"
python run.py all
if ($LASTEXITCODE -ne 0) { Write-Host "    run.py all partial fail (retry later)" -ForegroundColor DarkYellow }
Pop-Location

# 4. Test regression (verify code changes didn't break anything)
Write-Host "[4/4] Test regression..." -ForegroundColor Yellow
Push-Location "$HOME_DIR\engine"
python -m pytest tests -q
if ($LASTEXITCODE -ne 0) { Write-Host "    TESTS FAILED - check changes before predicting" -ForegroundColor Red }
Pop-Location

Write-Host "`n=== Update done ===" -ForegroundColor Cyan
Write-Host "Ready: tell Claude 'predict' to start"
