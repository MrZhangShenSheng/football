# -*- coding: utf-8 -*-
"""v11-S5：去水阈值重校（v12 动态ENV + 30联赛可算域）。

背景：S4 参数(boom=0.05/had_hot=1.5)系静态ENV下标定；v12 联赛级动态ENV 改变 λ
绝对值 → boomP 分布整体移位 → 旧参数不再排对场（V3W 验证段 +7.2% → −18.2%）。
方法：与 S4 同构两段法网格扫描，网格向 S4 最优角点外扩。
预注册判据（fade-strategy-prereg changeLog v12·跑前写死）：
  ① 拟合段(2024-01~2025-06) ROI 最高者为新参数
  ② 验证段(2025-10~2026-09-28) 新参数须优于生产参数(0.05/1.5)且改善>5pp 方可替换
  ③ 下注日数不坍缩超30%（新days ≥ 生产days×0.7）
  ④ 任一段 ROI>0 须过随机选场基线（200次同过滤随机4场·固定种子）
泄漏闸：hist_odds 独立源 + as-of 预测（同 S4 继承）。
产出：data/04-summaries/v11-s5-recalib.json
开发者 sszhang
"""
from __future__ import annotations

import json
import random
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import strength_loaders as sl
import strength_chain_eval as sce
from v11_s4_recalib import FIT_WINDOW, VAL_WINDOW, TOP_N, UNIT, LEAGUES, HIST
from v11_s4_recalib import payout_4s11, score_to_matrix_key, hist_crs_key_to_matrix

ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = ROOT / "data" / "04-summaries" / "v11-s5-recalib.json"

PROD_PARAMS = (0.05, 1.5)
BOOM_GRID = (0.02, 0.03, 0.04, 0.05, 0.06, 0.08)
HAD_GRID = (1.3, 1.4, 1.5, 1.6, 1.8, 2.0)
IMPROVE_FLOOR = 0.05
DAYS_FLOOR_RATIO = 0.7
RAND_BASELINE_N = 200
RAND_SEED = 20261007


def preload_days(ctx, z2i, memo):
    """一次全链预测：所有候选日的 cells/boom/hh/real 预计算（参数扫描只做过滤）。"""
    rows = []
    for f in sorted(HIST.glob("crs_hist_*.json")):
        rows += json.loads(f.read_text(encoding="utf-8"))["matches"]
    rows = [m for m in rows
            if m.get("crs") and m.get("score") and str(m.get("date", ""))[:10] >= FIT_WINDOW[0]]
    by_day = {}
    for m in rows:
        by_day.setdefault(str(m["date"])[:10], []).append(m)

    pre = {}
    for day in sorted(by_day):
        day_rows = by_day[day]
        if len(day_rows) < TOP_N:
            continue
        as_of = date.fromisoformat(day)
        cands = []
        for m in day_rows:
            hid, aid = z2i.get(m["home"]), z2i.get(m["away"])
            if not hid or not aid:
                continue
            pred = sce._predict_match(hid, aid, as_of, ctx, memo, beta=0.05)
            if not pred or not pred.get("matrix"):
                continue
            cells = []
            for ck, ov in (m.get("crs") or {}).items():
                mk = hist_crs_key_to_matrix(str(ck))
                if not mk or mk not in pred["matrix"]:
                    continue
                try:
                    o = float(ov)
                except (TypeError, ValueError):
                    continue
                if o > 1.0:
                    cells.append({"mk": mk, "odds": o, "p": pred["matrix"][mk]})
            if not cells:
                continue
            boom = sum(pred["matrix"].get(k, 0.0) for k in ("s1sh", "s1sd", "s1sa"))
            try:
                hh = float(m["had"]["h"])
            except (KeyError, TypeError, ValueError):
                hh = None
            had_hist_full = None
            try:
                had_hist_full = {k: float(m["had"][k]) for k in ("h", "d", "a")}
            except (KeyError, TypeError, ValueError):
                pass
            cands.append({"cells": cells, "real": score_to_matrix_key(str(m["score"])),
                          "boom": boom, "hh": hh,
                          "hadModel": dict(pred.get("had") or {}),
                          "hadHist": had_hist_full,
                          # v25 标签（v3 缓存用·预测逻辑零改动）：普查按体彩 league 切片
                          "league": m.get("league"), "home": m.get("home"),
                          "away": m.get("away"), "matchId": m.get("matchId")})
        if cands:
            pre[day] = cands
        print(f"  preload {day} ({len(pre)}日累计)", end="\r", flush=True)
    print()
    return pre


def eval_params(pre, lo, hi, boom_thr, had_hot, rng=None):
    total_stake = total_pay = 0.0
    n_days = hit_days = 0
    for day, cands in pre.items():
        if not (lo <= day <= hi):
            continue
        ok = [c for c in cands
              if (c["hh"] is None or c["hh"] >= had_hot) and c["boom"] <= boom_thr]
        if len(ok) < TOP_N:
            continue
        if rng is None:
            sel = sorted(ok, key=lambda c: -max(x["p"] for x in c["cells"]))[:TOP_N]
        else:
            sel = rng.sample(ok, TOP_N)
        legs = [(max(c["cells"], key=lambda x: x["p"])["mk"],
                 max(c["cells"], key=lambda x: x["p"])["odds"]) for c in sel]
        per = [legs[i][0] == sel[i]["real"] for i in range(4)]
        pay = payout_4s11(legs, per)
        total_pay += pay
        total_stake += 22.0
        n_days += 1
        if pay > 0:
            hit_days += 1
    roi = (total_pay - total_stake) / total_stake if total_stake else None
    return {"stake": total_stake, "pay": round(total_pay, 2), "roi": roi,
            "days": n_days, "hitDays": hit_days}


