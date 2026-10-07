# -*- coding: utf-8 -*-
"""v15 批次二：ρ 三口径对照（方向2扩展·平行轨不接生产）。

设计档：docs/2026-10-07-direction2-expansion-design.html
预注册：preregistration.json changeLog v6（跑前写死）。
三口径：①全局-0.05（基线参照） ②缓存拟合rho（现状实况） ③平局率映射rho（回归定系数·clip[-0.2,+0.1]）。
主指标：比分 top5 命中率（两段）。副指标：V3票型两段ROI。
判读：口径间 top5 命中率差 <1pp 斩 / ≥2pp 深化 / 1~2pp 观察。泄漏闸：as-of 预测。
产出：data/04-summaries/v15-rho-three-calib.json
开发者 sszhang
"""
from __future__ import annotations

import itertools
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import strength_loaders as sl
import strength_chain_eval as sce
import score_matrix as sm
from v11_s4_recalib import FIT_WINDOW, VAL_WINDOW, UNIT, CAP, LEAGUES, HIST
from v11_s4_recalib import payout_4s11, score_to_matrix_key, hist_crs_key_to_matrix

ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = ROOT / "data" / "04-summaries" / "v15-rho-three-calib.json"
GLOBAL_RHO = -0.05
DRAW_WIN = 100
RHO_CLIP = (-0.2, 0.1)


def draw_rates_static():
    """30联赛实际平局率（league库全历史·映射系数拟合用·静态）。"""
    agg = {}
    for f in (ROOT / "data/02-results/league").glob("*_matches.json"):
        lib = json.loads(f.read_text(encoding="utf-8"))
        mlist = lib.get("matches", []) if isinstance(lib, dict) else lib
        lg = lib.get("league", f.stem) if isinstance(lib, dict) else f.stem
        for m in mlist:
            if m.get("hg") is None:
                continue
            a = agg.setdefault(lg, [0, 0])
            a[0] += 1
            a[1] += 1 if m["hg"] == m["ag"] else 0
    return {k: v[1] / v[0] for k, v in agg.items() if v[0] >= 200}


def fit_rho_mapping(ctx):
    """缓存rho vs 平局率 散点线性回归（静态·映射系数）。"""
    dr = draw_rates_static()
    xs, ys = [], []
    for lg, rate in dr.items():
        rho = (ctx["dc"].get(lg) or {}).get("rho")
        if rho is None:
            continue
        xs.append(rate)
        ys.append(rho)
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    var = sum((x - mx) ** 2 for x in xs)
    slope = cov / var if var else 0.0
    return slope, my - slope * mx, n


def rolling_draw_rate(lg, as_of, matches_by_lg):
    """as-of 前 DRAW_WIN 场平局率（防泄漏）。"""
    rows = matches_by_lg.get(lg, [])
    past = [m for m in rows if str(m.get("date", ""))[:10] < str(as_of) and m.get("hg") is not None]
    if len(past) < 30:
        return None
    past = past[-DRAW_WIN:]
    return sum(1 for m in past if m["hg"] == m["ag"]) / len(past)


def preload_with_lam(ctx, z2i, memo, matches_by_lg):
    """全链预计算·存 lam/lg/rollingDrawRate（ρ 切换后仅需 dc_matrix 重算）。"""
    rows = []
    for f in sorted(HIST.glob("crs_hist_*.json")):
        rows += json.loads(f.read_text(encoding="utf-8"))["matches"]
    rows = [m for m in rows
            if m.get("crs") and m.get("score") and str(m.get("date", ""))[:10] >= FIT_WINDOW[0]]
    if "--smoke" in sys.argv:
        rows = [m for m in rows if str(m.get("date", ""))[:10] <= "2024-03-01"]
    by_day = {}
    for m in rows:
        by_day.setdefault(str(m["date"])[:10], []).append(m)
    pre = {}
    for day in sorted(by_day):
        day_rows = by_day[day]
        if len(day_rows) < 4:
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
            lg = pred["league"]
            lam = pred["lam"]
            cache_rho = (ctx["dc"].get(lg) or {}).get("rho", GLOBAL_RHO)
            r_rate = rolling_draw_rate(lg, as_of, matches_by_lg)
            cands.append({"lam": lam, "lg": lg, "cacheRho": cache_rho,
                          "drawRate": r_rate,
                          "real": score_to_matrix_key(str(m["score"])),
                          "histH": m.get("had", {}).get("h"),
                          "crsRaw": m.get("crs")})
        if cands:
            pre[day] = cands
        print(f"  preload {day} ({len(pre)}日累计)", end="\r", flush=True)
    print()
    return pre


def build_cells(cand, rho):
    """按 ρ 重算 39 格矩阵 → cells + boom。"""
    lam_h, lam_a = cand["lam"]
    matrix = sm.dc_matrix(lam_h, lam_a, rho)
    cells = {}
    for mk, p in matrix.items():
        cells[mk] = p
    boom = sum(cells.get(k, 0.0) for k in ("s1sh", "s1sd", "s1sa"))
    return cells, boom


