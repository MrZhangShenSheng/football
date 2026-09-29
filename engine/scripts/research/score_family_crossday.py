# -*- coding: utf-8 -*-
"""跨日混串修正：选场池从"自然日"改为"滚动窗口"（体彩在售口径）。

主公指正（2026-09-29）：体彩混串可以混多天的比赛（T040 先例=27日韩职×28日美职）。
原模拟按自然日分组=每天只从当天场次挑，人为切小了候选池。
本脚本：滚动窗口池（W 天）内取 gap 前 2 场跨日组串，窗口大小 W∈{1,2,3,7} 对照。

纪律：窗口内场次不重复使用（同场一注一腿）；先 dev 调参再盲测。
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import score_family_model as sfm
from plan_optimizer import build_all, enrich

UNIT = 2.0


def window_days(all_days, w):
    """滚动窗口：每一天 t 的池 = [t, t+w) 的场次。返回 (锚日, 池场次列表)。"""
    days = sorted(all_days)
    out = []
    ds = [date.fromisoformat(d) for d in days]
    for i, t in enumerate(ds):
        pool = []
        for j in range(i, len(ds)):
            if ds[j] < t + timedelta(days=w):
                pool += all_days[days[j]]
            else:
                break
        out.append((days[j] if False else t.isoformat(), pool))
    return out


def run_window(packs_by_day, w, n_legs=2, k=2):
    """窗口池内 gap 前 n_legs 场，各 k 选，2串复式。返回逐注结算。"""
    rows = []
    used = set()   # 场次去重（按 date+code）
    for anchor, pool in window_days(packs_by_day, w):
        avail = [p for p in pool if (p["date"], p["code"]) not in used]
        if len(avail) < n_legs:
            continue
        legs = sorted(avail, key=lambda p: -p["gap"])[:n_legs]
        mult = 1.0
        all_pass = True
        info = []
        for p in legs:
            used.add((p["date"], p["code"]))
            scores = {p["top1"]: p["o1"], p["top2"]: p["o2"]}
            hit = p["actual"] in scores
            if hit:
                mult *= scores[p["actual"]]
            else:
                all_pass = False
            info.append((p, hit))
        cost = (k ** n_legs) * UNIT
        pay = mult * UNIT if all_pass else 0.0
        rows.append({"anchor": anchor, "legs": info, "cost": cost,
                     "pay": pay, "win": all_pass})
    return rows


def seg_report(rows, tag):
    tc = sum(r["cost"] for r in rows)
    tp = sum(r["pay"] for r in rows)
    w = sum(1 for r in rows if r["win"])
    roi = (tp - tc) / tc * 100 if tc else 0
    print(f"  {tag:22} 注数 {len(rows):>3} · 投入 {tc:>6.0f}元 · 回款 {tp:>6.0f}元 · "
          f"中奖 {w:>2} · ROI {roi:+6.1f}%")
    return roi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default="1,2,3,7")
    a = ap.parse_args()

    meta_dev, dists_dev, meta_bl, dists_bl = build_all()
    dev = enrich(meta_dev, dists_dev)
    bl = enrich(meta_bl, dists_bl)
    by_dev = defaultdict(list)
    by_bl = defaultdict(list)
    for p in dev:
        by_dev[p["date"]].append(p)
    for p in bl:
        by_bl[p["date"]].append(p)

    print("=" * 80)
    print("跨日混串修正 · 滚动窗口池选场（W=1 即原版按自然日）")
    print("=" * 80)
    print("  结构：窗口内 gap 前 2 场 × 各 top1+top2 双选 × 2串复式 8 元\n")

    wins = {}
    for w in (int(x) for x in a.windows.split(",")):
        print(f"— 窗口 W={w} 天 —")
        r_dev = run_window(by_dev, w)
        r_bl = run_window(by_bl, w)
        roi_d = seg_report(r_dev, "dev(04-01~06-30)")
        roi_b = seg_report(r_bl, "盲测(07-01~09-28)")
        wins[w] = (r_dev, r_bl)
        print()

    print("=" * 80)
    print("判读")
    print("=" * 80)
    print("  · W 增大 = 候选池变大 = gap 前 2 的平均断层更大（选择质量↑）")
    print("  · 但窗口也拉长资金占用与在售时间；且场次去重后注数会减少")
    print("  · 对照：W=1 是原版按自然日；策略须在 dev 与盲测同向才可信")


if __name__ == "__main__":
    main()
