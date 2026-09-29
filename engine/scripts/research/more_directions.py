# -*- coding: utf-8 -*-
"""更多优化方向探索"""
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
print("更多优化方向探索")
print("=" * 100)

# 加载数据
packs = build_model_packs(cut_date="2026-01-01")

by_day = defaultdict(list)
for p in packs:
    by_day[p["date"]].append(p)

print(f"共 {len(packs)} 场 / {len(by_day)} 天")


def run_backtest_n_matches(packs, n_matches=2, gap_threshold=0.05):
    """回测 n 场串关"""
    by_day = defaultdict(list)
    for p in packs:
        by_day[p["date"]].append(p)

    total_cost = 0.0
    total_payout = 0.0
    n_tickets = 0
    n_hits = 0

    for day in sorted(by_day):
        day_packs = by_day[day]
        if len(day_packs) < n_matches:
            continue

        selected = sorted(day_packs, key=lambda p: -p["gap"])[:n_matches]

        all_picks = []
        for p in selected:
            k = 1 if p["gap"] > gap_threshold else 2
            picks = [(s, p["odds"].get(s, 999), p["actual"])
                     for s, prob in p["model_sorted"][:k]]
            all_picks.append(picks)

        all_bets = list(product(*all_picks))
        cost = len(all_bets) * UNIT
        payout = 0.0

        for combo in all_bets:
            all_correct = all(s == actual for s, odds, actual in combo)
            if all_correct:
                combo_odds = 1.0
                for s, odds, actual in combo:
                    combo_odds *= odds
                payout += UNIT * combo_odds

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
# 方向 H：选场数量优化
# ========================================

print("\n" + "=" * 100)
print("方向 H：选场数量优化（1场、2场、3场）")
print("=" * 100)

print(f"\n{'结构':<15} {'票数':>8} {'命中':>8} {'成本':>10} {'派彩':>10} {'盈利率':>12}")
print("-" * 65)

for n in [1, 2, 3]:
    r = run_backtest_n_matches(packs, n_matches=n)
    mark = "✅" if r["profit"] > 0 else ""
    struct_name = f"{n}串1" if n > 1 else "单关"
    print(f"{struct_name:<15} {r['n_tickets']:>8} {r['n_hits']:>8} {r['total_cost']:>10.0f} {r['total_payout']:>10.0f} {r['profit']*100:>+11.2f}% {mark}")


# ========================================
# 方向 I：按每日场次数过滤
# ========================================

print("\n" + "=" * 100)
print("方向 I：按每日场次数过滤")
print("=" * 100)

def run_backtest_filtered(packs, min_matches=2, max_matches=999, gap_threshold=0.05):
    """只在特定场次数的日子下注"""
    by_day = defaultdict(list)
    for p in packs:
        by_day[p["date"]].append(p)

    total_cost = 0.0
    total_payout = 0.0
    n_tickets = 0
    n_hits = 0

    for day in sorted(by_day):
        day_packs = by_day[day]
        if len(day_packs) < min_matches or len(day_packs) > max_matches:
            continue

        selected = sorted(day_packs, key=lambda p: -p["gap"])[:2]
        p1, p2 = selected[0], selected[1]

        k1 = 1 if p1["gap"] > gap_threshold else 2
        k2 = 1 if p2["gap"] > gap_threshold else 2

        picks1 = [(s, p1["odds"].get(s, 999)) for s, prob in p1["model_sorted"][:k1]]
        picks2 = [(s, p2["odds"].get(s, 999)) for s, prob in p2["model_sorted"][:k2]]

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

# 分析每日场次分布
day_counts = [len(day_packs) for day_packs in by_day.values()]
print(f"\n每日场次分布：")
print(f"  最小：{min(day_counts)}，最大：{max(day_counts)}，平均：{sum(day_counts)/len(day_counts):.1f}")

count_dist = Counter(day_counts)
print(f"  分布：", end="")
for c in sorted(count_dist.keys())[:10]:
    print(f"{c}场:{count_dist[c]}天  ", end="")
