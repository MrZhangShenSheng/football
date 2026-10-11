# -*- coding: utf-8 -*-
r"""v33 · 统一预测大模型（GBM 双塔·端到端 31 格比分分布·2026-10-11）

预注册（跑前写死）: engine/cache/strength_chain/fade-strategy-prereg.json → v33UnifiedModel
大哥立项: 现实意义预测大模型（非流程编排）——一个模型吃全特征直接吐比分分布

架构: HistGradientBoostingClassifier ×2（主队进球 0-7+ / 客队进球 0-7+ 各 8 类）
      → 外积 8×8 联合 → 映射体彩 31 格（含其他三格）→ 与基准同空间逐场 log-loss
基准（双考核·须全赢才转正）:
  ① 市场隐含 = crs 31 格 power 去水（k 二分）
  ② V3W 现任 = λ+rho 泊松矩阵（strength_chain 生产链同源）
判据: val 段配对 log-loss 差 (GBM−基准) bootstrap95%CI 上限 <0
纯净闸: v32.1 五道沿用（日期硬切/fit-only统计/同日先算后更/对照同切/V3W弱泄漏交底）

用法: python engine/scripts/research/v33_unified_score_model.py [--audit]
开发者 sszhang
"""
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import strength_loaders as sl          # noqa: E402
import strength_chain_eval as sce      # noqa: E402
import score_matrix as sm              # noqa: E402
from v18_dual_signal_backtest import LEAGUES  # noqa: E402
from v32_feature_sieve_backtest import load_hist, rolling_features, FIT_END, seg_of  # noqa: E402

HIST = ROOT / "engine" / "cache" / "hist_odds"
SUMM = ROOT / "data" / "04-summaries"
OUT_JSON = SUMM / "v33-unified-model.json"
OUT_HTML = SUMM / "2026-10-11-v33-unified-review.html"

GOAL_CLASSES = 8                    # 0..7（7=7+）
N_BOOT, BOOT_SEED = 2000, 20261011
GBM_PARAMS = dict(max_depth=4, max_iter=200, learning_rate=0.06,
                  min_samples_leaf=40, l2_regularization=1.0,
                  early_stopping=False, random_state=0)


def market_cell(h: int, a: int) -> str:
    """赛果 → 体彩格键（0-5 网格 + 其他三格）。"""
    if h <= 5 and a <= 5:
        return f"{h}:{a}"
    if h > a:
        return "other_h"
    if h == a:
        return "other_d"
    return "other_a"


def market_dist(crs: dict) -> dict | None:
    """crs 31 格 power 去水（k 二分解 Σp=1）。"""
    cells = {}
    for k, v in crs.items():
        try:
            o = float(v)
        except (TypeError, ValueError):
            continue
        if o > 1.0 and (":" in str(k) or str(k).startswith("other")):
            cells[str(k)] = o
    if len(cells) < 20:                       # 市场价不齐 → 降级跳过（铁律8口径）
        return None
    inv = np.array([1 / o for o in cells.values()])

    def norm(k):
        p = inv ** k
        return p / p.sum()

    lo, hi = 0.5, 5.0
    for _ in range(40):
        mid = (lo + hi) / 2
        s = norm(mid).sum()
        if abs(s - 1) < 1e-9:
            break
        if s > 1:
            lo = mid
        else:
            hi = mid
    p = norm((lo + hi) / 2)
    return dict(zip(cells.keys(), p))


def gbm_dist(ph: np.ndarray, pa: np.ndarray) -> dict:
    """8×8 外积 → 体彩 31 格。"""
    joint = np.outer(ph, pa)
    d = {}
    for h in range(6):
        for a in range(6):
            d[f"{h}:{a}"] = joint[h, a]
    idx = np.indices((GOAL_CLASSES, GOAL_CLASSES))
    big = (idx[0] >= 6) | (idx[1] >= 6)
    d["other_h"] = float(joint[(idx[0] > idx[1]) & big].sum())
    d["other_d"] = float(joint[(idx[0] == idx[1]) & big].sum())
    d["other_a"] = float(joint[(idx[1] > idx[0]) & big].sum())
    return d


def v3w_dist(lam: tuple, rho: float) -> dict:
    """λ+rho 矩阵 → 体彩 31 格（s1sh/s1sd/s1sa → 其他三格）。"""
    m = sm.dc_matrix(lam[0], lam[1], rho)
    d = {}
    for k, v in m.items():
        if k == "s1sh":
            d["other_h"] = d.get("other_h", 0.0) + v
        elif k == "s1sd":
            d["other_d"] = d.get("other_d", 0.0) + v
        elif k == "s1sa":
            d["other_a"] = d.get("other_a", 0.0) + v
        else:
            try:
                h, a = int(k[1:3]), int(k[4:6])
                d[f"{min(h,5)}:{min(a,5)}"] = d.get(f"{min(h,5)}:{min(a,5)}", 0.0) + v
            except (ValueError, IndexError):
                continue
    return d


