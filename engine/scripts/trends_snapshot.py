#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""赛前情报时序库：赔率 diff 链 / 情报摘要 / livescan 校验落盘。

设计: docs/2026-08-30-intel-timeline-design.html (intel-timeline)
消费方: sporttery_fetch.py (钩子①②) / run.py snapshot / backfill.py (对齐桥) / skill 会话 (livescan)
开发者 sszhang
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path

from common import log, ROOT

TRENDS_DIR = ROOT / "data" / "05-trends"
SCHEMA_VERSION = 1

# livescan 校验枚举（spec §4.3；禁止魔法值）
THREAT_LEVELS = ("high", "midhigh", "mid", "low")
SCAN_TRIGGERS = ("run.py update", "预测Step1", "run.py snapshot", "临场复扫", "出票后监控", "用户要求")

# 五池玩法键（提取/diff 用）
POOL_KEYS = ("had", "hhad", "crs", "ttg", "hafu")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def extract_odds(matches):
    """sporttery_matches.json 的 matches → 时序精简场记录（五池赔率转 float）。

    每场: {code, matchId, league, home, away, kickoff, had, hhad, crs, ttg, hafu}
    开发者 sszhang
    """
    out = []
    for m in matches or []:
        if not m.get("code"):
            continue
        rec = {"code": m["code"], "matchId": m.get("matchId"), "league": m.get("league"),
               "home": m.get("home"), "away": m.get("away"),
               "kickoff": f"{m.get('matchDate', '')} {m.get('matchTime', '')}".strip()}
        for pool in POOL_KEYS:
            src = m.get(pool) or {}
            rec[pool] = {k: _f(v) for k, v in src.items() if v is not None}
        out.append(rec)
    return out


# ---------- selftest ----------

def selftest():
    fx = [{
        "code": "周六001", "matchId": 1, "league": "意甲", "home": "A", "away": "B",
        "matchDate": "2026-08-30", "matchTime": "19:00:00",
        "had": {"h": "2.00", "d": "3.10", "a": "3.50"},
        "hhad": {"goalLine": "-1", "h": "3.00", "d": "3.40", "a": "2.02"},
        "crs": {"s01s00": "8.00", "s00s00": "10.0", "s1sh": "60.0"},
        "ttg": {"s0": "8.50", "s1": "4.20"},
        "hafu": {"hh": "3.10", "dd": "5.00"},
        "poolSingle": {"CRS": 1},
    }]
    out = extract_odds(fx)
    m = out[0]
    assert m["code"] == "周六001" and m["matchId"] == 1
    assert m["kickoff"] == "2026-08-30 19:00:00", m["kickoff"]
    assert m["had"] == {"h": 2.0, "d": 3.1, "a": 3.5}, m["had"]
    assert m["hhad"] == {"goalLine": -1, "h": 3.0, "d": 3.4, "a": 2.02}, m["hhad"]
    assert m["crs"] == {"s01s00": 8.0, "s00s00": 10.0, "s1sh": 60.0}, m["crs"]
    assert m["hafu"] == {"hh": 3.1, "dd": 5.0}
    assert "poolSingle" not in m and "sellStatus" not in m  # 非赔率字段不进时序
    print("[selftest] extract_odds OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
