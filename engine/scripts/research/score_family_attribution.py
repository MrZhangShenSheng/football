# -*- coding: utf-8 -*-
"""归因：模型回收率 98.7% vs 市场 83.2% 的差从哪来。

方法：k=1 单关口径，逐场记录 (命中与否, 命中赔率, 排序位次, 集中度, 联赛)，
然后做三层分解：
  ① 贡献排行：单场赔付贡献 top15（看是不是极端值撑起来的）
  ② 剔除极值：去掉命中赔率最高的 1/5/10 场后，双方回收率还剩多少
  ③ 赔率分段：把市场 top1 赔率分桶，看模型优势集中在哪段（稳定 vs 集中）
附加：模型在"未命中场次"损失 vs 市场损失（成本侧对照）。
"""
from __future__ import annotations

import argparse
import io
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import score_family_model as sfm   # 其模块级已 reconfigure stdout 为 utf-8

UNIT = 2.0
CUT = sfm.CUT


def build():
    zh = {}
    for tid, srcs in sfm.load_aliases().items():
        if srcs.get("zh"):
            zh[srcs["zh"]] = tid
    tl = sfm.league_timeline()
    hist = sfm.load_hist()
    blind = []
    for m in hist:
        if m["date"] < CUT:
            continue
        hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
        if hid and aid:
            blind.append({**m, "hid": hid, "aid": aid})
    merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
    merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
               for i, m in enumerate(blind)]
    merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

    stats = defaultdict(sfm.TeamStats)
    X_tr, y_tr = [], []
    X_bl, meta = [], []
    tot_g = tot_n = 0
    fam_dist = defaultdict(Counter)
    for r in merged:
        kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
        lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
        fv_h = stats[h].vector(0, lg_gf)
        fv_a = stats[a].vector(1, lg_gf)
        ready = fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST
        if kind == "L" and date < CUT and ready:
            X_tr.append(sfm.feature_row((fv_h, fv_a)))
            y_tr.append(sfm.CLASSES.index(sfm.family_of(hg, ag)))
            fam_dist[sfm.family_of(hg, ag)][(hg, ag)] += 1
        elif kind == "B":
            meta.append(blind[r[6]])
            X_bl.append(sfm.feature_row((fv_h, fv_a)))
        stats[h].add(hg, ag, True)
        stats[a].add(ag, hg, False)
        tot_g += hg + ag
        tot_n += 1
    model = sfm.train_softmax(X_tr, y_tr, len(sfm.CLASSES))
    P = sfm.predict_proba(model, X_bl)
    return meta, P, fam_dist


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topn", type=int, default=15)
    a = ap.parse_args()

    meta, P, fam_dist = build()
    # 模型分布
    dists = []
    for row in P:
        d = {}
        for ci, cname in enumerate(sfm.CLASSES):
            tot = sum(fam_dist.get(cname, {}).values()) or 1
            for s, c in fam_dist.get(cname, {}).items():
                d[s] = d.get(s, 0.0) + row[ci] * (c / tot)
        dists.append(d)

    recs = []
    for i, m in enumerate(meta):
        act = m["actual"]
        close = m["odds"]
        mkt = [s for s, _ in sorted(close.items(), key=lambda kv: kv[1])]
        mdl = [s for s, _ in sorted(dists[i].items(), key=lambda kv: -kv[1])]
        o_act = close.get(act)
        recs.append({
            "date": m["date"], "code": m["code"], "league": m["league"],
            "actual": act, "o_act": o_act,
            "mkt_hit": act in mkt[:1], "mkt_top1": mkt[0], "o_mkt_top1": close[mkt[0]],
            "mdl_hit": act in mdl[:1], "mdl_top1": mdl[0],
            "o_mdl_top1": close.get(mdl[0]),
            "conc": sfm_market_conc(close),
        })

    n = len(recs)
    mh = sum(r["mkt_hit"] for r in recs)
    dh = sum(r["mdl_hit"] for r in recs)
    mpay = sum(r["o_mkt_top1"] for r in recs if r["mkt_hit"]) * UNIT
    dpay = sum(r["o_act"] * UNIT for r in recs if r["mdl_hit"] and r["o_act"])
    cost = n * UNIT
    print("=" * 80)
    print(f"k=1 归因 · 盲测 {n} 场 · 市场命中 {mh} · 模型命中 {dh}")
    print("=" * 80)
    print(f"  市场：赔付 {mpay:.0f} 元 → 回收率 {mpay/cost*100:.1f}%")
    print(f"  模型：赔付 {dpay:.0f} 元 → 回收率 {dpay/cost*100:.1f}%")
    print(f"  差额 {dpay-mpay:+.0f} 元\n")

    # ---- ① 模型命中贡献排行 ----
    hits = sorted((r for r in recs if r["mdl_hit"] and r["o_act"]),
                  key=lambda r: -r["o_act"])
    print(f"— ① 模型命中单场贡献 top{a.topn}（看是否极端值撑起）—")
    cum = 0.0
    for r in hits[:a.topn]:
        cum += r["o_act"] * UNIT
        print(f"  {r['date']} {r['code']:6} {r['league']:5} 实开{r['actual'][0]}:{r['actual'][1]} "
              f"@{r['o_act']:<6} 累计占比 {cum/dpay*100:5.1f}%"
              + ("  ←市场也中" if r["mkt_hit"] else ""))
    top1_share = hits[0]["o_act"] * UNIT / dpay * 100 if hits else 0
    top5_share = sum(r["o_act"] for r in hits[:5]) * UNIT / dpay * 100
    print(f"  → 最大单场占模型总赔付 {top1_share:.1f}% · top5 占 {top5_share:.1f}%")

    # ---- ② 剔除极值稳健性 ----
    print("\n— ② 剔除极值后回收率（稳健性）—")
    print(f"  {'剔除':14} {'市场':>8} {'模型':>8} {'差':>8}")
    excl_odds = [r["o_act"] for r in hits[:10]]
    for ne in (0, 1, 3, 5, 10):
        excl = set(excl_odds[:ne]) if ne else set()
        # 按场次剔除（同赔率可能多场，按场次序剔）
        drop_idx = {id(r) for r in hits[:ne]}
        mp = sum(r["o_mkt_top1"] for r in recs
                 if r["mkt_hit"] and id(r) not in drop_idx) * UNIT
        dp = sum(r["o_act"] for r in recs
                 if r["mdl_hit"] and r["o_act"] and id(r) not in drop_idx) * UNIT
        cn = n - len(drop_idx)
        print(f"  去 top{ne:<3}命中场 {mp/(cn*UNIT)*100:>7.1f}% {dp/(cn*UNIT)*100:>7.1f}% "
              f"{(dp-mp)/(cn*UNIT)*100:>+7.1f}pp")

    # ---- ③ 赔率分段：优势在哪段 ----
    print("\n— ③ 按实开赔率分段（k=1 命中时的赔付结构）—")
    bands = [(5, 7), (7, 9), (9, 12), (12, 20), (20, 999)]
    print(f"  {'实开赔率段':12} {'场数':>5} {'市场中':>6} {'模型中':>6} {'市场中率':>8} {'模型中率':>8}")
    for lo, hi in bands:
        sub = [r for r in recs if r["o_act"] and lo <= r["o_act"] < hi]
        if not sub:
            continue
        mhc = sum(r["mkt_hit"] for r in sub)
        dhc = sum(r["mdl_hit"] for r in sub)
        print(f"  [{lo:>2},{hi:>3})     {len(sub):>5} {mhc:>6} {dhc:>6} "
              f"{mhc/len(sub)*100:>7.1f}% {dhc/len(sub)*100:>7.1f}%")

    # ---- ④ 排序位次：模型把实开排第几 vs 市场 ----
    print("\n— ④ 实开比分的预测位次分布（模型 vs 市场 top1 视角）—")
    for name, key in (("市场", "mkt_hit"), ("模型", "mdl_hit")):
        c = Counter(r[key] for r in recs)
        print(f"  {name}top1 命中 {c[1]}/{n} = {c[1]/n*100:.1f}%")

    # ---- ⑤ 独有命中：模型中而市场不中（alpha 的直接来源）----
    only_m = [r for r in recs if r["mdl_hit"] and not r["mkt_hit"] and r["o_act"]]
    only_k = [r for r in recs if r["mkt_hit"] and not r["mdl_hit"] and r["o_mkt_top1"]]
    print(f"\n— ⑤ 独有命中互换 —")
    print(f"  模型独中 {len(only_m)} 场（赔付 {sum(r['o_act'] for r in only_m)*UNIT:.0f} 元·"
          f"均赔 {st.mean([r['o_act'] for r in only_m]) if only_m else 0:.1f}）")
    print(f"  市场独中 {len(only_k)} 场（赔付 {sum(r['o_mkt_top1'] for r in only_k)*UNIT:.0f} 元·"
          f"均赔 {st.mean([r['o_mkt_top1'] for r in only_k]) if only_k else 0:.1f}）")
    if only_m:
        print("  模型独中场次明细（前 12）:")
        for r in sorted(only_m, key=lambda r: -r["o_act"])[:12]:
            print(f"    {r['date']} {r['code']:6} {r['league']:5} "
                  f"{r['actual'][0]}:{r['actual'][1]} @{r['o_act']}")


def sfm_market_conc(close):
    inv = sorted((1.0 / o for o in close.values() if o), reverse=True)
    s = sum(inv)
    return sum(inv[:3]) / s if s else 0


if __name__ == "__main__":
    main()
