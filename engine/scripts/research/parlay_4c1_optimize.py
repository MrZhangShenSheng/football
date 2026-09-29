# -*- coding: utf-8 -*-
"""4串1 深度优化 —— 高确定度选场"""
import json
import sys
import numpy as np
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path("engine/scripts/research")))

import score_family_model as sfm
from ticket_dynamic_k_backtest import load_hist_full, build_model_packs

UNIT = 2.0

print("=" * 100)
print("4串1 深度优化 —— 高确定度选场")
print("=" * 100)

# 加载数据
cut = "2026-01-01"
packs = build_model_packs(cut_date=cut)

by_day = defaultdict(list)
for p in packs:
    by_day[p["date"]].append(p)

print(f"共 {len(packs)} 场 / {len(by_day)} 天")


def simulate_4c1_strict(day_packs, min_gap_all=0.03, top_k=4, use_single=True):
    """严格版 4串1

    min_gap_all: 所有4场的 gap 都必须 > 此值
    top_k: 选 gap 最大的 k 场
    use_single: True=全部单选, False=动态选腿
    """
    sorted_packs = sorted(day_packs, key=lambda p: -p["gap"])[:top_k]

    if len(sorted_packs) < 4:
        return None

    # 检查最小 gap
    if sorted_packs[-1]["gap"] < min_gap_all:
        return None

    # 每场的选择
    all_picks = []
    for p in sorted_packs:
        if use_single:
            k = 1
        else:
            k = 1 if p["gap"] > 0.05 else 2
        picks = [(s, p["odds"].get(s, 999), p["actual"])
                 for s, prob in p["model_sorted"][:k]]
        all_picks.append(picks)

    # 所有组合
    all_bets = list(product(*all_picks))
    n_bets = len(all_bets)
    cost = n_bets * UNIT

    payout = 0.0
    hit = False
    max_mult = 0

    for combo in all_bets:
        all_correct = all(s == actual for s, odds, actual in combo)
        if all_correct:
            combo_odds = 1.0
            for s, odds, actual in combo:
                combo_odds *= odds
            payout += UNIT * combo_odds
            hit = True
            max_mult = max(max_mult, combo_odds)

    return {
        "cost": cost,
        "payout": payout,
        "hit": hit,
        "max_mult": max_mult,
        "n_bets": n_bets,
        "gaps": [p["gap"] for p in sorted_packs],
        "actuals": [p["actual"] for p in sorted_packs],
        "top1s": [p["model_sorted"][0][0] for p in sorted_packs],
    }


# ========================================
# 1. 分析每天的 gap 分布
# ========================================

print("\n" + "=" * 100)
print("1. 每日 gap 分布分析")
print("=" * 100)

gap_stats = []
for day in sorted(by_day):
    day_packs = by_day[day]
    if len(day_packs) >= 4:
        sorted_packs = sorted(day_packs, key=lambda p: -p["gap"])[:4]
        gaps = [p["gap"] for p in sorted_packs]
        min_gap = min(gaps)
        gap_stats.append({
            "day": day,
            "n_matches": len(day_packs),
            "gaps": gaps,
            "min_gap": min_gap,
            "avg_gap": sum(gaps) / 4,
        })

print(f"\n有4场以上的天数：{len(gap_stats)}")

# 按 min_gap 分布
min_gap_dist = Counter()
for gs in gap_stats:
    bucket = int(gs["min_gap"] * 100) // 1
    min_gap_dist[bucket] += 1

print(f"\n第4场（最小）gap 分布：")
print(f"  {'gap范围':>10} {'天数':>8} {'占比':>8}")
print("-" * 30)
for bucket in sorted(min_gap_dist.keys(), reverse=True):
    pct = min_gap_dist[bucket] / len(gap_stats) * 100
    print(f"  {bucket:>8}-{bucket+1}% {min_gap_dist[bucket]:>8} {pct:>7.1f}%")


# ========================================
# 2. 不同 min_gap 阈值的 4串1
# ========================================

print("\n" + "=" * 100)
print("2. 不同 min_gap 阈值的 4串1（纯单选）")
print("=" * 100)

min_gap_thresholds = [0.00, 0.01, 0.02, 0.03, 0.04, 0.05]

print(f"\n{'min_gap阈值':>12} {'有效天数':>10} {'命中':>8} {'总成本':>10} {'总派彩':>12} {'盈利率':>12} {'理论需票':>10}")
print("-" * 90)

for min_gap in min_gap_thresholds:
    total_cost = 0.0
    total_payout = 0.0
    n_tickets = 0
    n_hits = 0

    for day in sorted(by_day):
        result = simulate_4c1_strict(by_day[day], min_gap_all=min_gap, use_single=True)
        if result is None:
            continue

        total_cost += result["cost"]
        total_payout += result["payout"]
        n_tickets += 1
        if result["hit"]:
            n_hits += 1

    if n_tickets == 0:
        continue

    profit = (total_payout - total_cost) / total_cost if total_cost > 0 else -1
    # 理论上单选命中率约 12%，4串1 = 0.12^4 = 0.02%
    theory_tickets = int(1 / 0.0002) if min_gap == 0 else "?"
    mark = "✅" if profit > 0 else ""
    print(f"{min_gap:>12.2f} {n_tickets:>10} {n_hits:>8} {total_cost:>10.0f} {total_payout:>12.0f} "
          f"{profit*100:>+11.1f}%{mark} {theory_tickets:>10}")


