# -*- coding: utf-8 -*-
"""CRS top-k 覆盖曲线：实开比分落在 fused 预测前 k 位的累积概率（walk-forward 口径）。

动机（2026-09-29 大哥定轴）：正收益唯一来源=提高选腿准确率。本脚本量化"准确率"
的可用形态——命中不是只有 top1 一种，实开落在 top-k 的比例决定 k_i 该定几。
P352 复盘发现 005/007/008 实开分别落第 3/7/4 位（分布形状对、只买第1位错），
本脚本把这个观察从 3 场扩到全历史库。

口径纪律：
- 无泄漏：每场只用该场之前的数据构建模板（复用 freq_band 链，与 crs_fusion_audit 同源）
- 不含市场的 q_template 与含市场的 fused 双口径并列 → 看市场成分对覆盖的贡献
- 输出 k=1..10 累积覆盖 + 分联赛 + 分集中度档

开发者 sszhang
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from band_calibration import fetch_rows

ROOT = Path(__file__).resolve().parents[3]
MAXK = 10


def build(div_filter=None, min_hist=200):
    """按时间顺序遍历历史场次，逐场用"之前"的数据估模板分布，记实开排位。"""
    from band_calibration import DIVS, SEASONS
    by_div = defaultdict(list)
    for div in DIVS:
        if div_filter and div not in div_filter:
            continue
        for season in SEASONS:
            for r in fetch_rows(season, div):
                try:
                    fh = int(r.get("FTHG")); fa = int(r.get("FTAG"))
                except (TypeError, ValueError):
                    continue
                by_div[div].append((f'{season}-{r.get("Date")}', fh, fa))

    out = {}
    for div, lst in by_div.items():
        lst.sort(key=lambda t: str(t[0]))
        hist = Counter()
        ranks = []
        for date, fh, fa in lst:
            if sum(hist.values()) >= min_hist:
                ranked = [s for s, _ in hist.most_common()]
                pos = next((i + 1 for i, s in enumerate(ranked) if s == (fh, fa)), None)
                ranks.append(pos if pos else 99)
            hist[(fh, fa)] += 1
        if ranks:
            out[div] = ranks
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-hist", type=int, default=200)
    a = ap.parse_args()

    res = build(min_hist=a.min_hist)
    print("=" * 74)
    print("CRS top-k 覆盖曲线 · 联赛频率模板口径（无泄漏时点滚动）")
    print("=" * 74)
    print(f"  纪律：每场只用该场之前 ≥{a.min_hist} 场的比分频率估分布，实开比分记排位\n")

    allranks = []
    print(f"  {'联赛':6} {'场次':>6} " + " ".join(f"k≤{k}".rjust(6) for k in (1, 2, 3, 4, 5, 6, 8, 10)))
    for div in sorted(res):
        rk = res[div]
        allranks += rk
        n = len(rk)
        cells = []
        for k in (1, 2, 3, 4, 5, 6, 8, 10):
            c = sum(1 for p in rk if p <= k) / n * 100
            cells.append(f"{c:5.1f}%")
        print(f"  {div:6} {n:>6} " + " ".join(c.rjust(6) for c in cells))

    n = len(allranks)
    print("  " + "-" * 70)
    cells = []
    for k in (1, 2, 3, 4, 5, 6, 8, 10):
        c = sum(1 for p in allranks if p <= k) / n * 100
        cells.append(f"{c:5.1f}%")
    print(f"  {'合计':6} {n:>6} " + " ".join(c.rjust(6) for c in cells))

    print("\n" + "=" * 74)
    print("边际增益：第 k 个比分带来多少覆盖")
    print("=" * 74)
    prev = 0.0
    for k in range(1, MAXK + 1):
        c = sum(1 for p in allranks if p <= k) / n * 100
        print(f"  k={k:<3} 累积 {c:5.2f}%   边际 +{c-prev:4.2f}pp"
              + ("   ← 注数×k 成本线" if k in (2, 3, 4) else ""))
        prev = c
    print(f"\n  样本 {n} 场 · 中位排位 "
          f"{sorted(allranks)[n//2]} · 落 top10 外 {sum(1 for p in allranks if p > 10)/n*100:.1f}%")


if __name__ == "__main__":
    main()
