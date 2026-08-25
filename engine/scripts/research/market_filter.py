#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""胜平负串关筛选分析：按 Pinnacle 收盘价隐含概率分桶，看剔除低胜率场后的命中率与串关期望。

数学本质（纯跟盘 CLV=0）：单关期望 = 1/overround < 1（毛利折损）；
n 串全中期望 = Π(1/overround) 递减。筛选高概率场提高命中率体验，但不改变负期望——
要正收益必须 CLV > overround-1（出票赔率超收盘价的幅度 > 毛利）。
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))  # 归档后仍可直跑：scripts/ 入 path

import json

import numpy as np

from common import log, ROOT
from dc_predict import devig

LEAGUES = ["england-premier", "spain-laliga", "germany-bundesliga", "italy-serie-a",
           "france-ligue1", "france-ligue2", "netherlands-eredivisie", "portugal-primeira"]
CACHE = ROOT / "engine" / "cache"
SEASON = "2526"
BINS = [(0.0, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 0.80), (0.80, 1.01)]
THRESHOLDS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]


def main():
    rows = []
    for lg in LEAGUES:
        p = CACHE / f"odds_{lg}_{SEASON}.json"
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        for m in data.get("matches", []):
            if not (m.get("pin_h") and m.get("pin_d") and m.get("pin_a") and m.get("fthg") is not None):
                continue
            odds = [float(m["pin_h"]), float(m["pin_d"]), float(m["pin_a"])]
            probs = devig(odds)
            pred = int(np.argmax(probs))
            prob = float(probs[pred])
            hg, ag = int(m["fthg"]), int(m["ftag"])
            outcome = 0 if hg > ag else (1 if hg == ag else 2)
            overround = sum(1.0 / o for o in odds)
            rows.append({"prob": prob, "hit": int(pred == outcome), "overround": overround})

    n = len(rows)
    log("filter", f"共 {n} 场（有 Pinnacle 收盘+赛果）")

    print("\n== 按隐含概率分桶（看不同概率档的真实命中率）==")
    for lo, hi in BINS:
        b = [r for r in rows if lo <= r["prob"] < hi]
        if not b:
            continue
        hr = float(np.mean([r["hit"] for r in b]))
        ov = float(np.mean([r["overround"] for r in b]))
        print(f"  [{lo:.2f},{hi:.2f}): n={len(b):4d} 命中率={hr:.3f} avg毛利={ov:.4f} 单关期望={1/ov:.4f}")

    print("\n== 阈值筛选（剔除低胜率场后）==")
    for t in THRESHOLDS:
        b = [r for r in rows if r["prob"] >= t]
        if not b:
            continue
        hr = float(np.mean([r["hit"] for r in b]))
        ov = float(np.mean([r["overround"] for r in b]))
        print(f"  prob>={t:.2f}: n={len(b):4d}({len(b)/n*100:.0f}%) 命中率={hr:.3f} avg毛利={ov:.4f}")
        for ns in (2, 3, 5):
            ev = (1 / ov) ** ns
            print(f"        {ns}串全中期望={ev:.4f}（亏{(1-ev)*100:.1f}%）")


if __name__ == "__main__":
    main()
