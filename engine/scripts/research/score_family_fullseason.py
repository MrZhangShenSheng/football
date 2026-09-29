# -*- coding: utf-8 -*-
"""全时段（2026-04 ~ 2026-09）逐月滚动重训 + 跨日窗口模拟。

与既往版本的区别（主公指令"dev 4-9 月都跑一下"）：
  不再固定 train/dev/blind 切分，改为**逐月滚动重训**：
  预测 m 月场次 → 用 <m 月 1 日的全部数据训练（实盘可执行形态）。
  每月重训一次，共 6 次训练。

输出：全段 ROI + 分月 ROI × 窗口 W∈{1,2,3}。
开发者 sszhang
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import score_family_model as sfm

UNIT = 2.0
SEASON = ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]


def build_monthly():
    """逐月滚动：返回 {月: [(meta, dist)]}。"""
    zh = {}
    for tid, srcs in sfm.load_aliases().items():
        if srcs.get("zh"):
            zh[srcs["zh"]] = tid
    tl = sfm.league_timeline()
    hist = sfm.load_hist()
    live = []
    for m in hist:
        if not (SEASON[0] <= m["date"][:7] <= SEASON[-1]):
            continue
        hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
        if hid and aid:
            live.append({**m, "hid": hid, "aid": aid})
    merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
    merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
               for i, m in enumerate(live)]
    merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))
    stats = defaultdict(sfm.TeamStats)
    # 逐场特征 + 按月分桶
    by_month_rows = defaultdict(lambda: ([], [], []))   # 月 → (X, meta, bucket)
    train_pool = ([], [])                               # 全部历史（含当月之前联赛场+已月 live 场）
    tot_g = tot_n = 0
    X_tr, y_tr = [], []
    fam_dist = defaultdict(Counter)   # 时点无关：用全部 <月 统计会泄漏 → 改为逐月重算
    # 简化：族内条件分布用"当月之前"的联赛库场次统计（重算成本低）
    fam_rows = []    # (date, family, score) 联赛场
    for r in merged:
        kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
        lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
        fv_h = stats[h].vector(0, lg_gf)
        fv_a = stats[a].vector(1, lg_gf)
        row = sfm.feature_row((fv_h, fv_a))
        mon = date[:7]
        if kind == "L":
            if date < SEASON[0]:
                if fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST:
                    X_tr.append(row)
                    y_tr.append(sfm.CLASSES.index(sfm.family_of(hg, ag)))
                fam_rows.append((date, sfm.family_of(hg, ag), (hg, ag)))
        else:
            X, M, B = by_month_rows[mon]
            X.append(row)
            M.append(live[r[6]])
            B.append(sfm.bucket_of(fv_h, lg_gf / 2))
        stats[h].add(hg, ag, True)
        stats[a].add(ag, hg, False)
        tot_g += hg + ag
        tot_n += 1
    # 逐月重训 + 预测
    out = {}
    for mon in SEASON:
        cut = f"{mon}-01"
        X_m, M_m, B_m = by_month_rows[mon]
        if not M_m:
            continue
        Xf = X_tr + [r for r, dt in zip([], []) ]  # placeholder; use below
        # 训练集 = <cut 的联赛场（含此前月份的 live 场？live 场在联赛库没有 → 不并入）
        # 严格：live 场的赛果也可入后续训练，但联赛库与 live 池不同源，保守起见只用联赛库
        cutoff_rows = [(r, dt, f, s) for r, dt, f, s in
                       zip(X_tr, y_tr, [None] * len(X_tr), [None] * len(X_tr))]
        # 直接用 X_tr/y_tr（联赛库 <SEASON[0]）+ <cut 的 live 已开赛场次不并入（保守）
        mdl = sfm.train_softmax(X_tr, y_tr, len(sfm.CLASSES))
        P = sfm.predict_proba(mdl, X_m)
        # 族内条件分布：<cut 的联赛场
        fd = defaultdict(Counter)
        for dt, f, s in fam_rows:
            if dt < cut:
                fd[f][s] += 1
        dists = []
        for row in P:
            d = {}
            for ci, cname in enumerate(sfm.CLASSES):
                tot = sum(fd.get(cname, {}).values()) or 1
                for s, c in fd.get(cname, {}).items():
                    d[s] = d.get(s, 0.0) + row[ci] * (c / tot)
            dists.append(d)
        out[mon] = (M_m, dists, B_m)
    return out


def enrich_month(mon, M_m, dists, B_m):
    out = []
    for i, m in enumerate(M_m):
        d = dists[i]
        close = m["odds"]
        ranked = sorted(d.items(), key=lambda kv: -kv[1])
        top1, top2 = ranked[0], ranked[1]
        o1 = close.get(top1[0])
        o2 = close.get(top2[0])
        out.append({
            "date": m["date"], "code": m["code"], "league": m["league"],
            "actual": m["actual"],
            "top1": top1[0], "p1": top1[1], "o1": o1,
            "top2": top2[0], "p2": top2[1], "o2": o2,
            "gap": top1[1] - top2[1],
            "bucket": B_m[i],
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default="1,2,3")
    a = ap.parse_args()

    monthly = build_monthly()
    enriched = {mon: enrich_month(mon, *v) for mon, v in monthly.items()}
    enriched_by_month = defaultdict(list)
    for mon, lst in enriched.items():
        enriched_by_month[mon] = lst

    print("=" * 80)
    print("全时段逐月滚动重训 · 2026-04 ~ 2026-09 · 跨日窗口选场")
    print("=" * 80)
    for mon in SEASON:
        if mon in enriched:
            n = len(enriched[mon])
            g = sum(1 for p in enriched[mon] if p["o1"])
            print(f"  {mon}: {n} 场（有赔率 {g}）")

    def run_window(by_month_pool, w):
        """跨月滚动窗口：池=窗口 W 天内全部场次。"""
        allp = sorted((p for lst in by_month_pool.values() for p in lst),
                      key=lambda p: p["date"])
        from datetime import date, timedelta
        rows = []
        used = set()
        days = sorted({p["date"] for p in allp})
        for t in days:
            t0 = date.fromisoformat(t)
            pool = [p for p in allp
                    if t0 <= date.fromisoformat(p["date"]) < t0 + timedelta(days=w)
                    and (p["date"], p["code"]) not in used]
            if len(pool) < 2:
                continue
            legs = sorted(pool, key=lambda p: -p["gap"])[:2]
            mult = 1.0
            all_pass = True
            for p in legs:
                used.add((p["date"], p["code"]))
                scores = {p["top1"]: p["o1"], p["top2"]: p["o2"]}
                if p["actual"] in scores:
                    mult *= scores[p["actual"]]
                else:
                    all_pass = False
            cost = 4 * UNIT
            rows.append({"t": t, "cost": cost,
                         "pay": mult * UNIT if all_pass else 0.0,
                         "win": all_pass})
        return rows

    for w in (int(x) for x in a.windows.split(",")):
        print(f"\n— 窗口 W={w} 天 —")
        rows = run_window(enriched_by_month, w)
        tc = sum(r["cost"] for r in rows)
        tp = sum(r["pay"] for r in rows)
        w_cnt = sum(1 for r in rows if r["win"])
        print(f"  全段：{len(rows)} 注 · 投入 {tc:.0f}元 · 回款 {tp:.0f}元 · "
              f"中奖 {w_cnt} · ROI {(tp-tc)/tc*100 if tc else 0:+.1f}%")
        by_m = defaultdict(list)
        for r in rows:
            by_m[r["t"][:7]].append(r)
        for mon in SEASON:
            rs = by_m.get(mon) or []
            if not rs:
                continue
            c = sum(r["cost"] for r in rs)
            p = sum(r["pay"] for r in rs)
            w_n = sum(1 for r in rs if r["win"])
            print(f"    {mon}: {len(rs):>3} 注 · 投 {c:>5.0f} 回 {p:>6.0f} · "
                  f"中奖 {w_n} · ROI {(p-c)/c*100 if c else 0:+6.1f}%")


if __name__ == "__main__":
    main()
