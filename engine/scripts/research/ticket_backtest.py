# -*- coding: utf-8 -*-
r"""票型回测 v3（2026-09-29）—— 体彩规则限制版 + 完整指标。

体彩规则限制：
- HAD（胜平负）：最多 8 场
- HHAD（让球胜平负）：最多 4 场
- CRS（比分）：最多 4 场
- TTG（总进球）：最多 4 场
- HAFU（半全场）：最多 4 场

指标定义：
- 命中率：有任意注中奖的票数 / 总票数
- 回收率：总派彩 / 总投入
- 盈利率：回收率 - 1（正数才赚钱）
- 命中倍数：命中时的平均回报倍数（赚几倍本金）
- 期望倍数：命中率 × 命中倍数（>1 才正期望）
- 最大回报：单票最高回报倍数

开发者 sszhang
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter, defaultdict
from itertools import combinations, product
from pathlib import Path

random.seed(42)

HIST_ODDS_DIR = Path(__file__).resolve().parents[2] / "cache" / "hist_odds"

# 玩法最大场次限制
POOL_MAX_LEGS = {
    "HAD": 8,
    "HHAD": 4,
    "CRS": 4,
    "TTG": 4,
    "HAFU": 4,
}


def load_crs_data():
    """加载所有 CRS 历史数据"""
    matches = []
    for p in sorted(HIST_ODDS_DIR.glob("*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            for m in data.get("matches", []):
                crs = m.get("crs") or m.get("pools", {}).get("crs")
                actual = m.get("actual") or m.get("score")
                if not crs or not actual:
                    continue
                if isinstance(actual, str) and ":" in actual:
                    h, a = map(int, actual.split(":"))
                elif isinstance(actual, (list, tuple)):
                    h, a = actual
                else:
                    continue
                odds = {}
                for k, v in crs.items():
                    try:
                        if k.startswith("s") and "s" in k[1:]:
                            parts = k.split("s")
                            hg, ag = int(parts[1]), int(parts[2])
                            odds[f"{hg}:{ag}"] = float(v)
                        elif k in ("s1sh", "s1sd", "s1sa"):
                            label = {"s1sh": "胜其他", "s1sd": "平其他", "s1sa": "负其他"}[k]
                            odds[label] = float(v)
                        elif ":" in k:
                            odds[k] = float(v)
                    except (ValueError, KeyError):
                        continue
                if len(odds) < 20:
                    continue
                matches.append({
                    "date": m.get("date", p.stem),
                    "actual": (h, a),
                    "actual_str": f"{h}:{a}",
                    "odds": odds,
                })
        except (json.JSONDecodeError, OSError):
            continue
    return matches


# CRS 最多4场的串关结构
SHAPES_CRS = {
    "单关": {"n_legs": 1, "combos": lambda n: [(i,) for i in range(n)]},
    "2串1": {"n_legs": 2, "combos": lambda n: [tuple(range(n))]},
    "3串1": {"n_legs": 3, "combos": lambda n: [tuple(range(n))]},
    "3串4": {"n_legs": 3, "combos": lambda n: list(combinations(range(n), 2)) + [tuple(range(n))]},
    "3串7": {"n_legs": 3, "combos": lambda n: [c for k in (1,2,3) for c in combinations(range(n), k)]},
    "4串1": {"n_legs": 4, "combos": lambda n: [tuple(range(n))]},
    "4串5": {"n_legs": 4, "combos": lambda n: list(combinations(range(n), 3)) + [tuple(range(n))]},
    "4串11": {"n_legs": 4, "combos": lambda n: [c for k in (2,3,4) for c in combinations(range(n), k)]},
    "4串15": {"n_legs": 4, "combos": lambda n: [c for k in (1,2,3,4) for c in combinations(range(n), k)]},
    "全2关-3": {"n_legs": 3, "combos": lambda n: list(combinations(range(n), 2))},
    "全2关-4": {"n_legs": 4, "combos": lambda n: list(combinations(range(n), 2))},
}


def pick_by_market_top(match, k=1):
    sorted_opts = sorted(match["odds"].items(), key=lambda kv: kv[1])
    return [s for s, o in sorted_opts[:k]]


def pick_random(match, k=1):
    opts = list(match["odds"].keys())
    return random.sample(opts, min(k, len(opts)))


def simulate_ticket(sample, shape_name, pick_func, n_picks=1):
    shape = SHAPES_CRS[shape_name]
    n_legs = shape["n_legs"]
    combos = shape["combos"](n_legs)

    leg_picks = []
    for m in sample:
        picks = pick_func(m, n_picks)
        opts = [(p, m["odds"].get(p, 999)) for p in picks]
        leg_picks.append(opts)

    actuals = []
    for m in sample:
        h, a = m["actual"]
        actual_str = f"{h}:{a}"
        if actual_str not in m["odds"]:
            if h > a:
                actual_str = "胜其他"
            elif h < a:
                actual_str = "负其他"
            else:
                actual_str = "平其他"
        actuals.append(actual_str)

    n_bets = len(combos) * (n_picks ** n_legs)
    cost = n_bets * 2
    payout = 0.0
    any_hit = False
    max_single_payout = 0.0

    for combo in combos:
        combo_legs = [leg_picks[i] for i in combo]
        for bet in product(*combo_legs):
            all_hit = all(score == actuals[combo[j]] for j, (score, _) in enumerate(bet))
            if all_hit:
                combo_odds = math.prod(o for _, o in bet)
                single_payout = 2 * combo_odds
                payout += single_payout
                max_single_payout = max(max_single_payout, single_payout)
                any_hit = True

    return cost, payout, any_hit, max_single_payout


def run_backtest(matches, n_simulations=3000):
    results = []

    for shape_name, shape_cfg in SHAPES_CRS.items():
        n_legs = shape_cfg["n_legs"]
        if n_legs > POOL_MAX_LEGS["CRS"]:
            continue
        if n_legs > len(matches) // 10:
            continue

        for pick_name, pick_func in [("market", pick_by_market_top), ("random", pick_random)]:
            for n_picks in [1, 2, 3]:  # 增加三选
                # CRS 三选选项太多，跳过高腿数
                if n_picks == 3 and n_legs > 2:
                    continue

                total_cost = 0
                total_payout = 0
                total_hits = 0
                total_tickets = 0
                hit_payouts = []
                all_returns = []
                max_return = 0

                for _ in range(n_simulations):
                    sample = random.sample(matches, n_legs)
                    cost, payout, any_hit, max_single = simulate_ticket(sample, shape_name, pick_func, n_picks)

                    total_cost += cost
                    total_payout += payout
                    total_tickets += 1
                    ret = (payout - cost) / cost if cost > 0 else -1
                    all_returns.append(ret)
                    max_return = max(max_return, ret)

                    if any_hit:
                        total_hits += 1
                        hit_payouts.append(payout)

                hit_rate = total_hits / total_tickets if total_tickets else 0
                recovery_rate = total_payout / total_cost if total_cost else 0
                profit_rate = recovery_rate - 1

                # 命中倍数
                if hit_payouts:
                    single_cost = total_cost / total_tickets
                    avg_hit_payout = sum(hit_payouts) / len(hit_payouts)
                    avg_hit_mult = avg_hit_payout / single_cost
                else:
                    avg_hit_mult = 0

                # 期望倍数
                expected_mult = hit_rate * avg_hit_mult

                # 注数和成本
                combos = shape_cfg["combos"](n_legs)
                n_bets = len(combos) * (n_picks ** n_legs)
                single_cost = n_bets * 2

                results.append({
                    "shape": shape_name,
                    "pick": pick_name,
                    "n_picks": n_picks,
                    "n_legs": n_legs,
                    "n_bets": n_bets,
                    "single_cost": single_cost,
                    "hit_rate": hit_rate,
                    "recovery_rate": recovery_rate,
                    "profit_rate": profit_rate,
                    "avg_hit_mult": avg_hit_mult,
                    "expected_mult": expected_mult,
                    "max_return": max_return,
                })

    return results


def print_results(results):
    print("=" * 140)
    print("CRS 比分票型回测结果（体彩规则：比分最多4场）")
    print("=" * 140)
    print(f"{'结构':<10} {'策略':<8} {'复式':<4} {'腿数':>4} {'注数':>6} {'成本':>6} {'命中率':>8} {'回收率':>8} {'盈利率':>8} {'命中倍数':>8} {'期望倍数':>8} {'最大回报':>8}")
    print("-" * 140)

    for r in sorted(results, key=lambda x: (x["n_legs"], x["shape"], x["pick"], x["n_picks"])):
        picks_str = f"{r['n_picks']}选"
        profit_str = f"{r['profit_rate']*100:+.1f}%"
        print(f"{r['shape']:<10} {r['pick']:<8} {picks_str:<4} {r['n_legs']:>4} {r['n_bets']:>6} {r['single_cost']:>6} "
              f"{r['hit_rate']*100:>7.2f}% {r['recovery_rate']*100:>7.2f}% {profit_str:>8} "
              f"{r['avg_hit_mult']:>8.1f}x {r['expected_mult']:>8.3f} {r['max_return']*100:>7.0f}%")

    # ══════════════════════════════════════════════════════════════════════════
    # Top 榜单
    # ══════════════════════════════════════════════════════════════════════════

    print("\n" + "=" * 100)
    print("【盈利率 Top 10】—— 盈利率 = 回收率 - 1，越接近 0% 越好（全负说明市场有效）")
    print("=" * 100)
    print(f"{'#':>2} {'结构':<10} {'策略':<8} {'复式':<4} {'成本':>6} {'命中率':>8} {'回收率':>8} {'盈利率':>10} {'命中倍数':>10}")
    print("-" * 100)
    for i, r in enumerate(sorted(results, key=lambda x: -x["profit_rate"])[:10], 1):
        picks_str = f"{r['n_picks']}选"
        profit_str = f"{r['profit_rate']*100:+.1f}%"
        print(f"{i:>2} {r['shape']:<10} {r['pick']:<8} {picks_str:<4} {r['single_cost']:>6} "
              f"{r['hit_rate']*100:>7.2f}% {r['recovery_rate']*100:>7.2f}% {profit_str:>10} {r['avg_hit_mult']:>9.1f}x")

    print("\n" + "=" * 100)
    print("【命中倍数 Top 10】—— 命中时平均赚几倍本金（右尾肥度）")
    print("=" * 100)
    print(f"{'#':>2} {'结构':<10} {'策略':<8} {'复式':<4} {'成本':>6} {'命中率':>8} {'命中倍数':>10} {'最大回报':>10} {'盈利率':>10}")
    print("-" * 100)
    for i, r in enumerate(sorted([r for r in results if r["avg_hit_mult"] > 0], key=lambda x: -x["avg_hit_mult"])[:10], 1):
        picks_str = f"{r['n_picks']}选"
        profit_str = f"{r['profit_rate']*100:+.1f}%"
        print(f"{i:>2} {r['shape']:<10} {r['pick']:<8} {picks_str:<4} {r['single_cost']:>6} "
              f"{r['hit_rate']*100:>7.2f}% {r['avg_hit_mult']:>9.1f}x {r['max_return']*100:>9.0f}% {profit_str:>10}")

    print("\n" + "=" * 100)
    print("【期望倍数 Top 10】—— 期望倍数 = 命中率 × 命中倍数，>1 才正期望")
    print("=" * 100)
    print(f"{'#':>2} {'结构':<10} {'策略':<8} {'复式':<4} {'成本':>6} {'命中率':>8} {'命中倍数':>8} {'期望倍数':>10} {'盈利率':>10}")
    print("-" * 100)
    for i, r in enumerate(sorted(results, key=lambda x: -x["expected_mult"])[:10], 1):
        picks_str = f"{r['n_picks']}选"
        profit_str = f"{r['profit_rate']*100:+.1f}%"
        exp_str = f"{r['expected_mult']:.3f}"
        print(f"{i:>2} {r['shape']:<10} {r['pick']:<8} {picks_str:<4} {r['single_cost']:>6} "
              f"{r['hit_rate']*100:>7.2f}% {r['avg_hit_mult']:>7.1f}x {exp_str:>10} {profit_str:>10}")

    print("\n" + "=" * 100)
    print("【最大单票回报 Top 10】—— 单票最高赚多少倍（尾部极值）")
    print("=" * 100)
    print(f"{'#':>2} {'结构':<10} {'策略':<8} {'复式':<4} {'成本':>6} {'命中率':>8} {'最大回报':>12} {'盈利率':>10}")
    print("-" * 100)
    for i, r in enumerate(sorted(results, key=lambda x: -x["max_return"])[:10], 1):
        picks_str = f"{r['n_picks']}选"
        profit_str = f"{r['profit_rate']*100:+.1f}%"
        print(f"{i:>2} {r['shape']:<10} {r['pick']:<8} {picks_str:<4} {r['single_cost']:>6} "
              f"{r['hit_rate']*100:>7.2f}% {r['max_return']*100:>11.0f}% {profit_str:>10}")

    # ══════════════════════════════════════════════════════════════════════════
    # 维度分析
    # ══════════════════════════════════════════════════════════════════════════

    print("\n" + "=" * 100)
    print("【维度分析】")
    print("=" * 100)

    # 按策略
    market_r = [r for r in results if r["pick"] == "market"]
    random_r = [r for r in results if r["pick"] == "random"]
    print(f"\n策略对比（平均盈利率）:")
    print(f"  market（市场top-k）: {sum(r['profit_rate'] for r in market_r)/len(market_r)*100:+.1f}%")
    print(f"  random（随机）:      {sum(r['profit_rate'] for r in random_r)/len(random_r)*100:+.1f}%")

    # 按复式
    for n_p in [1, 2, 3]:
        subset = [r for r in results if r["n_picks"] == n_p]
        if subset:
            print(f"\n{n_p}选 平均: 命中率 {sum(r['hit_rate'] for r in subset)/len(subset)*100:.1f}%, "
                  f"盈利率 {sum(r['profit_rate'] for r in subset)/len(subset)*100:+.1f}%, "
                  f"命中倍数 {sum(r['avg_hit_mult'] for r in subset)/len(subset):.1f}x")

    # 按腿数
    print(f"\n按腿数:")
    for n_l in sorted(set(r["n_legs"] for r in results)):
        subset = [r for r in results if r["n_legs"] == n_l]
        print(f"  {n_l}腿: 命中率 {sum(r['hit_rate'] for r in subset)/len(subset)*100:.2f}%, "
              f"盈利率 {sum(r['profit_rate'] for r in subset)/len(subset)*100:+.1f}%, "
              f"命中倍数 {sum(r['avg_hit_mult'] for r in subset)/len(subset):.1f}x")

    # ══════════════════════════════════════════════════════════════════════════
    # 关键结论
    # ══════════════════════════════════════════════════════════════════════════

    print("\n" + "=" * 100)
    print("【关键结论】")
    print("=" * 100)

    best_profit = max(results, key=lambda x: x["profit_rate"])
    best_mult = max([r for r in results if r["avg_hit_mult"] > 0], key=lambda x: x["avg_hit_mult"])
    best_expected = max(results, key=lambda x: x["expected_mult"])

    print(f"\n1. 盈利率最高: {best_profit['shape']} + {best_profit['pick']} + {best_profit['n_picks']}选")
    print(f"   → 命中率 {best_profit['hit_rate']*100:.2f}%, 盈利率 {best_profit['profit_rate']*100:+.1f}%")

    print(f"\n2. 命中倍数最高: {best_mult['shape']} + {best_mult['pick']} + {best_mult['n_picks']}选")
    print(f"   → 命中率 {best_mult['hit_rate']*100:.2f}%, 命中时赚 {best_mult['avg_hit_mult']:.1f}x")

    print(f"\n3. 期望倍数最高: {best_expected['shape']} + {best_expected['pick']} + {best_expected['n_picks']}选")
    print(f"   → 期望倍数 {best_expected['expected_mult']:.3f}（{'正期望' if best_expected['expected_mult'] > 1 else '负期望'}）")

    # 是否有正期望票型
    positive = [r for r in results if r["expected_mult"] > 1]
    if positive:
        print(f"\n4. ⚠️ 发现 {len(positive)} 种正期望票型！")
        for r in positive:
            print(f"   - {r['shape']} + {r['pick']} + {r['n_picks']}选: 期望 {r['expected_mult']:.3f}")
    else:
        print(f"\n4. 所有票型均为负期望（期望倍数 < 1），市场有效")


if __name__ == "__main__":
    print("加载 CRS 历史数据...")
    matches = load_crs_data()
    print(f"共 {len(matches)} 场有效数据")

    print("\n运行回测（每票型 3000 次模拟）...\n")
    results = run_backtest(matches, n_simulations=3000)

    print_results(results)
