#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""比分端三层降级链分批回测（W1/W2/W3）·判据预注册 engine/cache/batch-backtest-prereg.json。

设计冻结：docs/superpowers/plans/2026-10-13-crs-batch-backtest.md §设计冻结。
防泄漏三道闸：team_state_on(dc_rolling) lag=2；决策价=crsOpen+as_of=开赛前一日；结算只读不回写。
开发者 sszhang"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HIST_DIR = ROOT / "engine" / "cache" / "hist_odds"
MIN_POOL_ITEMS = 20

def matrix_key_to_pick(k: str) -> str:
    """矩阵键 's01s00' → pick 键 '1:0'；other_* 三键直通。开发者 sszhang"""
    if k.startswith("other_"):
        return k
    if k.startswith("s") and k[3] == "s":
        return f"{int(k[1:3])}:{int(k[4:6])}"
    return k

def load_hist_window(date_lo: str, date_hi: str, *, hist_dir: Path | None = None) -> list[dict]:
    """两 hist 文件扫窗 → 标准化行。crs/crsOpen 均 ≥MIN_POOL_ITEMS 才收（四臂配对完整性）。"""
    base = hist_dir or HIST_DIR
    out, dropped = [], {"no_close": 0, "no_open": 0}
    for f in sorted(base.glob("crs_hist_*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        for m in doc.get("matches") or []:
            d = str(m.get("date") or "")
            if not (date_lo <= d <= date_hi):
                continue
            crs, crs_open = m.get("crs") or {}, m.get("crsOpen") or {}
            if len(crs) < MIN_POOL_ITEMS:
                dropped["no_close"] += 1; continue
            if len(crs_open) < MIN_POOL_ITEMS:
                dropped["no_open"] += 1; continue
            out.append({"date": d, "code": m.get("code"), "league": m.get("league"),
                        "home": m.get("home"), "away": m.get("away"), "score": m.get("score"),
                        "had": m.get("had") or {}, "ttg": m.get("ttg") or {},
                        "crs": crs, "crsOpen": crs_open})
    out.sort(key=lambda r: r["date"])
    return out
