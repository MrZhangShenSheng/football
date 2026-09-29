# -*- coding: utf-8 -*-
r"""高赔票型深扒（2026-09-29）—— 目标：找到期望值>1的组合。

桂林案例：50元中182万 = 36400倍
目标：命中率 × 命中倍数 > 1

测试组合：
1. 4串1 CRS - 基线 vs gap选场 vs v4模型
2. 4串1 CRS+HAD - 混合拉命中率
3. 4串5/4串11 - 容错结构
4. 3串4 - 短串高命中

开发者 sszhang
"""
from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from itertools import combinations, product
from pathlib import Path

random.seed(42)

HIST_ODDS_DIR = Path(__file__).resolve().parents[2] / "cache" / "hist_odds"


def load_data():
    """加载数据"""
    matches = []
    for p in sorted(HIST_ODDS_DIR.glob("*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            for m in data.get("matches", []):
                crs = m.get("crs") or {}
                had = m.get("had") or {}
                ttg = m.get("ttg") or {}
                score = m.get("score")

                if not score or not crs:
                    continue

                if isinstance(score, str) and ":" in score:
                    h, a = map(int, score.split(":"))
                elif isinstance(score, (list, tuple)):
                    h, a = score
                else:
                    continue

                crs_odds = {k: float(v) for k, v in crs.items() if v}
                had_odds = {k.lower(): float(v) for k, v in had.items() if v}
                ttg_odds = {k: float(v) for k, v in ttg.items() if v}

                if len(crs_odds) < 10:
                    continue

                # 实际结果
                actual_crs = f"{h}:{a}"
                if actual_crs not in crs_odds:
                    if h > a:
                        actual_crs = "other_h"
                    elif h < a:
                        actual_crs = "other_a"
                    else:
                        actual_crs = "other_d"

                actual_had = 'h' if h > a else ('a' if h < a else 'd')
                actual_ttg = str(min(h + a, 7))

                # 计算 gap（CRS top1 vs top2 概率差）
                sorted_crs = sorted(crs_odds.values())
                if len(sorted_crs) >= 2:
                    total = sum(1/o for o in sorted_crs if o > 0)
                    if total > 0:
                        probs = [1/o/total for o in sorted_crs]
                        gap = probs[0] - probs[1]
                    else:
                        gap = 0
                else:
                    gap = 0

                # 计算 HAD gap
                if len(had_odds) >= 2:
                    sorted_had = sorted(had_odds.values())
                    total_had = sum(1/o for o in sorted_had if o > 0)
                    if total_had > 0:
                        probs_had = [1/o/total_had for o in sorted_had]
                        gap_had = probs_had[0] - probs_had[1]
                    else:
                        gap_had = 0
                else:
                    gap_had = 0

                matches.append({
                    "actual_crs": actual_crs,
                    "actual_had": actual_had,
                    "actual_ttg": actual_ttg,
                    "crs_odds": crs_odds,
                    "had_odds": had_odds,
                    "ttg_odds": ttg_odds,
                    "gap_crs": gap,
                    "gap_had": gap_had,
                })
        except:
            continue
    return matches


def pick_top_k(odds_dict, k=1):
    """按赔率升序选 top-k"""
    if not odds_dict:
        return []
    sorted_opts = sorted(odds_dict.items(), key=lambda kv: kv[1])
    return sorted_opts[:k]


def check_hit(pool, pick, m):
    """检查命中"""
    if pool == "crs":
        return pick == m["actual_crs"]
    elif pool == "had":
        return pick == m["actual_had"]
    elif pool == "ttg":
        return pick == m["actual_ttg"]
    return False


def simulate_parlay(matches_sample, config, shape="4串1"):
    """
    模拟串关票。

    config = {"crs": 2, "had": 1, "ttg": 0}
    shape = "4串1" | "4串5" | "4串11" | "3串4" | "2串1"
    """
    n_legs = len(matches_sample)

    # 构建骨架组合
    if shape == "4串1":
        combos = [tuple(range(n_legs))]
    elif shape == "4串5":
        combos = list(combinations(range(n_legs), 3)) + [tuple(range(n_legs))]
    elif shape == "4串11":
        combos = [c for k in (2,3,4) for c in combinations(range(n_legs), k)]
    elif shape == "3串4":
        combos = list(combinations(range(n_legs), 2)) + [tuple(range(n_legs))]
    elif shape == "3串1":
        combos = [tuple(range(n_legs))]
    elif shape == "2串1":
        combos = [tuple(range(n_legs))]
    else:
        combos = [tuple(range(n_legs))]

    # 构建每腿选项
    def build_opts(m, cfg):
        opts = []
        for pool, k in cfg.items():
            if k <= 0:
                continue
            if pool == "crs":
                for pick, odds in pick_top_k(m["crs_odds"], k):
                    opts.append(("crs", pick, odds))
            elif pool == "had":
                for pick, odds in pick_top_k(m["had_odds"], k):
                    opts.append(("had", pick, odds))
            elif pool == "ttg":
                for pick, odds in pick_top_k(m["ttg_odds"], k):
                    opts.append(("ttg", pick, odds))
        return opts

    leg_opts = [build_opts(m, config) for m in matches_sample]
    if any(not opts for opts in leg_opts):
        return None

    # 展开所有注
    all_bets = list(product(*leg_opts))
    n_bets_per_combo = len(all_bets)
    total_bets = n_bets_per_combo * len(combos)
    cost = total_bets * 2
    payout = 0.0
    any_hit = False
    max_mult = 0

    for combo in combos:
        combo_leg_opts = [leg_opts[i] for i in combo]
        for bet in product(*combo_leg_opts):
            all_hit_flag = True
            combo_odds = 1.0
            for idx, (pool, pick, odds) in enumerate(bet):
                leg_idx = combo[idx]
                if not check_hit(pool, pick, matches_sample[leg_idx]):
                    all_hit_flag = False
                    break
                combo_odds *= odds

            if all_hit_flag:
                payout += 2 * combo_odds
                any_hit = True
                max_mult = max(max_mult, combo_odds)

    return {"cost": cost, "payout": payout, "hit": any_hit, "max_mult": max_mult, "n_bets": total_bets}


def run_deep_backtest(matches, n_sim=10000):
    """深度回测 - 增加模拟次数以捕捉稀有事件"""
    results = []

    # 按 gap 排序（用于 gap 选场策略）
    matches_by_crs_gap = sorted(matches, key=lambda m: -m["gap_crs"])
    matches_by_had_gap = sorted(matches, key=lambda m: -m["gap_had"])

    # 测试配置
    test_cases = [
        # (名称, 腿数, shape, config, 选场策略)
        # === 4串1 系列 ===
        ("4串1 CRS单选 随机", 4, "4串1", {"crs": 1, "had": 0, "ttg": 0}, "random"),
        ("4串1 CRS单选 gap", 4, "4串1", {"crs": 1, "had": 0, "ttg": 0}, "gap_crs"),
        ("4串1 CRS双选 随机", 4, "4串1", {"crs": 2, "had": 0, "ttg": 0}, "random"),
        ("4串1 CRS双选 gap", 4, "4串1", {"crs": 2, "had": 0, "ttg": 0}, "gap_crs"),
        ("4串1 CRS三选 gap", 4, "4串1", {"crs": 3, "had": 0, "ttg": 0}, "gap_crs"),

        # === 4串1 混合 ===
        ("4串1 CRS单+HAD单 gap", 4, "4串1", {"crs": 1, "had": 1, "ttg": 0}, "gap_crs"),
        ("4串1 CRS单+HAD双 gap", 4, "4串1", {"crs": 1, "had": 2, "ttg": 0}, "gap_crs"),
        ("4串1 CRS双+HAD单 gap", 4, "4串1", {"crs": 2, "had": 1, "ttg": 0}, "gap_crs"),
        ("4串1 CRS双+HAD双 gap", 4, "4串1", {"crs": 2, "had": 2, "ttg": 0}, "gap_crs"),
        ("4串1 CRS单+TTG单 gap", 4, "4串1", {"crs": 1, "had": 0, "ttg": 1}, "gap_crs"),
        ("4串1 CRS单+HAD单+TTG单 gap", 4, "4串1", {"crs": 1, "had": 1, "ttg": 1}, "gap_crs"),

        # === 4串5/4串11 容错 ===
        ("4串5 CRS双选 gap", 4, "4串5", {"crs": 2, "had": 0, "ttg": 0}, "gap_crs"),
        ("4串11 CRS双选 gap", 4, "4串11", {"crs": 2, "had": 0, "ttg": 0}, "gap_crs"),
        ("4串11 CRS双+HAD单 gap", 4, "4串11", {"crs": 2, "had": 1, "ttg": 0}, "gap_crs"),

        # === 3串系列 ===
        ("3串1 CRS双选 gap", 3, "3串1", {"crs": 2, "had": 0, "ttg": 0}, "gap_crs"),
        ("3串4 CRS双选 gap", 3, "3串4", {"crs": 2, "had": 0, "ttg": 0}, "gap_crs"),
        ("3串4 CRS双+HAD单 gap", 3, "3串4", {"crs": 2, "had": 1, "ttg": 0}, "gap_crs"),

        # === 2串1 对照 ===
        ("2串1 CRS双选 gap", 2, "2串1", {"crs": 2, "had": 0, "ttg": 0}, "gap_crs"),
        ("2串1 CRS双+HAD双 gap", 2, "2串1", {"crs": 2, "had": 2, "ttg": 0}, "gap_crs"),
    ]

    for name, n_legs, shape, config, pick_strategy in test_cases:
        total_cost = 0
        total_payout = 0
        total_hits = 0
        max_mult_seen = 0
        hit_mults = []

        for _ in range(n_sim):
            # 选场策略
            if pick_strategy == "random":
                sample = random.sample(matches, n_legs)
            elif pick_strategy == "gap_crs":
                # 从 gap top 20% 中随机选
                top_pool = matches_by_crs_gap[:len(matches)//5]
                if len(top_pool) >= n_legs:
                    sample = random.sample(top_pool, n_legs)
                else:
                    sample = random.sample(matches, n_legs)
            elif pick_strategy == "gap_had":
                top_pool = matches_by_had_gap[:len(matches)//5]
                if len(top_pool) >= n_legs:
                    sample = random.sample(top_pool, n_legs)
                else:
                    sample = random.sample(matches, n_legs)
            else:
                sample = random.sample(matches, n_legs)

            result = simulate_parlay(sample, config, shape)
            if result is None:
                continue

            total_cost += result["cost"]
            total_payout += result["payout"]
            if result["hit"]:
                total_hits += 1
                # 记录整票的回报倍数（派彩/成本），不是单注最高倍数
                ticket_mult = result["payout"] / result["cost"] if result["cost"] > 0 else 0
                hit_mults.append(ticket_mult)
            max_mult_seen = max(max_mult_seen, result["max_mult"])

        if total_cost == 0:
            continue

        hit_rate = total_hits / n_sim
        recovery = total_payout / total_cost
        profit = recovery - 1
        avg_hit_mult = sum(hit_mults) / len(hit_mults) if hit_mults else 0
        expected_value = hit_rate * avg_hit_mult  # 期望值

        results.append({
            "name": name,
            "hit_rate": hit_rate,
            "profit": profit,
            "avg_hit_mult": avg_hit_mult,
            "max_mult": max_mult_seen,
            "expected_value": expected_value,
            "n_hits": total_hits,
        })

    return results


def print_results(results):
    """打印结果"""
    print("=" * 120)
    print("高赔票型深扒结果（目标：期望值 > 1）")
    print("=" * 120)
    print(f"{'组合':<35} {'命中率':>10} {'盈利率':>10} {'命中倍数':>12} {'最大倍数':>12} {'期望值':>10} {'命中次数':>8}")
    print("-" * 120)

    for r in sorted(results, key=lambda x: -x["expected_value"]):
        ev_mark = "✅" if r["expected_value"] > 1 else "❌"
        print(f"{r['name']:<35} {r['hit_rate']*100:>9.3f}% {r['profit']*100:>9.1f}% "
              f"{r['avg_hit_mult']:>11.1f}x {r['max_mult']:>11.1f}x "
              f"{r['expected_value']:>9.4f} {ev_mark} {r['n_hits']:>7}")

    print("-" * 120)

    # 期望值 Top 5
    print("\n" + "=" * 80)
    print("【期望值 Top 5】—— 期望值 = 命中率 × 命中倍数，>1 才正期望")
    print("=" * 80)
    for i, r in enumerate(sorted(results, key=lambda x: -x["expected_value"])[:5], 1):
        ev_mark = "✅ 正期望!" if r["expected_value"] > 1 else ""
        print(f"{i}. {r['name']}")
        print(f"   命中率: {r['hit_rate']*100:.3f}%  命中倍数: {r['avg_hit_mult']:.1f}x  期望值: {r['expected_value']:.4f} {ev_mark}")

    # 命中倍数 Top 5
    print("\n" + "=" * 80)
    print("【命中倍数 Top 5】—— 命中时赚多少倍")
    print("=" * 80)
    for i, r in enumerate(sorted(results, key=lambda x: -x["avg_hit_mult"])[:5], 1):
        print(f"{i}. {r['name']}: {r['avg_hit_mult']:.1f}x (命中率 {r['hit_rate']*100:.3f}%)")

    # 分析
    print("\n" + "=" * 80)
    print("【关键分析】")
    print("=" * 80)

    best = max(results, key=lambda x: x["expected_value"])
    print(f"\n最优组合: {best['name']}")
    print(f"  期望值: {best['expected_value']:.4f}")
    print(f"  命中率: {best['hit_rate']*100:.3f}%")
    print(f"  命中倍数: {best['avg_hit_mult']:.1f}x")

    if best["expected_value"] > 1:
        print(f"\n🎯 找到正期望组合！")
        print(f"   理论上每投入100元，期望回收 {best['expected_value']*100:.1f} 元")
    else:
        gap_to_positive = 1 / best["expected_value"] if best["expected_value"] > 0 else float('inf')
        print(f"\n❌ 未找到正期望组合")
        print(f"   最优组合需要命中率提升 {gap_to_positive:.1f}x 才能跑正")


def main():
    print("加载历史数据...")
    matches = load_data()
    print(f"共 {len(matches)} 场有效数据")

    print(f"\n运行深度回测（19 种配置，每种 10000 次模拟）...")
    results = run_deep_backtest(matches, n_sim=10000)

    print_results(results)


if __name__ == "__main__":
    main()
