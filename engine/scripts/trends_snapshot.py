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
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            backup = path.with_suffix(f"{path.suffix}.corrupt-{datetime.now():%H%M%S}")
            path.replace(backup)
            log("trends", f"当日odds文件损坏，降级新base版（备份 {backup.name}）")
            doc = {"date": day, "type": "odds-timeline", "schemaVersion": SCHEMA_VERSION, "snapshots": []}
    doc.setdefault("snapshots", [])   # 形状防御：重载文件缺键不崩（M1）
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
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            log("trends", f"当日intel文件损坏，重置（{path.name}）")
    doc.setdefault("entries", [])     # 形状防御：重载文件缺键不崩（M1）
    doc["entries"].append(extract_intel(payload, code))
    atomic_write_json(path, doc)
    log("trends", f"intel摘要 → {path.name}（累计 {len(doc['entries'])} 条）")
    return path


def write_livescan(scan, day=None):
    """livescan 扫描事件校验后追加当日文件（skill 临场扫描唯一合法录入通道，SKILL Step 6.5）。
    校验: trigger ∈ SCAN_TRIGGERS; 每场 matchId+code 必填, threat ∈ THREAT_LEVELS。
    开发者 sszhang"""
    if scan.get("trigger") not in SCAN_TRIGGERS:
        raise ValueError(f"trigger 非法: {scan.get('trigger')}（合法: {', '.join(SCAN_TRIGGERS)}）")
    for i, m in enumerate(scan.get("matches") or []):
        if not (m.get("matchId") and m.get("code")):
            raise ValueError(f"matches[{i}] 缺 matchId/code（桥按 matchId 对齐，必填）")
        if m.get("threat") not in THREAT_LEVELS:
            raise ValueError(f"matches[{i}].threat 非法: {m.get('threat')}（合法: {', '.join(THREAT_LEVELS)}）")
    scan = {"at": _now_iso(), **{k: v for k, v in scan.items() if k != "at"}}  # 调用方误传 at 一律被服务端时间覆盖
    day = day or date.today().isoformat()
    path = _TRENDS_DIR / f"{day}-livescan.json"
    _TRENDS_DIR.mkdir(parents=True, exist_ok=True)
    doc = {"date": day, "type": "livescan", "schemaVersion": SCHEMA_VERSION, "scans": []}
    if path.exists():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            log("trends", f"当日livescan文件损坏，重置（{path.name}）")
    doc.setdefault("scans", [])       # 形状防御：重载文件缺键不崩（M1）
    doc["scans"].append(scan)
    atomic_write_json(path, doc)
    log("trends", f"livescan {len(scan.get('matches') or [])} 场 → {path.name}")
    return path


