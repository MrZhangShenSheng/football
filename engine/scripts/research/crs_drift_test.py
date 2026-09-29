# -*- coding: utf-8 -*-
"""因素盲测 · 赔率变动（steam move）：资金流方向能否预测赛果。

动机（2026-09-29）：前两轮证伪都栽在"用公开信息"——集中度闸门复刻市场排序，
近况比分倾向偏离了但更不准。赔率从开盘到封盘的移动反映的是下注资金流，不是
公开信息，是采集库里唯一非公开信息类特征。这是最后一个数据齐备的方向。

核心假设：赔率下跌（drop）= 资金押注该比分 = 该比分真实概率被低估。
若成立，"跌得最多的比分"命中率应高于"市场封盘 top-k"。

判定（和 crs_factor_test 同一套标准）：
1. 偏离度：drift 排序 ≠ 市场封盘排序
2. 准确率：偏离场次上 drift 命中率 > 市场命中率
3. 真实结算：直接算投注回收率（避免"命中率×均赔"的错误口径）

严格盲测：开盘/封盘快照都在开赛前，不含赛后信息。

用法：
  python engine/scripts/research/crs_drift_test.py
  python engine/scripts/research/crs_drift_test.py --min-move 0.08

开发者 sszhang
"""
from __future__ import annotations

import argparse
import io
import json
import statistics as st
import sys
from collections import Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[3]
UNIT = 2.0


def load() -> list[dict]:
    """带开盘+封盘双快照的场次。"""
    out = []
    for p in sorted((ROOT / "engine/cache/hist_odds").glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for m in d.get("matches", []):
            op, cl = m.get("crsOpen") or {}, m.get("crs") or {}
            sc = str(m.get("score") or "")
            if not op or not cl or ":" not in sc:
                continue
            try:
                act = tuple(int(x) for x in sc.split(":")[:2])
            except ValueError:
                continue
            # 只保留两快照都有报价的比分项
            pair = {}
            for k, v in cl.items():
                if k.startswith("other") or k not in op:
                    continue
                if not v or not op[k]:
                    continue
                try:
                    hh, aa = (int(x) for x in str(k).split(":")[:2])
                except ValueError:
                    continue
                pair[(hh, aa)] = (float(op[k]), float(v))
            if len(pair) < 20:
                continue
            out.append({"date": str(m.get("date") or "")[:10], "code": m.get("code"),
                        "league": m.get("league"), "actual": act, "pair": pair,
                        "snaps": m.get("crsSnapshots", 0)})
    out.sort(key=lambda x: x["date"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-move", type=float, default=0.0,
                    help="只在最大跌幅≥该比例的场次上评估（0=全部）")
    ap.add_argument("--topn", type=int, default=8,
                    help="drift 排序只在封盘前 N 热门内挑（避免选到极冷比分）")
    ap.add_argument("--reverse", action="store_true",
                    help="反向：优先选涨幅最大（资金流出）的比分")
    a = ap.parse_args()

    print("=" * 80)
    print("因素盲测 · 赔率变动（开盘→封盘资金流）")
    print("=" * 80)
    rows = load()
    if not rows:
        print("  无双快照数据（需先跑 sporttery_hist_odds.py 重采）")
        return
    print(f"  样本 {len(rows)} 场（{rows[0]['date']} ~ {rows[-1]['date']}）")
    print(f"  参数：最小跌幅 {a.min_move:.0%} · drift 候选限封盘前 {a.topn} 热门\n")

    KS = (1, 2, 3, 4)
    n_div = Counter(); hit_mkt = Counter(); hit_drf = Counter()
    n_all = Counter(); all_mkt = Counter(); all_drf = Counter()
    # 真实结算：单式直选，命中即按该项赔率赔付
    cost = Counter(); pay_mkt = Counter(); pay_drf = Counter()
    moves = []
    evaluated = 0

    for r in rows:
        pair = r["pair"]
        close = {s: v[1] for s, v in pair.items()}
        mkt = [s for s, _ in sorted(close.items(), key=lambda kv: kv[1])]
        # drift = 封盘/开盘 - 1，越负=跌得越多=资金越看好
        drift = {s: (v[1] / v[0] - 1.0) for s, v in pair.items()}
        pool = mkt[: a.topn]           # 候选池：封盘前 N 热门
        max_drop = -min(drift[s] for s in pool)
        if max_drop < a.min_move:
            continue
        evaluated += 1
        moves.append(max_drop)
        drf = sorted(pool, key=lambda s: drift[s], reverse=a.reverse)

        act = r["actual"]
        for k in KS:
            mk, dk = mkt[:k], drf[:k]
            n_all[k] += 1
            all_mkt[k] += int(act in mk)
            all_drf[k] += int(act in dk)
            cost[k] += k * UNIT
            if act in mk:
                pay_mkt[k] += close[act] * UNIT
            if act in dk:
                pay_drf[k] += close[act] * UNIT
            if set(mk) != set(dk):
                n_div[k] += 1
                hit_mkt[k] += int(act in mk)
                hit_drf[k] += int(act in dk)

    if not evaluated:
        print("  无满足跌幅门槛的场次")
        return

    print("=" * 80)
    print("条件 1：drift 排序是否偏离市场封盘排序")
    print("=" * 80)
    print(f"  评估场次 {evaluated} · 最大跌幅中位 {st.median(moves):.1%}")
    for k in KS:
        print(f"  top{k}：{n_div[k]}/{n_all[k]} = {n_div[k]/n_all[k]*100:.1f}% 排序不同")

    print("\n" + "=" * 80)
    print("条件 2：偏离场次上 drift 是否更准")
    print("=" * 80)
    print(f"  {'k':>3} {'偏离':>6} {'市场':>8} {'drift':>8} {'差值':>9} {'判定':>7}")
    for k in KS:
        if not n_div[k]:
            continue
        pm = hit_mkt[k] / n_div[k] * 100
        pdf = hit_drf[k] / n_div[k] * 100
        print(f"  {k:>3} {n_div[k]:>6} {pm:>7.1f}% {pdf:>7.1f}% {pdf-pm:>+8.1f}pp "
              f"{'有 alpha' if pdf > pm else '无':>9}")

    print("\n" + "=" * 80)
    print("条件 3：真实结算回收率（单式直选，命中按实际赔率赔付）")
    print("=" * 80)
    print(f"  {'k':>3} {'场次':>6} {'投入':>9} {'市场回款':>10} {'回收率':>8} "
          f"{'drift回款':>10} {'回收率':>8}")
    for k in KS:
        if not cost[k]:
            continue
        rm = pay_mkt[k] / cost[k] * 100
        rd = pay_drf[k] / cost[k] * 100
        print(f"  {k:>3} {n_all[k]:>6} {cost[k]:>8.0f}元 {pay_mkt[k]:>9.0f}元 "
              f"{rm:>7.1f}% {pay_drf[k]:>9.0f}元 {rd:>7.1f}%")
    print("\n  注：体彩 CRS 抽水约 20-25%，回收率 100% 为盈亏平衡线。")


if __name__ == "__main__":
    main()
