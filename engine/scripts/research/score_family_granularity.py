# -*- coding: utf-8 -*-
r"""特征粒度对比：比分级直方图(v5) vs 族级频率(v4) vs 聚合基线(v2)。

主公问（2026-09-29）："做更细致的特征工程合适吗？颗粒到具体比分的。"

三种粒度一次测清（inner 2026-01~03 + dev 2026-04~06 双段验证·盲测不跑）：
  v2  26 维  聚合基线（零封率/BTTS/动量等）
  v4  +10 维 族级频率（两队近10场各 5 族占比——每族样本 2~4）
  v5  +2K 维 比分级直方图（两队近10场出现的比分占比——每比分 0~3，稀疏）

预注册判定：候选须 inner 与 dev 双段同时 ≥ v2 才算改善。
开发者 sszhang
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import score_family_model as sfm
from score_family_v3 import Stats3, load_all

UNIT = 2.0
FAMS = list(sfm.FAMILIES)
MODES = ("v2", "v4", "v5")


def fam_hist(s: Stats3, n=10):
    fc, sc = Counter(), Counter()
    for opp, s_, c_, d, h in s.recent[-n:]:
        fc[sfm.family_of(s_, c_)] += 1
        sc[(s_, c_)] += 1
    tot = max(len(s.recent[-n:]), 1)
    return ([fc.get(f, 0) / tot for f in FAMS],
            {k: v / tot for k, v in sc.items()})


ALL_SCORES = [(h, a) for h in range(6) for a in range(6)]   # 固定36键·维度齐


def feat_row(s_h, s_a, lg_gf, mode):
    bh = s_h.v2_vector(0, lg_gf)
    ba = s_a.v2_vector(1, lg_gf)
    if mode == "v2":
        return bh + ba
    fh, hh = fam_hist(s_h)
    fa, ha = fam_hist(s_a)
    if mode == "v4":
        return bh + ba + fh + fa
    if mode == "v5":
        return bh + ba + [hh.get(k, 0.0) for k in ALL_SCORES] \
               + [ha.get(k, 0.0) for k in ALL_SCORES]
    if mode == "v45":
        return bh + ba + fh + fa + [hh.get(k, 0.0) for k in ALL_SCORES] \
               + [ha.get(k, 0.0) for k in ALL_SCORES]
    raise ValueError(mode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", default="v2,v4,v5,v45")
    args = ap.parse_args()
    modes = tuple(args.modes.split(","))

    merged, live = load_all()
    stats = defaultdict(Stats3)
    # 收集：train(<2026-01) 与 eval(inner 01~03 / dev 04~06)，各 mode 独立特征行
    tr = {m: ([], []) for m in modes}
    ev = {m: {"inner": ([], [], []), "dev": ([], [], [])} for m in modes}
    tot_g = tot_n = 0
    for r in merged:
        kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
        lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
        s_h, s_a = stats[h], stats[a]
        is_live = kind == "B"
        is_eval = is_live and "2026-01-01" <= date < "2026-07-01"
        ready = s_h.n >= sfm.MIN_HIST and s_a.n >= sfm.MIN_HIST
        if ready and (kind == "L" and date < "2026-01-01" or is_eval):
            seg_key = None
            if is_eval:
                seg_key = "inner" if date < "2026-04-01" else "dev"
            for m in modes:
                row = feat_row(s_h, s_a, lg_gf, m)
                if seg_key:
                    ev[m][seg_key][0].append(row)
                    ev[m][seg_key][2].append(live[r[6]])
                else:
                    tr[m][0].append(row)
                    tr[m][1].append(sfm.CLASSES.index(sfm.family_of(hg, ag)))
        s_h.add(a, hg, ag, date, True)
        s_a.add(h, ag, hg, date, False)
        tot_g += hg + ag
        tot_n += 1

    fam = defaultdict(Counter)
    for r in merged:
        if r[0] == "L" and r[1] < "2026-01-01":
            fam[sfm.family_of(r[4], r[5])][(r[4], r[5])] += 1

    print("=" * 78)
    print("特征粒度对比 · inner(01~03) + dev(04~06) · 判定=双段同时≥v2")
    print("=" * 78)
    summary = {}
    for mode in modes:
        Xtr, ytr = tr[mode]
        mdl = sfm.train_softmax(Xtr, ytr, len(sfm.CLASSES))
        print(f"\n【{mode}】{len(Xtr[0])} 维 · 训练 {len(Xtr)} 场")
        all_ok = True
        for key in ("inner", "dev"):
            Xs, _, ms = ev[mode][key]
            if not ms:
                continue
            P = sfm.predict_proba(mdl, Xs)
            h1 = p1 = 0
            n = len(ms)
            for i, mm in enumerate(ms):
                act = mm["actual"]
                close = mm["odds"]
                dd = {}
                for ci, cname in enumerate(sfm.CLASSES):
                    tot = sum(fam.get(cname, {}).values()) or 1
                    for s, c in fam.get(cname, {}).items():
                        dd[s] = dd.get(s, 0.0) + P[i][ci] * (c / tot)
                t1 = max(dd.items(), key=lambda kv: kv[1])[0]
                if act == t1:
                    h1 += 1
                    if act in close:
                        p1 += close[act] * UNIT
            cost = n * UNIT
            print(f"  {key:6} top1 {h1/n*100:.1f}% · 回收率 {p1/cost*100:.1f}%")
            summary[(mode, key)] = (h1 / n, p1 / cost)
    print("\n" + "=" * 78)
    print("结论")
    print("=" * 78)
    for mode in modes[1:]:
        ok = all(summary[(mode, k)][0] >= summary[("v2", k)][0]
                 and summary[(mode, k)][1] >= summary[("v2", k)][1]
                 for k in ("inner", "dev"))
        print(f"  {mode} vs v2：{'双段全过 ✓' if ok else '未全过 ✗'}")


if __name__ == "__main__":
    main()