def main():
    print("══ v11-S5 去水阈值重校（v12 动态ENV · 30联赛）══\n")
    print("预注册判据: 拟合段选优 + 验证段须优于生产参数且改善>5pp + 下注日不坍缩30% + 正收益须过随机基线\n")

    ctx = sl.build_ctx(LEAGUES)
    z2i = sl.zh_to_id()
    memo = {}
    print("── 预计算全链候选（一次）──")
    pre = preload_days(ctx, z2i, memo)
    print(f"预计算完成: {len(pre)}个候选日\n")

    print(f"── A) 拟合段网格（{FIT_WINDOW[0]}~{FIT_WINDOW[1]}·{len(BOOM_GRID)}×{len(HAD_GRID)}组）──")
    fit_results = {}
    for boom in BOOM_GRID:
        for had in HAD_GRID:
            r = eval_params(pre, *FIT_WINDOW, boom_thr=boom, had_hot=had)
            fit_results[(boom, had)] = r
            tag = f"boom={boom:.2f} had_hot={had:.1f}"
            mark = " ←生产" if (boom, had) == PROD_PARAMS else ""
            print(f"  {tag:28s} ROI {r['roi']*100:+7.1f}% ({r['days']}日·回款{r['hitDays']}日){mark}")

    non_prod = {k: v for k, v in fit_results.items() if k != PROD_PARAMS}
    best_key = max(non_prod, key=lambda k: non_prod[k]["roi"])
    best = fit_results[best_key]
    print(f"\n最优新参数: boom={best_key[0]:.2f} had_hot={best_key[1]:.1f} "
          f"(拟合段 ROI {best['roi']*100:+.1f}%)")

    print(f"\n── B) 验证段对照（{VAL_WINDOW[0]}~{VAL_WINDOW[1]}）──")
    val_prod = eval_params(pre, *VAL_WINDOW, boom_thr=PROD_PARAMS[0], had_hot=PROD_PARAMS[1])
    val_best = eval_params(pre, *VAL_WINDOW, boom_thr=best_key[0], had_hot=best_key[1])
    print(f"  生产参数(0.05/1.5):            ROI {val_prod['roi']*100:+7.1f}% ({val_prod['days']}日)")
    print(f"  新参数({best_key[0]:.2f}/{best_key[1]:.1f}):           ROI {val_best['roi']*100:+7.1f}% ({val_best['days']}日)")
    val_improve = (val_best["roi"] or 0.0) - (val_prod["roi"] or 0.0)
    print(f"  验证段改善: {val_improve*100:+.1f}pp")

    baseline_out = None
    if (best["roi"] is not None and best["roi"] > 0) or (val_best["roi"] is not None and val_best["roi"] > 0):
        print(f"\n── C) 随机选场基线（{RAND_BASELINE_N}次·同过滤随机4场）──")
        for label, window, params in (("拟合段", FIT_WINDOW, best_key), ("验证段", VAL_WINDOW, best_key)):
            rois = []
            for i in range(RAND_BASELINE_N):
                rng = random.Random(RAND_SEED + i)
                r = eval_params(pre, *window, boom_thr=params[0], had_hot=params[1], rng=rng)
                rois.append(r["roi"])
            rois_s = sorted(x for x in rois if x is not None)
            p95 = rois_s[int(len(rois_s) * 0.95)] if rois_s else None
            mean = sum(rois_s) / len(rois_s) if rois_s else None
            actual = fit_results[best_key]["roi"] if label == "拟合段" else val_best["roi"]
            print(f"  {label}: 随机均值 {mean*100:+.1f}% · P95 {p95*100:+.1f}% · 实选 {actual*100:+.1f}%")
            baseline_out = baseline_out or {}
            baseline_out[label] = {"mean": round(mean, 4), "p95": round(p95, 4),
                                   "actual": round(actual, 4),
                                   "beatsRandom": actual > p95}

    crit2 = val_improve > IMPROVE_FLOOR and (val_best["roi"] or -1) > (val_prod["roi"] or 0.0)
    crit3 = val_best["days"] >= val_prod["days"] * DAYS_FLOOR_RATIO
    fit_pos = best["roi"] is not None and best["roi"] > 0
    val_pos = val_best["roi"] is not None and val_best["roi"] > 0
    crit4 = (not (fit_pos or val_pos)) or (
        (not fit_pos or (baseline_out or {}).get("拟合段", {}).get("beatsRandom", False))
        and (not val_pos or (baseline_out or {}).get("验证段", {}).get("beatsRandom", False)))
    verdict = ("REPLACE 生产参数" if (crit2 and crit3 and crit4) else
               "KEEP 生产参数（判据未全过）")
    print(f"\n══ 判定: {verdict} ══")
    print(f"  ②改善>5pp: {'过' if crit2 else '不过'}  ③下注日: {'过' if crit3 else '不过'}  ④随机基线: {'过' if crit4 else '不过'}")

    result = {"ranAt": "2026-10-07", "env": "v12动态ENV·30联赛", "preReg": "fade-strategy-prereg changeLog v12",
              "bestParams": {"boom": best_key[0], "had_hot": best_key[1]},
              "grid": {"boom": list(BOOM_GRID), "had_hot": list(HAD_GRID)},
              "fitAll": {f"{b:.2f}|{h:.1f}": v for (b, h), v in fit_results.items()},
              "fit": best, "valProd": val_prod, "valBest": val_best,
              "valImprove": round(val_improve, 4),
              "randomBaseline": baseline_out,
              "criteria": {"improveOver5pp": crit2, "daysNoCollapse": crit3, "beatsRandom": crit4},
              "verdict": verdict}
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n归档 {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
