#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""P0 验证：xG 接入 λ 的 walk-forward RPS 对比。

对比 5 路概率的逐场 RPS：
  纯市场  |  原 DC  |  xG-DC  |  原 DC 融合  |  xG-DC 融合
验收（goal-driven）：xG-DC 融合在 >4/8 联赛 RPS 优于纯市场 = 模型增强成功。
同时统计各联赛 xG 覆盖率（缺 xG 场 fallback 实际进球，覆盖率低则结果≈原 DC）。
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


def mean_rps(records, key):
    if not records:
        return None
    return round(float(np.mean([bt.rps(r[key], r["outcome"]) for r in records])), 4)


def main():
    a, b = bt.FUSION_DEFAULT["a"], bt.FUSION_DEFAULT["b"]
    fus_path = bt.CACHE_DIR / "fusion.json"
    if fus_path.exists():
        f = json.loads(fus_path.read_text(encoding="utf-8"))
        a, b = f["a"], f["b"]

    rows = []
    wins_fused_xg_vs_mkt = 0   # xG-DC 融合优于纯市场
    wins_xg_vs_dc = 0          # xG-DC 本身优于原 DC

    for lg in LEAGUES:
        matches = bt.load_matches(lg, [SEASON])
        if not matches:
            continue
        xg_cov = sum(1 for m in matches if m.get("hxg") is not None) / max(len(matches), 1)
        mbm = load_market(lg, SEASON)
        rec_dc = bt.walk_forward(matches, mbm, a, b, use_xg=False)
        rec_xg = bt.walk_forward(matches, mbm, a, b, use_xg=True)
        if len(rec_dc) < 3 or len(rec_dc) != len(rec_xg):
            log("xg_verify", f"{lg}: 样本 {len(rec_dc)}/{len(rec_xg)} 不齐，跳过")
            continue

        mkt = mean_rps(rec_dc, "p_mkt")
        dc = mean_rps(rec_dc, "p_dc")
        xg = mean_rps(rec_xg, "p_dc")
        fused_dc = mean_rps(rec_dc, "p_fused")
        fused_xg = mean_rps(rec_xg, "p_fused")

        rows.append({
            "league": lg, "n": len(rec_dc), "xg_coverage": round(xg_cov, 3),
            "rps_mkt": mkt, "rps_dc": dc, "rps_xg": xg,
            "rps_fused_dc": fused_dc, "rps_fused_xg": fused_xg,
            "delta_xg_vs_dc": round(xg - dc, 4),
            "delta_fused_xg_vs_mkt": round(fused_xg - mkt, 4),
        })
        if xg < dc:
            wins_xg_vs_dc += 1
        if fused_xg < mkt:
            wins_fused_xg_vs_mkt += 1
        log("xg_verify", f"{lg}: n={len(rec_dc)} xG覆盖={xg_cov:.2f} "
                  f"市场={mkt} DC={dc} xG-DC={xg} 融合DC={fused_dc} 融合xG={fused_xg}")

    n = len(rows)
    verdict = "成功(>4/8 联赛融合xG优于市场)" if wins_fused_xg_vs_mkt > 4 else f"未达标({wins_fused_xg_vs_mkt}/8)"
    result = {
        "season": SEASON, "fusion": {"a": a, "b": b},
        "rows": rows,
        "summary": {
            "xg_dc_beats_orig_dc": f"{wins_xg_vs_dc}/{n}",
            "fused_xg_beats_market": f"{wins_fused_xg_vs_mkt}/{n}",
            "verdict": verdict,
        },
        "ranAt": date.today().isoformat(),
    }
    dest = ROOT / "data" / "04-summaries" / "xg_verify.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log("xg_verify", f"✅ 验证完成 → {dest.name}")
    log("xg_verify", f"xG-DC 本身优于原 DC: {wins_xg_vs_dc}/{n}")
    log("xg_verify", f"xG-DC 融合优于纯市场: {wins_fused_xg_vs_mkt}/{n} → {verdict}")


if __name__ == "__main__":
    main()
