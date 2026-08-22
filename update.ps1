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
Write-Host "[3/5] Data refresh (run.py all)..." -ForegroundColor Yellow
Push-Location "$HOME_DIR\engine\scripts"
python run.py all
if ($LASTEXITCODE -ne 0) { Write-Host "    run.py all partial fail (retry later)" -ForegroundColor DarkYellow }
Pop-Location

# 4. Sporttery 5-pool fetch (v4.5: fixtures + crs/ttg/hafu odds + poolSingle)
Write-Host "[4/5] Sporttery 5-pool fetch..." -ForegroundColor Yellow
Push-Location "$HOME_DIR\engine\scripts"
python sporttery_fetch.py
if ($LASTEXITCODE -ne 0) { Write-Host "    sporttery_fetch fail (API may throttle, auto-retry on predict)" -ForegroundColor DarkYellow }
Pop-Location

# 5. Test regression (verify code changes didn't break anything)
Write-Host "[5/5] Test regression..." -ForegroundColor Yellow
Push-Location "$HOME_DIR\engine"
python -m pytest tests -q
if ($LASTEXITCODE -ne 0) { Write-Host "    TESTS FAILED - check changes before predicting" -ForegroundColor Red }
Pop-Location

Write-Host "`n=== Update done ===" -ForegroundColor Cyan
Write-Host "Ready: tell Claude 'predict' to start"
Write-Host ""
Write-Host "Knowledge base freshness:" -ForegroundColor DarkGray
$lg = Get-ChildItem "$HOME_DIR\data\00-leagues\*.json" -ErrorAction SilentlyContinue
$fd = @($lg | Where-Object { (Get-Content $_ -Raw) -match '"computedFrom": "fd"' })
$manual = @($lg | Where-Object { (Get-Content $_ -Raw) -match 'espn-manual|claude-manual|none' })
Write-Host "  fd-coverage (fresh): $($fd.Count) leagues" -ForegroundColor Green
Write-Host "  manual (check on predict): $($manual.Count) leagues" -ForegroundColor DarkYellow
Write-Host "  -> manual leagues auto-refresh via Step 2.5 on next 'predict'" -ForegroundColor DarkGray
# sporttery 5-pool summary (v4.5)
$sm = "$HOME_DIR\engine\cache\sporttery_matches.json"
if (Test-Path $sm) {
    $smd = Get-Content $sm -Raw | ConvertFrom-Json
    $pools = @{}
    foreach ($m in $smd.matches) {
        foreach ($p in $m.poolSingle.PSObject.Properties) {
            if (-not $pools[$p.Name]) { $pools[$p.Name] = @{ sell = 0; single = 0 } }
            $pools[$p.Name].sell++
            if ("$($p.Value)" -eq "1") { $pools[$p.Name].single++ }
        }
    }
    $poolStr = ($pools.GetEnumerator() | Sort-Object Name | ForEach-Object { "$($_.Name) sell$($_.Value.sell)/single$($_.Value.single)" }) -join ", "
    Write-Host "  sporttery 5-pool: $($smd.count) matches, $poolStr" -ForegroundColor Green
}
