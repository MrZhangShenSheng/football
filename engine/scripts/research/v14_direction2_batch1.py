# -*- coding: utf-8 -*-
"""v14 批次一：方向2扩展——D三变体甜点票型 + 口径归因矩阵（一次 preload 三线全出）。

设计档：docs/2026-10-07-direction2-expansion-design.html
预注册：fade-strategy-prereg changeLog v14（跑前写死）。
Part1 D变体: D1=3场top2仅3串1(8注16元)/D2=3场top2三串4(20注40元)/D3=2场top2双串1(4注8元) vs A基线。
Part2 归因矩阵: 2×2×2 过滤(0.05/1.5 vs 0.10/1.3)×选场排序(topP vs EV)×选格(topP vs EV)·A档单选骨架。
判据/判读口径见舱 v14，跑前写死。泄漏闸同 S5 继承。
产出：data/04-summaries/v14-batch1.json
开发者 sszhang
"""
from __future__ import annotations

import itertools
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import strength_loaders as sl
from v11_s4_recalib import FIT_WINDOW, VAL_WINDOW, UNIT, CAP, LEAGUES
from v11_s5_recalib import preload_days, PROD_PARAMS

ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = ROOT / "data" / "04-summaries" / "v14-batch1.json"
RAND_BASELINE_N = 200
RAND_SEED = 20261007

ALL_COMBOS_4 = (list(itertools.combinations(range(4), 2))
                + list(itertools.combinations(range(4), 3))
                + [(0, 1, 2, 3)])
TIERS = {
    "A":  {"n": 4, "k": 1, "combos": ALL_COMBOS_4},
    "D1": {"n": 3, "k": 2, "combos": [(0, 1, 2)]},
    "D2": {"n": 3, "k": 2, "combos": [(0, 1), (0, 2), (1, 2), (0, 1, 2)]},
    "D3": {"n": 2, "k": 2, "combos": [(0, 1)]},
}
FILTERS = {"s5(0.05/1.5)": (0.05, 1.5), "fade(0.10/1.3)": (0.10, 1.3)}
SORT_KEYS = ("topP", "EV")
PICK_KEYS = ("topP", "EV")


def cell_key(x, key):
    return x["p"] if key == "topP" else x["p"] * x["odds"]


def filter_matches(cands, filt):
    boom_thr, had_hot = filt
    return [c for c in cands
            if (c["hh"] is None or c["hh"] >= had_hot) and c["boom"] <= boom_thr]


def tickets_general(sel, combos):
    units = 0
    pay = 0.0
    for grp in combos:
        u, ok, prod_odds = 1, True, 1.0
        for i in grp:
            cells, hit = sel[i]
            u *= len(cells)
            if hit is None:
                ok = False
            else:
                prod_odds *= hit["odds"]
        units += u
        if ok:
            pay += min(UNIT * prod_odds, CAP)
    return units, pay


def eval_tier(pre, lo, hi, tier, rng=None):
    cfg = TIERS[tier]
    units_t = pay_t = 0.0
    days = hit_days = 0
    for day, cands in pre.items():
        if not (lo <= day <= hi):
            continue
        ok = filter_matches(cands, PROD_PARAMS)
        if len(ok) < cfg["n"]:
            continue
        ok = sorted(ok, key=lambda c: -max(x["p"] for x in c["cells"]))[:cfg["n"]]
        sel = []
        for c in ok:
            if rng is None:
                cells = sorted(c["cells"], key=lambda x: -x["p"])[:cfg["k"]]
            else:
                pool = sorted(c["cells"], key=lambda x: -x["p"])
                cells = rng.sample(pool, cfg["k"]) if len(pool) > cfg["k"] else pool[:cfg["k"]]
            hit = next((x for x in cells if x["mk"] == c["real"]), None)
            sel.append((cells, hit))
        u, p = tickets_general(sel, cfg["combos"])
        units_t += u
        pay_t += p
        days += 1
        if p > 0:
            hit_days += 1
    stake = units_t * UNIT
    roi = (pay_t - stake) / stake if stake else None
    return {"units": int(units_t), "pay": round(pay_t, 2), "roi": roi,
            "days": days, "hitDays": hit_days,
            "costPerDay": round(stake / days, 1) if days else None}


