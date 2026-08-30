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
$LIVE_SKILL_LINK = "$SKILL_DIR\football-live-assessment"
if (Test-Path $LIVE_SKILL_LINK) { cmd /c rmdir "$LIVE_SKILL_LINK" }
cmd /c mklink /J "$LIVE_SKILL_LINK" "$HOME_DIR\skill\football-live-assessment"
if ((Test-Path "$SKILL_LINK\SKILL.md") -and (Test-Path "$LIVE_SKILL_LINK\SKILL.md")) {
    Write-Host "    OK: prediction + live assessment skills" -ForegroundColor Green
} else {
    Write-Host "    junction failed" -ForegroundColor Red; exit 1
}

# 2.5 skill -> arsenal sync (only when arsenal tracks this skill's dir;
#     football-betting-prediction removed from arsenal 2026-08-29 — no longer maintained there,
#     re-create the dir manually to re-enable sync)
if (Test-Path "D:\project\arsenal\football-betting-prediction") {
    Write-Host "    syncing to arsenal..." -ForegroundColor Yellow
    Copy-Item "$HOME_DIR\skill\SKILL.md" "D:\project\arsenal\football-betting-prediction\SKILL.md" -Force
    fc.exe /b "$HOME_DIR\skill\SKILL.md" "D:\project\arsenal\football-betting-prediction\SKILL.md" | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "    OK: arsenal byte-identical" -ForegroundColor Green }
    else { Write-Host "    ARSENAL SYNC FAILED - manual cmp required" -ForegroundColor Red }
}

# 3. Persist FOOTBALL_HOME (needs new terminal to take effect)
Write-Host "[3/4] Setting FOOTBALL_HOME..." -ForegroundColor Yellow
[Environment]::SetEnvironmentVariable("FOOTBALL_HOME", $HOME_DIR, "User")
Write-Host "    OK (reopen terminal)" -ForegroundColor Green

# 4. Data init
Write-Host "[4/4] Data init (run.py all + sporttery 5-pool + ESPN history)..." -ForegroundColor Yellow
Push-Location "$HOME_DIR\engine\scripts"
python run.py all
if ($LASTEXITCODE -ne 0) { Write-Host "    run.py all partial fail (data source may be down, retry later)" -ForegroundColor DarkYellow }
python sporttery_fetch.py
if ($LASTEXITCODE -ne 0) { Write-Host "    sporttery_fetch fail (API may throttle, auto-retry on predict)" -ForegroundColor DarkYellow }
# non-fd leagues: ESPN history backfill + local DC fit (idempotent via models/ gate)
python espn_fetch.py history jpn.1 2025
if ($LASTEXITCODE -ne 0) { Write-Host "    jpn history fail (retry: run.py learn)" -ForegroundColor DarkYellow }
python espn_fetch.py history ksa.1 2025
if ($LASTEXITCODE -ne 0) { Write-Host "    ksa history fail" -ForegroundColor DarkYellow }
python espn_fetch.py history swe.1 2025
if ($LASTEXITCODE -ne 0) { Write-Host "    swe history fail" -ForegroundColor DarkYellow }
# K-League via sporttery league-results (v4.8; ESPN has no K-League data)
python sporttery_fetch.py league-results korea
if ($LASTEXITCODE -ne 0) { Write-Host "    korea history fail (re-run anytime)" -ForegroundColor DarkYellow }
python dc_fit.py japan --source local --publish
python dc_fit.py saudi --source local --publish
python dc_fit.py sweden --source local --publish
python dc_fit.py korea --source local --publish
Pop-Location

