#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""Elo 修正项验证：logit 融合加 +c·g(elo_diff)，walk-forward 网格搜 c 最小化 RPS。

对比 3 路 RPS：纯市场 | 原 DC 融合(a=0.4) | Elo 融合(in-sample best c)
验收：Elo 融合 >4/8 联赛优于纯市场 = 独立信号转正（模型增强成功）。

诚实标注：best_c 为 in-sample 网格优（乐观上界）。若连乐观上界都打不过市场，
则 Elo 无信号、铁证纯跟盘；若上界能过，再升级 walk-forward 估 c 去过拟合。
Elo 来源：elo_build.py 自建赛前 Elo（fd 比分序贯，walk-forward 严格、零外部依赖）。
"""
import json
from datetime import date, datetime

import numpy as np

from common import log, ROOT
import backtest as bt
from dc_predict import fuse

SEASON = "2526"
LEAGUES = [
    "england-premier", "spain-laliga", "germany-bundesliga", "italy-serie-a",
    "france-ligue1", "france-ligue2", "netherlands-eredivisie", "portugal-primeira",
]
# -1.0 .. 1.0 步 0.1（elo_diff/100 后量级与 logit 同阶）
C_GRID = [round(-1.0 + 0.1 * i, 2) for i in range(21)]


def load_market(league, season):
    raw = json.loads((bt.CACHE_DIR / f"odds_{league}_{season}.json").read_text(encoding="utf-8"))
    mbm = {}
    for m in raw["matches"]:
        try:
            d = datetime.strptime(m["date"], "%d/%m/%Y").date()
        except (ValueError, TypeError):
            continue
        if m.get("pin_h") and m.get("pin_d") and m.get("pin_a") and m.get("fthg") is not None:
            mbm[(d.isoformat(), m["home"], m["away"])] = (
                float(m["pin_h"]), float(m["pin_d"]), float(m["pin_a"]))
    return mbm


def load_elo_map(league, season):
    p = bt.CACHE_DIR / f"elo_history_{league}_{season}.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {(r["date"], r["home"], r["away"]): r["elo_diff"] for r in data["rows"]}


def fused_elo_probs(rec, a, b, ed, c):
    if ed is None:
        return rec["p_fused"]
    return fuse(rec["p_dc"], rec["p_mkt"], a, b, ed, c)


def main():
    a, b = bt.FUSION_DEFAULT["a"], bt.FUSION_DEFAULT["b"]
    fus_path = bt.CACHE_DIR / "fusion.json"
    if fus_path.exists():
        f = json.loads(fus_path.read_text(encoding="utf-8"))
        a, b = f["a"], f["b"]

    rows = []
    wins_elo_vs_mkt = 0
    wins_elo_vs_fused = 0
    best_cs = []

    for lg in LEAGUES:
        matches = bt.load_matches(lg, [SEASON])
        if not matches:
            continue
        records = bt.walk_forward(matches, load_market(lg, SEASON), a, b, use_xg=False)
        if len(records) < 3:
            log("elo_verify", f"{lg}: 样本 {len(records)}，跳过")
            continue
        elo_map = load_elo_map(lg, SEASON)
        elo_of = lambda r: elo_map.get((r["date"], r["home"], r["away"]))
        miss = sum(1 for r in records if elo_of(r) is None)

        mkt_rps = float(np.mean([bt.rps(r["p_mkt"], r["outcome"]) for r in records]))
        fused_rps = float(np.mean([bt.rps(r["p_fused"], r["outcome"]) for r in records]))

        best_c, best_rps = 0.0, fused_rps  # c=0 等价原融合
        for c in C_GRID:
            rps_c = float(np.mean([bt.rps(fused_elo_probs(r, a, b, elo_of(r), c), r["outcome"])
                                  for r in records]))
            if rps_c < best_rps:
                best_rps, best_c = rps_c, c

        rows.append({
            "league": lg, "n": len(records), "elo_miss": miss,
            "rps_mkt": round(mkt_rps, 4), "rps_fused": round(fused_rps, 4),
            "rps_elo": round(best_rps, 4), "best_c": best_c,
            "delta_elo_vs_mkt": round(best_rps - mkt_rps, 4),
            "delta_elo_vs_fused": round(best_rps - fused_rps, 4),
        })
        best_cs.append(best_c)
        if best_rps < mkt_rps:
            wins_elo_vs_mkt += 1
        if best_rps < fused_rps:
            wins_elo_vs_fused += 1
        log("elo_verify", f"{lg}: n={len(records)} miss={miss} 市场={mkt_rps:.4f} 融合={fused_rps:.4f} "
                  f"Elo融合={best_rps:.4f}(c={best_c}) vs市场={best_rps-mkt_rps:+.4f}")

    n = len(rows)
    verdict = "成功(>4/8 in-sample上界过线)" if wins_elo_vs_mkt > 4 else f"未达标({wins_elo_vs_mkt}/8)"
    result = {
        "season": SEASON, "fusion": {"a": a, "b": b},
        "c_grid": [C_GRID[0], C_GRID[-1]],
        "note": "best_c 为 in-sample 网格优（乐观上界），过线需再 walk-forward 估 c 去过拟合",
        "rows": rows,
        "summary": {
            "elo_beats_market": f"{wins_elo_vs_mkt}/{n}",
            "elo_beats_orig_fused": f"{wins_elo_vs_fused}/{n}",
            "best_c_median": float(np.median(best_cs)) if best_cs else None,
            "verdict": verdict,
        },
        "ranAt": date.today().isoformat(),
    }
    dest = ROOT / "data" / "04-summaries" / "elo_verify.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log("elo_verify", f"✅ 验证完成 → {dest.name}")
    log("elo_verify", f"Elo融合(in-sample上界)优于纯市场: {wins_elo_vs_mkt}/{n} | 优于原融合: {wins_elo_vs_fused}/{n}")
    log("elo_verify", f"best_c 中位数: {result['summary']['best_c_median']} → {verdict}")


if __name__ == "__main__":
    main()
