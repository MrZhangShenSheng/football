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
    """base 全量 → 依次应用 changes/removed → {code: 场记录}（diff 基准/分析回放通用）。
    深拷贝池 dict：回放态与快照互不污染（write_snapshot 读→回放→写回的审计保真）。开发者 sszhang"""
    state = {}
    for s in snapshots or []:
        if s.get("base"):
            state = {m["code"]: {**m, **{k: dict(v) for k, v in m.items() if isinstance(v, dict)}}
                     for m in s.get("matches") or []}
            continue
        for c in s.get("changes") or []:
            rec = state.setdefault(c["code"], {"code": c["code"]})
            for k, v in c.items():
                if k == "code":
                    continue
                if isinstance(v, dict) and isinstance(rec.get(k), dict):
                    rec[k].update(v)          # 池内项级合并（rec[k] 已是拷贝）
                else:
                    rec[k] = dict(v) if isinstance(v, dict) else v   # 新场池dict同样拷贝
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


_TRENDS_DIR = TRENDS_DIR          # 可重定向（selftest 用；运行时不变）


def _set_trends_dir(p):
    """selftest 钩子：重定向时序库目录。开发者 sszhang"""
    globals()["_TRENDS_DIR"] = p


def _now_iso():
    """本地时区 ISO8601（+08:00）。开发者 sszhang"""
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")[:-2] + ":00"


