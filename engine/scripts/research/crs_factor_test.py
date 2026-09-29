# -*- coding: utf-8 -*-
"""因素盲测：近况比分倾向能否让模型排序偏离市场且更准。

动机（2026-09-29）：集中度闸门在 4999 场大样本下证伪——它提升准确率但赔率
同步缩水，信息已被市场完全定价。唯一出路是找市场未定价的信息。本脚本建立
"因素是否有 alpha"的判定框架，先测第一个因素：近况比分倾向。

判定标准（两个条件必须同时满足，缺一即无 alpha）：
1. 偏离度：模型 top-k 必须和市场 top-k 不同（否则只是复刻市场，不可能盈利）
2. 准确率：在偏离的那部分场次上，模型命中率 > 市场命中率

严格盲测纪律：
- 近况只用该场之前的比赛（按日期截断，逐场滚动）
- 赔率用开赛前最后快照（hist_odds 采集时已保证）
- 无泄漏：不使用当场赛果计算任何特征

因素定义（近况比分倾向）：
  取两队各最近 N 场，统计其比分分布 → 混合成该场的先验
  与联赛基准比分分布融合，得到模型排序

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

from common import load_aliases

ROOT = Path(__file__).resolve().parents[3]


def zh_map() -> dict[str, str]:
    out = {}
    for tid, srcs in load_aliases().items():
        if srcs.get("zh"):
            out[srcs["zh"]] = tid
    return out


def load_hist() -> list[dict]:
    """hist_odds 全量，按日期升序（滚动累积用）。"""
    out = []
    for p in sorted((ROOT / "engine/cache/hist_odds").glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for m in d.get("matches", []):
            sc = str(m.get("score") or "")
            crs = m.get("crs") or {}
            if ":" not in sc or not crs:
                continue
            try:
                h, a = (int(x) for x in sc.split(":")[:2])
            except ValueError:
                continue
            odds = {}
            for kk, v in crs.items():
                if str(kk).startswith("other") or not v:
                    continue
                try:
                    hh, aa = (int(x) for x in str(kk).split(":")[:2])
                    odds[(hh, aa)] = float(v)
                except (ValueError, TypeError):
                    continue
            if len(odds) < 20:
                continue
            out.append({"date": str(m.get("date") or "")[:10],
                        "code": m.get("code"), "league": m.get("league"),
                        "home": m.get("home"), "away": m.get("away"),
                        "actual": (h, a), "odds": odds})
    out.sort(key=lambda x: x["date"])
    return out


def market_rank(odds: dict) -> list[tuple]:
    """市场排序：赔率升序 = 隐含概率降序。"""
    return [s for s, _ in sorted(odds.items(), key=lambda kv: kv[1])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recent", type=int, default=6, help="近况取最近几场")
    ap.add_argument("--weight", type=float, default=0.35,
                    help="近况权重（0=纯市场，1=纯近况）")
    ap.add_argument("--min-hist", type=int, default=4,
                    help="两队各至少要有几场历史才参与")
    a = ap.parse_args()

    print("=" * 80)
    print("因素盲测 · 近况比分倾向 vs 市场排序")
    print("=" * 80)

    rows = load_hist()
    print(f"  样本 {len(rows)} 场（{rows[0]['date']} ~ {rows[-1]['date']}）")
    print(f"  参数：近况 {a.recent} 场 · 权重 {a.weight} · 门槛 {a.min_hist} 场\n")

    # 逐场滚动：先判定，再把本场并入历史（严格无泄漏）
    team_hist: dict[str, list] = defaultdict(list)
    league_hist: dict[str, Counter] = defaultdict(Counter)

    stats = {"eval": 0, "diverge": 0}
    # 命中计数：k → [市场命中, 模型命中]；仅在偏离场次上计
    hit_mkt = Counter(); hit_mdl = Counter(); n_div = Counter()
    # 全样本命中（含未偏离），用于看整体
    all_mkt = Counter(); all_mdl = Counter(); n_all = Counter()
    # 偏离场次的赔率账：模型选中项的赔率均值 vs 市场
    odds_mdl = []; odds_mkt = []

    KS = (1, 2, 3, 4)
    for r in rows:
        h, aw, lg = r["home"], r["away"], r["league"]
        hh = team_hist[h]; ah = team_hist[aw]
        if len(hh) >= a.min_hist and len(ah) >= a.min_hist and league_hist[lg]:
            # --- 近况先验：两队最近 N 场的比分分布混合 ---
            rec = Counter()
            for sc in hh[-a.recent:]:
                rec[sc] += 1
            for sc in ah[-a.recent:]:
                # 客队视角翻转（该队作为客队时的比分倾向）
                rec[sc] += 1
            base = league_hist[lg]
            bt = sum(base.values()) or 1
            rt = sum(rec.values()) or 1

            mk = market_rank(r["odds"])
            # 模型分：市场隐含概率 × (1-w) + 近况+联赛先验 × w
            inv = {s: 1.0 / o for s, o in r["odds"].items()}
            it = sum(inv.values()) or 1
            score = {}
            for s in r["odds"]:
                p_mkt = inv[s] / it
                p_rec = rec.get(s, 0) / rt
                p_base = base.get(s, 0) / bt
                p_prior = 0.6 * p_rec + 0.4 * p_base
                score[s] = (1 - a.weight) * p_mkt + a.weight * p_prior
            md = [s for s, _ in sorted(score.items(), key=lambda kv: -kv[1])]

            stats["eval"] += 1
            act = r["actual"]
            for k in KS:
                n_all[k] += 1
                all_mkt[k] += int(act in mk[:k])
                all_mdl[k] += int(act in md[:k])
                if set(mk[:k]) != set(md[:k]):
                    n_div[k] += 1
                    hit_mkt[k] += int(act in mk[:k])
                    hit_mdl[k] += int(act in md[:k])
                    if k == 3:
                        odds_mkt.append(sum(r["odds"][s] for s in mk[:k]) / k)
                        odds_mdl.append(sum(r["odds"][s] for s in md[:k]) / k)
            if set(mk[:3]) != set(md[:3]):
                stats["diverge"] += 1

        # 并入历史（放在判定之后 = 无泄漏）
        team_hist[h].append(r["actual"])
        team_hist[aw].append(r["actual"])
        league_hist[lg][r["actual"]] += 1

    ev = stats["eval"]
    print("=" * 80)
    print("条件 1：模型排序是否偏离市场")
    print("=" * 80)
    print(f"  可评估场次 {ev}（两队各有 ≥{a.min_hist} 场历史）")
    for k in KS:
        if n_all[k]:
            print(f"  top{k}：{n_div[k]}/{n_all[k]} = {n_div[k]/n_all[k]*100:.1f}% 场次排序不同")
    if not ev:
        print("\n  无可评估场次，退出")
        return

    print("\n" + "=" * 80)
    print("条件 2：偏离场次上，模型是否比市场更准（这是 alpha 的唯一证据）")
    print("=" * 80)
    print(f"  {'k':>3} {'偏离场次':>7} {'市场命中':>9} {'模型命中':>9} {'差值':>9} {'判定':>6}")
    for k in KS:
        if not n_div[k]:
            continue
        pm = hit_mkt[k] / n_div[k] * 100
        pd = hit_mdl[k] / n_div[k] * 100
        d = pd - pm
        verdict = "有 alpha" if d > 0 else "无"
        print(f"  {k:>3} {n_div[k]:>7} {pm:>8.1f}% {pd:>8.1f}% {d:>+8.1f}pp {verdict:>8}")

    print("\n" + "=" * 80)
    print("全样本对照（含未偏离场次，看整体水位）")
    print("=" * 80)
    print(f"  {'k':>3} {'场次':>7} {'市场':>8} {'模型':>8} {'差值':>9}")
    for k in KS:
        if not n_all[k]:
            continue
        pm = all_mkt[k] / n_all[k] * 100
        pd = all_mdl[k] / n_all[k] * 100
        print(f"  {k:>3} {n_all[k]:>7} {pm:>7.1f}% {pd:>7.1f}% {pd-pm:>+8.1f}pp")

    if odds_mkt:
        import statistics as st
        print("\n" + "=" * 80)
        print("赔率对照（top3 偏离场次）：模型选的腿是否更值钱")
        print("=" * 80)
        print(f"  市场 top3 均赔 {st.mean(odds_mkt):.2f} · 模型 top3 均赔 "
              f"{st.mean(odds_mdl):.2f} · 差 {st.mean(odds_mdl)-st.mean(odds_mkt):+.2f}")
        print("  → 模型均赔更高=选了更冷的腿，若命中率不降则是真 alpha")

        print("  注：不要用「命中率×均赔」当期望——命中的那项赔率通常远低于"
              "top-k 均值（热门更易中），两个量对不上。期望必须逐场按实际中奖项"
              "赔率累加，见 crs_sim_invest.py 的真实结算。")


if __name__ == "__main__":
    main()
