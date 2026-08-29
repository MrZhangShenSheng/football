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

# 1.5 skill -> arsenal sync (only when arsenal tracks this skill's dir;
#     football-betting-prediction removed from arsenal 2026-08-29 — no longer maintained there,
#     re-create the dir manually to re-enable sync)
if (Test-Path "D:\project\arsenal\football-betting-prediction") {
    Write-Host "[1.5/7] Syncing skill to arsenal..." -ForegroundColor Yellow
    Copy-Item "$HOME_DIR\skill\SKILL.md" "D:\project\arsenal\football-betting-prediction\SKILL.md" -Force
    fc.exe /b "$HOME_DIR\skill\SKILL.md" "D:\project\arsenal\football-betting-prediction\SKILL.md" | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "    OK: byte-identical" -ForegroundColor Green }
    else { Write-Host "    ARSENAL SYNC FAILED - manual cmp required" -ForegroundColor Red }
}

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

# 6. Verify loop (v4.7: auto-backfill -> corpus+assertions -> calibrate -> ablate, gates auto-skip; v4.10 ticket settle included)
#    v5.1+: boldplay settle is inside run.py verify (idempotent quiet) - no separate call needed
Write-Host "[6/7] Verify loop (run.py verify, incl. ticket settle + ladder-card settle)..." -ForegroundColor Yellow
Push-Location "$HOME_DIR\engine\scripts"
python run.py verify
if ($LASTEXITCODE -ne 0) { Write-Host "    verify partial fail (ESPN cache lag common, next update retries)" -ForegroundColor DarkYellow }
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
$fd = @($lg | Where-Object { ([System.IO.File]::ReadAllText($_.FullName, [System.Text.Encoding]::UTF8)) -match '"computedFrom": "fd"' })
$manual = @($lg | Where-Object { ([System.IO.File]::ReadAllText($_.FullName, [System.Text.Encoding]::UTF8)) -match 'espn-manual|claude-manual|none' })
Write-Host "  fd-coverage (fresh): $($fd.Count) leagues" -ForegroundColor Green
Write-Host "  manual (check on predict): $($manual.Count) leagues" -ForegroundColor DarkYellow
Write-Host "  -> manual leagues auto-refresh via Step 2.5 on next 'predict'" -ForegroundColor DarkGray
# sporttery 5-pool summary (v4.5)
$sm = "$HOME_DIR\engine\cache\sporttery_matches.json"
if (Test-Path $sm) {
    $smd = [System.IO.File]::ReadAllText($sm, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
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
    $cp = [System.IO.File]::ReadAllText($corpusPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $cal = if ($cp.readiness.calibrateReady) { "ready" } else { "gap $($cp.readiness.calibrateGap)" }
    $abl = if ($cp.readiness.ablateReady) { "ready" } else { "waiting" }
    Write-Host "  corpus: $($cp.n_total) records / $($cp.n_rounds) rounds (filled $($cp.readiness.n_result) / CLV $($cp.readiness.n_clv)) | calibrate $cal | ablate $abl" -ForegroundColor Green
    $nPlans = @($cp.plans.PSObject.Properties | Where-Object { $_.Value -is [array] }).Count
    $trendExists = if (Test-Path "$HOME_DIR\data\04-summaries\trend.html") { "yes" } else { "no" }
    Write-Host "  plans tracked: $nPlans | trend.html: $trendExists (logloss/hit-rate/CLV/calibration/buckets/plan-accuracy)" -ForegroundColor Green
}
# attribution ledger summary (P2 2026-08-29: error attribution factors F1-F10 -> ablate gate)
$attrPath = "$HOME_DIR\data\04-summaries\attribution.json"
if (Test-Path $attrPath) {
    $ad = [System.IO.File]::ReadAllText($attrPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $nErr = @($ad.records.PSObject.Properties).Count
    $fs = ($ad.factorStats.PSObject.Properties | ForEach-Object { "$($_.Name):$($_.Value.nPrimary)" }) -join " "
    $cand = if ($ad.ablateCandidates.Count -gt 0) { "ablate-ready: $($ad.ablateCandidates -join ',')" } else { "ablate gate: <20/factor" }
    Write-Host "  attribution: $nErr errors | $fs | $cand -> data/04-summaries/attribution.json" -ForegroundColor Green
}
# ticket ledger summary (v4.10: real-money tickets; a ticket without settlement record is a plan, not a purchase)
$tk = "$HOME_DIR\data\06-tickets\tickets.json"
if (Test-Path $tk) {
    $td = [System.IO.File]::ReadAllText($tk, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $pending = @($td.tickets | Where-Object { $_.settled.status -eq "pending" }).Count
    $net = "{0:+0.0;-0.0}" -f $td.meta.totalNet
    Write-Host "  ticket ledger: $($td.tickets.Count) tickets ($pending pending) | stake $($td.meta.totalStake) | net $net -> data/06-tickets/tickets.html" -ForegroundColor Green
}
$ml = "$HOME_DIR\engine\cache\models\latest.json"
if (Test-Path $ml) {
    $lv = [System.IO.File]::ReadAllText($ml, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $parts = @()
    foreach ($prop in $lv.PSObject.Properties) {
        $lg = $prop.Name; $ver = $prop.Value
        $mp = "$HOME_DIR\engine\cache\models\${lg}_dc_v${ver}.meta.json"
        $n = if (Test-Path $mp) { ([System.IO.File]::ReadAllText($mp, [System.Text.Encoding]::UTF8) | ConvertFrom-Json).nTrain } else { "?" }
        $parts += "$lg v$ver($n)"
    }
    Write-Host "  local DC models: $($parts -join ', ')" -ForegroundColor Green
}