def atomic_write_json(path, data):
    """tmp + rename 原子写（并发竞态防护，ADR D7）。开发者 sszhang"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_snapshot(extracted, trigger, day=None):
    """提取结果 → 追加当日 odds 时序文件。无文件/损坏 → 新 base 全量版（损坏转 .corrupt-<ts> 备份）。
    返回文件 Path。开发者 sszhang"""
    day = day or date.today().isoformat()
    path = _TRENDS_DIR / f"{day}-odds.json"
    _TRENDS_DIR.mkdir(parents=True, exist_ok=True)
    doc = {"date": day, "type": "odds-timeline", "schemaVersion": SCHEMA_VERSION, "snapshots": []}
    prev = {}
    if path.exists():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            prev = replay_odds(doc.get("snapshots"))
        except json.JSONDecodeError:
            backup = path.with_suffix(f"{path.suffix}.corrupt-{datetime.now():%H%M%S}")
            path.replace(backup)
            log("trends", f"当日odds文件损坏，降级新base版（备份 {backup.name}）")
            doc = {"date": day, "type": "odds-timeline", "schemaVersion": SCHEMA_VERSION, "snapshots": []}
    changes, removed = diff_odds(prev, extracted)
    snap = {"at": _now_iso(), "trigger": trigger, "base": not doc["snapshots"]}
    if snap["base"]:
        snap["matches"] = extracted
    else:
        snap["changes"] = changes
        if removed:
            snap["removed"] = removed
    doc["snapshots"].append(snap)
    atomic_write_json(path, doc)
    n_chg = len(changes) if not snap["base"] else len(extracted)
    log("trends", f"odds快照 {'base' if snap['base'] else 'diff'} {n_chg} 项 → {path.name}")
    return path


def _key_player(l):
    """主力判定（修正系数9 同款口径）：apps>=2 且 starts/apps>=0.7。开发者 sszhang"""
    apps, starts = int(l.get("apps") or 0), int(l.get("starts") or 0)
    return apps >= 2 and starts / max(apps, 1) >= 0.7


def extract_intel(payload, code):
    """insight 落盘 payload → 时序摘要 entry（伤停 keyPlayer 标记+d 符号/排名/近10/场均球/H2H）。
    开发者 sszhang"""
    inj = payload.get("injuries") or {}
    def slim(side):
        return [{"name": x.get("name"), "pos": x.get("pos"), "apps": x.get("apps"),
                 "starts": x.get("starts"), "keyPlayer": _key_player(x)}
                for x in (inj.get(side) or [])]
    home_i, away_i = slim("home"), slim("away")
    st = payload.get("standing") or {}
    fm = (payload.get("form") or {})
    avg, l10 = fm.get("goalAvg") or {}, (fm.get("last10HomeAway") or {})
    h2h = (payload.get("h2h") or {}).get("statistics") or {}
    return {
        "at": _now_iso(), "matchId": payload.get("matchId"), "code": code,
        "league": (payload.get("match") or {}).get("league"),
        "fullFile": f"engine/cache/sporttery_insight_{payload.get('matchId')}.json",
        "injuries": {"home": home_i, "away": away_i, "d": len(home_i) - len(away_i)},
        "rank": {"home": int(st["home"]["ranking"]) if (st.get("home") or {}).get("ranking") else None,
                 "away": int(st["away"]["ranking"]) if (st.get("away") or {}).get("ranking") else None},
        "form": {"homeLast10": f"{l10.get('homeWinGoalMatchCnt', 0)}胜{l10.get('homeDrawMatchCnt', 0)}平"
                               f"{l10.get('homeLossGoalMatchCnt', 0)}负",
                 "awayLast10": f"{l10.get('awayWinGoalMatchCnt', 0)}胜{l10.get('awayDrawMatchCnt', 0)}平"
                               f"{l10.get('awayLossGoalMatchCnt', 0)}负",
                 "homeGoalAvg": _f(avg.get("homeGoalAvgCnt")), "awayGoalAvg": _f(avg.get("awayGoalAvgCnt"))},
        "h2hSummary": f"{h2h.get('winGoalMatchCnt', 0)}胜{h2h.get('drawMatchCnt', 0)}平"
                      f"{h2h.get('lossGoalMatchCnt', 0)}负" if h2h else None,
    }


def write_intel_entry(payload, code, day=None):
    """insight 摘要追加当日 intel 文件（同场重复拉取=多 entry，时序语义）。开发者 sszhang"""
    day = day or date.today().isoformat()
    path = _TRENDS_DIR / f"{day}-intel.json"
    _TRENDS_DIR.mkdir(parents=True, exist_ok=True)
    doc = {"date": day, "type": "intel-timeline", "schemaVersion": SCHEMA_VERSION, "entries": []}
    if path.exists():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log("trends", f"当日intel文件损坏，重置（{path.name}）")
    doc["entries"].append(extract_intel(payload, code))
    atomic_write_json(path, doc)
    log("trends", f"intel摘要 → {path.name}（累计 {len(doc['entries'])} 条）")
    return path


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

    # ---- write_snapshot（临时目录跑，不污染真 05-trends）----
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        real_dir = globals()["_TRENDS_DIR"]
        _set_trends_dir(Path(td))          # 测试钩子：重定向 TRENDS_DIR
        try:
            day = "2026-08-30"
            m1 = [{"code": "周六001", "matchId": 1, "league": "意甲", "home": "A", "away": "B",
                   "kickoff": "2026-08-30 19:00:00", "had": {"h": 2.0, "d": 3.1, "a": 3.5},
                   "hhad": {}, "crs": {"s01s00": 8.0}, "ttg": {}, "hafu": {}}]
            p = write_snapshot(m1, "run.py update", day)
            doc = json.loads(p.read_text(encoding="utf-8"))
            assert doc["schemaVersion"] == 1 and doc["snapshots"][0]["base"] is True
            assert len(doc["snapshots"][0]["matches"]) == 1
            # 二刷：无变化 → changes 为空的增量版
            write_snapshot(m1, "临场复扫", day)
            doc = json.loads(p.read_text(encoding="utf-8"))
            assert len(doc["snapshots"]) == 2 and doc["snapshots"][1]["base"] is False
            assert doc["snapshots"][1]["changes"] == [], doc["snapshots"][1]
            # 三刷：调价 → 只出该项
            m2 = [dict(m1[0])]
            m2[0]["crs"] = {"s01s00": 8.5}
            write_snapshot(m2, "run.py snapshot", day)
            doc = json.loads(p.read_text(encoding="utf-8"))
            assert doc["snapshots"][2]["changes"] == [{"code": "周六001", "crs": {"s01s00": 8.5}}]
            # 回放一致性：终态 == 三刷输入
            assert replay_odds(doc["snapshots"])["周六001"]["crs"]["s01s00"] == 8.5
            # 四刷重载：base保真回归（评审裁定——replay须深拷贝，不得就地污染历史快照）
            m3 = [dict(m1[0])]
            m3[0]["crs"] = {"s01s00": 9.0}
            write_snapshot(m3, "临场复扫", day)
            doc = json.loads(p.read_text(encoding="utf-8"))
            assert doc["snapshots"][0]["matches"][0]["crs"]["s01s00"] == 8.0, \
                "base快照须保持原始值（replay深拷贝防污染）"
            # 损坏降级：写坏文件 → 新 base 版 + .corrupt 备份
            p.write_text("{broken json", encoding="utf-8")
            write_snapshot(m1, "run.py update", day)
            doc = json.loads(p.read_text(encoding="utf-8"))
            assert doc["snapshots"][0]["base"] is True and len(doc["snapshots"]) == 1
            assert list(Path(td).glob("*.corrupt-*")), "损坏文件应有备份"
        finally:
            _set_trends_dir(real_dir)      # 恢复
    print("[selftest] write_snapshot OK")

    # ---- extract_intel + write_intel_entry ----
    insight_fx = {
        "fetchedAt": "2026-08-30", "source": "sporttery", "matchId": 2041147,
        "match": {"code": "周六027", "league": "意甲"},
        "standing": {"home": {"ranking": "8"}, "away": {"ranking": "16"}},
        "injuries": {
            "home": [{"name": "耶尔德兹", "pos": "前锋", "apps": 1, "starts": 1},
                     {"name": "替补X", "pos": "中场", "apps": 1, "starts": 0},
                     {"name": "布雷默", "pos": "后卫", "apps": 3, "starts": 3}],
            "away": [{"name": "Nicolussi", "pos": "中场", "apps": 0, "starts": 0}]},
        "form": {"goalAvg": {"homeGoalAvgCnt": "1.1", "awayGoalAvgCnt": "0.8"},
                 "last10HomeAway": {"homeWinGoalMatchCnt": 6, "homeDrawMatchCnt": 2, "homeLossGoalMatchCnt": 2,
                                     "awayWinGoalMatchCnt": 3, "awayDrawMatchCnt": 2, "awayLossGoalMatchCnt": 5}},
        "h2h": {"statistics": {"winGoalMatchCnt": 7, "drawMatchCnt": 2, "lossGoalMatchCnt": 1}},
    }
    e = extract_intel(insight_fx, "周六027")
    assert e["matchId"] == 2041147 and e["code"] == "周六027" and e["league"] == "意甲"
    assert e["rank"] == {"home": 8, "away": 16}, e["rank"]
    assert e["injuries"]["d"] == 2, e["injuries"]["d"]                # 3主-1客（布雷默入fixture）
    assert e["injuries"]["home"][0]["keyPlayer"] is False             # apps=1<2：开季小样本不判主力（探针修正）
    assert e["injuries"]["home"][1]["keyPlayer"] is False             # apps1/starts0
    assert e["injuries"]["home"][2]["keyPlayer"] is True              # apps3/starts3：主力（口径正例）
    assert e["form"]["homeLast10"] == "6胜2平2负" and e["form"]["awayLast10"] == "3胜2平5负"
    assert e["form"]["homeGoalAvg"] == 1.1 and e["form"]["awayGoalAvg"] == 0.8
    assert e["h2hSummary"] == "7胜2平1负"
    real_dir2 = globals()["_TRENDS_DIR"]
    with tempfile.TemporaryDirectory() as td:
        _set_trends_dir(Path(td))
        try:
            p = write_intel_entry(insight_fx, "周六027", day="2026-08-30")
            doc = json.loads(p.read_text(encoding="utf-8"))
            assert doc["type"] == "intel-timeline" and len(doc["entries"]) == 1
            assert doc["entries"][0]["fullFile"].endswith("sporttery_insight_2041147.json")
            write_intel_entry(insight_fx, "周六027", day="2026-08-30")   # 同场重复拉取=多entry
            assert len(json.loads(p.read_text(encoding="utf-8"))["entries"]) == 2
        finally:
            _set_trends_dir(real_dir2)
    print("[selftest] extract_intel + write_intel_entry OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
