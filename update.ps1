# update.ps1 - Football KB update (run after git pull, or standalone)
# Differs from install: no junction/env-var setup, adds test regression
# Idempotent: safe to re-run

$ErrorActionPreference = "Stop"
$HOME_DIR = $PSScriptRoot
$env:FOOTBALL_HOME = $HOME_DIR

Write-Host "=== Football KB Update ===" -ForegroundColor Cyan
Write-Host "Root: $HOME_DIR`n"

# 1. git pull (skill + scripts + tests may have changed)
Write-Host "[1/7] git pull..." -ForegroundColor Yellow
Push-Location $HOME_DIR
git pull --ff-only
if ($LASTEXITCODE -ne 0) { Write-Host "    git pull failed (check local changes)" -ForegroundColor DarkYellow }
Pop-Location

# 2. pip deps sync (only installs if changed)
Write-Host "[2/7] Sync Python deps..." -ForegroundColor Yellow
python -m pip install -r "$HOME_DIR\engine\requirements.txt" --quiet --disable-pip-version-check
Write-Host "    OK" -ForegroundColor Green

# 3. Data refresh (odds + league profiles + DC refit with --auto)
Write-Host "[3/7] Data refresh (run.py all)..." -ForegroundColor Yellow
Push-Location "$HOME_DIR\engine\scripts"
python run.py all
if ($LASTEXITCODE -ne 0) { Write-Host "    run.py all partial fail (retry later)" -ForegroundColor DarkYellow }
Pop-Location

# 4. Sporttery 5-pool fetch (v4.5: fixtures + crs/ttg/hafu odds + poolSingle)
Write-Host "[4/7] Sporttery 5-pool fetch..." -ForegroundColor Yellow
Push-Location "$HOME_DIR\engine\scripts"
python sporttery_fetch.py
if ($LASTEXITCODE -ne 0) { Write-Host "    sporttery_fetch fail (API may throttle, auto-retry on predict)" -ForegroundColor DarkYellow }
Pop-Location

# 5. Learning loop (v4.5.1: non-fd espn incremental fetch -> local fit -> version publish)
Write-Host "[5/7] Learning loop (run.py learn)..." -ForegroundColor Yellow
Push-Location "$HOME_DIR\engine\scripts"
python run.py learn
if ($LASTEXITCODE -ne 0) { Write-Host "    learn partial fail (ESPN may be down, retry next update)" -ForegroundColor DarkYellow }
Pop-Location

# 6. Trend report (v4.5.2: corpus rebuild + trend.html refresh, local-only)
Write-Host "[6/7] Trend report (run.py corpus)..." -ForegroundColor Yellow
Push-Location "$HOME_DIR\engine\scripts"
python run.py corpus
if ($LASTEXITCODE -ne 0) { Write-Host "    corpus/trend partial fail (local-only, just re-run)" -ForegroundColor DarkYellow }
Pop-Location

# 7. Test regression (verify code changes didn't break anything)
Write-Host "[7/7] Test regression..." -ForegroundColor Yellow
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
# learning corpus readiness + model versions (v4.5.1) + trend (v4.5.2)
$corpusPath = "$HOME_DIR\data\04-summaries\corpus.json"
if (Test-Path $corpusPath) {
    $cp = Get-Content $corpusPath -Raw | ConvertFrom-Json
    $cal = if ($cp.readiness.calibrateReady) { "ready" } else { "gap $($cp.readiness.calibrateGap)" }
    $abl = if ($cp.readiness.ablateReady) { "ready" } else { "waiting" }
    Write-Host "  corpus: $($cp.n_total) records / $($cp.n_rounds) rounds (filled $($cp.readiness.n_result) / CLV $($cp.readiness.n_clv)) | calibrate $cal | ablate $abl" -ForegroundColor Green
    $nPlans = @($cp.plans.PSObject.Properties | Where-Object { $_.Value -is [array] }).Count
    $trendExists = if (Test-Path "$HOME_DIR\data\04-summaries\trend.html") { "yes" } else { "no" }
    Write-Host "  plans tracked: $nPlans | trend.html: $trendExists (logloss/hit-rate/CLV/calibration/buckets/plan-accuracy)" -ForegroundColor Green
}
$ml = "$HOME_DIR\engine\cache\models\latest.json"
if (Test-Path $ml) {
    $lv = Get-Content $ml -Raw | ConvertFrom-Json
    $parts = @()
    foreach ($prop in $lv.PSObject.Properties) {
        $lg = $prop.Name; $ver = $prop.Value
        $mp = "$HOME_DIR\engine\cache\models\${lg}_dc_v${ver}.meta.json"
        $n = if (Test-Path $mp) { (Get-Content $mp -Raw | ConvertFrom-Json).nTrain } else { "?" }
        $parts += "$lg v$ver($n)"
    }
    Write-Host "  local DC models: $($parts -join ', ')" -ForegroundColor Green
}
