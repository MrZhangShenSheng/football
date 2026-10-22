# freq_stats_r2.py
"""r2 频次表：赛前滚动维度（A2.xg/A4.style/A4.discipline）→ 格形预测力检验。

判据预注册于 data/04-summaries/score-attribution/freq-prereg-r2.json（frozenAt 2026-10-22，
冻结于任何数值计算之前，含先验预期与反钓鱼声明）。本脚本只跑 prereg 列出的 6 表。

与 r1（freq_stats.py）的关键差别：on 条件全部来自【本场之前】的滚动均值（赛前可观测），
band 全部来自本场 result。两者无共用输入 ⟹ 设计上免疫 r1 的 t3/t5 循环缺陷。

开发者 sszhang
"""
import json
import math
from pathlib import Path
from statistics import quantiles

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parents[1]
HIST_DIR = ROOT / "data" / "04-summaries" / "score-attribution" / "hist"
PREREG = ROOT / "data" / "04-summaries" / "score-attribution" / "freq-prereg-r2.json"
OUT = ROOT / "data" / "04-summaries" / "score-attribution" / "freq-tables-r2.json"

GATE_CI_LO = 0.0
GATE_MIN_N_ON = 150

# 第四闸（r2 实测后新增）：频次差过门 ≠ 对市场有概率增量。
# r2 五表三闸全过（diff +0.05~0.08·CI 全离 0·n 数千），但样本外 log-loss 对
# 「仅市场」基准仅改善 0.01%（research/r2_market_increment.py M2），实质为零。
# ⟹ 任何表在进校准通道前必须另跑样本外概率增量检验，改善 >= 0.5% 方可放行。
GATE_MIN_OOS_LOGLOSS_GAIN_PCT = 0.5
GATE_NOTE_FOURTH = (
    "第四闸·样本外概率增量：频次 diff 显著只说明分组与格形相关，不说明"
    "条件变量带来市场之外的信息。须跑 research/r2_market_increment.py 口径"
    f"（时间切分 70/30·对照『仅市场』基准），样本外 log-loss 相对改善 >= "
    f"{GATE_MIN_OOS_LOGLOSS_GAIN_PCT}% 才放行。r2 实测 0.01% ⟹ 五表降级。")


def load_bills() -> list[dict]:
    bills = []
    for p in sorted(HIST_DIR.glob("*-attribution.json")):
        bills.extend(json.loads(p.read_text(encoding="utf-8")))
    return [b for b in bills if "baseline" in b]


def _offset(b: dict, dim: str) -> dict | None:
    return next((o for o in b.get("offsets", []) if o.get("dim") == dim), None)


def _shape(b: dict, dim: str) -> dict | None:
    return next((s for s in b.get("shape_factors", []) if s.get("dim") == dim), None)


def _diff_ci_lo(p_on, n_on, p_off, n_off):
    if not n_on or not n_off or p_on is None or p_off is None:
        return None
    se = math.sqrt(p_on * (1 - p_on) / n_on + p_off * (1 - p_off) / n_off)
    return round((p_on - p_off) - 1.96 * se, 4)


def table(pool: list[dict], on, band) -> dict:
    on_b = [b for b in pool if on(b)]
    off_b = [b for b in pool if not on(b)]
    k_on = sum(1 for b in on_b if band(b))
    k_off = sum(1 for b in off_b if band(b))
    n_on, n_off = len(on_b), len(off_b)
    r_on = k_on / n_on if n_on else None
    r_off = k_off / n_off if n_off else None
    t = {"n_on": n_on, "n_off": n_off,
         "rate_on": round(r_on, 4) if r_on is not None else None,
         "rate_off": round(r_off, 4) if r_off is not None else None,
         "diff": round(r_on - r_off, 4) if r_on is not None and r_off is not None else None,
         "diff_ci_lo": _diff_ci_lo(r_on, n_on, r_off, n_off),
         "preObservable": True}
    if n_on == 0:
        t["verdict"] = "no_data"
        t["verdict_reason"] = "on 组 0 场——prereg noDataRule：不编造"
    elif t["diff_ci_lo"] is not None and t["diff_ci_lo"] > GATE_CI_LO and n_on >= GATE_MIN_N_ON:
        # 前三闸过只到 pending_increment，不直接发校准授权（第四闸见模块头常量）
        t["verdict"] = "observational"
        t["verdict_reason"] = (
            "前三闸过（diff_ci_lo>0 且 n_on>=150 且赛前可观测），但未过第四闸："
            "样本外对市场概率增量 <0.5%（r2 实测 0.01%）⟹ 不发校准授权。"
            "若后续换条件变量重跑，须同步重跑 research/r2_market_increment.py。")
        t["gate_status"] = {"ci_lo": True, "n_on": True, "pre_observable": True,
                            "oos_increment": False,
                            "note": GATE_NOTE_FOURTH}
    else:
        t["verdict"] = "observational"
        bad = []
        if t["diff_ci_lo"] is None or t["diff_ci_lo"] <= 0:
            bad.append(f"diff_ci_lo={t['diff_ci_lo']}<=0")
        if n_on < GATE_MIN_N_ON:
            bad.append(f"n_on={n_on}<150")
        t["verdict_reason"] = "未过转正门：" + "、".join(bad)
    return t


