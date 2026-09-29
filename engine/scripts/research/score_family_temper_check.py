# -*- coding: utf-8 -*-
"""对照实验：模型的 +15.5pp 是信息还是"概率平坦化"假象？

平坦化假说：把市场概率做 T>1 温度平滑后取 top1，会系统性地从最热门滑向次热门，
命中时赔率更高——不需要任何信息即可复现"高回收率"。
若温度化市场 ≈ 模型回收率 → 模型优势 = 平坦化（无信息）
若模型 > 温度化市场 → 模型的特征确实在挑场次（真信息）
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import score_family_model as sfm

UNIT = 2.0


def temper(odds: dict, T: float):
    """市场隐含概率的温度化：p^(1/T) 归一（T>1 = 压平）。返回排序。"""
    inv = {s: (1.0 / o) ** (1.0 / T) for s, o in odds.items() if o}
    return [s for s, _ in sorted(inv.items(), key=lambda kv: -kv[1])]


def main():
    zh = {}
    for tid, srcs in sfm.load_aliases().items():
        if srcs.get("zh"):
            zh[srcs["zh"]] = tid
    tl = sfm.league_timeline()
    hist = sfm.load_hist()
    blind = []
    for m in hist:
        if m["date"] < sfm.CUT:
            continue
        hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
        if hid and aid:
            blind.append({**m, "hid": hid, "aid": aid})
    merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
    merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
               for i, m in enumerate(blind)]
    merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))
    stats = defaultdict(sfm.TeamStats)
    X_tr, y_tr, X_bl, meta = [], [], [], []
    tot_g = tot_n = 0
    for r in merged:
        kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
        lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
        fv_h = stats[h].vector(0, lg_gf)
        fv_a = stats[a].vector(1, lg_gf)
        if kind == "L" and date < sfm.CUT and fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST:
            X_tr.append(sfm.feature_row((fv_h, fv_a)))
            y_tr.append(sfm.CLASSES.index(sfm.family_of(hg, ag)))
        elif kind == "B":
            meta.append(blind[r[6]])
            X_bl.append(sfm.feature_row((fv_h, fv_a)))
        stats[h].add(hg, ag, True)
        stats[a].add(ag, hg, False)
        tot_g += hg + ag
        tot_n += 1
    model = sfm.train_softmax(X_tr, y_tr, len(sfm.CLASSES))
    import numpy as np
    P = sfm.predict_proba(model, X_bl)
    # 模型分布
    fam_dist = defaultdict(Counter)
    for r in merged:
        if r[0] == "L" and r[1] < sfm.CUT:
            fam_dist[sfm.family_of(r[4], r[5])][(r[4], r[5])] += 1
    dists = []
    for row in P:
        d = {}
        for ci, cname in enumerate(sfm.CLASSES):
            tot = sum(fam_dist.get(cname, {}).values()) or 1
            for s, c in fam_dist.get(cname, {}).items():
                d[s] = d.get(s, 0.0) + row[ci] * (c / tot)
        dists.append(d)

    TS = (1.0, 1.15, 1.3, 1.5, 2.0)
    cost = len(meta) * UNIT
    print("=" * 76)
    print(f"平坦化对照 · 盲测 {len(meta)} 场 · k=1 单关")
    print("=" * 76)
    print(f"  {'策略':22} {'命中':>5} {'命中率':>7} {'赔付':>8} {'回收率':>8}")
    pays = {t: 0.0 for t in TS}
    hits = {t: 0 for t in TS}
    mpay = mhit = 0
    for i, m in enumerate(meta):
        act = m["actual"]
        close = m["odds"]
        o_act = close.get(act)
        if o_act is None:
            continue
        # 市场
        mkt = [s for s, _ in sorted(close.items(), key=lambda kv: kv[1])]
        if act == mkt[0]:
            mpay += o_act * UNIT
            mhit += 1
        # 温度化市场
        for t in TS:
            if t == 1.0:
                continue
            r = temper(close, t)
            if act == r[0]:
                pays[t] += o_act * UNIT
                hits[t] += 1
        # 模型
        mdl = [s for s, _ in sorted(dists[i].items(), key=lambda kv: -kv[1])]
        if act == mdl[0]:
            pays["model"] = pays.get("model", 0.0) + o_act * UNIT
            hits["model"] = hits.get("model", 0) + 1
    print(f"  {'市场 top1':22} {mhit:>5} {mhit/len(meta)*100:>6.1f}% {mpay:>7.0f}元 {mpay/cost*100:>7.1f}%")
    for t in TS:
        if t == 1.0:
            continue
        print(f"  {f'市场温度化 T={t}':22} {hits[t]:>5} {hits[t]/len(meta)*100:>6.1f}% "
              f"{pays[t]:>7.0f}元 {pays[t]/cost*100:>7.1f}%")
    print(f"  {'族特征模型 top1':22} {hits['model']:>5} {hits['model']/len(meta)*100:>6.1f}% "
          f"{pays['model']:>7.0f}元 {pays['model']/cost*100:>7.1f}%")
    print("\n  判读：模型回收率若 ≤ T=1.5 档 → 优势主要是平坦化效应（无信息）；")
    print("  若显著高于所有温度档 → 特征在真实挑场次。")


if __name__ == "__main__":
    main()