Write-Host "`n=== Install done ===" -ForegroundColor Cyan
Write-Host "Test:     cd engine\scripts; python -m pytest ..\tests -q"
Write-Host "Use:      tell Claude 'predict' in new terminal"
Write-Host "          after buying tickets, say 'wo mai le' (我买了) -> ticket ledger (data/06-tickets/, git = proof)"
Write-Host "Update:   ./update.ps1"
Write-Host ""
Write-Host "Knowledge base ready status:" -ForegroundColor DarkGray
$lg = Get-ChildItem "$HOME_DIR\data\00-leagues\*.json" -ErrorAction SilentlyContinue
$fd = @($lg | Where-Object { ([System.IO.File]::ReadAllText($_.FullName, [System.Text.Encoding]::UTF8)) -match '"computedFrom": "fd"' })
$espn = @($lg | Where-Object { ([System.IO.File]::ReadAllText($_.FullName, [System.Text.Encoding]::UTF8)) -match '"standingsSource": "espn' })
$cn = @($lg | Where-Object { ([System.IO.File]::ReadAllText($_.FullName, [System.Text.Encoding]::UTF8)) -match '"standingsSource": "titan007"' })
Write-Host "  fd-coverage (auto): $($fd.Count) leagues" -ForegroundColor Green
Write-Host "  espn-fetch (auto): $($espn.Count) leagues" -ForegroundColor Green
Write-Host "  titan007 fallback (auto): $($cn.Count) leagues" -ForegroundColor Green
Write-Host "  -> non-fd leagues (JPN/KSA/Nordic) auto-init on first 'predict' via Step 2.5" -ForegroundColor DarkGray
# sporttery 5-pool check (v4.5: crs/ttg/hafu odds + poolSingle)
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
} else {
    Write-Host "  sporttery 5-pool: not fetched (sporttery_fetch.py runs on predict)" -ForegroundColor DarkYellow
}
# local DC model versions (v4.5.1 learning loop)
$modelsLatest = "$HOME_DIR\engine\cache\models\latest.json"
if (Test-Path $modelsLatest) {
    $lv = [System.IO.File]::ReadAllText($modelsLatest, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $parts = @()
    foreach ($prop in $lv.PSObject.Properties) {
        $lg = $prop.Name; $ver = $prop.Value
        $mp = "$HOME_DIR\engine\cache\models\${lg}_dc_v${ver}.meta.json"
        $n = if (Test-Path $mp) { ([System.IO.File]::ReadAllText($mp, [System.Text.Encoding]::UTF8) | ConvertFrom-Json).nTrain } else { "?" }
        $parts += "$lg v$ver($n matches)"
    }
    Write-Host "  local DC models: $($parts -join ', ')" -ForegroundColor Green
} else {
    Write-Host "  local DC models: none published (retry step 4 espn history)" -ForegroundColor DarkYellow
}
# first-run verify loop (v4.7: backfill + corpus + trend; corpus history arrives via git; v4.10 ticket settle included)
Push-Location "$HOME_DIR\engine\scripts"
python run.py verify | Out-Null
Pop-Location
if (Test-Path "$HOME_DIR\data\04-summaries\trend.html") {
    Write-Host "  verify loop: trend.html ready (run.py verify wired: backfill->ticket settle->assertions->calibrate->ablate)" -ForegroundColor Green
} else {
    Write-Host "  verify loop: generation failed (re-run: run.py verify)" -ForegroundColor DarkYellow
}
# ticket ledger summary (v4.10: real-money tickets arrive via git history)
$tkInstall = "$HOME_DIR\data\06-tickets\tickets.json"
if (Test-Path $tkInstall) {
    $tkd = [System.IO.File]::ReadAllText($tkInstall, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $pendingI = @($tkd.tickets | Where-Object { $_.settled.status -eq "pending" }).Count
    $netI = "{0:+0.0;-0.0}" -f $tkd.meta.totalNet
    Write-Host "  ticket ledger: $($tkd.tickets.Count) tickets ($pendingI pending) | stake $($tkd.meta.totalStake) | net $netI -> data/06-tickets/tickets.html" -ForegroundColor Green
}
# intel timeline summary (v5.4: odds diff-chain / intel / livescan; auto-snapshot on refresh)
$trendsDir = "$HOME_DIR\data\05-trends"
$oddsFiles = @(Get-ChildItem "$trendsDir\*-odds.json" -ErrorAction SilentlyContinue)
if ($oddsFiles.Count -gt 0) {
    $snaps = 0; $entries = 0; $scans = 0
    foreach ($f in $oddsFiles) { $snaps += @(([System.IO.File]::ReadAllText($f.FullName, [System.Text.Encoding]::UTF8) | ConvertFrom-Json).snapshots).Count }
    foreach ($f in @(Get-ChildItem "$trendsDir\*-intel.json" -ErrorAction SilentlyContinue)) { $entries += @(([System.IO.File]::ReadAllText($f.FullName, [System.Text.Encoding]::UTF8) | ConvertFrom-Json).entries).Count }
    foreach ($f in @(Get-ChildItem "$trendsDir\*-livescan.json" -ErrorAction SilentlyContinue)) { $scans += @(([System.IO.File]::ReadAllText($f.FullName, [System.Text.Encoding]::UTF8) | ConvertFrom-Json).scans).Count }
    Write-Host "  intel timeline: $($oddsFiles.Count) day(s) odds chain / $snaps snapshots | intel $entries | livescan $scans (preSnapshots bridge on backfill)" -ForegroundColor Green
} else {
    Write-Host "  intel timeline: not yet on disk (sporttery_fetch auto-creates on first refresh)" -ForegroundColor DarkYellow
}
