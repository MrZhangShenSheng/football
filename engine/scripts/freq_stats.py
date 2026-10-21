# freq_stats.py
"""归因账本 → 维度组合→格形偏移频次表（判据预注册 freq-prereg.json·铁律4）。开发者 sszhang"""
import json
import math
import sys
from pathlib import Path
from statistics import quantiles

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parents[1]
HIST_DIR = ROOT / "data" / "04-summaries" / "score-attribution" / "hist"
PREREG_PATH = ROOT / "data" / "04-summaries" / "score-attribution" / "freq-prereg.json"
OUT_PATH = ROOT / "data" / "04-summaries" / "score-attribution" / "freq-tables.json"

PRESS_FAMILY = {"press_counter", "crush", "early_crush"}   # t3 on 组（prereg 冻结）
ENGLAND_LEAGUES = {"英超", "英冠"}                          # t6 子集（prereg 冻结）
GATE_CI_LO = 0.0        # 转正门：diff_ci_lo > 0
GATE_MIN_N_ON = 150     # 转正门：n_on >= 150

def load_bills() -> list[dict]:
    """hist/ 全部账本加载（forward 账本期3并入）。开发者 sszhang"""
    bills = []
    for p in sorted(HIST_DIR.glob("*-attribution.json")):
        bills.extend(json.loads(p.read_text(encoding="utf-8")))
    return [b for b in bills if "baseline" in b]

def _wilson_lo(k: int, n: int) -> float:
    """Wilson 95% 下限。开发者 sszhang"""
    if n == 0:
        return 0.0
    z, p = 1.96, k / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    rad = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (center - rad) / denom)

def freq_table(bills: list[dict], on, band) -> dict:
    """单表：on 组 vs off 组的 band 命中率差（Wilson CI）。开发者 sszhang"""
    on_b = [b for b in bills if on(b)]; off_b = [b for b in bills if not on(b)]
    k_on = sum(1 for b in on_b if band(b)); k_off = sum(1 for b in off_b if band(b))
    n_on, n_off = len(on_b), len(off_b)
    return {"n_on": n_on, "n_off": n_off,
            "rate_on": round(k_on / n_on, 4) if n_on else None,
            "rate_off": round(k_off / n_off, 4) if n_off else None,
            "diff": round((k_on / n_on - k_off / n_off), 4) if n_on and n_off else None,
            "ci_lo_on": round(_wilson_lo(k_on, n_on), 4) if n_on else None}

def _diff_ci_lo(t: dict) -> float | None:
    """两比例差不合并 SE 正态近似 95% 下限（prereg reportRequirements 冻结公式）。开发者 sszhang"""
    if t["n_on"] == 0 or t["n_off"] == 0 or t["rate_on"] is None or t["rate_off"] is None:
        return None
    p_on, p_off = t["rate_on"], t["rate_off"]
    se = math.sqrt(p_on * (1 - p_on) / t["n_on"] + p_off * (1 - p_off) / t["n_off"])
    return round(t["diff"] - 1.96 * se, 4)

def _verdict(t: dict, cap: str | None) -> tuple[str, str]:
    """转正门判定（prereg gate 冻结）：no_data / calibration_ready / observational。开发者 sszhang"""
    if t["n_on"] == 0:
        return "no_data", "on 组 0 场（历史窗缺口）——prereg noDataRule：不编造"
    if cap:
        return "observational", f"verdictCap（prereg 冻结）：{cap}"
    if t["diff_ci_lo"] is not None and t["diff_ci_lo"] > GATE_CI_LO and t["n_on"] >= GATE_MIN_N_ON:
        return "calibration_ready", "diff_ci_lo>0 且 n_on>=150（prereg gate）"
    return "observational", "未过转正门：diff_ci_lo>0 且 n_on>=150 需同时成立（prereg gate）"

def _e5_divergence(b: dict) -> float | None:
    """t5 背离度 = max(|E5.h|,|E5.a|)，E5 缺席场 None。开发者 sszhang"""
    for o in b.get("offsets", []):
        if o.get("dim") == "E5.finishing":
            return max(abs(o.get("h") or 0.0), abs(o.get("a") or 0.0))
    return None

def _total_goals(b: dict) -> int:
    return (b["result"]["hg"] or 0) + (b["result"]["ag"] or 0)

def _abs_margin(b: dict) -> int:
    return abs((b["result"]["hg"] or 0) - (b["result"]["ag"] or 0))