# ========================================
# 3. 分析 top1 命中情况
# ========================================

print("\n" + "=" * 100)
print("3. 单场 top1 命中率分析（按 gap 分层）")
print("=" * 100)

gap_hit_stats = defaultdict(lambda: {"n": 0, "hit": 0})

for p in packs:
    bucket = int(p["gap"] * 100) // 2 * 2
    gap_hit_stats[bucket]["n"] += 1
    if p["model_sorted"][0][0] == p["actual"]:
        gap_hit_stats[bucket]["hit"] += 1

print(f"\n{'gap范围':>10} {'样本数':>10} {'命中数':>10} {'命中率':>10}")
print("-" * 50)
for bucket in sorted(gap_hit_stats.keys(), reverse=True)[:10]:
    b = gap_hit_stats[bucket]
    if b["n"] >= 10:
        rate = b["hit"] / b["n"] * 100
        print(f"  {bucket:>6}-{bucket+2}% {b['n']:>10} {b['hit']:>10} {rate:>9.1f}%")


# ========================================
# 4. 4串1 需要的最低命中率
# ========================================

print("\n" + "=" * 100)
print("4. 4串1 盈亏平衡分析")
print("=" * 100)

# 假设平均赔率
avg_odds = 8.0  # 单场 top1 平均赔率约 8

print(f"\n假设单场平均赔率 {avg_odds}x：")
print(f"  4串1 平均赔率：{avg_odds**4:.0f}x")
print(f"  每注成本 2 元，命中回报 {avg_odds**4 * 2:.0f} 元")

# 盈亏平衡命中率
breakeven_rate = 1 / (avg_odds ** 4)
print(f"\n盈亏平衡需要命中率：{breakeven_rate*100:.4f}%")
print(f"  即每 {int(1/breakeven_rate):.0f} 票中 1 次")

# 当前单场 top1 命中率约 12%
single_hit_rate = 0.12
actual_4c1_rate = single_hit_rate ** 4
print(f"\n当前单场 top1 命中率 {single_hit_rate*100:.0f}%：")
print(f"  理论 4串1 命中率：{actual_4c1_rate*100:.4f}%")
print(f"  即每 {int(1/actual_4c1_rate):.0f} 票中 1 次")

# 期望收益
ev = actual_4c1_rate * (avg_odds ** 4) - 1
print(f"\n期望收益率：{ev*100:+.1f}%")

if ev > 0:
    print("✅ 理论上 4串1 纯单选有正期望！")
else:
    print("❌ 当前命中率下 4串1 期望为负")
    needed_rate = breakeven_rate ** 0.25
    print(f"   需要单场命中率提升到 {needed_rate*100:.1f}% 才能盈亏平衡")


# ========================================
# 5. 扩大样本：用更早的切分点
# ========================================

print("\n" + "=" * 100)
print("5. 扩大样本验证（切分点 2025-12-01）")
print("=" * 100)

packs_large = build_model_packs(cut_date="2025-12-01")
by_day_large = defaultdict(list)
for p in packs_large:
    by_day_large[p["date"]].append(p)

print(f"共 {len(packs_large)} 场 / {len(by_day_large)} 天")

# 跑 4串1 纯单选
total_cost = 0.0
total_payout = 0.0
n_tickets = 0
n_hits = 0
hit_details = []

for day in sorted(by_day_large):
    result = simulate_4c1_strict(by_day_large[day], min_gap_all=0.0, use_single=True)
    if result is None:
        continue

    total_cost += result["cost"]
    total_payout += result["payout"]
    n_tickets += 1
    if result["hit"]:
        n_hits += 1
        hit_details.append({
            "day": day,
            "mult": result["max_mult"],
            "gaps": result["gaps"],
            "actuals": result["actuals"],
            "top1s": result["top1s"],
        })

profit = (total_payout - total_cost) / total_cost if total_cost > 0 else -1

print(f"\n4串1 纯单选（大样本）：")
print(f"  票数：{n_tickets}")
print(f"  命中：{n_hits} ({n_hits/n_tickets*100:.2f}%)")
print(f"  总成本：{total_cost:.0f} 元")
print(f"  总派彩：{total_payout:.0f} 元")
print(f"  盈利率：{profit*100:+.1f}%")

if hit_details:
    print(f"\n命中详情：")
    for h in hit_details:
        print(f"  {h['day']}: {h['mult']:.0f}x")
        print(f"    gaps: {[f'{g:.3f}' for g in h['gaps']]}")
        print(f"    实际: {h['actuals']}")
        print(f"    预测: {h['top1s']}")
