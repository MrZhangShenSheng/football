#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""ρ 分诊：用 walk-forward 逐场数据算 DC-市场 误差共变 + 概率共变性。

决定后续路线（合成三路调研结论）：
  ρ_prob(概率共变) > 0.95 或 ρ_err(RPS共变) > 0.8 → DC 无独立信号 → 纯跟盘路线
  否则 → 模型增强路线（xG 接入 λ + Elo 协变量）

复用 backtest.walk_forward 的忠实 walk-forward 逐场 records（每 REFIT_EVERY 场重拟合，
只用该场之前数据，防泄漏）。门槛来自 fusion-architecture 调研的 Bates-Granger 推论。
"""
import json
from datetime import date, datetime

import numpy as np

from common import log, ROOT
import backtest as bt

SEASON = "2526"
LEAGUES = [
    "england-premier", "spain-laliga", "germany-bundesliga", "italy-serie-a",
    "france-ligue1", "france-ligue2", "netherlands-eredivisie", "portugal-primeira",
]
THRESH_PROB = 0.95   # 概率共变门槛：超过即 DC 基本是市场副本
THRESH_ERR = 0.80    # RPS 误差共变门槛：超过即 DC 无市场之外的独立信号


def load_market(league, season):
    """复用 backtest main 的 market_by_match 构造（Pinnacle 收盘价 + 含 fthg 的场次）。"""
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


def corr(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3 or a.std() < 1e-9 or b.std() < 1e-9:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def main():
    a, b = bt.FUSION_DEFAULT["a"], bt.FUSION_DEFAULT["b"]
    fus_path = bt.CACHE_DIR / "fusion.json"
    if fus_path.exists():
        f = json.loads(fus_path.read_text(encoding="utf-8"))
        a, b = f["a"], f["b"]

    all_rps_dc, all_rps_mkt, all_p_dc, all_p_mkt = [], [], [], []
    per_league = []

    for lg in LEAGUES:
        matches = bt.load_matches(lg, [SEASON])
        if not matches:
            log("rho", f"{lg}: 无赛果数据，跳过")
            continue
        records = bt.walk_forward(matches, load_market(lg, SEASON), a, b)
        if len(records) < 3:
            log("rho", f"{lg}: 可评样本 {len(records)}（<3），跳过")
            continue

        rps_dc = [bt.rps(r["p_dc"], r["outcome"]) for r in records]
        rps_mkt = [bt.rps(r["p_mkt"], r["outcome"]) for r in records]
        rho_err = corr(rps_dc, rps_mkt)
        rho_prob = [corr([r["p_dc"][i] for r in records],
                         [r["p_mkt"][i] for r in records]) for i in range(3)]

        all_rps_dc += rps_dc
        all_rps_mkt += rps_mkt
        all_p_dc += [r["p_dc"] for r in records]
        all_p_mkt += [r["p_mkt"] for r in records]

        rho_err_s = round(rho_err, 4) if rho_err is not None else None
        rho_prob_s = [round(x, 4) if x is not None else None for x in rho_prob]
        per_league.append({
            "league": lg, "n": len(records),
            "rho_err_rps": rho_err_s, "rho_prob_hda": rho_prob_s,
            "mean_rps_dc": round(float(np.mean(rps_dc)), 4),
            "mean_rps_mkt": round(float(np.mean(rps_mkt)), 4),
        })
        log("rho", f"{lg}: n={len(records)} ρ_err={rho_err_s} ρ_prob(H/D/A)={rho_prob_s} "
                  f"DC={np.mean(rps_dc):.4f} Mkt={np.mean(rps_mkt):.4f}")

    overall_rho_err = corr(all_rps_dc, all_rps_mkt)
    overall_rho_prob = [corr([p[i] for p in all_p_dc], [p[i] for p in all_p_mkt]) for i in range(3)]
    overall = {
        "n": len(all_rps_dc),
        "rho_err_rps": round(overall_rho_err, 4) if overall_rho_err is not None else None,
        "rho_prob_hda": [round(x, 4) if x is not None else None for x in overall_rho_prob],
    }

    max_prob = max((x for x in overall["rho_prob_hda"] if x is not None), default=0.0)
    no_signal = (max_prob > THRESH_PROB
                 or (overall["rho_err_rps"] is not None and overall["rho_err_rps"] > THRESH_ERR))
    verdict = "无独立信号→纯跟盘路线" if no_signal else "有独立信号→模型增强路线"

    result = {
        "season": SEASON, "fusion": {"a": a, "b": b},
        "thresholds": {"rho_prob": THRESH_PROB, "rho_err_rps": THRESH_ERR},
        "per_league": per_league, "overall": overall, "verdict": verdict,
        "ranAt": date.today().isoformat(),
    }
    dest = ROOT / "data" / "04-summaries" / "rho_diagnose.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log("rho", f"✅ 诊断完成 → {dest.name}")
    log("rho", f"整体 ρ_err(RPS共变)={overall['rho_err_rps']} ρ_prob(H/D/A)={overall['rho_prob_hda']}")
    log("rho", f"判定：{verdict}")


if __name__ == "__main__":
    main()