def main() -> None:
    """prereg 六表逐项计算落 freq-tables.json（每项带 verdict）。开发者 sszhang"""
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    bills = load_bills()
    band_tot4 = lambda b: _total_goals(b) >= 4   # 总球>=4（prereg 冻结口径）

    # t1 德比：shape_factors 含 dim=="derby"
    t1_on = lambda b: any(sf.get("dim") == "derby" for sf in b.get("shape_factors", []))
    # t2 红牌：offsets 含 E1.red
    t2_on = lambda b: any(o.get("dim") == "E1.red" for o in b.get("offsets", []))
    # t3 压上反杀族：script.type 三族
    t3_on = lambda b: (b.get("script") or {}).get("type") in PRESS_FAMILY
    # t4 FIFA 病毒：L0.stage evidence 含 fifa_virus
    t4_on = lambda b: any(sf.get("dim") == "L0.stage" and "fifa_virus" in str(sf.get("evidence", ""))
                          for sf in b.get("shape_factors", []))

    # t5 xG 背离 P75（2627 子集= E5 在场；分位口径 inclusive 线性插值·prereg 冻结）
    e5_subset = [b for b in bills if _e5_divergence(b) is not None]
    if e5_subset:
        vals = sorted(_e5_divergence(b) for b in e5_subset)
        p75 = round(quantiles(vals, n=4, method="inclusive")[2], 4)
        t5_on = lambda b, _p75=p75: (_e5_divergence(b) is not None and _e5_divergence(b) >= _p75)
    else:
        p75, t5_on = None, (lambda b: False)

    tables = {}
    for tid, on, band, cap in (
        ("t1_derby_vs_rest", t1_on, band_tot4, None),
        ("t2_redcard_vs_none", t2_on, band_tot4, None),
        ("t3_press_counter_family", t3_on, lambda b: _abs_margin(b) >= 3,
         "构造性表格：classify_script 净胜≥3 前置 ⟹ rate_on≡1.0，diff 无预测信息"),
        ("t4_fifa_virus", t4_on, band_tot4, None),
        ("t5_xg_divergence_p75", t5_on, band_tot4, None),
    ):
        t = freq_table(bills if tid != "t5_xg_divergence_p75" else e5_subset, on, band)
        t["diff_ci_lo"] = _diff_ci_lo(t)
        verdict, reason = _verdict(t, cap)
        t.update({"verdict": verdict, "verdict_reason": reason})
        tables[tid] = t
    tables["t5_xg_divergence_p75"]["subset_n"] = len(e5_subset)
    tables["t5_xg_divergence_p75"]["p75_threshold"] = p75

    # t6 英格兰裁判红牌分布（观察表无门）：总分布照常，by referee 无数据如实标注
    en = [b for b in bills if b.get("league") in ENGLAND_LEAGUES]
    en_red = [b for b in en if t2_on(b)]
    tables["t6_england_referee_red"] = {
        "subset_n": len(en),
        "red_rate_overall": round(len(en_red) / len(en), 4) if en else None,
        "by_league": {lg: {"n": sum(1 for b in en if b["league"] == lg),
                           "red_n": sum(1 for b in en_red if b["league"] == lg),
                           "red_rate": round(sum(1 for b in en_red if b["league"] == lg) /
                                             max(1, sum(1 for b in en if b["league"] == lg)), 4)}
                      for lg in sorted(ENGLAND_LEAGUES)},
        "by_referee_top5": None, "by_referee_bottom5": None,
        "by_referee_note": "no_data：hist 账单 referee 维度 100% in missing（prereg knownGap 冻结预期）——不编造，待 referee 源接入",
        "verdict": "observational",
        "verdict_reason": "prereg 预注册即观察表无门（verdictCap 冻结）",
    }

    out = {"generatedAt": prereg["frozenAt"], "prereg": PREREG_PATH.name,
           "universe": {"bills_with_baseline": len(bills), "e5_subset_n": len(e5_subset)},
           "gate": prereg["gate"]["rule"], "tables": tables}
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"universe bills={len(bills)} e5_subset={len(e5_subset)} p75={p75}")
    for tid, t in tables.items():
        print(f"{tid}: n_on={t.get('n_on')} n_off={t.get('n_off')} diff={t.get('diff')} "
              f"diff_ci_lo={t.get('diff_ci_lo')} verdict={t['verdict']}")

if __name__ == "__main__":
    main()