def ll_of(dist: dict, cell: str) -> float:
    p = dist.get(cell, 0.0)
    return -np.log(max(p, 1e-12))


def boot_ci_upper(diffs: list) -> float:
    rng = np.random.RandomState(BOOT_SEED)
    arr = np.array(diffs)
    means = sorted(float(arr[rng.randint(0, len(arr), len(arr))].mean()) for _ in range(N_BOOT))
    return round(means[int(0.975 * N_BOOT)], 6)


def run():
    print("装载 hist + 滚动特征...")
    matches = load_hist()
    rolling_features(matches)
    print("V3W λ 重放（~13分钟）...")
    z2i = sl.zh_to_id()
    ctx = sl.build_ctx(LEAGUES)
    memo = {}
    rows = []
    for m in matches:
        mdist = market_dist(m["crs"])
        if not mdist:
            continue
        hid, aid = z2i.get(m["home"]), z2i.get(m["away"])
        if not (hid and aid):
            continue
        pred = sce._predict_match(hid, aid, date.fromisoformat(m["date"]), ctx, memo, beta=0.05)
        if not (pred and pred.get("lam")):
            continue
        lg = pred.get("league")
        rho = float((ctx["dc"].get(lg) or {}).get("rho", -0.05))
        vdist = v3w_dist(pred["lam"], rho)
        ttg_imp = 0.0
        for k in map(str, range(8)):
            o = (m.get("ttg") or {}).get(k)
            if o and float(o) > 1:
                ttg_imp += 1 / float(o)
        rows.append({"m": m, "market": mdist, "v3w": vdist,
                     "lam": pred["lam"], "ttg_imp": ttg_imp})
    print(f"三源齐备: {len(rows)} 场")

    # 特征（as-of·复用 v32 语义）
    X, yh, ya = [], [], []
    for r in rows:
        m = r["m"]
        X.append([r["lam"][0], r["lam"][1], r["lam"][0] + r["lam"][1], r["ttg_imp"],
                  m["f_lgavg"], m["f_t5"], m["p"], 1.0 if m["fav"] == 2 else 0.0])
        yh.append(min(m["h"], 7))
        ya.append(min(m["a"], 7))
    X = np.array(X)
    fit_mask = np.array([seg_of(r["m"]["date"]) == "fit" for r in rows])
    val_mask = ~fit_mask
    assert fit_mask.sum() and val_mask.sum()

    print(f"GBM 训练: fit {fit_mask.sum()} / val {val_mask.sum()} · 参数 {GBM_PARAMS}")
    mh = HistGradientBoostingClassifier(**GBM_PARAMS).fit(X[fit_mask], np.array(yh)[fit_mask])
    ma = HistGradientBoostingClassifier(**GBM_PARAMS).fit(X[fit_mask], np.array(ya)[fit_mask])
    PH = mh.predict_proba(X)
    PA = ma.predict_proba(X)

    per = []
    for i, r in enumerate(rows):
        cell = market_cell(r["m"]["h"], r["m"]["a"])
        gd = gbm_dist(PH[i], PA[i])
        per.append({"date": r["m"]["date"], "match": f"{r['m']['home']} vs {r['m']['away']}",
                    "cell": cell, "seg": seg_of(r["m"]["date"]),
                    "ll_gbm": ll_of(gd, cell), "ll_mkt": ll_of(r["market"], cell),
                    "ll_v3w": ll_of(r["v3w"], cell)})

    def seg_stats(seg):
        ps = [p for p in per if p["seg"] == seg]
        if not ps:
            return None
        d_mkt = [p["ll_gbm"] - p["ll_mkt"] for p in ps]
        d_v3w = [p["ll_gbm"] - p["ll_v3w"] for p in ps]
        base_mkt = np.mean([p["ll_mkt"] for p in ps])
        return {"n": len(ps),
                "meanLL": {"gbm": round(float(np.mean([p['ll_gbm'] for p in ps])), 4),
                           "market": round(float(base_mkt), 4),
                           "v3w": round(float(np.mean([p['ll_v3w'] for p in ps])), 4)},
                "diffVsMarket": {"mean": round(float(np.mean(d_mkt)), 5),
                                 "ciUpper": boot_ci_upper(d_mkt)},
                "diffVsV3W": {"mean": round(float(np.mean(d_v3w)), 5),
                              "ciUpper": boot_ci_upper(d_v3w)}}

    stats = {seg: seg_stats(seg) for seg in ("fit", "val")}
    v = stats["val"]
    beat_mkt = v["diffVsMarket"]["ciUpper"] < 0
    beat_v3w = v["diffVsV3W"]["ciUpper"] < 0
    if beat_mkt and beat_v3w:
        verdict = "PASS(双基准全赢→立转正批次:前向影子对照30轮后替换生产)"
    elif beat_v3w and not beat_mkt:
        verdict = "CLONE(打赢V3W打不赢市场=市场克隆·留档不转正)"
    else:
        verdict = "FAIL(打不赢V3W→大模型路线句号·V3W继续当班)"

    result = {"ranAt": "2026-10-11", "script": str(Path(__file__).relative_to(ROOT)),
              "preReg": "fade-strategy-prereg v33UnifiedModel（6b1a16e）",
              "data": {"threeSource": len(rows), "fit": int(fit_mask.sum()), "val": int(val_mask.sum())},
              "params": GBM_PARAMS,
              "features": ["λ主", "λ客", "λ和", "TTG隐含和", "联赛场均", "近5场均", "had_p", "客热门"],
              "stats": stats, "verdict": verdict, "audit": {}, "predictions": per}
    result["audit"]["reconcile"] = (f"{len(per)}场三源逐场log-loss落盘·val {stats['val']['n']}场"
                                    f"·GBM {stats['val']['meanLL']['gbm']} vs 市场 {stats['val']['meanLL']['market']}"
                                    f" vs V3W {stats['val']['meanLL']['v3w']}")
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    OUT_HTML.write_text(gen_html(result), encoding="utf-8")
    print("\n" + "=" * 72)
    for seg in ("fit", "val"):
        s = stats[seg]
        print(f"[{seg}] n={s['n']} logloss GBM {s['meanLL']['gbm']} · 市场 {s['meanLL']['market']} · V3W {s['meanLL']['v3w']}")
        print(f"      Δvs市场 {s['diffVsMarket']['mean']} (CI上限 {s['diffVsMarket']['ciUpper']}) · "
              f"ΔvsV3W {s['diffVsV3W']['mean']} (CI上限 {s['diffVsV3W']['ciUpper']})")
    print(f"\n终局: {verdict}")
    print(f"产物: {OUT_JSON.name}")
    return result


