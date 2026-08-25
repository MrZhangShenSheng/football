#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""DC 比分 top1/top3 命中率回测：walk-forward 逐场算 7x7 比分矩阵，取概率最高比分看实际是否命中。

对比基线：
- 随机均匀：top1=1/49, top3=3/49（49=7x7 比分格子）
- 经验频率：全数据最频繁 N 个比分的命中率（"无脑赌高频比分"基线）
- 校准：DC top 比分平均预测概率 vs 实际命中率（比分概率是否可靠）

诚实标注：最频繁比分(如1-1)占比约10-12%，top3约28-30%。
DC top3 若持平/略超经验频率基线 → DC 比分排序无增量（与三向结论一致：实力信号已被市场定价）。
矩阵截断 7x7：实际比分≥7球（如7-0）不在任何 top 比分内，top1/top3 均判 miss——反映 DC 对极端比分无能为力。
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # 归档后仍可直跑：scripts/ 入 path

import json
from collections import Counter
from datetime import date, datetime

import numpy as np

from common import log, ROOT
import backtest as bt
from dc_fit import load_matches

SEASON = "2526"
N_SCORES = 49  # 7x7 Dixon-Coles 比分矩阵格子数
LEAGUES = [
    "england-premier", "spain-laliga", "germany-bundesliga", "italy-serie-a",
    "france-ligue1", "france-ligue2", "netherlands-eredivisie", "portugal-primeira",
]


def load_market(league, season):
    raw = json.loads((bt.CACHE_DIR / f"odds_{league}_{season}.json").read_text(encoding="utf-8"))
    mbm = {}
    for m in raw["matches"]:
        try:
            d = datetime.strptime(m["date"], "%d/%m/%Y").date()
        except (ValueError, TypeError):
            continue
        if m.get("pin_h") and m.get("pin_d") and m.get("pin_a") and m.get("fthg") is not None:
            mbm[(d.isoformat(), m["home"], m["away"])] = (
                float(m["pin_h"]), float(m["pin_d"]), float(m["pin_a"]))
    return mbm


def main():
    a, b = bt.FUSION_DEFAULT["a"], bt.FUSION_DEFAULT["b"]
    fus_path = bt.CACHE_DIR / "fusion.json"
    if fus_path.exists():
        f = json.loads(fus_path.read_text(encoding="utf-8"))
        a, b = f["a"], f["b"]

    all_rows = []
    by_league = []
    for lg in LEAGUES:
        matches = load_matches(lg, [SEASON])
        if not matches:
            continue
        records = bt.walk_forward(matches, load_market(lg, SEASON), a, b, use_xg=False)
        rows = []
        for r in records:
            top = r.get("top_scores", [])
            if not top:
                continue
            actual = f"{r['hg']}-{r['ag']}"
            scores = [t["score"] for t in top]
            rows.append({
                "actual": actual,
                "top1_hit": int(actual == scores[0]),
                "top3_hit": int(actual in scores[:3]),
                "top1_prob": top[0]["prob"],
                "top3_cov": sum(t["prob"] for t in top[:3]),
            })
        n = len(rows)
        if n == 0:
            continue
        t1 = float(np.mean([r["top1_hit"] for r in rows]))
        t3 = float(np.mean([r["top3_hit"] for r in rows]))
        by_league.append({
            "league": lg, "n": n,
            "dc_top1": round(t1, 4), "dc_top3": round(t3, 4),
            "avg_top1_prob": round(float(np.mean([r["top1_prob"] for r in rows])), 4),
            "avg_top3_cov": round(float(np.mean([r["top3_cov"] for r in rows])), 4),
        })
        log("score_bt", f"{lg}: n={n} DC top1={t1:.3f} top3={t3:.3f}")
        all_rows.extend(rows)

    total = len(all_rows)
    if total == 0:
        log("score_bt", "无样本，退出")
        return
    cnt = Counter(r["actual"] for r in all_rows)
    freq_sorted = cnt.most_common()
    emp_top1 = freq_sorted[0][1] / total
    emp_top3 = sum(c for _, c in freq_sorted[:3]) / total

    dc_top1 = float(np.mean([r["top1_hit"] for r in all_rows]))
    dc_top3 = float(np.mean([r["top3_hit"] for r in all_rows]))
    avg_top1_prob = float(np.mean([r["top1_prob"] for r in all_rows]))
    avg_top3_cov = float(np.mean([r["top3_cov"] for r in all_rows]))

    print("\n== DC 比分 top1/top3 命中率 ==")
    print(f"{'联赛':<24}{'n':>5}{'DCtop1':>8}{'DCtop3':>8}{'均p1':>8}{'均cov3':>8}")
    for r in by_league:
        print(f"{r['league']:<24}{r['n']:>5}{r['dc_top1']:>8.3f}{r['dc_top3']:>8.3f}"
              f"{r['avg_top1_prob']:>8.3f}{r['avg_top3_cov']:>8.3f}")
    print(f"{'总体':<24}{total:>5}{dc_top1:>8.3f}{dc_top3:>8.3f}{avg_top1_prob:>8.3f}{avg_top3_cov:>8.3f}")
    print(f"\n基线对比：")
    print(f"  随机均匀:  top1={1/N_SCORES:.4f}  top3={3/N_SCORES:.4f}")
    print(f"  经验频率:  top1={emp_top1:.4f}  top3={emp_top3:.4f}"
          f"  (最频繁: {freq_sorted[0][0]}={freq_sorted[0][1]}, 前3: {[s for s, _ in freq_sorted[:3]]})")
    print(f"  DC实际:    top1={dc_top1:.4f}  top3={dc_top3:.4f}")
    print(f"  DC vs 经验:  top1={dc_top1-emp_top1:+.4f}  top3={dc_top3-emp_top3:+.4f}")
    print(f"\n校准（DC top 比分平均预测概率 vs 实际命中率）：")
    print(f"  top1: 预测={avg_top1_prob:.4f} 命中={dc_top1:.4f}  "
          f"差={avg_top1_prob-dc_top1:+.4f} ({'高估' if avg_top1_prob > dc_top1 else '低估'})")
    print(f"  top3: 预测={avg_top3_cov:.4f} 命中={dc_top3:.4f}  "
          f"差={avg_top3_cov-dc_top3:+.4f} ({'高估' if avg_top3_cov > dc_top3 else '低估'})")

    result = {
        "season": SEASON, "fusion": {"a": a, "b": b}, "total": total,
        "ranAt": date.today().isoformat(),
        "by_league": by_league,
        "overall": {
            "dc_top1": round(dc_top1, 4), "dc_top3": round(dc_top3, 4),
            "rand_top1": round(1 / N_SCORES, 4), "rand_top3": round(3 / N_SCORES, 4),
            "emp_top1": round(emp_top1, 4), "emp_top3": round(emp_top3, 4),
            "delta_vs_emp_top1": round(dc_top1 - emp_top1, 4),
            "delta_vs_emp_top3": round(dc_top3 - emp_top3, 4),
            "top_freq_scores": [{"score": s, "count": c} for s, c in freq_sorted[:5]],
            "calibration": {
                "top1_pred": round(avg_top1_prob, 4), "top1_hit": round(dc_top1, 4),
                "top3_pred": round(avg_top3_cov, 4), "top3_hit": round(dc_top3, 4),
            },
        },
    }
    dest = ROOT / "data" / "04-summaries" / "dc_score_backtest.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log("score_bt", f"✅ → {dest.name}")


if __name__ == "__main__":
    main()