def find_pre_snapshots(code, d):
    """赛果对齐桥：在比赛日 ±1 天的 odds/intel 时序里找该场赛前最后状态（spec 口径"比赛日±1天"）。

    两段式：第一遍宽窗 d-1..d+3（与 backfill 对票窗口一致）从时间线记录解析该 code 的
    kickoff 与 matchId；第二段以 kickoff 日期为中心扫 ±1 天文件取最后时点
    （odds 命中判定与 last_odds 取值都在这一段）；kickoff 解析不到时回退预测日 d±1。
    匹配键=matchId（体彩编号每周复用，ADR D8）：odds 日首版带 code→matchId 映射，
    先解析 matchId 再对 intel 精确匹配。返回 {matchId, lastOddsAt, lastIntelAt} 或 None。
    开发者 sszhang
    """
    from datetime import timedelta
    base = date.fromisoformat(d)
    match_id, kickoff = None, None
    for delta in (-1, 0, 1, 2, 3):                      # 第一遍：宽窗解析 kickoff/matchId
        path = _TRENDS_DIR / f"{(base + timedelta(days=delta)).isoformat()}-odds.json"
        if not path.exists():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        for s in doc.get("snapshots") or []:
            for m in s.get("matches") or []:
                if m.get("code") == code:
                    match_id = m.get("matchId") or match_id
                    kickoff = m.get("kickoff") or kickoff
            for c in s.get("changes") or []:
                if c.get("code") == code:
                    match_id = c.get("matchId") or match_id
                    kickoff = c.get("kickoff") or kickoff
    if match_id is None:
        return None
    try:
        center = date.fromisoformat(str(kickoff)[:10]) if kickoff else base
    except ValueError:
        center = base                                    # kickoff 畸形 → 回退预测日±1
    last_odds = None
    for delta in (-1, 0, 1):                             # 第二段：比赛日±1 取赛前最后时点
        path = _TRENDS_DIR / f"{(center + timedelta(days=delta)).isoformat()}-odds.json"
        if not path.exists():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        for s in doc.get("snapshots") or []:
            hit = (any(m.get("code") == code for m in s.get("matches") or [])
                   or any(c.get("code") == code for c in s.get("changes") or []))
            if hit:
                last_odds = s["at"]
    last_intel = None
    for delta in (-1, 0, 1):
        path = _TRENDS_DIR / f"{(center + timedelta(days=delta)).isoformat()}-intel.json"
        if not path.exists():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        for e in doc.get("entries") or []:
            if e.get("matchId") == match_id:
                last_intel = e["at"]
    return {"matchId": match_id, "lastOddsAt": last_odds, "lastIntelAt": last_intel}


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

    # ---- write_livescan 校验 ----
    scan_ok = {"trigger": "出票后监控", "verdict": "无真边际无可修订",
               "matches": [{"code": "周六027", "matchId": 2041147,
                            "tickets": [{"ticket": "T010", "pick": "0:1", "frozenOdds": 25.0}],
                            "oddsNow": {"crs": {"0:1": 25.0}},
                            "signals": {"oddsMoveVsFrozen": 0.0, "keyPlayerOut": {"team": "home", "player": "耶尔德兹"}},
                            "threat": "high", "note": "测试"}]}
    real_dir3 = globals()["_TRENDS_DIR"]
    with tempfile.TemporaryDirectory() as td:
        _set_trends_dir(Path(td))
        try:
            p = write_livescan(scan_ok, day="2026-08-30")
            assert json.loads(p.read_text(encoding="utf-8"))["scans"][0]["trigger"] == "出票后监控"
            for bad in ({"trigger": "胡乱触发", **{k: v for k, v in scan_ok.items() if k != "trigger"}},   # 非法trigger
                        {**scan_ok, "matches": [{**scan_ok["matches"][0], "threat": "极高"}]},              # 非法threat
                        {**scan_ok, "matches": [{k: v for k, v in scan_ok["matches"][0].items() if k != "matchId"}]}):  # 缺matchId
                try:
                    write_livescan(bad, day="2026-08-30")
                    assert False, "应抛 ValueError"
                except ValueError:
                    pass
            # 回归（评审裁定）：缺 matches 键的空扫描须写盘成功且不抛异常（写后 log 不得 KeyError）
            p2 = write_livescan({"trigger": "用户要求", "verdict": "无场次"}, day="2026-08-30")
            assert p2.exists()
        finally:
            _set_trends_dir(real_dir3)
    print("[selftest] write_livescan OK")

    # ---- find_pre_snapshots 桥（比赛日±1中心：宽窗解析 kickoff → 开球日±1 取值）----
    real_dir4 = globals()["_TRENDS_DIR"]
    with tempfile.TemporaryDirectory() as td:
        _set_trends_dir(Path(td))
        try:
            # 赛前夜 d-1 扫过（含 matchId），比赛日 d 又扫（调价）
            (Path(td) / "2026-08-29-odds.json").write_text(json.dumps({
                "date": "2026-08-29", "type": "odds-timeline", "schemaVersion": 1,
                "snapshots": [{"at": "2026-08-29T23:50:00+08:00", "trigger": "出票后监控", "base": True,
                                "matches": [{"code": "周六026", "matchId": 2041146, "league": "葡超",
                                              "home": "维塞乌", "away": "波尔图", "kickoff": "2026-08-30 01:00:00",
                                              "had": {"h": 11.25, "d": 5.6, "a": 1.16}, "hhad": {}, "crs": {"s01s00": 25.0},
                                              "ttg": {}, "hafu": {}}]}]}, ensure_ascii=False), encoding="utf-8")
            (Path(td) / "2026-08-30-odds.json").write_text(json.dumps({
                "date": "2026-08-30", "type": "odds-timeline", "schemaVersion": 1,
                "snapshots": [
                    {"at": "2026-08-30T00:30:00+08:00", "trigger": "临场复扫", "base": True,
                     "matches": [{"code": "周六026", "matchId": 2041146, "kickoff": "2026-08-30 01:00:00",
                                   "had": {"h": 11.5, "d": 5.7, "a": 1.15}, "hhad": {}, "crs": {"s01s00": 28.0},
                                   "ttg": {}, "hafu": {}}]}]}, ensure_ascii=False), encoding="utf-8")
            (Path(td) / "2026-08-29-intel.json").write_text(json.dumps({
                "date": "2026-08-29", "type": "intel-timeline", "schemaVersion": 1,
                "entries": [{"at": "2026-08-29T23:52:00+08:00", "matchId": 2041146, "code": "周六026"}]},
                ensure_ascii=False), encoding="utf-8")
            r = find_pre_snapshots("周六026", "2026-08-29")
            assert r == {"matchId": 2041146, "lastOddsAt": "2026-08-30T00:30:00+08:00",
                         "lastIntelAt": "2026-08-29T23:52:00+08:00"}, r
            # 窗口锚比赛日而非预测日：预测日早于全部 fixture 文件仍命中（kickoff=08-30 → 中心 08-30）
            r_early = find_pre_snapshots("周六026", "2026-08-28")
            assert r_early == {"matchId": 2041146, "lastOddsAt": "2026-08-30T00:30:00+08:00",
                               "lastIntelAt": "2026-08-29T23:52:00+08:00"}, r_early
            assert find_pre_snapshots("不存在", "2026-08-29") is None
        finally:
            _set_trends_dir(real_dir4)
    print("[selftest] find_pre_snapshots OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif len(sys.argv) >= 3 and sys.argv[1] == "livescan":
        write_livescan(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
    else:
        print(__doc__)
