# -*- coding: utf-8 -*-
"""4串1 三方向优化实验"""
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
print("4串1 三方向优化实验")
print("=" * 100)

# 加载数据
cut = "2026-01-01"
packs = build_model_packs(cut_date=cut)

by_day = defaultdict(list)
for p in packs:
    by_day[p["date"]].append(p)

print(f"共 {len(packs)} 场 / {len(by_day)} 天")


# ========================================
# 方向 1：提升单场命中率 —— 用 top3 代替 top1
# ========================================

print("\n" + "=" * 100)
print("方向 1：提升单场命中率 —— 扩大选择范围")
print("=" * 100)

# 分析不同 topK 的命中率
for k in [1, 2, 3, 4, 5]:
    hit = 0
    for p in packs:
        topk_scores = [s for s, prob in p["model_sorted"][:k]]
        if p["actual"] in topk_scores:
            hit += 1
    print(f"  Top{k} 命中率：{hit}/{len(packs)} = {hit/len(packs)*100:.1f}%")


def simulate_4c1_topk(day_packs, top_k=1):
    """4串1 用 topK 选择"""
    if len(day_packs) < 4:
        return None

    sorted_packs = sorted(day_packs, key=lambda p: -p["gap"])[:4]

    all_picks = []
    for p in sorted_packs:
        picks = [(s, p["odds"].get(s, 999), p["actual"])
                 for s, prob in p["model_sorted"][:top_k]]
        all_picks.append(picks)

    all_bets = list(product(*all_picks))
    n_bets = len(all_bets)
    cost = n_bets * UNIT

    payout = 0.0
    hit = False

    for combo in all_bets:
        all_correct = all(s == actual for s, odds, actual in combo)
        if all_correct:
            combo_odds = 1.0
            for s, odds, actual in combo:
                combo_odds *= odds
            payout += UNIT * combo_odds
            hit = True

    return {"cost": cost, "payout": payout, "hit": hit, "n_bets": n_bets}


print(f"\n4串1 不同 topK 回测：")
print(f"{'topK':>6} {'票数':>8} {'注数/票':>10} {'命中':>8} {'总成本':>12} {'总派彩':>12} {'盈利率':>12}")
print("-" * 80)

for top_k in [1, 2, 3]:
    total_cost = 0.0
    total_payout = 0.0
    n_tickets = 0
    n_hits = 0

    for day in sorted(by_day):
        result = simulate_4c1_topk(by_day[day], top_k=top_k)
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
    avg_bets = total_cost / n_tickets / UNIT
    mark = "✅" if profit > 0 else ""
    print(f"{top_k:>6} {n_tickets:>8} {avg_bets:>10.1f} {n_hits:>8} {total_cost:>12.0f} {total_payout:>12.0f} {profit*100:>+11.1f}%{mark}")


# ========================================
# 方向 2：4串11 容错结构
# ========================================

print("\n" + "=" * 100)
print("方向 2：4串11 容错结构（允许错1场）")
print("=" * 100)

def simulate_4c11(day_packs, top_k=1):
    """4串11 = 4串1 + 4个3串1

    允许错1场：
    - 全中：4串1 派彩
    - 错1场：对应的 3串1 派彩
    """
    if len(day_packs) < 4:
        return None

    sorted_packs = sorted(day_packs, key=lambda p: -p["gap"])[:4]

    all_picks = []
    for p in sorted_packs:
        picks = [(s, p["odds"].get(s, 999), p["actual"])
                 for s, prob in p["model_sorted"][:top_k]]
        all_picks.append(picks)

    # 4串11 = 1个4串1 + 4个3串1 = 5倍注数
    all_bets_4c1 = list(product(*all_picks))
    n_bets_4c1 = len(all_bets_4c1)
    n_bets_3c1 = n_bets_4c1  # 每个3串1注数相同
    n_bets_total = n_bets_4c1 + 4 * n_bets_3c1  # 实际是 C(4,3) 个3串1

    # 简化：4串11 实际是 11注（4串1×1 + 3串1×4 + 2串1×6）
    # 但我们只算 4串1 + 4个3串1
    cost = n_bets_4c1 * UNIT * 5  # 5倍（1个4串1 + 4个3串1）

    payout = 0.0
    hit_type = None

    for combo in all_bets_4c1:
        correct_count = sum(1 for s, odds, actual in combo if s == actual)

        if correct_count == 4:
            # 全中：4串1 派彩
            combo_odds = 1.0
            for s, odds, actual in combo:
                combo_odds *= odds
            payout += UNIT * combo_odds
            hit_type = "4c1"
        elif correct_count == 3:
            # 错1场：计算对应的 3串1
            for i in range(4):
                # 跳过第 i 场，计算剩余 3 场
                sub_correct = sum(1 for j, (s, odds, actual) in enumerate(combo)
                                  if j != i and s == actual)
                if sub_correct == 3:
                    sub_odds = 1.0
                    for j, (s, odds, actual) in enumerate(combo):
                        if j != i:
                            sub_odds *= odds
                    payout += UNIT * sub_odds
                    hit_type = "3c1"

    return {"cost": cost, "payout": payout, "hit": hit_type is not None,
            "hit_type": hit_type, "n_bets": n_bets_total}


