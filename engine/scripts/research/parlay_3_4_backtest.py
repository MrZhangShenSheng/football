# -*- coding: utf-8 -*-
"""3串1 和 4串1 结构回测 —— 探索多关玩法"""
import json
import sys
import numpy as np
from collections import Counter, defaultdict
from itertools import product, combinations
from pathlib import Path

sys.path.insert(0, str(Path("engine/scripts/research")))

import score_family_model as sfm
from ticket_dynamic_k_backtest import load_hist_full, build_model_packs

UNIT = 2.0

print("=" * 100)
print("多关结构回测 —— 3串1 / 4串1")
print("=" * 100)

# 加载数据
cut = "2026-01-01"
packs = build_model_packs(cut_date=cut)

by_day = defaultdict(list)
for p in packs:
    by_day[p["date"]].append(p)

print(f"共 {len(packs)} 场 / {len(by_day)} 天")


def dynamic_k(gap, threshold=0.05):
    """动态选腿"""
    return 1 if gap > threshold else 2


def simulate_parlay(day_packs, n_legs, gap_threshold=0.05):
    """模拟 n 串 1

    返回：cost, payout, hit, max_mult, n_bets
    """
    if len(day_packs) < n_legs:
        return None

    # 选 gap 最大的 n 场
    sorted_packs = sorted(day_packs, key=lambda p: -p["gap"])[:n_legs]

    # 每场的选择
    all_picks = []
    for p in sorted_packs:
        k = dynamic_k(p["gap"], gap_threshold)
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
        # 检查是否全中
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
    }


def run_backtest_structure(n_legs, gap_threshold=0.05, min_matches=None):
    """回测指定结构"""
    if min_matches is None:
        min_matches = n_legs

    total_cost = 0.0
    total_payout = 0.0
    n_tickets = 0
    n_hits = 0
    max_mult_seen = 0
    bet_counts = Counter()
    hit_details = []

    for day in sorted(by_day):
        day_packs = by_day[day]
        if len(day_packs) < min_matches:
            continue

        result = simulate_parlay(day_packs, n_legs, gap_threshold)
        if result is None:
            continue

        total_cost += result["cost"]
        total_payout += result["payout"]
        n_tickets += 1
        bet_counts[result["n_bets"]] += 1

        if result["hit"]:
            n_hits += 1
            max_mult_seen = max(max_mult_seen, result["max_mult"])
            hit_details.append({
                "date": day,
                "mult": result["max_mult"],
                "payout": result["payout"],
                "gaps": result["gaps"],
            })

    profit = (total_payout - total_cost) / total_cost if total_cost > 0 else 0

    return {
        "n_legs": n_legs,
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "hit_rate": n_hits / n_tickets if n_tickets > 0 else 0,
        "total_cost": total_cost,
        "total_payout": total_payout,
        "profit": profit,
        "avg_cost": total_cost / n_tickets if n_tickets > 0 else 0,
        "max_mult": max_mult_seen,
        "bet_counts": dict(bet_counts),
        "hit_details": hit_details,
    }


# ========================================
# 1. 基础结构对比
# ========================================

print("\n" + "=" * 100)
print("1. 基础结构对比（动态1-2选，gap阈值0.05）")
print("=" * 100)

structures = [2, 3, 4]

print(f"\n{'结构':<10} {'票数':>8} {'命中':>8} {'命中率':>10} {'总成本':>12} {'总派彩':>12} {'盈利率':>12} {'最大倍数':>12}")
print("-" * 100)

results = {}
for n in structures:
    r = run_backtest_structure(n, gap_threshold=0.05)
    results[n] = r
    mark = "✅" if r["profit"] > 0 else ""
    print(f"{n}串1{'':<6} {r['n_tickets']:>8} {r['n_hits']:>8} {r['hit_rate']*100:>9.2f}% "
          f"{r['total_cost']:>12.0f} {r['total_payout']:>12.0f} {r['profit']*100:>+11.1f}%{mark} {r['max_mult']:>11.1f}x")

print("-" * 100)


# ========================================
# 2. 4串1 详细分析
# ========================================

print("\n" + "=" * 100)
print("2. 4串1 详细分析")
print("=" * 100)

r4 = results[4]
print(f"\n基础统计：")
print(f"  票数：{r4['n_tickets']}")
print(f"  命中：{r4['n_hits']} ({r4['hit_rate']*100:.2f}%)")
print(f"  总成本：{r4['total_cost']:.0f} 元")
print(f"  总派彩：{r4['total_payout']:.0f} 元")
print(f"  盈利率：{r4['profit']*100:+.1f}%")
print(f"  注数分布：{r4['bet_counts']}")

