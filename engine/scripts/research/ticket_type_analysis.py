# -*- coding: utf-8 -*-
r"""票型穷举与实测分析（2026-09-29）。

主公需求：穷举可出票的类型 → 分析各票型的赔率/胜率 → 为建模打地基。

票型维度：
  1. 串关结构（shape）：单关、2串1、3串1、4串1、4串11、8串9、全2关、N串1等
  2. 选项模式（options）：单选、双选、三选（复式展开）
  3. 玩法类型（pool）：HAD、CRS、TTG（不同赔率分布）
  4. 腿数（n_legs）：1~8
  5. 选腿策略（pick）：概率降序、gap断层、随机等

穷举输出：所有合法组合 → 各组合的理论特征（注数、合赔分布、命中率理论值）
实测输出：用 hist_odds 历史数据回测各票型的实际 ROI

开发者 sszhang
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from itertools import combinations, product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ══════════════════════════════════════════════════════════════════════════════
# 一、票型维度穷举
# ══════════════════════════════════════════════════════════════════════════════

# 体彩允许的串关结构（官方规则）
SHAPES = {
    '单关':   {'min_legs': 1, 'max_legs': 1,  'combos': lambda n: [(i,) for i in range(n)]},
    '2串1':   {'min_legs': 2, 'max_legs': 2,  'combos': lambda n: [tuple(range(n))] if n == 2 else None},
    '3串1':   {'min_legs': 3, 'max_legs': 3,  'combos': lambda n: [tuple(range(n))] if n == 3 else None},
    '4串1':   {'min_legs': 4, 'max_legs': 4,  'combos': lambda n: [tuple(range(n))] if n == 4 else None},
    '5串1':   {'min_legs': 5, 'max_legs': 5,  'combos': lambda n: [tuple(range(n))] if n == 5 else None},
    '6串1':   {'min_legs': 6, 'max_legs': 6,  'combos': lambda n: [tuple(range(n))] if n == 6 else None},
    '7串1':   {'min_legs': 7, 'max_legs': 7,  'combos': lambda n: [tuple(range(n))] if n == 7 else None},
    '8串1':   {'min_legs': 8, 'max_legs': 8,  'combos': lambda n: [tuple(range(n))] if n == 8 else None},
    '3串4':   {'min_legs': 3, 'max_legs': 3,  'combos': lambda n: list(combinations(range(n), 2)) + [tuple(range(n))] if n == 3 else None},
    '4串5':   {'min_legs': 4, 'max_legs': 4,  'combos': lambda n: list(combinations(range(n), 3)) + [tuple(range(n))] if n == 4 else None},
    '4串11':  {'min_legs': 4, 'max_legs': 4,  'combos': lambda n: [c for k in (2,3,4) for c in combinations(range(n), k)] if n == 4 else None},
    '5串16':  {'min_legs': 5, 'max_legs': 5,  'combos': lambda n: [c for k in (2,3,4,5) for c in combinations(range(n), k)] if n == 5 else None},
    '6串22':  {'min_legs': 6, 'max_legs': 6,  'combos': lambda n: [c for k in (2,3,4,5,6) for c in combinations(range(n), k)] if n == 6 else None},
    '8串9':   {'min_legs': 8, 'max_legs': 8,  'combos': lambda n: list(combinations(range(n), 7)) + [tuple(range(n))] if n == 8 else None},
    '8串247': {'min_legs': 8, 'max_legs': 8,  'combos': lambda n: [c for k in (2,3,4,5,6,7,8) for c in combinations(range(n), k)] if n == 8 else None},
    '全2关':  {'min_legs': 2, 'max_legs': 8,  'combos': lambda n: list(combinations(range(n), 2)) if n >= 2 else None},
    '全3关':  {'min_legs': 3, 'max_legs': 8,  'combos': lambda n: list(combinations(range(n), 3)) if n >= 3 else None},
}

# 选项模式
OPTIONS = {
    'single': 1,    # 单选
    'dual': 2,      # 双选
    'triple': 3,    # 三选
}

# 玩法类型
POOLS = ['HAD', 'CRS', 'TTG']

# 选腿策略
PICKS = ['prob_desc', 'gap_top', 'random']


def enumerate_ticket_types():
    """穷举所有合法票型组合。"""
    types = []
    for shape_name, shape_cfg in SHAPES.items():
        for n_legs in range(shape_cfg['min_legs'], min(shape_cfg['max_legs'], 8) + 1):
            for opt_name, opt_k in OPTIONS.items():
                for pool in POOLS:
                    # CRS/TTG 通常不做三选（选项太多）
                    if pool in ('CRS', 'TTG') and opt_k > 2:
                        continue
                    # 计算注数
                    combos = shape_cfg['combos'](n_legs)
                    if combos is None:
                        continue
                    n_combos = len(combos)
                    n_bets = n_combos * (opt_k ** n_legs)
                    types.append({
                        'shape': shape_name,
                        'n_legs': n_legs,
                        'options': opt_name,
                        'pool': pool,
                        'n_combos': n_combos,
                        'n_bets': n_bets,
                        'cost_2yuan': n_bets * 2,
                    })
    return types


def print_ticket_types():
    """打印票型穷举表。"""
    types = enumerate_ticket_types()
    print("=" * 90)
    print("票型穷举（体彩合法组合）")
    print("=" * 90)
    print(f"{'结构':<10} {'腿数':>4} {'选项':<8} {'玩法':<5} {'骨架数':>6} {'注数':>8} {'成本':>8}")
    print("-" * 90)

    # 按成本分组
    by_cost = defaultdict(list)
    for t in types:
        by_cost[t['cost_2yuan']].append(t)

    for t in sorted(types, key=lambda x: (x['cost_2yuan'], x['shape'], x['pool'])):
        print(f"{t['shape']:<10} {t['n_legs']:>4} {t['options']:<8} {t['pool']:<5} "
              f"{t['n_combos']:>6} {t['n_bets']:>8} {t['cost_2yuan']:>8}")

    print("-" * 90)
    print(f"合计 {len(types)} 种票型")

    # 统计
    print("\n按成本区间分布：")
    brackets = [(0, 10), (10, 50), (50, 100), (100, 500), (500, 1000), (1000, float('inf'))]
    for lo, hi in brackets:
        cnt = sum(1 for t in types if lo < t['cost_2yuan'] <= hi)
        if cnt:
            print(f"  {lo}~{hi} 元: {cnt} 种")

    return types


# ══════════════════════════════════════════════════════════════════════════════
# 二、各票型的理论特征
# ══════════════════════════════════════════════════════════════════════════════

def theoretical_features(shape_name, n_legs, opt_k, avg_leg_odds=2.0, avg_leg_prob=0.45):
    """
    给定票型参数，计算理论特征：
    - 单注全中概率
    - 期望合赔
    - 期望 ROI（假设市场公平）
    """
    shape_cfg = SHAPES.get(shape_name)
    if not shape_cfg:
        return None
    combos = shape_cfg['combos'](n_legs)
    if combos is None:
        return None

    n_combos = len(combos)
    n_bets = n_combos * (opt_k ** n_legs)

    # 单注全中概率（假设各腿独立）
    # 单选：p^n_legs_in_combo
    # 双选：每腿命中率提升（假设次选项命中率 = 0.7 * 主选项）
    if opt_k == 1:
        p_leg = avg_leg_prob
    elif opt_k == 2:
        p_leg = avg_leg_prob + 0.25  # 双选覆盖更多
    else:
        p_leg = avg_leg_prob + 0.35  # 三选

    # 平均组合长度
    avg_combo_len = sum(len(c) for c in combos) / n_combos

    # 至少一注中的概率（近似）
    p_any_win = 1 - (1 - p_leg ** avg_combo_len) ** n_combos

    # 平均合赔（单注）
    avg_combo_odds = avg_leg_odds ** avg_combo_len

    return {
        'n_combos': n_combos,
        'n_bets': n_bets,
        'avg_combo_len': round(avg_combo_len, 2),
        'p_leg': round(p_leg, 3),
        'p_any_win': round(p_any_win, 4),
        'avg_combo_odds': round(avg_combo_odds, 2),
        'cost': n_bets * 2,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 三、用历史数据回测
# ══════════════════════════════════════════════════════════════════════════════

def load_hist_odds():
    """加载历史赔率数据。"""
    p = Path(__file__).resolve().parents[2] / "data" / "05-hist-odds" / "hist_odds.json"
    if not p.exists():
        return []
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def backtest_shape(matches, shape_name, n_legs, opt_k, pool='HAD', n_trials=1000):
    """
    回测指定票型：
    - 从历史数据中随机抽取 n_legs 场
    - 按概率降序选腿
    - 计算命中率和 ROI
    """
    import random

    shape_cfg = SHAPES.get(shape_name)
    if not shape_cfg:
        return None

    valid = [m for m in matches if m.get('actual') and m.get('odds')]
    if len(valid) < n_legs:
        return None

    combos_fn = shape_cfg['combos']

    wins = 0
    total_payout = 0.0
    total_cost = 0.0

    for _ in range(n_trials):
        # 随机抽 n_legs 场
        sample = random.sample(valid, n_legs)

        combos = combos_fn(n_legs)
        if combos is None:
            continue

        # 每场选 top-k 选项（按赔率倒数=隐含概率降序）
        leg_picks = []
        for m in sample:
            odds = m['odds']  # {(h,a): odds}
            # 按赔率升序 = 概率降序
            sorted_opts = sorted(odds.items(), key=lambda kv: kv[1])[:opt_k]
            leg_picks.append(sorted_opts)

        # 展开所有注
        actual = [tuple(m['actual']) for m in sample]

        n_bets = len(combos) * (opt_k ** n_legs)
        cost = n_bets * 2
        total_cost += cost

        # 检查每个组合的每种选项组合
        payout = 0.0
        for combo in combos:
            # combo 中各腿的选项组合
            combo_legs = [leg_picks[i] for i in combo]
            for bet in product(*combo_legs):
                # bet = [(score1, odds1), (score2, odds2), ...]
                all_hit = all(score == actual[combo[j]] for j, (score, _) in enumerate(bet))
                if all_hit:
                    combo_odds = math.prod(o for _, o in bet)
                    payout += 2 * combo_odds
                    wins += 1

        total_payout += payout

    return {
        'shape': shape_name,
        'n_legs': n_legs,
        'options': opt_k,
        'pool': pool,
        'trials': n_trials,
        'wins': wins,
        'win_rate': round(wins / n_trials, 4) if n_trials else 0,
        'total_cost': total_cost,
        'total_payout': round(total_payout, 2),
        'roi': round((total_payout / total_cost - 1) * 100, 2) if total_cost else 0,
    }


def main():
    print("=" * 90)
    print("票型分析 · 第一步：穷举合法票型")
    print("=" * 90)

    types = print_ticket_types()

    # 筛选实用票型（成本 ≤ 100 元）
    practical = [t for t in types if t['cost_2yuan'] <= 100]
    print(f"\n实用票型（成本 ≤ 100 元）：{len(practical)} 种")

    # 进一步筛选常用票型
    common_shapes = ['单关', '2串1', '3串1', '4串1', '4串11', '全2关']
    common = [t for t in practical if t['shape'] in common_shapes]
    print(f"常用结构票型：{len(common)} 种")

    print("\n" + "=" * 90)
    print("票型分析 · 第二步：理论特征（HAD 玩法·均价2.0·均概率0.45）")
    print("=" * 90)
    print(f"{'结构':<10} {'腿数':>4} {'选项':>4} {'骨架':>4} {'注数':>6} {'均长':>5} "
          f"{'腿中率':>6} {'票中率':>7} {'均赔':>8}")
    print("-" * 90)

    for t in common:
        feat = theoretical_features(t['shape'], t['n_legs'], OPTIONS[t['options']])
        if feat:
            print(f"{t['shape']:<10} {t['n_legs']:>4} {OPTIONS[t['options']]:>4} "
                  f"{feat['n_combos']:>4} {feat['n_bets']:>6} {feat['avg_combo_len']:>5} "
                  f"{feat['p_leg']:>6.3f} {feat['p_any_win']:>7.4f} {feat['avg_combo_odds']:>8.2f}")

    print("\n" + "=" * 90)
    print("票型分析 · 第三步：历史回测（待主公确认后执行）")
    print("=" * 90)
    print("回测需要对每种票型跑 1000+ 次随机抽样，预计耗时较长。")
    print("建议先确认要回测的票型范围。")


if __name__ == "__main__":
    main()
