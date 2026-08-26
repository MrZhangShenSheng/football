#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""半场参数生成：fd 多季 HTHG/HTAG → 联赛级半场占比 s（β收缩200球,clamp[0.3,0.6]）
+ 半场 rho_half（0-0/1-1 两格矩估计加权平均,clamp[-0.2,0.2]）。
产出 engine/cache/half_share.json。开发者 sszhang
设计: docs/2026-08-26-pool-coverage-design.html §6
"""
import json
import math
from collections import Counter
from pathlib import Path

from band_calibration import DIVS, SEASONS, fetch_rows
from common import ROOT

CACHE = ROOT / "engine" / "cache" / "half_share.json"
PRIOR_GOALS = 200          # β收缩先验强度（球）
S_CLAMP = (0.3, 0.6)
RHO_CLAMP = (-0.2, 0.2)
S_FALLBACK = 0.45


def league_rows(league: str) -> list:
    rows = []
    for season in SEASONS:
        for div, name in DIVS.items():
            if name == league:
                rows += fetch_rows(season, div)
    return rows


def half_stats(rows: list) -> dict:
    """返回半场统计：分主客半场球、全场球、半场比分Counter、场数（缺列场跳过计数）。"""
    hth_s = hta_s = ft_s = n = 0
    sc = Counter()
    for r in rows:
        try:
            hth, hta = int(r["HTHG"]), int(r["HTAG"])
            fth, fta = int(r["FTHG"]), int(r["FTAG"])
        except (KeyError, ValueError, TypeError):
            continue
        hth_s += hth; hta_s += hta; ft_s += fth + fta; n += 1
        sc[(hth, hta)] += 1
    return {"hth": hth_s, "hta": hta_s, "ft": ft_s, "sc": sc, "n": n}


def shrink_s(stats: dict, prior_s: float) -> float:
    ht_sum = stats["hth"] + stats["hta"]
    s = (ht_sum + PRIOR_GOALS * prior_s) / (stats["ft"] + PRIOR_GOALS)
    return max(S_CLAMP[0], min(S_CLAMP[1], s))


def rho_half(stats: dict) -> float:
    """矩估计：obs00=p00*(1-lh*la*rho) → r00=(1-obs00/p00)/(lh*la)；
    obs11=p11_pois*(1-rho) → r11=1-obs11/p11；按格频数加权平均。"""
    n = stats["n"]
    if n == 0:
        return 0.0
    lh = stats["hth"] / n
    la = stats["hta"] / n
    base = math.exp(-lh - la)
    est = wsum = 0.0
    c00, c11 = stats["sc"].get((0, 0), 0), stats["sc"].get((1, 1), 0)
    p00 = base
    if c00 and p00 > 1e-12:
        r = (1 - (c00 / n) / p00) / (lh * la)
        est += r * c00; wsum += c00
    p11 = base * lh * la
    if c11 and p11 > 1e-12:
        r = 1 - (c11 / n) / p11
        est += r * c11; wsum += c11
    rho = est / wsum if wsum else 0.0
    return max(RHO_CLAMP[0], min(RHO_CLAMP[1], rho))


def main() -> None:
    all_rows = {lg: league_rows(lg) for lg in sorted(set(DIVS.values()))}
    st = {lg: half_stats(rows) for lg, rows in all_rows.items()}
    tot_ht = sum(v["hth"] + v["hta"] for v in st.values())
    tot_ft = sum(v["ft"] for v in st.values())
    prior_s = tot_ht / tot_ft if tot_ft else S_FALLBACK
    g_stats = {"hth": sum(v["hth"] for v in st.values()),
               "hta": sum(v["hta"] for v in st.values()),
               "ft": tot_ft,
               "sc": Counter(), "n": sum(v["n"] for v in st.values())}
    for v in st.values():
        g_stats["sc"].update(v["sc"])
    out = {
        "meta": {"priorGoals": PRIOR_GOALS, "sClamp": list(S_CLAMP), "rhoClamp": list(RHO_CLAMP)},
        "global": {"s": round(shrink_s(g_stats, prior_s), 4),
                   "rho_half": round(rho_half(g_stats), 4), "n": g_stats["n"]},
        "leagues": {lg: {"s": round(shrink_s(v, prior_s), 4),
                         "rho_half": round(rho_half(v), 4), "n": v["n"]}
                    for lg, v in st.items() if v["n"] > 0},
    }
    CACHE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[half-params] global s={out['global']['s']} rho={out['global']['rho_half']} n={out['global']['n']}")
    for lg, v in out["leagues"].items():
        print(f"  {lg}: s={v['s']} rho_half={v['rho_half']} n={v['n']}")
    # 自检断言（TDD 验收线）
    assert 0.40 <= out["global"]["s"] <= 0.50, f"global s={out['global']['s']} 越界（足球统计常识 0.44~0.47）"
    assert out["global"]["n"] > 8000, f"样本 {out['global']['n']} 异常（8联赛×4季应 >8000）"
    print("[half-params] 自检通过 → engine/cache/half_share.json")


if __name__ == "__main__":
    main()