print(f"\n4串11 回测（topK=1）：")

total_cost = 0.0
total_payout = 0.0
n_tickets = 0
n_hits_4c1 = 0
n_hits_3c1 = 0
hit_details = []

for day in sorted(by_day):
    result = simulate_4c11(by_day[day], top_k=1)
    if result is None:
        continue
    total_cost += result["cost"]
    total_payout += result["payout"]
    n_tickets += 1
    if result["hit"]:
        if result["hit_type"] == "4c1":
            n_hits_4c1 += 1
        else:
            n_hits_3c1 += 1
        hit_details.append({"day": day, "type": result["hit_type"], "payout": result["payout"]})

profit = (total_payout - total_cost) / total_cost if total_cost > 0 else -1
print(f"  票数：{n_tickets}")
print(f"  命中：4串1 {n_hits_4c1} 次，3串1 {n_hits_3c1} 次")
print(f"  总成本：{total_cost:.0f} 元")
print(f"  总派彩：{total_payout:.0f} 元")
print(f"  盈利率：{profit*100:+.1f}%")

if hit_details:
    print(f"\n命中详情：")
    for h in hit_details:
        print(f"  {h['day']}: {h['type']} → {h['payout']:.0f} 元")


# 不同 topK 的 4串11
print(f"\n4串11 不同 topK：")
print(f"{'topK':>6} {'票数':>8} {'4c1命中':>10} {'3c1命中':>10} {'总成本':>12} {'总派彩':>12} {'盈利率':>12}")
print("-" * 90)

for top_k in [1, 2]:
    total_cost = 0.0
    total_payout = 0.0
    n_tickets = 0
    n_hits_4c1 = 0
    n_hits_3c1 = 0

    for day in sorted(by_day):
        result = simulate_4c11(by_day[day], top_k=top_k)
        if result is None:
            continue
        total_cost += result["cost"]
        total_payout += result["payout"]
        n_tickets += 1
        if result["hit"]:
            if result["hit_type"] == "4c1":
                n_hits_4c1 += 1
            else:
                n_hits_3c1 += 1

    if n_tickets == 0:
        continue

    profit = (total_payout - total_cost) / total_cost if total_cost > 0 else -1
    mark = "✅" if profit > 0 else ""
    print(f"{top_k:>6} {n_tickets:>8} {n_hits_4c1:>10} {n_hits_3c1:>10} {total_cost:>12.0f} {total_payout:>12.0f} {profit*100:>+11.1f}%{mark}")


# ========================================
# 方向 3：只选低赔率比分
# ========================================

print("\n" + "=" * 100)
print("方向 3：只选低赔率比分（高频比分）")
print("=" * 100)

# 分析比分赔率和命中率
score_stats = defaultdict(lambda: {"n": 0, "hit": 0, "odds_sum": 0})

for p in packs:
    actual = p["actual"]
    for s, prob in p["model_sorted"][:5]:
        odds = p["odds"].get(s, 999)
        score_stats[s]["n"] += 1
        score_stats[s]["odds_sum"] += odds
        if s == actual:
            score_stats[s]["hit"] += 1

