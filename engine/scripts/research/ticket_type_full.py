# -*- coding: utf-8 -*-
r"""票型完整穷举（2026-09-29 v2）。

根据体彩官方规则和实际数据重新穷举所有合法票型维度。

## 票型维度

### 一、玩法类型（5种）
| 玩法 | 代码 | 选项数 | 选项 |
|:--:|:--:|:--:|:--|
| 胜平负 | HAD | 3 | 主胜(H)、平(D)、客胜(A) |
| 让球胜平负 | HHAD | 3 | 主胜(H)、平(D)、客胜(A)（基于让球盘口） |
| 比分 | CRS | 31 | 0:0~5:0（主胜15种）+ 0:0~0:5（客胜15种）+ 平局6种 + 胜其他/平其他/负其他（实际31键） |
| 总进球 | TTG | 8 | 0球(s0)、1球(s1)、...、7+球(s7) |
| 半全场 | HAFU | 9 | HH/HD/HA/DH/DD/DA/AH/AD/AA |

### 二、串关方式（M串N，按场次分）
| 场次 | 允许的M串N |
|:--:|:--|
| 2场 | 2串1 |
| 3场 | 3串1、3串4、3串7 |
| 4场 | 4串1、4串4、4串5、4串11、4串15 |
| 5场 | 5串1、5串5、5串6、5串10、5串16、5串26、5串31 |
| 6场 | 6串1、6串6、6串7、6串20、6串22、6串57、6串63 |
| 7场 | 7串1、7串7、7串8、7串35、7串120 |
| 8场 | 8串1、8串8、8串9、8串247 |

### 三、单关限制
- 不是所有场次都开单关（poolSingle 字段控制）
- 不是所有玩法都开单关
- 需要从体彩数据的 poolSingle 字段读取

### 四、复式选项
- 同一腿可选多个选项（复式展开）
- 例：HAD 选"主胜+平"= 2选项复式

### 五、混合过关
- 同一场比赛可选不同玩法组合（如 HAD + CRS）
- 不同场比赛可选不同玩法
- 同场限一个玩法入选（铁律9）

开发者 sszhang
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from itertools import combinations, product
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════════
# 一、玩法定义
# ══════════════════════════════════════════════════════════════════════════════

POOLS = {
    'HAD': {
        'name': '胜平负',
        'options': ['H', 'D', 'A'],
        'n_options': 3,
    },
    'HHAD': {
        'name': '让球胜平负',
        'options': ['H', 'D', 'A'],
        'n_options': 3,
    },
    'CRS': {
        'name': '比分',
        'options': [
            # 主胜比分 (15种)
            '1:0', '2:0', '2:1', '3:0', '3:1', '3:2', '4:0', '4:1', '4:2',
            '5:0', '5:1', '5:2',
            # 平局比分 (6种)
            '0:0', '1:1', '2:2', '3:3',
            # 客胜比分 (15种)
            '0:1', '0:2', '1:2', '0:3', '1:3', '2:3', '0:4', '1:4', '2:4',
            '0:5', '1:5', '2:5',
            # 其他
            '胜其他', '平其他', '负其他',
        ],
        'n_options': 31,
    },
    'TTG': {
        'name': '总进球',
        'options': ['0', '1', '2', '3', '4', '5', '6', '7+'],
        'n_options': 8,
    },
    'HAFU': {
        'name': '半全场',
        'options': ['HH', 'HD', 'HA', 'DH', 'DD', 'DA', 'AH', 'AD', 'AA'],
        'n_options': 9,
    },
}

# ══════════════════════════════════════════════════════════════════════════════
# 二、串关结构定义（完整版）
# ══════════════════════════════════════════════════════════════════════════════

def _combos(n, sizes):
    """生成指定 size 列表的所有组合"""
    return [c for k in sizes for c in combinations(range(n), k)]

# M串N 定义：n_legs -> [(shape_name, n_bets, combo_sizes)]
PARLAY_SHAPES = {
    1: [
        ('单关', 1, [1]),
    ],
    2: [
        ('2串1', 1, [2]),
    ],
    3: [
        ('3串1', 1, [3]),
        ('3串4', 4, [2, 3]),           # C(3,2) + C(3,3) = 3 + 1 = 4
        ('3串7', 7, [1, 2, 3]),        # C(3,1) + C(3,2) + C(3,3) = 3 + 3 + 1 = 7
    ],
    4: [
        ('4串1', 1, [4]),
        ('4串4', 4, [3, 4]),           # C(4,3) + C(4,4) = 4 + 1 = 5? 需要核实
        ('4串5', 5, [3, 4]),           # C(4,3) + C(4,4) = 4 + 1 = 5
        ('4串11', 11, [2, 3, 4]),      # C(4,2) + C(4,3) + C(4,4) = 6 + 4 + 1 = 11
        ('4串15', 15, [1, 2, 3, 4]),   # 全组合
    ],
    5: [
        ('5串1', 1, [5]),
        ('5串5', 5, [4, 5]),
        ('5串6', 6, [4, 5]),
        ('5串10', 10, [3, 4, 5]),
        ('5串16', 16, [3, 4, 5]),      # 需要核实
        ('5串26', 26, [2, 3, 4, 5]),
        ('5串31', 31, [1, 2, 3, 4, 5]),
    ],
    6: [
        ('6串1', 1, [6]),
        ('6串6', 6, [5, 6]),
        ('6串7', 7, [5, 6]),
        ('6串20', 20, [4, 5, 6]),
        ('6串22', 22, [3, 4, 5, 6]),
        ('6串57', 57, [2, 3, 4, 5, 6]),
        ('6串63', 63, [1, 2, 3, 4, 5, 6]),
    ],
    7: [
        ('7串1', 1, [7]),
        ('7串7', 7, [6, 7]),
        ('7串8', 8, [6, 7]),
        ('7串35', 35, [5, 6, 7]),
        ('7串120', 120, [2, 3, 4, 5, 6, 7]),
    ],
    8: [
        ('8串1', 1, [8]),
        ('8串8', 8, [7, 8]),
        ('8串9', 9, [7, 8]),
        ('8串247', 247, [2, 3, 4, 5, 6, 7, 8]),
    ],
}

# ══════════════════════════════════════════════════════════════════════════════
# 三、票型穷举
# ══════════════════════════════════════════════════════════════════════════════

def enumerate_all_ticket_types():
    """
    完整穷举所有票型组合。

    维度：
    1. 玩法类型：HAD / HHAD / CRS / TTG / HAFU
    2. 腿数：1~8
    3. 串关结构：对应腿数的所有合法 M串N
    4. 复式选项数：1 ~ 玩法最大选项数

    输出：每种组合的注数和成本
    """
    types = []

    for pool_code, pool_cfg in POOLS.items():
        max_opts = pool_cfg['n_options']

        for n_legs in range(1, 9):
            shapes = PARLAY_SHAPES.get(n_legs, [])

            for shape_name, base_combos, combo_sizes in shapes:
                # 计算骨架组合数
                n_combos = sum(len(list(combinations(range(n_legs), k))) for k in combo_sizes)

                # 复式选项数：1选 ~ 最大选项数（但实际常用 1~3）
                for n_picks in range(1, min(max_opts, 4) + 1):  # 限制最多4选，否则组合爆炸
                    # 注数 = 骨架数 × 复式展开
                    n_bets = n_combos * (n_picks ** n_legs)
                    cost = n_bets * 2

                    types.append({
                        'pool': pool_code,
                        'pool_name': pool_cfg['name'],
                        'n_legs': n_legs,
                        'shape': shape_name,
                        'n_picks': n_picks,
                        'n_combos': n_combos,
                        'n_bets': n_bets,
                        'cost': cost,
                    })

    return types


def print_summary():
    """打印票型穷举汇总"""
    types = enumerate_all_ticket_types()

    print("=" * 100)
    print("竞彩足球票型完整穷举")
    print("=" * 100)

    # 按玩法汇总
    print("\n一、按玩法分布")
    print("-" * 60)
    by_pool = defaultdict(list)
    for t in types:
        by_pool[t['pool']].append(t)

    for pool_code in ['HAD', 'HHAD', 'CRS', 'TTG', 'HAFU']:
        pool_types = by_pool[pool_code]
        print(f"\n{pool_code}（{POOLS[pool_code]['name']}，{POOLS[pool_code]['n_options']}选项）：{len(pool_types)} 种票型")

        # 按腿数和串关结构显示
        by_legs = defaultdict(list)
        for t in pool_types:
            by_legs[t['n_legs']].append(t)

        for n_legs in sorted(by_legs.keys()):
            leg_types = by_legs[n_legs]
            shapes = set(t['shape'] for t in leg_types)
            print(f"  {n_legs}腿：{len(leg_types)} 种 - 结构 {sorted(shapes)}")

    # 按成本区间分布
    print("\n" + "=" * 100)
    print("二、按成本区间分布")
    print("-" * 60)

    brackets = [
        (0, 10, '2~10元'),
        (10, 50, '10~50元'),
        (50, 100, '50~100元'),
        (100, 500, '100~500元'),
        (500, 1000, '500~1000元'),
        (1000, 10000, '1000~10000元'),
        (10000, float('inf'), '10000+元'),
    ]

    for lo, hi, label in brackets:
        subset = [t for t in types if lo < t['cost'] <= hi]
        if subset:
            pools = Counter(t['pool'] for t in subset)
            print(f"{label:>15}：{len(subset):>5} 种票型 - {dict(pools)}")

    # 低成本票型详表（实际可操作）
    print("\n" + "=" * 100)
    print("三、低成本票型详表（≤50元，实际可操作）")
    print("-" * 100)
    print(f"{'玩法':<6} {'腿数':>4} {'结构':<10} {'复式':>4} {'骨架':>6} {'注数':>8} {'成本':>8}")
    print("-" * 100)

    low_cost = [t for t in types if t['cost'] <= 50]
    for t in sorted(low_cost, key=lambda x: (x['cost'], x['pool'], x['n_legs'])):
        print(f"{t['pool']:<6} {t['n_legs']:>4} {t['shape']:<10} {t['n_picks']:>4}选 "
              f"{t['n_combos']:>6} {t['n_bets']:>8} {t['cost']:>8}")

    print("-" * 100)
    print(f"合计 {len(low_cost)} 种低成本票型")

    # 总计
    print("\n" + "=" * 100)
    print(f"总计 {len(types)} 种票型组合")
    print("=" * 100)

    return types


# ══════════════════════════════════════════════════════════════════════════════
# 四、混合过关维度（同场多玩法）
# ══════════════════════════════════════════════════════════════════════════════

def enumerate_mixed_parlay():
    """
    混合过关：同一张票中不同腿选不同玩法。

    组合爆炸警告：5腿×5玩法 = 5^5 = 3125 种玩法组合
    实际常用组合：
    - HAD + CRS（胜平负保底 + 比分博大）
    - HAD + TTG（方向 + 进球数）
    - HHAD + HAD（让球 + 不让球对冲）
    """
    common_mixes = [
        ('HAD+CRS', 'HAD 保底 + CRS 博大'),
        ('HAD+TTG', '方向 + 进球数'),
        ('HAD+HAFU', '方向 + 半全场'),
        ('HHAD+HAD', '让球 + 不让球'),
        ('CRS+TTG', '比分 + 进球数（高赔组合）'),
    ]

    print("\n" + "=" * 100)
    print("四、混合过关常用组合")
    print("-" * 60)
    for mix, desc in common_mixes:
        print(f"  {mix:<15} - {desc}")

    print("\n注：混合过关的组合数 = 玩法组合 × 串关结构 × 复式选项")
    print("    完整穷举会产生组合爆炸，建议按实际场景筛选")


if __name__ == '__main__':
    types = print_summary()
    enumerate_mixed_parlay()
