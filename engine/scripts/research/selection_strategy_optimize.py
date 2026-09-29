# -*- coding: utf-8 -*-
"""比分模型优化 —— 选场策略优化"""
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
print("选场策略优化 —— 不只选 gap 最大")
print("=" * 100)

# 加载数据
packs = build_model_packs(cut_date="2026-01-01")

by_day = defaultdict(list)
for p in packs:
    by_day[p["date"]].append(p)

print(f"共 {len(packs)} 场 / {len(by_day)} 天")


def run_backtest_strategy(packs, select_fn, gap_threshold=0.05):
    """用自定义选场函数回测"""
    by_day = defaultdict(list)
    for p in packs:
        by_day[p["date"]].append(p)

    total_cost = 0.0
    total_payout = 0.0
    n_tickets = 0
    n_hits = 0

    for day in sorted(by_day):
        day_packs = by_day[day]
        if len(day_packs) < 2:
            continue

        # 用选场函数选择2场
        selected = select_fn(day_packs)
        if len(selected) < 2:
            continue

        p1, p2 = selected[0], selected[1]

        k1 = 1 if p1["gap"] > gap_threshold else 2
        k2 = 1 if p2["gap"] > gap_threshold else 2

        picks1 = [(s, p1["odds"].get(s, 999)) for s, prob in p1["model_sorted"][:k1]]
        picks2 = [(s, p2["odds"].get(s, 999)) for s, prob in p2["model_sorted"][:k2]]

        if not picks1 or not picks2:
            continue

        all_bets = list(product(picks1, picks2))
        cost = len(all_bets) * UNIT
        payout = 0.0

        for (s1, o1), (s2, o2) in all_bets:
            if s1 == p1["actual"] and s2 == p2["actual"]:
                payout += UNIT * o1 * o2

        total_cost += cost
        total_payout += payout
        n_tickets += 1
        if payout > 0:
            n_hits += 1

    profit = (total_payout - total_cost) / total_cost if total_cost > 0 else -1
    return {
        "n_tickets": n_tickets,
        "n_hits": n_hits,
        "total_cost": total_cost,
        "total_payout": total_payout,
        "profit": profit,
    }


# ========================================
# 基准：gap 最大的2场
# ========================================

print("\n" + "=" * 100)
print("策略对比")
print("=" * 100)

def select_top_gap(day_packs):
    """选 gap 最大的2场（基准）"""
    return sorted(day_packs, key=lambda p: -p["gap"])[:2]

def select_top_prob(day_packs):
    """选 top1 概率最高的2场"""
    return sorted(day_packs, key=lambda p: -p["model_sorted"][0][1])[:2]

def select_top_odds_value(day_packs):
    """选 (概率 × 赔率) 最高的2场（期望值）"""
    def ev(p):
        s, prob = p["model_sorted"][0]
        odds = p["odds"].get(s, 1)
        return prob * odds
    return sorted(day_packs, key=lambda p: -ev(p))[:2]

def select_low_odds(day_packs):
    """选 top1 赔率最低的2场（更可能命中）"""
    def top1_odds(p):
        s = p["model_sorted"][0][0]
        return p["odds"].get(s, 999)
    return sorted(day_packs, key=top1_odds)[:2]

def select_mid_gap(day_packs):
    """选 gap 中等的2场（避免极端）"""
    sorted_packs = sorted(day_packs, key=lambda p: -p["gap"])
    n = len(sorted_packs)
    if n >= 4:
        return sorted_packs[1:3]  # 第2、3名
    return sorted_packs[:2]

def select_diverse(day_packs):
    """选 gap 最大 + 概率最高（多样化）"""
    by_gap = sorted(day_packs, key=lambda p: -p["gap"])
    by_prob = sorted(day_packs, key=lambda p: -p["model_sorted"][0][1])
    p1 = by_gap[0]
    # 第二场选概率最高但不是第一场
    for p in by_prob:
        if p != p1:
            return [p1, p]
    return by_gap[:2]

def select_combo_score(day_packs):
    """选 (gap × prob × log(odds)) 综合得分最高的2场"""
    import math
    def score(p):
        s, prob = p["model_sorted"][0]
        odds = p["odds"].get(s, 1)
        return p["gap"] * prob * math.log(odds + 1)
    return sorted(day_packs, key=lambda p: -score(p))[:2]

strategies = [
    ("基准：gap 最大", select_top_gap),
    ("top1 概率最高", select_top_prob),
    ("期望值最高", select_top_odds_value),
    ("赔率最低", select_low_odds),
    ("gap 中等", select_mid_gap),
    ("多样化选场", select_diverse),
    ("综合得分", select_combo_score),
]

print(f"\n{'策略':<20} {'盈利率':>12} {'命中':>8} {'成本':>10} {'派彩':>10}")
print("-" * 65)

results = []
for name, fn in strategies:
    r = run_backtest_strategy(packs, fn)
    mark = "✅" if r["profit"] > 0 else ""
    print(f"{name:<20} {r['profit']*100:>+11.2f}% {r['n_hits']:>8} {r['total_cost']:>10.0f} {r['total_payout']:>10.0f} {mark}")
    results.append((name, r))

print("-" * 65)

best = max(results, key=lambda x: x[1]["profit"])
print(f"\n最优策略：{best[0]}")
print(f"  盈利率：{best[1]['profit']*100:+.2f}%")


# ========================================
# 更多 gap 阈值测试
# ========================================

print("\n" + "=" * 100)
print("Gap 阈值优化（基准选场策略）")
print("=" * 100)

gap_thresholds = [0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10]

print(f"\n{'gap阈值':>10} {'盈利率':>12} {'命中':>8} {'成本':>10} {'派彩':>10}")
print("-" * 55)

for th in gap_thresholds:
    r = run_backtest_strategy(packs, select_top_gap, gap_threshold=th)
    mark = "✅" if r["profit"] > 0 else ""
    print(f"{th:>10.2f} {r['profit']*100:>+11.2f}% {r['n_hits']:>8} {r['total_cost']:>10.0f} {r['total_payout']:>10.0f} {mark}")


# ========================================
# 组合：最优选场 + 最优阈值
# ========================================

print("\n" + "=" * 100)
print("网格搜索：选场策略 × gap阈值")
print("=" * 100)

best_profit = -999
best_combo = None

print(f"\n{'策略':<20} {'gap阈值':>10} {'盈利率':>12} {'命中':>8}")
print("-" * 55)

for name, fn in strategies:
    for th in gap_thresholds:
        r = run_backtest_strategy(packs, fn, gap_threshold=th)
        if r["profit"] > 0:
            print(f"{name:<20} {th:>10.2f} {r['profit']*100:>+11.2f}% {r['n_hits']:>8} ✅")
        if r["profit"] > best_profit:
            best_profit = r["profit"]
            best_combo = (name, th, r)

print("-" * 55)
if best_combo:
    print(f"\n最优组合：{best_combo[0]} + gap阈值={best_combo[1]}")
    print(f"  盈利率：{best_combo[2]['profit']*100:+.2f}%")
    print(f"  命中：{best_combo[2]['n_hits']}/{best_combo[2]['n_tickets']}")
