# install.ps1 - Football prediction knowledge base installer (Windows)
# Usage: after clone, cd to repo root, run ./install.ps1
# Idempotent: safe to re-run

$ErrorActionPreference = "Stop"
$HOME_DIR = $PSScriptRoot
$env:FOOTBALL_HOME = $HOME_DIR

Write-Host "=== Football KB Install ===" -ForegroundColor Cyan
Write-Host "Root: $HOME_DIR`n"

# 1. Python deps
Write-Host "[1/4] Installing Python deps..." -ForegroundColor Yellow
python -m pip install -r "$HOME_DIR\engine\requirements.txt" --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Write-Host "pip install failed" -ForegroundColor Red; exit 1 }
Write-Host "    OK" -ForegroundColor Green

# 2. skill junction (Claude Code discovery)
Write-Host "[2/4] Creating skill junction..." -ForegroundColor Yellow
$SKILL_DIR = "$env:USERPROFILE\.claude\skills"
if (-not (Test-Path $SKILL_DIR)) { New-Item -ItemType Directory -Force $SKILL_DIR | Out-Null }
$SKILL_LINK = "$SKILL_DIR\football-betting-prediction"
if (Test-Path $SKILL_LINK) { cmd /c rmdir "$SKILL_LINK" }
cmd /c mklink /J "$SKILL_LINK" "$HOME_DIR\skill"
if (Test-Path "$SKILL_LINK\SKILL.md") {
    Write-Host "    OK: $SKILL_LINK" -ForegroundColor Green
} else {
    Write-Host "    junction failed" -ForegroundColor Red; exit 1
}

# 3. Persist FOOTBALL_HOME (needs new terminal to take effect)
Write-Host "[3/4] Setting FOOTBALL_HOME..." -ForegroundColor Yellow
[Environment]::SetEnvironmentVariable("FOOTBALL_HOME", $HOME_DIR, "User")
Write-Host "    OK (reopen terminal)" -ForegroundColor Green

# 4. Data init
Write-Host "[4/4] Data init (run.py all)..." -ForegroundColor Yellow
Push-Location "$HOME_DIR\engine\scripts"
python run.py all
if ($LASTEXITCODE -ne 0) { Write-Host "    run.py all partial fail (data source may be down, retry later)" -ForegroundColor DarkYellow }
Pop-Location

Write-Host "`n=== Install done ===" -ForegroundColor Cyan
Write-Host "Test:     cd engine\scripts; python -m pytest tests -q"
Write-Host "Use:      tell Claude 'predict' in new terminal"
Write-Host "Update:   git pull; python engine\scripts\run.py all"
