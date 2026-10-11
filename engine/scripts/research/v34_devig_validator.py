# -*- coding: utf-8 -*-
r"""v34 · GBM 去水器 EV 校准小批量验证（2026-10-11）

预注册（跑前写死）: engine/cache/strength_chain/fade-strategy-prereg.json → v34DevigValidator
设计: 两臂同规则 CRS 逐场 top-EV 选格（每场 1 格·EV=p×赔率-1 最高者）只换概率源
  臂A = power 去水（现行）    臂B = GBM 重建（v33 双塔外积）
核心指标 = EV 校准差: |Σclaimed EV 比例 − realized 回报率|
判据: ①B 校准差显著小于 A（配对 bootstrap CI 不含 0）且 realized 命中率不劣化（差 CI 下限 > -3pp）
      → GBM 去水器接入 boldplay EV 链；②否则 power 留任
λ 缓存本轮落盘（engine/cache/strength_chain/v34_lambda_cache.json）免重放

用法: python engine/scripts/research/v34_devig_validator.py [--audit]
开发者 sszhang
"""
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import strength_loaders as sl          # noqa: E402
import strength_chain_eval as sce      # noqa: E402
from v18_dual_signal_backtest import LEAGUES  # noqa: E402
from v32_feature_sieve_backtest import load_hist, rolling_features, FIT_END, seg_of  # noqa: E402
from v33_unified_score_model import (market_dist, market_cell, gbm_dist,   # noqa: E402
                                     GBM_PARAMS, GOAL_CLASSES)

SUMM = ROOT / "data" / "04-summaries"
LAM_CACHE = ROOT / "engine" / "cache" / "strength_chain" / "v34_lambda_cache.json"
OUT_JSON = SUMM / "v34-devig-validator.json"
N_BOOT, BOOT_SEED = 2000, 20261012
HIT_TOL_PP = -3.0


def get_lams(matches):
    """λ 重放（带缓存·指纹=场数+首末日期）。"""
    fp = f"{len(matches)}|{matches[0]['date']}|{matches[-1]['date']}"
    if LAM_CACHE.exists():
        d = json.loads(LAM_CACHE.read_text(encoding="utf-8"))
        if d.get("fingerprint") == fp:
            print(f"λ 缓存命中: {len(d['lams'])} 场")
            return d["lams"]
    z2i = sl.zh_to_id()
    ctx = sl.build_ctx(LEAGUES)
    memo = {}
    lams = {}
    for m in matches:
        hid, aid = z2i.get(m["home"]), z2i.get(m["away"])
        if not (hid and aid):
            continue
        pred = sce._predict_match(hid, aid, date.fromisoformat(m["date"]), ctx, memo, beta=0.05)
        if pred and pred.get("lam"):
            lams[f"{m['date']}|{m['home']}|{m['away']}"] = pred["lam"]
    LAM_CACHE.write_text(json.dumps({"fingerprint": fp, "lams": lams}, ensure_ascii=False), encoding="utf-8")
    print(f"λ 重放完成并落缓存: {len(lams)} 场")
    return lams


