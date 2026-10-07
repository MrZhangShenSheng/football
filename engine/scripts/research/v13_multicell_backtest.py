# -*- coding: utf-8 -*-
"""v13：比分多格复式回测（V3W-v2 链·方向 2 立项）。

档案：docs/2026-10-07-optimization-directions.html
预注册：fade-strategy-prereg changeLog v13（跑前写死）。
三档：A=k[1,1,1,1]基线 11注22元 / B=k[2,2,2,2]复式 72注144元 /
      C=k[2,2,1,1]复式 29注58元（加格场=top1与top2概率差最小的2场）。
派彩：每过关组合至多 1 注命中（格集含真比分唯一）·赔率=各腿命中格赔率积。
判据：①拟合段每注 ROI 选档 ②验证段须优于 A 且改善>5pp ③回款日率不劣化
     ④候选档拟合段 ROI>0 须过随机格基线（200次·同选场同k·全格池随机取）。
泄漏闸：hist_odds 独立源 + as-of 预测（同 S5 继承）。
产出：data/04-summaries/v13-multicell-backtest.json
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
OUT_PATH = ROOT / "data" / "04-summaries" / "v13-multicell-backtest.json"
RAND_BASELINE_N = 200
RAND_SEED = 20261007

BOOM_THR, HAD_HOT = PROD_PARAMS
TOP_MATCHES = 4


def tickets_for(sel):
    """sel: [(cells, hit_cell_or_None), ...] 恰 4 场 → (注数, 派彩)。"""
    ks = [len(c) for c, _ in sel]
    n_units = (sum(ks[i] * ks[j] for i, j in itertools.combinations(range(4), 2))
               + sum(ks[i] * ks[j] * ks[k] for i, j, k in itertools.combinations(range(4), 3))
               + ks[0] * ks[1] * ks[2] * ks[3])
    hits = [h for _, h in sel]
    pay = 0.0
    for i, j in itertools.combinations(range(4), 2):
        if hits[i] and hits[j]:
            pay += min(UNIT * hits[i]["odds"] * hits[j]["odds"], CAP)
    for i, j, k in itertools.combinations(range(4), 3):
        if hits[i] and hits[j] and hits[k]:
            pay += min(UNIT * hits[i]["odds"] * hits[j]["odds"] * hits[k]["odds"], CAP)
    if all(hits):
        pay += min(UNIT * hits[0]["odds"] * hits[1]["odds"] * hits[2]["odds"] * hits[3]["odds"], CAP)
    return n_units, pay


def pick_cells(cand, k):
    cells = sorted(cand["cells"], key=lambda x: -x["p"])[:k]
    real = cand["real"]
    hit = next((c for c in cells if c["mk"] == real), None)
    return cells, hit


def build_day_sel(day_cands, k_plan):
    """k_plan: 4 元素每场格数；返回 sel 或 None（场不足）。"""
    sel = []
    for cand, k in zip(day_cands, k_plan):
        cells, hit = pick_cells(cand, k)
        sel.append((cells, hit))
    return sel


def eval_tier(pre, lo, hi, tier, rng=None):
    """tier: 'A'|'B'|'C'。rng 给定时用随机格（同选场同k·全格池取样）。"""
    total_pay = total_units = 0.0
    n_days = hit_days = 0
    for day, cands in pre.items():
        if not (lo <= day <= hi):
            continue
        ok = [c for c in cands
              if (c["hh"] is None or c["hh"] >= HAD_HOT) and c["boom"] <= BOOM_THR]
        if len(ok) < TOP_MATCHES:
            continue
        ok = sorted(ok, key=lambda c: -max(x["p"] for x in c["cells"]))[:TOP_MATCHES]
        if tier == "A":
            k_plan = [1, 1, 1, 1]
        elif tier == "B":
            k_plan = [2, 2, 2, 2]
        else:
            gaps = sorted(range(4), key=lambda i: gap_of(ok[i]))
            k_plan = [1, 1, 1, 1]
            for i in gaps[:2]:
                k_plan[i] = 2
        if rng is None:
            sel = build_day_sel(ok, k_plan)
        else:
            sel = []
            for cand, k in zip(ok, k_plan):
                pool = sorted(cand["cells"], key=lambda x: -x["p"])
                if len(pool) <= k:
                    cells, hit = pick_cells(cand, k)
                else:
                    cells = rng.sample(pool, k)
                    hit = next((c for c in cells if c["mk"] == cand["real"]), None)
                sel.append((cells, hit))
        n_units, pay = tickets_for(sel)
        total_units += n_units
        total_pay += pay
        n_days += 1
        if pay > 0:
            hit_days += 1
    stake = total_units * UNIT
    roi = (total_pay - stake) / stake if stake else None
    return {"units": int(total_units), "pay": round(total_pay, 2),
            "roi": roi, "days": n_days, "hitDays": hit_days,
            "unitCostPerDay": round(stake / n_days, 1) if n_days else None}


def gap_of(cand):
    ps = sorted((x["p"] for x in cand["cells"]), reverse=True)
    return (ps[0] - ps[1]) if len(ps) > 1 else 0.0


def main():
    print("══ v13 比分多格复式回测（V3W-v2 链·三档对照）══\n")
    print("预注册: fade-strategy-prereg changeLog v13·判据跑前写死\n")
    ctx = sl.build_ctx(LEAGUES)
    z2i = sl.zh_to_id()
    memo = {}
    print("── 预计算全链候选（复用 S5 框架）──")
    pre = preload_days(ctx, z2i, memo)
    print(f"预计算完成: {len(pre)}个候选日\n")

    tiers = ["A", "B", "C"]
    print(f"── A) 拟合段（{FIT_WINDOW[0]}~{FIT_WINDOW[1]}）──")
    fit = {}
    for t in tiers:
        fit[t] = eval_tier(pre, *FIT_WINDOW, tier=t)
        r = fit[t]
        name = {"A": "top1单选(基线)", "B": "全top2复式", "C": "2场top2复式"}[t]
        print(f"  {t}: {name:<12} ROI {r['roi']*100:+7.1f}% ({r['days']}日·{r['units']}注·日均{r['unitCostPerDay']}元·回款{r['hitDays']}日)")

    cand_tier = max(fit, key=lambda t: fit[t]["roi"] if fit[t]["roi"] is not None else -9)
    if cand_tier == "A":
        print(f"\n拟合段最优即基线 A——多格无正增益，直接判 KEEP")
        best = None
    else:
        print(f"\n拟合段候选档: {cand_tier}")

    val = {t: eval_tier(pre, *VAL_WINDOW, tier=t) for t in tiers}
    print(f"\n── B) 验证段（{VAL_WINDOW[0]}~{VAL_WINDOW[1]}）──")
    for t in tiers:
        r = val[t]
        name = {"A": "top1单选(基线)", "B": "全top2复式", "C": "2场top2复式"}[t]
        print(f"  {t}: {name:<12} ROI {r['roi']*100:+7.1f}% ({r['days']}日·{r['units']}注·日均{r['unitCostPerDay']}元·回款{r['hitDays']}日)")

    baseline_out = None
    crits = {"improve": False, "hitRate": False, "beatsRandom": None}
    verdict = "KEEP top1单选（拟合段即基线最优·多格无正增益）"
    if cand_tier != "A":
        imp = (val[cand_tier]["roi"] or 0.0) - (val["A"]["roi"] or 0.0)
        crits["improve"] = imp > 0.05 and (val[cand_tier]["roi"] or -1) > (val["A"]["roi"] or 0.0)
        rate_c = val[cand_tier]["hitDays"] / val[cand_tier]["days"] if val[cand_tier]["days"] else 0
        rate_a = val["A"]["hitDays"] / val["A"]["days"] if val["A"]["days"] else 0
        crits["hitRate"] = rate_c >= rate_a
        print(f"\n── C) 判据 ──")
        print(f"  ②验证段改善: {imp*100:+.1f}pp ({'过' if crits['improve'] else '不过'})  ③回款日率: {rate_c:.3f} vs {rate_a:.3f} ({'过' if crits['hitRate'] else '不过'})")
        if fit[cand_tier]["roi"] is not None and fit[cand_tier]["roi"] > 0:
            print(f"  ④随机格基线（{RAND_BASELINE_N}次·同选场同k全格池随机取）")
            rois = []
            for i in range(RAND_BASELINE_N):
                rng = random.Random(RAND_SEED + i)
                rois.append(eval_tier(pre, *FIT_WINDOW, tier=cand_tier, rng=rng)["roi"])
            rois_s = sorted(x for x in rois if x is not None)
            p95 = rois_s[int(len(rois_s) * 0.95)]
            mean = sum(rois_s) / len(rois_s)
            print(f"  随机均值 {mean*100:+.1f}% · P95 {p95*100:+.1f}% · 实选 {fit[cand_tier]['roi']*100:+.1f}%")
            baseline_out = {"mean": round(mean, 4), "p95": round(p95, 4),
                            "actual": round(fit[cand_tier]["roi"], 4),
                            "beatsRandom": fit[cand_tier]["roi"] > p95}
            crits["beatsRandom"] = baseline_out["beatsRandom"]
            print(f"  ④{'过' if crits['beatsRandom'] else '不过'}")
        else:
            crits["beatsRandom"] = True  # 拟合段为负·无正收益须自证伪
        verdict = (f"REPLACE→{cand_tier}档" if all(v for v in crits.values()) else
                   f"KEEP top1单选（{cand_tier}档判据未全过）")
        print(f"\n══ 判定: {verdict} ══")

    result = {"ranAt": "2026-10-07", "preReg": "fade-strategy-prereg changeLog v13",
              "chain": "V3W-v2", "tiers": {t: {"fit": fit[t], "val": val[t]} for t in tiers},
              "candidate": cand_tier, "criteria": crits,
              "randomBaseline": baseline_out, "verdict": verdict,
              "note": "日成本单列不设门槛·钱包裁定=大哥权"}
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n归档 {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