def eval_matrix_cell(pre, lo, hi, filt, sort_key, pick_key):
    units_t = pay_t = 0.0
    days = hit_days = 0
    for day, cands in pre.items():
        if not (lo <= day <= hi):
            continue
        ok = filter_matches(cands, filt)
        if len(ok) < 4:
            continue
        ok4 = sorted(ok, key=lambda c: -max(cell_key(x, sort_key) for x in c["cells"]))[:4]
        hits, legs = [], []
        for c in ok4:
            cell = max(c["cells"], key=lambda x: cell_key(x, pick_key))
            legs.append((cell["mk"], cell["odds"]))
            hits.append(cell["mk"] == c["real"])
        # 单选骨架：4串11 公式（每组合至多1注·命中格赔率积）
        p = 0.0
        for i, j in itertools.combinations(range(4), 2):
            if hits[i] and hits[j]:
                p += min(UNIT * legs[i][1] * legs[j][1], CAP)
        for i, j, k in itertools.combinations(range(4), 3):
            if hits[i] and hits[j] and hits[k]:
                p += min(UNIT * legs[i][1] * legs[j][1] * legs[k][1], CAP)
        if all(hits):
            p += min(UNIT * legs[0][1] * legs[1][1] * legs[2][1] * legs[3][1], CAP)
        u = len(ALL_COMBOS_4)
        units_t += u
        pay_t += p
        days += 1
        if p > 0:
            hit_days += 1
    stake = units_t * UNIT
    roi = (pay_t - stake) / stake if stake else None
    return {"roi": roi, "days": days, "hitDays": hit_days}