def run():
    print("装载 hist + 滚动特征...")
    matches = load_hist()
    rolling_features(matches)
    lams = get_lams(matches)

    rows = []
    for m in matches:
        mdist = market_dist(m["crs"])
        if not mdist:
            continue
        lam = lams.get(f"{m['date']}|{m['home']}|{m['away']}")
        if not lam or not m.get("ttg"):
            continue
        ttg_imp = 0.0
        for k in map(str, range(8)):
            o = m["ttg"].get(k)
            if o and float(o) > 1:
                ttg_imp += 1 / float(o)
        rows.append({"m": m, "market": mdist, "lam": lam, "ttg_imp": ttg_imp})
    fit_rows = [r for r in rows if seg_of(r["m"]["date"]) == "fit"]
    val_rows = [r for r in rows if seg_of(r["m"]["date"]) == "val"]
    print(f"三源齐备: {len(rows)} · fit {len(fit_rows)} / val {len(val_rows)}")

    def feat(r):
        return [r["lam"][0], r["lam"][1], r["lam"][0] + r["lam"][1], r["ttg_imp"],
                r["m"]["f_lgavg"], r["m"]["f_t5"], r["m"]["p"],
                1.0 if r["m"]["fav"] == 2 else 0.0]

    Xf = np.array([feat(r) for r in fit_rows])
    yh_f = np.array([min(r["m"]["h"], 7) for r in fit_rows])
    ya_f = np.array([min(r["m"]["a"], 7) for r in fit_rows])
    mh = HistGradientBoostingClassifier(**GBM_PARAMS).fit(Xf, yh_f)
    ma = HistGradientBoostingClassifier(**GBM_PARAMS).fit(Xf, ya_f)
    Xv = np.array([feat(r) for r in val_rows])
    PH = mh.predict_proba(Xv)
    PA = ma.predict_proba(Xv)

    def top_ev(dist, crs):
        best, best_ev = None, -9
        for cell, p in dist.items():
            o = crs.get(cell)
            if not o:
                continue
            ev = p * float(o) - 1
            if ev > best_ev:
                best, best_ev = cell, ev
        return best, best_ev

    legs = []
    for i, r in enumerate(val_rows):
        m = r["m"]
        cell_true = market_cell(m["h"], m["a"])
        gd = gbm_dist(PH[i], PA[i])
        a_cell, a_ev = top_ev(r["market"], m["crs"])
        b_cell, b_ev = top_ev(gd, m["crs"])
        if not a_cell or not b_cell:
            continue
        a_hit, b_hit = a_cell == cell_true, b_cell == cell_true
        legs.append({"date": m["date"], "match": f"{m['home']} vs {m['away']}",
                     "cell_true": cell_true,
                     "A": {"cell": a_cell, "ev": round(a_ev, 4), "hit": a_hit,
                           "odds": round(float(m["crs"][a_cell]), 3),
                           "ret": round(float(m["crs"][a_cell]) if a_hit else 0.0, 3)},
                     "B": {"cell": b_cell, "ev": round(b_ev, 4), "hit": b_hit,
                           "odds": round(float(m["crs"][b_cell]), 3),
                           "ret": round(float(m["crs"][b_cell]) if b_hit else 0.0, 3)}})

    def arm_stats(arm):
        n = len(legs)
        hits = sum(1 for l in legs if l[arm]["hit"])
        ret = sum(l[arm]["ret"] for l in legs)
        claimed = sum(l[arm]["ev"] for l in legs)
        return {"n": n, "hitRate": round(hits / n, 4),
                "realizedROI": round(ret / n - 1, 4),
                "claimedEVSum": round(claimed / n, 4),
                "calibGap": round(abs(claimed / n - (ret / n - 1)), 4)}

    statA, statB = arm_stats("A"), arm_stats("B")
    # 配对 bootstrap：校准差 B−A（负=B更好）·命中率差 B−A
    rng = np.random.RandomState(BOOT_SEED)
    n = len(legs)
    calA = np.array([abs(l["A"]["ev"] - (l["A"]["ret"] - 1)) for l in legs])
    calB = np.array([abs(l["B"]["ev"] - (l["B"]["ret"] - 1)) for l in legs])
    hitA = np.array([l["A"]["hit"] for l in legs], dtype=float)
    hitB = np.array([l["B"]["hit"] for l in legs], dtype=float)
    d_cal, d_hit = [], []
    for _ in range(N_BOOT):
        idx = rng.randint(0, n, n)
        d_cal.append(float((calB[idx] - calA[idx]).mean()))
        d_hit.append(float((hitB[idx] - hitA[idx]).mean()))
    d_cal.sort(); d_hit.sort()
    cal_ci = [round(d_cal[int(0.025 * N_BOOT)], 5), round(d_cal[int(0.975 * N_BOOT)], 5)]
    hit_ci = [round(d_hit[int(0.025 * N_BOOT)], 5), round(d_hit[int(0.975 * N_BOOT)], 5)]

    overlap = sum(1 for l in legs if l["A"]["cell"] == l["B"]["cell"]) / n
    pass1 = cal_ci[1] < 0
    pass2 = hit_ci[0] > HIT_TOL_PP / 100
    verdict = ("PASS(校准显著改善且命中率不劣化 → GBM去水器接入boldplay EV链·转正另立commit)"
               if pass1 and pass2 else
               f"FAIL({('校准未显著改善' if not pass1 else '')}{'命中劣化超3pp' if not pass2 else ''})·power去水留任")

    result = {"ranAt": "2026-10-11", "preReg": "fade-strategy-prereg v34DevigValidator（80c0907）",
              "armA_power": statA, "armB_gbm": statB,
              "paired": {"calibDiff_CI": cal_ci, "hitDiff_CI": hit_ci, "cellOverlap": round(overlap, 3)},
              "verdict": verdict, "legs": legs}
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print("\n" + "=" * 72)
    print(f"臂A(power): 命中{statA['hitRate']:.1%} ROI{statA['realizedROI']:+.1%} 校准差{statA['calibGap']}")
    print(f"臂B(GBM):   命中{statB['hitRate']:.1%} ROI{statB['realizedROI']:+.1%} 校准差{statB['calibGap']}")
    print(f"配对差: 校准ΔCI {cal_ci} · 命中ΔCI {hit_ci} · 格重合 {overlap:.0%}")
    print(f"终局: {verdict}")
    return result


def audit():
    result = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    legs = result["legs"]
    for arm in ("A", "B"):
        hits = sum(1 for l in legs if l[arm]["hit"])
        ret = sum(l[arm]["ret"] for l in legs)
        s = result[f"arm{'A_power' if arm == 'A' else 'B_gbm'}"]
        if abs(hits / len(legs) - s["hitRate"]) > 5e-4 or abs(ret / len(legs) - 1 - s["realizedROI"]) > 5e-4:
            print(f"FAIL 臂{arm} 复算不符")
            return
    print(f"[审计] PASS · {len(legs)}场两臂复算一致")


if __name__ == "__main__":
    if "--audit" in sys.argv:
        audit()
    else:
        run()
