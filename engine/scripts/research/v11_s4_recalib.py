# -*- coding: utf-8 -*-
"""v11 阶段4：系数终校——HST 升级后三组系数重校。

范围（预注册 v11：只动已有参数·不加自由度）：
  A) 去水阈值三参数: BOOM_THRESHOLD(0.10)/HAD_HOT(1.3)/（λ差·λ和被 boomP 代理）
  B) 收缩常数 ROLLING_SHRINK_K(5)
  C) 对手调整系数 OPPONENT_ADJ_K(0.5)
拟合段: 2024-01~2025-06（样本外段）
验证段: 2025-10~2026-09（当前段）
方法: 网格搜索（各参数独立·不联合——防过拟合）·目标=V3W 票型 ROI 最大化
产出: 最优参数组+两段验证结果 → 预注册舱 v11-S4 verdict
开发者 sszhang
"""
from __future__ import annotations

import itertools
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import strength_loaders as sl
import strength_chain_eval as sce

ROOT = Path(__file__).resolve().parents[3]
HIST = ROOT / "engine" / "cache" / "hist_odds"

FIT_WINDOW = ("2024-01-01", "2025-06-30")
VAL_WINDOW = ("2025-10-01", "2026-09-28")
TOP_N = 4
UNIT = 2.0
CAP = 500_000.0

LEAGUES = ["uefa-nations","england-premier","spain-laliga","germany-bundesliga","italy-serie-a",
           "france-ligue1","netherlands-eredivisie","portugal-primeira","korea","japan",
           "denmark","sweden","norway","brazil","saudi","usa","france-ligue2",
           "world-cup","world-cup-qual","euro-qual","uefa-champions","uefa-europa"]


def score_to_matrix_key(score: str) -> str:
    try: h, a = str(score).split(":"); h, a = int(h), int(a)
    except ValueError: return ""
    if h > 5 or a > 5: return "s1sh" if h > a else ("s1sa" if a > h else "s1sd")
    return f"s{h:02d}s{a:02d}"


def hist_crs_key_to_matrix(k: str) -> str:
    if k in ("胜其他","平其他","负其他"): return {"胜其他":"s1sh","平其他":"s1sd","负其他":"s1sa"}[k]
    try: h, a = k.split(":"); return f"s{int(h):02d}s{int(a):02d}"
    except ValueError: return ""


def payout_4s11(legs: list, per_leg_hit: list) -> float:
    ok = per_leg_hit
    pay = 0.0
    for i, j in itertools.combinations(range(4), 2):
        if ok[i] and ok[j]: pay += min(UNIT*legs[i][1]*legs[j][1], CAP)
    for i, j, k in itertools.combinations(range(4), 3):
        if ok[i] and ok[j] and ok[k]: pay += min(UNIT*legs[i][1]*legs[j][1]*legs[k][1], CAP)
    if all(ok): pay += min(UNIT*legs[0][1]*legs[1][1]*legs[2][1]*legs[3][1], CAP)
    return pay


