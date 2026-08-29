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


def replay_odds(snapshots):
    """base 全量 → 依次应用 changes/removed → {code: 场记录}（diff 基准/分析回放通用）。开发者 sszhang"""
    state = {}
    for s in snapshots or []:
        if s.get("base"):
            state = {m["code"]: dict(m) for m in s.get("matches") or []}
            continue
        for c in s.get("changes") or []:
            rec = state.setdefault(c["code"], {"code": c["code"]})
            for k, v in c.items():
                if k == "code":
                    continue
                if isinstance(v, dict) and isinstance(rec.get(k), dict):
                    rec[k].update(v)          # 池内项级合并
                else:
                    rec[k] = v
        for code in s.get("removed") or []:
            state.pop(code, None)
    return state


def diff_odds(prev, new_matches):
    """回放态 vs 新提取 → (项级 changes, removed)。池 dict 递归一层只留变化项；
    元数据（kickoff/队名等）变化整值进 changes。开发者 sszhang"""
    changes, new_codes = [], set()
    for m in new_matches:
        code = m["code"]
        new_codes.add(code)
        if code not in prev:
            changes.append(dict(m))            # 新上考场：全量即变化
            continue
        delta = {}
        old = prev[code]
        for k, v in m.items():
            if k == "code":
                continue
            if isinstance(v, dict) and isinstance(old.get(k), dict):
                sub = {ik: iv for ik, iv in v.items() if old[k].get(ik) != iv}
                if sub:
                    delta[k] = sub
            elif old.get(k) != v:
                delta[k] = v
        if delta:
            changes.append({"code": code, **delta})
    removed = [c for c in prev if c not in new_codes]
    return changes, removed


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

    # ---- replay + diff ----
    snap = {"date": "2026-08-30", "type": "odds-timeline", "schemaVersion": 1, "snapshots": [
        {"at": "2026-08-30T10:00+08:00", "trigger": "run.py update", "base": True, "matches": [
            {"code": "周六001", "matchId": 1, "league": "意甲", "home": "A", "away": "B",
             "kickoff": "2026-08-30 19:00:00",
             "had": {"h": 2.0, "d": 3.1, "a": 3.5}, "hhad": {"goalLine": -1, "h": 3.0, "d": 3.4, "a": 2.02},
             "crs": {"s01s00": 8.0}, "ttg": {}, "hafu": {}},
            {"code": "周六002", "matchId": 2, "league": "英超", "home": "C", "away": "D",
             "kickoff": "2026-08-30 21:00:00", "had": {"h": 1.5, "d": 4.0, "a": 6.0},
             "hhad": {}, "crs": {}, "ttg": {}, "hafu": {}}]},
        {"at": "2026-08-30T14:00+08:00", "trigger": "临场复扫", "base": False,
         "changes": [{"code": "周六001", "crs": {"s01s00": 8.5}, "had": {"a": 3.8}},
                     {"code": "周日001", "matchId": 3, "league": "德乙", "home": "E", "away": "F",
                      "kickoff": "2026-08-30 19:30:00", "had": {"h": 1.7, "d": 3.6, "a": 3.7},
                      "hhad": {}, "crs": {}, "ttg": {}, "hafu": {}}],
         "removed": ["周六002"]},
    ]}
    state = replay_odds(snap["snapshots"])
    assert set(state) == {"周六001", "周日001"}, set(state)          # removed 生效
    assert state["周六001"]["crs"]["s01s00"] == 8.5                    # changes 应用
    assert state["周六001"]["had"]["a"] == 3.8 and state["周六001"]["had"]["h"] == 2.0  # 项级合并
    # diff: 无变化 → 空；调价 → 只出该项；新场 → 全量；停售 → removed
    changes, removed = diff_odds(state, list(state.values()))
    assert changes == [] and removed == [], (changes, removed)
    new = [dict(state["周六001"])]  # 周日001停售不入new（removed用例；brief fixture笔误修正）
    new[0]["crs"] = {**new[0]["crs"], "s01s00": 9.0}
    new.append({"code": "周一001", "matchId": 4, "league": "芬超", "home": "G", "away": "H",
                "kickoff": "2026-08-31 23:00:00", "had": {"h": 1.9, "d": 3.5, "a": 3.15},
                "hhad": {}, "crs": {}, "ttg": {}, "hafu": {}})
    changes, removed = diff_odds(state, new)
    assert changes == [{"code": "周六001", "crs": {"s01s00": 9.0}},
                       {"code": "周一001", "matchId": 4, "league": "芬超", "home": "G", "away": "H",
                        "kickoff": "2026-08-31 23:00:00", "had": {"h": 1.9, "d": 3.5, "a": 3.15},
                        "hhad": {}, "crs": {}, "ttg": {}, "hafu": {}}], changes
    assert removed == ["周日001"], removed
    print("[selftest] replay_odds + diff_odds OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