def main() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    bills = load_bills()

    # ── 池与分位阈值（各维度在场子集分别算，不跨池混用）──
    style_pool = [b for b in bills if _offset(b, "A4.style")]
    disc_pool = [b for b in bills if _shape(b, "A4.discipline")]
    xg_pool = [b for b in bills if _offset(b, "A2.xg")]

    def _st_pair(b):
        """从 rolling_evidence 取双方滚动射正（on 条件源·赛前可观测）。"""
        re_ = b.get("rolling_evidence") or {}
        return re_.get("st_h"), re_.get("st_a")

    def _fouls_mean(b):
        re_ = b.get("rolling_evidence") or {}
        fh, fa = re_.get("fouls_h"), re_.get("fouls_a")
        return (fh + fa) / 2 if fh is not None and fa is not None else None

    def _xg_sum(b):
        re_ = b.get("rolling_evidence") or {}
        xh, xa = re_.get("xg_h"), re_.get("xg_a")
        return xh + xa if xh is not None and xa is not None else None

    st_gaps = sorted(abs(a - b_) for a, b_ in
                     (_st_pair(b) for b in style_pool) if a is not None and b_ is not None)
    st_all = sorted(v for b in style_pool for v in _st_pair(b) if v is not None)
    fouls = sorted(v for b in disc_pool if (v := _fouls_mean(b)) is not None)
    xgs = sorted(v for b in xg_pool if (v := _xg_sum(b)) is not None)

    q = lambda arr, i: round(quantiles(arr, n=4, method="inclusive")[i], 4) if len(arr) >= 4 else None
    gap_p75, st_med, st_p75, st_p25 = q(st_gaps, 2), q(st_all, 1), q(st_all, 2), q(st_all, 0)
    f_p25, f_p75 = q(fouls, 0), q(fouls, 2)
    xg_p75 = q(xgs, 2)

    tot4 = lambda b: (b["result"]["hg"] or 0) + (b["result"]["ag"] or 0) >= 4
    tot1 = lambda b: (b["result"]["hg"] or 0) + (b["result"]["ag"] or 0) <= 1
    net2 = lambda b: abs((b["result"]["hg"] or 0) - (b["result"]["ag"] or 0)) >= 2

    def on_gap(b):
        a, c = _st_pair(b)
        return a is not None and c is not None and abs(a - c) >= gap_p75

    def on_both_high(b):
        a, c = _st_pair(b)
        return a is not None and c is not None and a >= st_med and c >= st_med

    def on_mismatch(b):
        a, c = _st_pair(b)
        if a is None or c is None:
            return False
        return (a >= st_p75 and c <= st_p25) or (c >= st_p75 and a <= st_p25)

    specs = [
        ("r2t1_style_gap_p75", style_pool, on_gap, net2),
        ("r2t2_style_both_high", style_pool, on_both_high, tot4),
        ("r2t3_discipline_low_fouls", disc_pool,
         lambda b: (v := _fouls_mean(b)) is not None and v <= f_p25, tot4),
        ("r2t4_discipline_high_fouls", disc_pool,
         lambda b: (v := _fouls_mean(b)) is not None and v >= f_p75, tot1),
        ("r2t5_xgform_high", xg_pool,
         lambda b: (v := _xg_sum(b)) is not None and v >= xg_p75, tot4),
        ("r2t6_style_mismatch", style_pool, on_mismatch, net2),
    ]

    tables = {}
    for tid, pool, on, band in specs:
        t = table(pool, on, band)
        t["pool_n"] = len(pool)
        tables[tid] = t

    out = {"generatedAt": prereg["frozenAt"], "prereg": PREREG.name,
           "universe": {"bills": len(bills), "style_pool": len(style_pool),
                        "disc_pool": len(disc_pool), "xg_pool": len(xg_pool)},
           "thresholds": {"style_gap_p75": gap_p75, "st_median": st_med,
                          "st_p75": st_p75, "st_p25": st_p25,
                          "fouls_p25": f_p25, "fouls_p75": f_p75, "xg_sum_p75": xg_p75},
           "gate": prereg["gate"]["rule"],
           "circularityNote": ("on 条件全部来自本场之前滚动均值，band 全部来自本场 result，"
                               "无共用输入 ⟹ 设计上免疫 r1 的 t3/t5 循环缺陷"),
           "tables": tables}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"池 bills={len(bills)} style={len(style_pool)} disc={len(disc_pool)} xg={len(xg_pool)}")
    print(f"阈值 gap_p75={gap_p75} st_med={st_med} st_p75={st_p75} st_p25={st_p25} "
          f"fouls_p25={f_p25} fouls_p75={f_p75} xg_p75={xg_p75}\n")
    for tid, t in tables.items():
        print(f"{tid}:")
        print(f"   n_on={t['n_on']} n_off={t['n_off']} rate_on={t['rate_on']} "
              f"rate_off={t['rate_off']}")
        print(f"   diff={t['diff']} diff_ci_lo={t['diff_ci_lo']} → {t['verdict']}")
    ready = [k for k, v in tables.items() if v["verdict"] == "calibration_ready"]
    print(f"\n过门：{len(ready)}/6" + (f" → {'、'.join(ready)}" if ready else "（无）"))


if __name__ == "__main__":
    main()