def main():
    print("══ v14 批次一：D三变体 + 口径归因矩阵 ══\n", flush=True)
    print("预注册: fade-strategy-prereg changeLog v14\n", flush=True)
    ctx = sl.build_ctx(LEAGUES)
    z2i = sl.zh_to_id()
    memo = {}
    print("── preload ──", flush=True)
    pre = preload_days(ctx, z2i, memo)
    print(f"preload 完: {len(pre)}日\n", flush=True)

    print("── Part1 · D三变体 vs A基线 ──", flush=True)
    fit = {t: eval_tier(pre, *FIT_WINDOW, tier=t) for t in TIERS}
    for t, r in fit.items():
        print(f"  {t:<3} 拟合段 ROI {r['roi']*100:+7.1f}% ({r['days']}日·{r['units']}注·日均{r['costPerDay']}元·回款{r['hitDays']}日)", flush=True)
    cand_tier = max(fit, key=lambda t: fit[t]["roi"] if fit[t]["roi"] is not None else -9)
    val = {t: eval_tier(pre, *VAL_WINDOW, tier=t) for t in TIERS}
    print("", flush=True)
    for t, r in val.items():
        print(f"  {t:<3} 验证段 ROI {r['roi']*100:+7.1f}% ({r['days']}日·{r['units']}注·日均{r['costPerDay']}元·回款{r['hitDays']}日)", flush=True)

    baseline_out = crits = None
    verdict = "KEEP A基线"
    if cand_tier != "A":
        imp = (val[cand_tier]["roi"] or 0.0) - (val["A"]["roi"] or 0.0)
        rate_c = val[cand_tier]["hitDays"] / max(val[cand_tier]["days"], 1)
        rate_a = val["A"]["hitDays"] / max(val["A"]["days"], 1)
        c2 = imp > 0.05 and (val[cand_tier]["roi"] or -1) > (val["A"]["roi"] or 0.0)
        c3 = rate_c >= rate_a
        c4 = True
        baseline_out = None
        if fit[cand_tier]["roi"] is not None and fit[cand_tier]["roi"] > 0:
            rois = []
            for i in range(RAND_BASELINE_N):
                rng = random.Random(RAND_SEED + i)
                rois.append(eval_tier(pre, *FIT_WINDOW, tier=cand_tier, rng=rng)["roi"])
            rois_s = sorted(x for x in rois if x is not None)
            p95, mean = rois_s[int(len(rois_s) * 0.95)], sum(rois_s) / len(rois_s)
            c4 = fit[cand_tier]["roi"] > p95
            baseline_out = {"mean": round(mean, 4), "p95": round(p95, 4),
                            "actual": round(fit[cand_tier]["roi"], 4), "beatsRandom": c4}
        crits = {"improve5pp": c2, "hitRateNoWorse": c3, "beatsRandom": c4, "imp": round(imp, 4)}
        verdict = (f"REPLACE→{cand_tier}" if (c2 and c3 and c4) else f"KEEP A基线（{cand_tier}判据未全过）")
        print(f"\n  候选={cand_tier}·改善{imp*100:+.1f}pp·②{c2} ③{c3} ④{c4}\n  判定: {verdict}", flush=True)
    else:
        print("\n  拟合段最优即基线 A——判定: KEEP A基线", flush=True)

    print("\n── Part2 · 2×2×2 归因矩阵（A档单选骨架）──", flush=True)
    grid = {}
    for fname, filt in FILTERS.items():
        for sk in SORT_KEYS:
            for pk in PICK_KEYS:
                g = (fname, sk, pk)
                grid[g] = {"fit": eval_matrix_cell(pre, *FIT_WINDOW, filt, sk, pk),
                           "val": eval_matrix_cell(pre, *VAL_WINDOW, filt, sk, pk)}
                f_, v_ = grid[g]["fit"], grid[g]["val"]
                print(f"  {fname:<14} sort={sk:<4} pick={pk:<4} 拟合{f_['roi']*100:+7.1f}% 验证{v_['roi']*100:+7.1f}%", flush=True)

    # 主效应：逐维在其余两维各档位上取差值平均（段=验证段）
    def effect(dim_idx, base_combos):
        vals = {"A": [], "B": []}
        names = (list(FILTERS.keys()), list(SORT_KEYS), list(PICK_KEYS))
        for c in base_combos:
            other = [names[d][c[d]] for d in range(3)]
            for side, li in (("A", 0), ("B", 1)):
                combo = list(other)
                combo[dim_idx] = names[dim_idx][li]
                r = grid[tuple(combo)]["val"]["roi"]
                vals[side].append(r if r is not None else 0.0)
        return sum(vals["B"]) / len(vals["B"]) - sum(vals["A"]) / len(vals["A"])

    others = [(fi, si, pi) for fi in (0, 1) for si in (0, 1) for pi in (0, 1)]
    eff_pick = effect(2, others)
    eff_sort = effect(1, others)
    eff_filt = effect(0, others)
    print(f"\n  主效应(验证段): 选格 {eff_pick*100:+.2f}pp · 排序 {eff_sort*100:+.2f}pp · 过滤 {eff_filt*100:+.2f}pp", flush=True)
    judge = ("格口径真效应(≥3pp)" if abs(eff_pick) >= 0.03 else
             "弱效应挂forward(1~3pp)" if abs(eff_pick) >= 0.01 else
             "9pp主因在过滤/排序(<1pp)")
    print(f"  判读: {judge}", flush=True)

    result = {"ranAt": "2026-10-07", "preReg": "fade-strategy-prereg changeLog v14",
              "tiers": {t: {"fit": fit[t], "val": val[t]} for t in TIERS},
              "candidate": cand_tier, "criteria": crits, "randomBaseline": baseline_out,
              "verdict": verdict,
              "matrix": {f"{f}|{s}|{p}": v for (f, s, p), v in grid.items()},
              "mainEffect": {"pick": round(eff_pick, 4), "sort": round(eff_sort, 4),
                             "filter": round(eff_filt, 4)},
              "judge": judge}
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n归档 {OUT_PATH.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