if r4['hit_details']:
    print(f"\n命中详情：")
    for h in r4['hit_details']:
        print(f"  {h['date']}: {h['mult']:.1f}x → 派彩 {h['payout']:.0f} 元, gaps={[f'{g:.3f}' for g in h['gaps']]}")


# ========================================
# 3. 4串1 参数优化
# ========================================

print("\n" + "=" * 100)
print("3. 4串1 参数优化（调整 gap 阈值和最小场次）")
print("=" * 100)

gap_thresholds = [0.03, 0.04, 0.05, 0.06, 0.08]
min_matches_list = [4, 5, 6, 8]

print(f"\n{'gap阈值':>10} {'最小场次':>10} {'票数':>8} {'命中':>8} {'成本':>10} {'派彩':>10} {'盈利率':>12}")
print("-" * 80)

best_profit = -999
best_params = None

for gap_th in gap_thresholds:
    for min_m in min_matches_list:
        r = run_backtest_structure(4, gap_threshold=gap_th, min_matches=min_m)
        if r['n_tickets'] == 0:
            continue
        mark = "✅" if r["profit"] > 0 else ""
        print(f"{gap_th:>10.2f} {min_m:>10} {r['n_tickets']:>8} {r['n_hits']:>8} "
              f"{r['total_cost']:>10.0f} {r['total_payout']:>10.0f} {r['profit']*100:>+11.1f}%{mark}")

        if r["profit"] > best_profit:
            best_profit = r["profit"]
            best_params = (gap_th, min_m, r)

print("-" * 80)

if best_params:
    gap_th, min_m, r = best_params
    print(f"\n4串1 最优参数：")
    print(f"  gap 阈值：{gap_th}")
    print(f"  最小场次：{min_m}")
    print(f"  盈利率：{r['profit']*100:+.1f}%")
    print(f"  命中：{r['n_hits']}/{r['n_tickets']} 票")


# ========================================
# 4. 混合策略：2串1 + 4串1
# ========================================

print("\n" + "=" * 100)
print("4. 混合策略：每日同时买 2串1 + 4串1")
print("=" * 100)

# 模拟每日同时买两种
total_cost_mix = 0.0
total_payout_mix = 0.0
n_days = 0
hits_2 = hits_4 = 0

for day in sorted(by_day):
    day_packs = by_day[day]
    if len(day_packs) < 4:  # 至少4场才能同时买
        continue

    n_days += 1

    # 2串1
    r2 = simulate_parlay(day_packs, 2, gap_threshold=0.05)
    if r2:
        total_cost_mix += r2["cost"]
        total_payout_mix += r2["payout"]
        if r2["hit"]:
            hits_2 += 1

    # 4串1
    r4 = simulate_parlay(day_packs, 4, gap_threshold=0.05)
    if r4:
        total_cost_mix += r4["cost"]
        total_payout_mix += r4["payout"]
        if r4["hit"]:
            hits_4 += 1

profit_mix = (total_payout_mix - total_cost_mix) / total_cost_mix if total_cost_mix > 0 else 0

print(f"\n混合策略（每日 2串1 + 4串1）：")
print(f"  有效天数：{n_days}")
print(f"  总成本：{total_cost_mix:.0f} 元")
print(f"  总派彩：{total_payout_mix:.0f} 元")
print(f"  盈利率：{profit_mix*100:+.1f}%")
print(f"  2串1命中：{hits_2} 天")
print(f"  4串1命中：{hits_4} 天")


# ========================================
# 5. 理论分析
# ========================================

print("\n" + "=" * 100)
print("5. 理论分析：4串1 的挑战")
print("=" * 100)

# 假设单场命中率
single_hit_rates = [0.10, 0.15, 0.20, 0.25, 0.30]

print(f"\n假设单场双选命中率，n串1 的理论命中率：")
print(f"{'单场命中率':>12} {'2串1':>10} {'3串1':>10} {'4串1':>10}")
print("-" * 50)

for p in single_hit_rates:
    p2 = p ** 2
    p3 = p ** 3
    p4 = p ** 4
    print(f"{p*100:>11.0f}% {p2*100:>9.2f}% {p3*100:>9.2f}% {p4*100:>9.2f}%")

print(f"\n当前模型单场 top2 命中率约 23%：")
print(f"  理论 2串1 命中率：{0.23**2*100:.2f}%")
print(f"  理论 3串1 命中率：{0.23**3*100:.2f}%")
print(f"  理论 4串1 命中率：{0.23**4*100:.2f}%")
print(f"\n实际观测：")
print(f"  2串1：{results[2]['hit_rate']*100:.2f}%")
print(f"  3串1：{results[3]['hit_rate']*100:.2f}%")
print(f"  4串1：{results[4]['hit_rate']*100:.2f}%")
