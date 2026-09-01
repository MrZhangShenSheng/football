#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DC 参数诊断：检查过拟合、异常值、隐含进球、主场优势合理性。"""
import json, math, glob, os
from pathlib import Path

CACHE = Path("engine/cache")

LEAGUES = [
    "england-premier", "spain-laliga", "germany-bundesliga",
    "italy-serie-a", "france-ligue1", "netherlands-eredivisie",
    "portugal-liga", "portugal-primeira", "turkey-super-lig",
    "belgium-first-a", "greece-super",
    "england-championship", "germany-bundesliga2",
    "italy-serie-b", "france-ligue2",
]

print("=" * 90)
print("DC 参数诊断报告 — 2526赛季")
print("=" * 90)

summary = []

for lg in LEAGUES:
    fp = CACHE / f"{lg}_dc.json"
    if not fp.exists():
        continue
    d = json.loads(fp.read_text(encoding="utf-8"))
    teams = d["teams"]
    n = len(teams)
    if n == 0:
        continue
    attacks = [v["attack"] for v in teams.values()]
    defenses = [v["defense"] for v in teams.values()]
    a_mean = sum(attacks) / n
    a_std = (sum((x - a_mean) ** 2 for x in attacks) / n) ** 0.5
    d_mean = sum(defenses) / n
    d_std = (sum((x - d_mean) ** 2 for x in defenses) / n) ** 0.5

    a_outliers = [
        (t, round(v["attack"], 3), round((v["attack"] - a_mean) / max(a_std, 0.01), 1))
        for t, v in teams.items()
        if a_std > 0 and abs((v["attack"] - a_mean) / a_std) > 2.0
    ]
    d_outliers = [
        (t, round(v["defense"], 3), round((v["defense"] - d_mean) / max(d_std, 0.01), 1))
        for t, v in teams.items()
        if d_std > 0 and abs((v["defense"] - d_mean) / d_std) > 2.0
    ]

    implied_home = math.exp(a_mean + d_mean + d["homeAdv"])
    implied_away = math.exp(a_mean + d_mean)

    print(f"\n{'─' * 90}")
    print(f"  {lg}  ({n}队, {d['matchesUsed']}场, {d['dateRange'][0]}~{d['dateRange'][1]})")
    print(f"{'─' * 90}")
    print(f"  homeAdv={d['homeAdv']:.4f}  rho={d['rho']:.4f}  xi={d['xi']}")
    print(f"  attack:  mean={a_mean:+.3f}  std={a_std:.3f}  range=[{min(attacks):+.3f}, {max(attacks):+.3f}]")
    print(f"  defense: mean={d_mean:+.3f}  std={d_std:.3f}  range=[{min(defenses):+.3f}, {max(defenses):+.3f}]")
    print(f"  隐含场均进球: 主={implied_home:.2f}  客={implied_away:.2f}  总={implied_home + implied_away:.2f}")

    issues = []
    if d["homeAdv"] < 0.15:
        issues.append("主场优势过低(<0.15)")
    if d["homeAdv"] > 0.50:
        issues.append("主场优势过高(>0.50)")
    if abs(d["rho"]) > 0.25:
        issues.append(f"|rho|过高({d['rho']})")
    for t, val, z in a_outliers:
        if abs(val) > 0.8:
            issues.append(f"{t} attack极端({val:+.3f},z={z})")
    for t, val, z in d_outliers:
        if abs(val) > 1.2:
            issues.append(f"{t} defense极端({val:+.3f},z={z})")
    if implied_home + implied_away < 1.5:
        issues.append(f"隐含总进球偏低({implied_home + implied_away:.2f})")
    if implied_home + implied_away > 4.0:
        issues.append(f"隐含总进球偏高({implied_home + implied_away:.2f})")

    if issues:
        for iss in issues:
            print(f"  🔴 {iss}")
    else:
        print(f"  ✅ 参数正常")

    # Backtest result if available
    bt_path = Path("data/04-summaries") / f"backtest_{lg}_2526.json"
    if bt_path.exists():
        bt = json.loads(bt_path.read_text(encoding="utf-8"))
        m = bt["metrics"]
        print(f"  📊 RPS: 市场={m['market_only']['rps']}  融合={m['fused']['rps']}  DC={m['dc_only']['rps']}")
        gap = m["fused"]["rps"] - m["market_only"]["rps"]
        tag = "✅" if gap < 0 else "❌"
        print(f"     融合vs市场: {gap:+.4f} {tag}")

    summary.append({
        "league": lg,
        "homeAdv": d["homeAdv"],
        "rho": d["rho"],
        "a_std": round(a_std, 3),
        "d_std": round(d_std, 3),
        "implied_goals": round(implied_home + implied_away, 2),
        "n_outliers": len(a_outliers) + len(d_outliers),
        "issues": issues,
    })

print(f"\n{'=' * 90}")
print("问题汇总")
print("=" * 90)
for s in summary:
    if s["issues"]:
        print(f"  {s['league']}: {'; '.join(s['issues'])}")
    else:
        print(f"  {s['league']}: ✅")