print()

print(f"\n{'过滤条件':<20} {'票数':>8} {'命中':>8} {'成本':>10} {'派彩':>10} {'盈利率':>12}")
print("-" * 75)

filters = [
    ("全部（基准）", 2, 999),
    (">=5场的日子", 5, 999),
    (">=8场的日子", 8, 999),
    (">=10场的日子", 10, 999),
    (">=12场的日子", 12, 999),
    ("5-10场的日子", 5, 10),
    ("<=8场的日子", 2, 8),
]

for name, min_m, max_m in filters:
    r = run_backtest_filtered(packs, min_matches=min_m, max_matches=max_m)
    if r["n_tickets"] == 0:
        continue
    mark = "✅" if r["profit"] > 0 else ""
    print(f"{name:<20} {r['n_tickets']:>8} {r['n_hits']:>8} {r['total_cost']:>10.0f} {r['total_payout']:>10.0f} {r['profit']*100:>+11.2f}% {mark}")


# ========================================
# 方向 J：按 gap 质量过滤
# ========================================

print("\n" + "=" * 100)
print("方向 J：按 gap 质量过滤（只在高质量日子下注）")
print("=" * 100)

def run_backtest_gap_filter(packs, min_top2_gap=0.0, gap_threshold=0.05):
    """只在 top2 的 gap 都大于阈值时下注"""
    by_day = defaultdict(list)
    for p in packs:
        by_day[p["date"]].append(p)

    total_cost = 0.0
    total_payout = 0.0
    n_tickets = 0
    n_hits = 0
    skipped = 0

    for day in sorted(by_day):
        day_packs = by_day[day]
        if len(day_packs) < 2:
            continue

        selected = sorted(day_packs, key=lambda p: -p["gap"])[:2]

        # 检查第2场的 gap 是否足够
        if selected[1]["gap"] < min_top2_gap:
            skipped += 1
            continue

        p1, p2 = selected[0], selected[1]

        k1 = 1 if p1["gap"] > gap_threshold else 2
        k2 = 1 if p2["gap"] > gap_threshold else 2

        picks1 = [(s, p1["odds"].get(s, 999)) for s, prob in p1["model_sorted"][:k1]]
        picks2 = [(s, p2["odds"].get(s, 999)) for s, prob in p2["model_sorted"][:k2]]

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
        "skipped": skipped,
        "total_cost": total_cost,
        "total_payout": total_payout,
        "profit": profit,
    }

print(f"\n{'第2场最小gap':>15} {'票数':>8} {'跳过':>8} {'命中':>8} {'成本':>10} {'盈利率':>12}")
print("-" * 70)

for min_gap in [0.00, 0.01, 0.02, 0.03, 0.04, 0.05]:
    r = run_backtest_gap_filter(packs, min_top2_gap=min_gap)
    if r["n_tickets"] == 0:
        continue
    mark = "✅" if r["profit"] > 0 else ""
    print(f"{min_gap:>15.2f} {r['n_tickets']:>8} {r['skipped']:>8} {r['n_hits']:>8} {r['total_cost']:>10.0f} {r['profit']*100:>+11.2f}% {mark}")


# ========================================
# 方向 K：周中 vs 周末
# ========================================

print("\n" + "=" * 100)
print("方向 K：周中 vs 周末表现")
print("=" * 100)

from datetime import datetime

def run_backtest_weekday(packs, weekdays=None, gap_threshold=0.05):
    """按星期几过滤"""
    by_day = defaultdict(list)
    for p in packs:
        by_day[p["date"]].append(p)

    total_cost = 0.0
    total_payout = 0.0
    n_tickets = 0
    n_hits = 0

    for day in sorted(by_day):
        # 解析星期几
        dt = datetime.strptime(day, "%Y-%m-%d")
        wd = dt.weekday()  # 0=周一, 6=周日

        if weekdays is not None and wd not in weekdays:
            continue

        day_packs = by_day[day]
        if len(day_packs) < 2:
            continue

        selected = sorted(day_packs, key=lambda p: -p["gap"])[:2]
        p1, p2 = selected[0], selected[1]

        k1 = 1 if p1["gap"] > gap_threshold else 2
        k2 = 1 if p2["gap"] > gap_threshold else 2

        picks1 = [(s, p1["odds"].get(s, 999)) for s, prob in p1["model_sorted"][:k1]]
        picks2 = [(s, p2["odds"].get(s, 999)) for s, prob in p2["model_sorted"][:k2]]

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