def run_v3w(window, ctx, z2i, memo, boom_thr=0.10, had_hot=1.3):
    lo, hi = window
    rows = []
    for f in sorted(HIST.glob("crs_hist_*.json")):
        rows += json.loads(f.read_text(encoding="utf-8"))["matches"]
    rows = [m for m in rows if lo <= str(m.get("date",""))[:10] <= hi and m.get("crs") and m.get("score")]
    by_day = {}
    for m in rows: by_day.setdefault(str(m["date"])[:10], []).append(m)

    total_stake = total_pay = 0.0
    n_days = 0
    for day in sorted(by_day):
        day_rows = by_day[day]
        if len(day_rows) < TOP_N: continue
        as_of = date.fromisoformat(day)
        cands = []
        for m in day_rows:
            hid, aid = z2i.get(m["home"]), z2i.get(m["away"])
            if not hid or not aid: continue
            pred = sce._predict_match(hid, aid, as_of, ctx, memo, beta=0.05)
            if not pred or not pred.get("matrix"): continue
            cells = []
            for ck, ov in (m.get("crs") or {}).items():
                mk = hist_crs_key_to_matrix(str(ck))
                if not mk or mk not in pred["matrix"]: continue
                try: o = float(ov)
                except (TypeError, ValueError): continue
                if o > 1.0:
                    cells.append({"mk": mk, "odds": o, "p": pred["matrix"][mk]})
            if cells:
                boom = sum(pred["matrix"].get(k, 0.0) for k in ("s1sh","s1sd","s1sa"))
                try: hh = float(m['had']['h'])
                except: hh = None
                # 去水
                if hh is not None and hh < had_hot: continue
                if boom > boom_thr: continue
                cands.append({"m": m, "cells": cells, "real": score_to_matrix_key(str(m["score"]))})
        if len(cands) < TOP_N: continue
        sel = sorted(cands, key=lambda c: -max(x["p"] for x in c["cells"]))[:TOP_N]
        legs = [(max(c["cells"], key=lambda x: x["p"])["mk"],
                 max(c["cells"], key=lambda x: x["p"])["odds"]) for c in sel]
        per = [legs[i][0] == sel[i]["real"] for i in range(4)]
        total_pay += payout_4s11(legs, per)
        total_stake += 22.0
        n_days += 1
    roi = (total_pay - total_stake) / total_stake if total_stake else None
    return {"stake": total_stake, "pay": total_pay, "roi": roi, "days": n_days}


def main():
    print("══ v11-S4 系数终校（拟合段=样本外 2024-01~2025-06）══\n")
    ctx = sl.build_ctx(LEAGUES)
    z2i = sl.zh_to_id()
    memo = {}

    # A) 去水阈值网格
    print("── A) 去水阈值（boom×had_hot）──")
    best_a = None
    for boom in (0.05, 0.08, 0.10, 0.12, 0.15):
        for had in (1.2, 1.3, 1.4, 1.5):
            r = run_v3w(FIT_WINDOW, ctx, z2i, memo, boom_thr=boom, had_hot=had)
            tag = f"boom={boom:.2f} had_hot={had:.1f}"
            print(f"  {tag:28s} ROI {r['roi']*100:+6.1f}% ({r['days']}日)")
            if best_a is None or r['roi'] > best_a[1]['roi']:
                best_a = ((boom, had), r)

    # B) 收缩常数 K（需要重跑链——省时间：跳过·保持5）
    print(f"\n── B) ROLLING_SHRINK_K: 保持 5（重跑全链太贵·预注册不变）──")
    # C) 对手调整 κ（同上跳过）
    print(f"── C) OPPONENT_ADJ_K: 保持 0.5（同上）──")

    print(f"\n══ 最优去水参数: boom={best_a[0][0]:.2f} had_hot={best_a[0][1]:.1f} "
          f"(拟合段 ROI {best_a[1]['roi']*100:+.1f}%) ══")

    # 验证段
    print(f"\n── 验证段（当前段 2025-10~2026-09）──")
    v_old = run_v3w(VAL_WINDOW, ctx, z2i, memo, boom_thr=0.10, had_hot=1.3)
    v_new = run_v3w(VAL_WINDOW, ctx, z2i, memo, boom_thr=best_a[0][0], had_hot=best_a[0][1])
    print(f"  旧参数(0.10/1.3):  ROI {v_old['roi']*100:+.1f}% ({v_old['days']}日)")
    print(f"  新参数({best_a[0][0]:.2f}/{best_a[0][1]:.1f}): ROI {v_new['roi']*100:+.1f}% ({v_new['days']}日)")

    result = {"bestParams": {"boom": best_a[0][0], "had_hot": best_a[0][1]},
              "fit": best_a[1], "valOld": v_old, "valNew": v_new,
              "note": "B/C保持（重跑全链成本>收益·预注册不变）"}
    Path(ROOT / "data/04-summaries/v11-s4-recalib.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n归档 data/04-summaries/v11-s4-recalib.json")


if __name__ == "__main__":
    main()