def rho_of(cand, mode, slope, intercept):
    if mode == "global":
        return GLOBAL_RHO
    if mode == "cache":
        return cand["cacheRho"]
    r = cand["drawRate"]
    if r is None:
        return cand["cacheRho"]
    return max(RHO_CLIP[0], min(RHO_CLIP[1], slope * r + intercept))


def eval_mode(pre, lo, hi, mode, slope, intercept):
    """两段：top5 命中率 + V3票型(A档·去水过滤·4串11)两指标。"""
    top5_hit = top5_n = 0
    units_t = pay_t = 0.0
    days = hit_days = 0
    for day, cands in pre.items():
        if not (lo <= day <= hi):
            continue
        day_cells = []
        for c in cands:
            cells, boom = build_cells(c, rho_of(c, mode, slope, intercept))
            c["cellsNow"] = cells
            c["boomNow"] = boom
            day_cells.append(c)
            if c["real"] in sorted(cells, key=cells.get, reverse=True)[:5]:
                top5_hit += 1
            top5_n += 1
        ok = [c for c in day_cells
              if (c["histH"] is None or float(c["histH"]) >= 1.5) and c["boomNow"] <= 0.05]
        if len(ok) < 4:
            continue
        ok = sorted(ok, key=lambda c: -max(c["cellsNow"].values()))[:4]
        legs, per = [], []
        # 有价格内取 max p（对齐 S5/v13 口径）·赔率从 crsRaw 取（ρ 无关）
        for c in ok:
            priced = []
            for ck, ov in (c["crsRaw"] or {}).items():
                mk = hist_crs_key_to_matrix(str(ck))
                if mk and mk in c["cellsNow"]:
                    try:
                        o = float(ov)
                    except (TypeError, ValueError):
                        continue
                    if o > 1.0:
                        priced.append((mk, o, c["cellsNow"][mk]))
            if not priced:
                per = None
                break
            best_mk, odds, _ = max(priced, key=lambda t: t[2])
            legs.append((best_mk, odds))
            per.append(best_mk == c["real"])
        if per is None or len(legs) < 4:
            continue
        pay = payout_4s11(legs, per)
        units_t += 11
        pay_t += pay
        days += 1
        if pay > 0:
            hit_days += 1
    roi = (pay_t - units_t * UNIT) / (units_t * UNIT) if units_t else None
    return {"top5Rate": top5_hit / top5_n if top5_n else None, "top5N": top5_n,
            "v3Roi": roi, "days": days, "hitDays": hit_days}


def main():
    print("══ v15 批次二：ρ 三口径对照 ══\n", flush=True)
    print("预注册: preregistration.json changeLog v6\n", flush=True)
    ctx = sl.build_ctx(LEAGUES)
    z2i = sl.zh_to_id()
    memo = {}
    slope, intercept, npairs = fit_rho_mapping(ctx)
    print(f"映射回归: rho = {slope:.3f}×drawRate + {intercept:.3f} (n={npairs}联赛)\n", flush=True)

    matches_by_lg = {}
    for f in (ROOT / "data/02-results/league").glob("*_matches.json"):
        lib = json.loads(f.read_text(encoding="utf-8"))
        mlist = lib.get("matches", []) if isinstance(lib, dict) else lib
        lg = lib.get("league", f.stem) if isinstance(lib, dict) else f.stem
        matches_by_lg.setdefault(lg, []).extend(mlist)
    for v in matches_by_lg.values():
        v.sort(key=lambda m: str(m.get("date", "")))

    print("── preload（存lam·ρ切换仅重算矩阵）──", flush=True)
    pre = preload_with_lam(ctx, z2i, memo, matches_by_lg)
    print(f"preload 完: {len(pre)}日\n", flush=True)

    modes = {"global(-0.05)": "global", "cache(现状)": "cache", "drawRateMap": "map"}
    out = {}

    def fmt(x):
        return f"{x*100:.2f}%" if x is not None else "n/a"

    for label, mode in modes.items():
        fit_r = eval_mode(pre, *FIT_WINDOW, mode, slope, intercept)
        val_r = eval_mode(pre, *VAL_WINDOW, mode, slope, intercept)
        out[label] = {"fit": fit_r, "val": val_r}
        print(f"  {label:<16} top5命中率 拟合{fmt(fit_r['top5Rate'])} 验证{fmt(val_r['top5Rate'])}"
              f" · V3ROI 拟合{fmt(fit_r['v3Roi'])} 验证{fmt(val_r['v3Roi'])}", flush=True)

    rates = sorted(m["val"]["top5Rate"] for m in out.values() if m["val"]["top5Rate"] is not None)
    spread = rates[-1] - rates[0] if rates else None
    judge = (None if spread is None else
             ("<1pp 快刀斩立档" if spread < 0.01 else
              "≥2pp 映射口径预注册深化" if spread >= 0.02 else "1~2pp 观察"))
    print(f"\n口径间 top5 命中率极差: {'n/a' if spread is None else f'{spread*100:.2f}pp'} → 判读: {judge}", flush=True)

    result = {"ranAt": "2026-10-07", "preReg": "preregistration.json changeLog v6",
              "mapping": {"slope": round(slope, 4), "intercept": round(intercept, 4), "n": npairs},
              "modes": out, "spread": (round(spread, 4) if spread is not None else None), "judge": judge}
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n归档 {OUT_PATH.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