print(f"\n{'时段':<20} {'票数':>8} {'命中':>8} {'成本':>10} {'派彩':>10} {'盈利率':>12}")
print("-" * 70)

time_filters = [
    ("全部（基准）", None),
    ("周一", [0]),
    ("周二", [1]),
    ("周三", [2]),
    ("周四", [3]),
    ("周五", [4]),
    ("周六", [5]),
    ("周日", [6]),
    ("周中（一~五）", [0, 1, 2, 3, 4]),
    ("周末（六日）", [5, 6]),
]

for name, wds in time_filters:
    r = run_backtest_weekday(packs, weekdays=wds)
    if r["n_tickets"] == 0:
        continue
    mark = "✅" if r["profit"] > 0 else ""
    print(f"{name:<20} {r['n_tickets']:>8} {r['n_hits']:>8} {r['total_cost']:>10.0f} {r['total_payout']:>10.0f} {r['profit']*100:>+11.2f}% {mark}")


# ========================================
# 方向 L：top1 预测比分类型过滤
# ========================================

print("\n" + "=" * 100)
print("方向 L：按 top1 预测比分类型过滤")
print("=" * 100)

def get_score_type(score):
    """比分类型分类"""
    h, a = score
    total = h + a
    if h == a:
        return "平局"
    elif total <= 2:
        return "小比分"
    elif total >= 4:
        return "大比分"
    else:
        return "中等比分"

# 分析预测比分类型
pred_types = Counter()
for p in packs:
    t = get_score_type(p["model_sorted"][0][0])
    pred_types[t] += 1

print(f"\n预测比分类型分布：")
for t, c in pred_types.most_common():
    print(f"  {t}: {c} ({c/len(packs)*100:.1f}%)")

def run_backtest_score_type(packs, allowed_types=None, gap_threshold=0.05):
    """只选特定比分类型的场次"""
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

        # 过滤：只保留特定比分类型
        if allowed_types:
            day_packs = [p for p in day_packs
                        if get_score_type(p["model_sorted"][0][0]) in allowed_types]

        if len(day_packs) < 2:
            continue

        selected = sorted(day_packs, key=lambda p: -p["gap"])[:2]
        p1, p2 = selected[0], selected[1]

        k1 = 1 if p1["gap"] > gap_threshold else 2
        k2 = 1 if p2["gap"] > gap_threshold else 2

        picks1 = [(s, p1["odds"].get(s, 999)) for s, prob in p1["model_sorted"][:k1]]
        picks2 = [(s, p2["odds"].get(s, 999)) for s, prob in p2["model_sorted"][:k2]]

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

print(f"\n{'比分类型过滤':<25} {'票数':>8} {'命中':>8} {'成本':>10} {'盈利率':>12}")
print("-" * 70)

type_filters = [
    ("全部（基准）", None),
    ("只选平局预测", ["平局"]),
    ("只选小比分预测", ["小比分"]),
    ("只选中等比分预测", ["中等比分"]),
    ("排除平局", ["小比分", "中等比分", "大比分"]),
    ("平局+小比分", ["平局", "小比分"]),
]

for name, types in type_filters:
    r = run_backtest_score_type(packs, allowed_types=types)
    if r["n_tickets"] == 0:
        continue
    mark = "✅" if r["profit"] > 0 else ""
    print(f"{name:<25} {r['n_tickets']:>8} {r['n_hits']:>8} {r['total_cost']:>10.0f} {r['profit']*100:>+11.2f}% {mark}")