def gen_html(r):
    rows = "".join(
        f"<tr><td>{seg}</td><td class='num'>{s['n']}</td>"
        f"<td class='num'>{s['meanLL']['gbm']}</td><td class='num'>{s['meanLL']['market']}</td>"
        f"<td class='num'>{s['meanLL']['v3w']}</td>"
        f"<td class='num'>{s['diffVsMarket']['mean']} (↑{s['diffVsMarket']['ciUpper']})</td>"
        f"<td class='num'>{s['diffVsV3W']['mean']} (↑{s['diffVsV3W']['ciUpper']})</td></tr>"
        for seg, s in r["stats"].items())
    v_cls = "ok" if r["verdict"].startswith("PASS") else ("warn" if "CLONE" in r["verdict"] else "bad")
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>v33 统一预测大模型 · 双基准考核</title>
<style>
body{{font-family:"Microsoft YaHei","PingFang SC",sans-serif;color:#1c2733;background:#f7f9fb;margin:0;padding:24px;max-width:1000px;margin-inline:auto}}
h1{{font-size:20px}} h2{{font-size:15px;border-bottom:2px solid #1a5fb4;padding-bottom:4px}}
.card{{background:#fff;border:1px solid #dde4ea;border-radius:10px;padding:13px 16px;margin:8px 0}}
table{{border-collapse:collapse;width:100%;font-size:12.5px}}
th,td{{border:1px solid #dde4ea;padding:4px 7px}} th{{background:#f0f4f7}}
.num{{font-variant-numeric:tabular-nums}} .ok{{color:#0f6b4f;font-weight:600}} .bad{{color:#a33;font-weight:600}} .warn{{color:#b3541e;font-weight:600}}
.small{{font-size:11.5px;color:#5b6b7a}}
</style></head><body>
<h1>v33 统一预测大模型（GBM 双塔 31 格）· 双基准考核</h1>
<div class="card">跑时 {r['ranAt']} · 三源齐备 {r['data']['threeSource']} 场 · 判据=val段配对log-loss差 CI上限&lt;0</div>
<div class="card"><b>终局：</b><span class="{v_cls}">{r['verdict']}</span></div>
<h2>分段成绩</h2>
<div class="card"><table><tr><th>段</th><th>n</th><th>LL·GBM</th><th>LL·市场</th><th>LL·V3W</th><th>Δvs市场(均值·CI上限)</th><th>ΔvsV3W(均值·CI上限)</th></tr>{rows}</table></div>
<div class="card"><pre>{r['audit']['reconcile']}</pre></div>
<div class="small">开发者 sszhang · 2026-10-11 · 逐场预测见 JSON predictions[]·确定性GBM(random_state=0·no early stop)</div>
</body></html>"""


def audit():
    result = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    per = result["predictions"]
    n = len(per)
    for seg in ("fit", "val"):
        ps = [p for p in per if p["seg"] == seg]
        m = np.mean([p["ll_gbm"] - p["ll_mkt"] for p in ps])
        stored = result["stats"][seg]["diffVsMarket"]["mean"]
        if abs(m - stored) > 1e-4:
            print(f"FAIL {seg}: 重算Δ {m} ≠ 落盘 {stored}")
            return
    print(f"[审计] PASS · {n}场逐场重算一致（GBM确定性·重放见脚本）")


if __name__ == "__main__":
    if "--audit" in sys.argv:
        audit()
    else:
        run()