print(f"\n高频比分的命中率（出现>100次）：")
print(f"{'比分':>10} {'出现次数':>10} {'命中次数':>10} {'命中率':>10} {'平均赔率':>10}")
print("-" * 60)

for s in sorted(score_stats.keys(), key=lambda x: -score_stats[x]["n"])[:10]:
    st = score_stats[s]
    if st["n"] >= 100:
        rate = st["hit"] / st["n"] * 100
        avg_odds = st["odds_sum"] / st["n"]
        print(f"{str(s):>10} {st['n']:>10} {st['hit']:>10} {rate:>9.1f}% {avg_odds:>9.1f}")


def simulate_4c1_low_odds(day_packs, max_odds=6.0, top_k=2):
    """4串1 只选低赔率比分"""
    if len(day_packs) < 4:
        return None

    sorted_packs = sorted(day_packs, key=lambda p: -p["gap"])[:4]

    all_picks = []
    for p in sorted_packs:
        # 只选赔率 <= max_odds 的比分
        picks = [(s, p["odds"].get(s, 999), p["actual"])
                 for s, prob in p["model_sorted"][:top_k]
                 if p["odds"].get(s, 999) <= max_odds]
        if not picks:
            # 如果没有低赔率的，选 top1
            s, prob = p["model_sorted"][0]
            picks = [(s, p["odds"].get(s, 999), p["actual"])]
        all_picks.append(picks)

    all_bets = list(product(*all_picks))
    n_bets = len(all_bets)
    cost = n_bets * UNIT

    payout = 0.0
    hit = False

    for combo in all_bets:
        all_correct = all(s == actual for s, odds, actual in combo)
        if all_correct:
            combo_odds = 1.0
            for s, odds, actual in combo:
                combo_odds *= odds
            payout += UNIT * combo_odds
            hit = True

    return {"cost": cost, "payout": payout, "hit": hit, "n_bets": n_bets}


print(f"\n4串1 只选低赔率比分：")
print(f"{'最大赔率':>10} {'票数':>8} {'命中':>8} {'总成本':>12} {'总派彩':>12} {'盈利率':>12}")
print("-" * 70)

for max_odds in [5.0, 6.0, 7.0, 8.0, 10.0, 999]:
    total_cost = 0.0
    total_payout = 0.0
    n_tickets = 0
    n_hits = 0

    for day in sorted(by_day):
        result = simulate_4c1_low_odds(by_day[day], max_odds=max_odds, top_k=2)
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
    mark = "✅" if profit > 0 else ""
    label = "不限" if max_odds > 100 else f"{max_odds:.0f}"
    print(f"{label:>10} {n_tickets:>8} {n_hits:>8} {total_cost:>12.0f} {total_payout:>12.0f} {profit*100:>+11.1f}%{mark}")


# ========================================
# 综合：最优组合
# ========================================

print("\n" + "=" * 100)
print("综合：组合优化")
print("=" * 100)

# 组合测试：4串11 + 低赔率 + topK
configs = [
    ("4c1 top1", lambda dp: simulate_4c1_topk(dp, top_k=1)),
    ("4c1 top2", lambda dp: simulate_4c1_topk(dp, top_k=2)),
    ("4c11 top1", lambda dp: simulate_4c11(dp, top_k=1)),
    ("4c11 top2", lambda dp: simulate_4c11(dp, top_k=2)),
    ("4c1 低赔率top2", lambda dp: simulate_4c1_low_odds(dp, max_odds=7.0, top_k=2)),
]

print(f"\n{'策略':<20} {'票数':>8} {'命中':>8} {'总成本':>12} {'总派彩':>12} {'盈利率':>12}")
print("-" * 80)

for name, sim_fn in configs:
    total_cost = 0.0
    total_payout = 0.0
    n_tickets = 0
    n_hits = 0

    for day in sorted(by_day):
        result = sim_fn(by_day[day])
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
    mark = "✅" if profit > 0 else ""
    print(f"{name:<20} {n_tickets:>8} {n_hits:>8} {total_cost:>12.0f} {total_payout:>12.0f} {profit*100:>+11.1f}%{mark}")

print("-" * 80)
