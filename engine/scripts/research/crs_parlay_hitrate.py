# -*- coding: utf-8 -*-
"""串关胜率表：给定单场 top-k 覆盖率 c，n 串全中胜率 = c^n（独立近似）。

动机（2026-09-29 大哥定轴）："通过提高预测选腿的准确率提高胜率达到正收益"。
本表把 crs_topk_coverage 的单场覆盖曲线换算成串关胜率与实际回款，回答：
  ① 在 30 元预算内，哪个 (n 串, k 选) 组合的胜率最高
  ② 单场覆盖率要提高到多少，某个结构才能翻正
  ③ 命中时回款多大（以小博大的"大"有多大）

赔率口径：用 2026-09-28 实盘 CRS 均价（top-k 位平均赔率，k 越大纳入越冷的项）。
独立近似偏保守：⑨实测轮内负相关 2.21 → 连乘低估全中率（实际胜率应更高）。
开发者 sszhang
"""
from __future__ import annotations

import io
import sys
from itertools import product
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# crs_topk_coverage 15300 场实测累积覆盖
COVER = {1: .1212, 2: .2201, 3: .3085, 4: .3890, 5: .4624,
         6: .5322, 7: .5962, 8: .6529}
# 2026-09-28 实盘 CRS 池：top-k 位的典型赔率（位次越后越冷）
ODDS_AT = {1: 5.8, 2: 6.6, 3: 7.6, 4: 9.0, 5: 11.0, 6: 13.5, 7: 16.0, 8: 20.0}
UNIT = 2.0
BUDGET = 30.0


def geo_odds(k):
    """k 选时的"最低回款"用最小赔率（最坏命中）、典型回款用几何均值。"""
    os_ = [ODDS_AT[i] for i in range(1, k + 1)]
    g = 1.0
    for o in os_:
        g *= o
    return min(os_), g ** (1.0 / k)


def main():
    print("=" * 78)
    print("串关胜率表 · 15300 场实测覆盖 × 实盘赔率 · 预算 30 元")
    print("=" * 78)
    print("  胜率=全中概率（c^n 独立近似·实测轮内负相关→真实略高）")
    print("  最低/典型回款=命中时拿到多少（以小博大的倍数）\n")
    print(f"  {'结构':11} {'注数':>4} {'成本':>6} {'单场覆盖':>8} {'全中胜率':>8} "
          f"{'最低回款':>9} {'典型回款':>9} {'期望':>8} {'可行':>5}")

    rows = []
    for n in (2, 3, 4, 5):
        for k in (1, 2, 3, 4):
            bets = k ** n
            cost = bets * UNIT
            c = COVER[k]
            win = c ** n
            omin, ogeo = geo_odds(k)
            r_min = omin ** n * UNIT
            r_typ = ogeo ** n * UNIT
            ev = win * r_typ / cost if cost else 0
            ok = cost <= BUDGET
            rows.append((n, k, bets, cost, c, win, r_min, r_typ, ev, ok))
            print(f"  {n}串{k}选{'':5} {bets:>4} {cost:>5.0f}元 {c*100:>7.1f}% "
                  f"{win*100:>7.2f}% {r_min:>8.0f}元 {r_typ:>8.0f}元 "
                  f"{(ev-1)*100:>+7.1f}% {'✅' if ok else '❌超线':>5}")

    print("\n" + "=" * 78)
    print("翻正门槛：单场覆盖率要多高，该结构期望才 ≥0")
    print("=" * 78)
    print("  解 c_req: c^n × 典型回款 = 成本 → c_req = (cost/r_typ)^(1/n)\n")
    print(f"  {'结构':11} {'当前覆盖':>8} {'需要覆盖':>8} {'缺口':>8} {'当前是k=?的水平'}")
    for n, k, bets, cost, c, win, r_min, r_typ, ev, ok in rows:
        if not ok:
            continue
        creq = (cost / r_typ) ** (1.0 / n)
        gap = (creq - c) * 100
        # 当前覆盖需要相当于 top几 的水平
        eq = next((kk for kk in sorted(COVER) if COVER[kk] >= creq), ">8")
        print(f"  {n}串{k}选{'':5} {c*100:>7.1f}% {creq*100:>7.1f}% "
              f"{gap:>+7.1f}pp  ≈ 现在 top{eq} 的覆盖水平"
              + ("  ← 缺口最小" if abs(gap) < 6 else ""))

    print("\n" + "=" * 78)
    print("关键换算：选腿准确率每提升 1pp，胜率提升多少")
    print("=" * 78)
    for n in (2, 3, 4):
        for k in (2, 3):
            if k ** n * UNIT > BUDGET:
                continue
            c = COVER[k]
            base = c ** n
            up = (c + 0.01) ** n
            print(f"  {n}串{k}选：覆盖 {c*100:.1f}%→{c*100+1:.1f}% "
                  f"胜率 {base*100:.2f}%→{up*100:.2f}% "
                  f"(相对提升 {(up/base-1)*100:+.1f}%)")
    print("\n  → n 串把覆盖提升 n 次方放大：3 串时 +1pp 覆盖 ≈ 胜率相对 +10%")
    print("  → 提高选腿准确率的杠杆所在：准确率的收益被串关指数放大")


if __name__ == "__main__":
    main()
