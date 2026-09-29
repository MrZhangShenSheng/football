# -*- coding: utf-8 -*-
r"""票型回测 v4（2026-09-29）—— gap选场 + v4模型策略。

对比：
1. market（市场概率降序）—— 基线
2. gap + v4（gap断层选场 + 族模型概率）—— 闯关票策略

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score_family_model as sfm

HIST_ODDS_DIR = Path(__file__).resolve().parents[2] / "cache" / "hist_odds"
LEAGUE_DIR = Path(__file__).resolve().parents[3] / "data" / "00-leagues"


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

                # 计算 gap（市场 top1 vs top2 概率差）
                sorted_odds = sorted(odds.values())
                if len(sorted_odds) >= 2:
                    # 赔率转概率（去水）
                    total = sum(1/o for o in sorted_odds)
                    probs = [1/o/total for o in sorted_odds]
                    gap = probs[0] - probs[1]  # top1 - top2 概率差
                else:
                    gap = 0

                matches.append({
                    "date": m.get("date", p.stem),
                    "home": m.get("home", ""),
                    "away": m.get("away", ""),
                    "league": m.get("league", ""),
                    "actual": (h, a),
                    "actual_str": f"{h}:{a}",
                    "odds": odds,
                    "gap": gap,
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
    "4串1": {"n_legs": 4, "combos": lambda n: [tuple(range(n))]},
    "4串5": {"n_legs": 4, "combos": lambda n: list(combinations(range(n), 3)) + [tuple(range(n))]},
    "4串11": {"n_legs": 4, "combos": lambda n: [c for k in (2,3,4) for c in combinations(range(n), k)]},
    "全2关-4": {"n_legs": 4, "combos": lambda n: list(combinations(range(n), 2))},
}


def pick_by_market_top(match, k=1):
    """市场概率降序（赔率升序）选 top-k"""
    sorted_opts = sorted(match["odds"].items(), key=lambda kv: kv[1])
    return [s for s, o in sorted_opts[:k]]


def pick_random(match, k=1):
    """随机选 k 个"""
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

    for combo in combos:
        combo_legs = [leg_picks[i] for i in combo]
        for bet in product(*combo_legs):
            all_hit = all(score == actuals[combo[j]] for j, (score, _) in enumerate(bet))
            if all_hit:
                combo_odds = math.prod(o for _, o in bet)
                payout += 2 * combo_odds
                any_hit = True

    return cost, payout, any_hit


def run_backtest_comparison(matches, n_simulations=2000):
    """对比回测：市场选腿 vs gap选场"""
    results = []

    # 按 gap 降序排序（gap 大的场次模型更有信心）
    matches_by_gap = sorted(matches, key=lambda m: -m["gap"])

    for shape_name, shape_cfg in SHAPES_CRS.items():
        n_legs = shape_cfg["n_legs"]
        if n_legs > 4:  # CRS 最多4场
            continue
        if n_legs > len(matches) // 10:
            continue

        for n_picks in [1, 2]:
            # === 策略1：市场选腿（随机抽场） ===
            total_cost_mkt, total_payout_mkt, total_hits_mkt = 0, 0, 0
            for _ in range(n_simulations):
                sample = random.sample(matches, n_legs)
                cost, payout, any_hit = simulate_ticket(sample, shape_name, pick_by_market_top, n_picks)
                total_cost_mkt += cost
                total_payout_mkt += payout
                if any_hit:
                    total_hits_mkt += 1

            hit_rate_mkt = total_hits_mkt / n_simulations
            profit_mkt = (total_payout_mkt / total_cost_mkt - 1) if total_cost_mkt else -1

            # === 策略2：gap选场 + 市场选腿（选 gap 最大的场次） ===
            total_cost_gap, total_payout_gap, total_hits_gap = 0, 0, 0
            # gap 选场：每次从 gap top 20% 中随机选
            top_gap_pool = matches_by_gap[:len(matches)//5]

            for _ in range(n_simulations):
                if len(top_gap_pool) >= n_legs:
                    sample = random.sample(top_gap_pool, n_legs)
                else:
                    sample = random.sample(matches, n_legs)
                cost, payout, any_hit = simulate_ticket(sample, shape_name, pick_by_market_top, n_picks)
                total_cost_gap += cost
                total_payout_gap += payout
                if any_hit:
                    total_hits_gap += 1

            hit_rate_gap = total_hits_gap / n_simulations
            profit_gap = (total_payout_gap / total_cost_gap - 1) if total_cost_gap else -1

            # === 策略3：gap选场 + 双选（top1+top2） ===
            if n_picks == 1:  # 只对单选做双选对比
                total_cost_gap2, total_payout_gap2, total_hits_gap2 = 0, 0, 0
                for _ in range(n_simulations):
                    if len(top_gap_pool) >= n_legs:
                        sample = random.sample(top_gap_pool, n_legs)
                    else:
                        sample = random.sample(matches, n_legs)
                    cost, payout, any_hit = simulate_ticket(sample, shape_name, pick_by_market_top, 2)
                    total_cost_gap2 += cost
                    total_payout_gap2 += payout
                    if any_hit:
                        total_hits_gap2 += 1

                hit_rate_gap2 = total_hits_gap2 / n_simulations
                profit_gap2 = (total_payout_gap2 / total_cost_gap2 - 1) if total_cost_gap2 else -1
            else:
                hit_rate_gap2, profit_gap2 = None, None

            results.append({
                "shape": shape_name,
                "n_legs": n_legs,
                "n_picks": n_picks,
                # 市场基线
                "hit_mkt": hit_rate_mkt,
                "profit_mkt": profit_mkt,
                # gap 选场
                "hit_gap": hit_rate_gap,
                "profit_gap": profit_gap,
                # gap 选场 + 双选
                "hit_gap2": hit_rate_gap2,
                "profit_gap2": profit_gap2,
            })

    return results


def print_comparison(results):
    print("=" * 140)
    print("CRS 票型回测对比：市场选腿 vs gap选场")
    print("=" * 140)
    print(f"{'结构':<10} {'腿数':>4} {'复式':>4} │ {'市场命中':>8} {'市场盈利':>10} │ {'gap命中':>8} {'gap盈利':>10} │ {'gap双选命中':>10} {'gap双选盈利':>12} │ {'提升':>8}")
    print("-" * 140)

    for r in sorted(results, key=lambda x: (x["n_legs"], x["shape"], x["n_picks"])):
        gap2_hit = f"{r['hit_gap2']*100:>9.2f}%" if r['hit_gap2'] is not None else "    -    "
        gap2_profit = f"{r['profit_gap2']*100:>11.1f}%" if r['profit_gap2'] is not None else "      -     "

        # 计算 gap vs 市场的提升
        improve = r['profit_gap'] - r['profit_mkt']

        print(f"{r['shape']:<10} {r['n_legs']:>4} {r['n_picks']:>4}选 │ "
              f"{r['hit_mkt']*100:>7.2f}% {r['profit_mkt']*100:>9.1f}% │ "
              f"{r['hit_gap']*100:>7.2f}% {r['profit_gap']*100:>9.1f}% │ "
              f"{gap2_hit} {gap2_profit} │ "
              f"{improve*100:>+7.1f}%")

    print("-" * 140)

    # 汇总
    print("\n" + "=" * 100)
    print("【策略对比汇总】")
    print("=" * 100)

    mkt_profits = [r['profit_mkt'] for r in results]
    gap_profits = [r['profit_gap'] for r in results]
    gap2_profits = [r['profit_gap2'] for r in results if r['profit_gap2'] is not None]

    print(f"市场选腿平均盈利率: {sum(mkt_profits)/len(mkt_profits)*100:>+.1f}%")
    print(f"gap选场平均盈利率:  {sum(gap_profits)/len(gap_profits)*100:>+.1f}%")
    if gap2_profits:
        print(f"gap+双选平均盈利率: {sum(gap2_profits)/len(gap2_profits)*100:>+.1f}%")

    # 按票型分析 gap 提升
    print("\n" + "=" * 100)
    print("【gap选场提升最大的票型 Top 5】")
    print("=" * 100)
    by_improve = sorted(results, key=lambda r: r['profit_gap'] - r['profit_mkt'], reverse=True)
    for i, r in enumerate(by_improve[:5], 1):
        improve = r['profit_gap'] - r['profit_mkt']
        print(f"{i}. {r['shape']} {r['n_picks']}选: 市场 {r['profit_mkt']*100:>+.1f}% → gap {r['profit_gap']*100:>+.1f}% (提升 {improve*100:>+.1f}%)")

    # gap + 双选的最优票型
    if gap2_profits:
        print("\n" + "=" * 100)
        print("【gap选场+双选 盈利率 Top 5】—— 这是闯关票策略的核心组合")
        print("=" * 100)
        by_gap2 = sorted([r for r in results if r['profit_gap2'] is not None],
                         key=lambda r: r['profit_gap2'], reverse=True)
        for i, r in enumerate(by_gap2[:5], 1):
            print(f"{i}. {r['shape']}: 命中 {r['hit_gap2']*100:.2f}%, 盈利 {r['profit_gap2']*100:>+.1f}%")


if __name__ == "__main__":
    print("加载 CRS 历史数据...")
    matches = load_crs_data()
    print(f"共 {len(matches)} 场有效数据")

    # 统计 gap 分布
    gaps = [m["gap"] for m in matches]
    print(f"gap 分布: min={min(gaps):.4f}, max={max(gaps):.4f}, mean={sum(gaps)/len(gaps):.4f}")

    print("\n运行对比回测（每票型 2000 次模拟）...")
    results = run_backtest_comparison(matches, n_simulations=2000)

    print_comparison(results)
