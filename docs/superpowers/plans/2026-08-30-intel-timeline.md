# 赛前情报时序库（intel-timeline）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在数据获取点埋钩子自动落盘赛前情报时序（赔率 diff 链 / 情报摘要 / 临场扫描），回填时挂赛果对齐桥，为将来因子验证供原料。

**Architecture:** 新建纯函数库 `trends_snapshot.py`（提取/回放/diff/原子追加/校验），在 `sporttery_fetch.py` 两处数据获取点接线，`run.py` 加 `snapshot` 手动子命令，`backfill.py` 回填成功后挂 `preSnapshots` 指针（matchId 匹配，窗口=预测日±1 天）。

**Tech Stack:** Python 3 标准库（json/pathlib/datetime/itertools），零新增第三方依赖。无 pytest——`--selftest` 脚本自测（仓库惯例）。

**Spec:** `docs/2026-08-30-intel-timeline-design.html`（§4 schema / §5 改动清单 / §6 错误边界 / §10 ADR）

## Global Constraints

- 主入口命令一律 `python engine/scripts/run.py <子命令>`（仓库命令路径铁律）
- 输出 JSON：`ensure_ascii=False, indent=1`，文件尾换行（仓库惯例，tickets.json 除外它的 indent=2）
- 文件命名：`data/05-trends/YYYY-MM-DD-{odds|intel|livescan}.json`（日期开头，仓库惯例）
- `SCHEMA_VERSION = 1`；只加字段不删，旧文件不回改
- 枚举常量禁止魔法值：`THREAT_LEVELS`、`SCAN_TRIGGERS` 顶层次定义（用户编码约束）
- 原子写一律 `tmp` 后缀临时文件 + `Path.replace()`（ADR D7）
- 函数 docstring 标注开发者 `sszhang`（用户编码约束，仓库先例 `extract_odds`）
- 体彩赔率原始值为字符串（`"17.00"`），提取时转 `float`
- 不修改 `data/02-results/` 预测锁定字段，桥只增补 `preSnapshots` 字段（铁律 7）

---

### Task 1: trends_snapshot 模块骨架 + extract_odds 提取

**Files:**
- Create: `engine/scripts/trends_snapshot.py`
- Test: 同文件 `--selftest` 入口（仓库无 pytest，脚本自测惯例）

**Interfaces:**
- Consumes: 无（首任务）
- Produces: `extract_odds(matches: list[dict]) -> list[dict]`——输入 sporttery_matches.json 的 `matches` 数组（赔率为字符串），输出时序精简场记录（赔率 float，含五池+元数据）。后续 Task 2/3/6 依赖此签名。

- [ ] **Step 1: 写失败的自测（fixture 用真实数据形状）**

创建 `engine/scripts/trends_snapshot.py`，只含自测部分（实现先不写）：

```python
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
    raise NotImplementedError


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
```

- [ ] **Step 2: 跑自测确认失败**

Run: `cd /Users/zhangshensheng/Documents/GitHub/football/engine/scripts && python3 trends_snapshot.py --selftest`
Expected: FAIL——`NotImplementedError`

- [ ] **Step 3: 实现 extract_odds**

替换 `raise NotImplementedError` 为：

```python
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
            rec[pool] = {k: (_f(v) if pool != "hhad" or k != "goalLine" else _f(v))
                         for k, v in src.items() if v is not None}
        out.append(rec)
    return out
```

（注：goalLine 体彩也返回字符串数字如 `"-1"`，`_f` 统一转 float；`None` 池保留空 dict。）

- [ ] **Step 4: 跑自测确认通过**

Run: `cd /Users/zhangshensheng/Documents/GitHub/football/engine/scripts && python3 trends_snapshot.py --selftest`
Expected: `[selftest] extract_odds OK`

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/trends_snapshot.py
git commit -m "feat(trends): extract_odds 五池时序提取(含selftest)"
```

---

### Task 2: replay_odds 回放 + diff_odds 项级对比

**Files:**
- Modify: `engine/scripts/trends_snapshot.py`

**Interfaces:**
- Consumes: Task 1 的 `extract_odds` 输出结构（场记录含五池 dict）
- Produces: `replay_odds(snapshots: list[dict]) -> dict[str, dict]`（base+changes 应用 → `{code: 场记录}`）；`diff_odds(prev: dict[str, dict], new_matches: list[dict]) -> tuple[list[dict], list[str]]`（项级 changes + removed）。Task 3/8 依赖。

- [ ] **Step 1: 追加失败的自测（selftest 函数末尾加）**

```python
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
    new = [dict(state["周六001"]), dict(state["周日001"])]
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
```

- [ ] **Step 2: 跑自测确认失败**

Run: `cd /Users/zhangshensheng/Documents/GitHub/football/engine/scripts && python3 trends_snapshot.py --selftest`
Expected: FAIL——`NameError: name 'replay_odds' is not defined`

- [ ] **Step 3: 实现两个函数（插在 extract_odds 之后）**

```python
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
```

- [ ] **Step 4: 跑自测确认通过**

Run: `cd /Users/zhangshensheng/Documents/GitHub/football/engine/scripts && python3 trends_snapshot.py --selftest`
Expected: 两行 OK（`extract_odds OK` / `replay_odds + diff_odds OK`）

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/trends_snapshot.py
git commit -m "feat(trends): replay_odds回放+diff_odds项级diff(selftest三情形)"
```

---

### Task 3: write_snapshot 原子追加 + 损坏降级

**Files:**
- Modify: `engine/scripts/trends_snapshot.py`

**Interfaces:**
- Consumes: Task 1 `extract_odds`、Task 2 `replay_odds/diff_odds`
- Produces: `write_snapshot(extracted: list[dict], trigger: str, day: str | None = None) -> Path`——读当日文件（有→diff 追加 changes 版；无/损坏→新 base 版），原子写。Task 6 钩子①调用。附带 `atomic_write_json(path, data)` 工具函数（Task 4/5 复用）。

- [ ] **Step 1: 追加失败的自测**

```python
    # ---- write_snapshot（临时目录跑，不污染真 05-trends）----
    import tempfile
    import os
    with tempfile.TemporaryDirectory() as td:
        real_dir = globals().get("_TRENDS_DIR_OVERRIDE")
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
            # 损坏降级：写坏文件 → 新 base 版 + .corrupt 备份
            p.write_text("{broken json", encoding="utf-8")
            write_snapshot(m1, "run.py update", day)
            doc = json.loads(p.read_text(encoding="utf-8"))
            assert doc["snapshots"][0]["base"] is True and len(doc["snapshots"]) == 1
            assert list(Path(td).glob("*.corrupt-*")), "损坏文件应有备份"
        finally:
            _set_trends_dir(real_dir)      # 恢复
    print("[selftest] write_snapshot OK")
```

- [ ] **Step 2: 跑自测确认失败**

Run: `cd /Users/zhangshensheng/Documents/GitHub/football/engine/scripts && python3 trends_snapshot.py --selftest`
Expected: FAIL——`NameError: _set_trends_dir` / `write_snapshot`

- [ ] **Step 3: 实现（含测试钩子与原子写）**

```python
_TRENDS_DIR = TRENDS_DIR          # 可重定向（selftest 用；运行时不变）


def _set_trends_dir(p):
    """selftest 钩子：重定向时序库目录。开发者 sszhang"""
    globals()["_TRENDS_DIR"] = p


def _now_iso():
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
```

（`real_dir = globals().get("_TRENDS_DIR_OVERRIDE")` 在 Step 1 测试代码里应读 `globals()["_TRENDS_DIR"]`——实现时把测试里那行改为 `real_dir = globals()["_TRENDS_DIR"]`，保证 finally 恢复正确。）

- [ ] **Step 4: 跑自测确认通过**

Run: `cd /Users/zhangshensheng/Documents/GitHub/football/engine/scripts && python3 trends_snapshot.py --selftest`
Expected: 三行 OK

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/trends_snapshot.py
git commit -m "feat(trends): write_snapshot原子追加+损坏降级corrupt备份(selftest)"
```

---

### Task 4: extract_intel 情报摘要 + 追加

**Files:**
- Modify: `engine/scripts/trends_snapshot.py`

**Interfaces:**
- Consumes: Task 3 `atomic_write_json`；insight payload 结构（`sporttery_fetch.cmd_insight` 的落盘格式：`injuries.home/away[]` 含 `name/pos/apps/starts`、`standing.home/away.ranking`、`form.goalAvg/last10HomeAway`、`h2h.statistics`）
- Produces: `extract_intel(payload: dict, code: str) -> dict`；`write_intel_entry(payload: dict, code: str, day=None) -> Path`。Task 6 钩子②调用。

- [ ] **Step 1: 追加失败的自测（fixture 取自真实 insight 落盘形状）**

```python
    # ---- extract_intel + write_intel_entry ----
    insight_fx = {
        "fetchedAt": "2026-08-30", "source": "sporttery", "matchId": 2041147,
        "match": {"code": "周六027", "league": "意甲"},
        "standing": {"home": {"ranking": "8"}, "away": {"ranking": "16"}},
        "injuries": {
            "home": [{"name": "耶尔德兹", "pos": "前锋", "apps": 1, "starts": 1},
                     {"name": "替补X", "pos": "中场", "apps": 1, "starts": 0}],
            "away": [{"name": "Nicolussi", "pos": "中场", "apps": 0, "starts": 0}]},
        "form": {"goalAvg": {"homeGoalAvgCnt": "1.1", "awayGoalAvgCnt": "0.8"},
                 "last10HomeAway": {"homeWinGoalMatchCnt": 6, "homeDrawMatchCnt": 2, "homeLossGoalMatchCnt": 2,
                                     "awayWinGoalMatchCnt": 3, "awayDrawMatchCnt": 2, "awayLossGoalMatchCnt": 5}},
        "h2h": {"statistics": {"winGoalMatchCnt": 7, "drawMatchCnt": 2, "lossGoalMatchCnt": 1}},
    }
    e = extract_intel(insight_fx, "周六027")
    assert e["matchId"] == 2041147 and e["code"] == "周六027" and e["league"] == "意甲"
    assert e["rank"] == {"home": 8, "away": 16}, e["rank"]
    assert e["injuries"]["d"] == 1, e["injuries"]["d"]                # 2主-1客
    assert e["injuries"]["home"][0]["keyPlayer"] is True              # apps1/starts1：n不足但starts率1.0→?
    assert e["injuries"]["home"][1]["keyPlayer"] is False             # apps1/starts0
    assert e["form"]["homeLast10"] == "6胜2平2负" and e["form"]["awayLast10"] == "3胜2平5负"
    assert e["form"]["homeGoalAvg"] == 1.1 and e["form"]["awayGoalAvg"] == 0.8
    assert e["h2hSummary"] == "7胜2平1负"
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
```

（`real_dir2` 在 with 前取 `globals()["_TRENDS_DIR"]`；**keyPlayer 判定口径**：修正系数9 标准 `apps>=2 and starts/apps>=0.7`——fixture 里耶尔德兹 apps=1 不满足 apps>=2，上面断言 `is True` 是**故意错的探针**：实现时把断言改为符合口径的预期——耶尔德兹 `keyPlayer is False`（apps=1 < 2，开季小样本不判主力），并把 fixture 另加一条 `{"name": "布雷默", "pos": "后卫", "apps": 3, "starts": 3}` 断言 `is True`。写代码时以口径为准修正测试，勿迁就断言。）

- [ ] **Step 2: 跑自测确认失败**

Run: `cd /Users/zhangshensheng/Documents/GitHub/football/engine/scripts && python3 trends_snapshot.py --selftest`
Expected: FAIL——`NameError: extract_intel`

- [ ] **Step 3: 实现（keyPlayer 口径按修正系数9：apps>=2 且 starts/apps>=0.7）**

```python
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
```

同时把 Step 1 fixture/断言按口径修正（耶尔德兹 `is False` + 布雷默 `is True`）。

- [ ] **Step 4: 跑自测确认通过**

Run: `cd /Users/zhangshensheng/Documents/GitHub/football/engine/scripts && python3 trends_snapshot.py --selftest`
Expected: 四行 OK

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/trends_snapshot.py
git commit -m "feat(trends): extract_intel摘要(keyPlayer口径同修正系数9)+write_intel_entry"
```

---

### Task 5: write_livescan 校验落盘 + CLI

**Files:**
- Modify: `engine/scripts/trends_snapshot.py`

**Interfaces:**
- Consumes: Task 3 `atomic_write_json`；枚举 `THREAT_LEVELS/SCAN_TRIGGERS`
- Produces: `write_livescan(scan: dict, day=None) -> Path`（校验 trigger/threat/matchId 必填，非法即 raise ValueError）；CLI `python trends_snapshot.py livescan <scan.json>`（skill 会话录入入口）。Task 9 的 SKILL.md 纪律引用此命令。

- [ ] **Step 1: 追加失败的自测**

```python
    # ---- write_livescan 校验 ----
    scan_ok = {"trigger": "出票后监控", "verdict": "无真边际无可修订",
               "matches": [{"code": "周六027", "matchId": 2041147,
                            "tickets": [{"ticket": "T010", "pick": "0:1", "frozenOdds": 25.0}],
                            "oddsNow": {"crs": {"0:1": 25.0}},
                            "signals": {"oddsMoveVsFrozen": 0.0, "keyPlayerOut": {"team": "home", "player": "耶尔德兹"}},
                            "threat": "high", "note": "测试"}]}
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
        finally:
            _set_trends_dir(real_dir3)
    print("[selftest] write_livescan OK")
```

（`real_dir3` 同前取法。）

- [ ] **Step 2: 跑自测确认失败**

Run: `cd /Users/zhangshensheng/Documents/GitHub/football/engine/scripts && python3 trends_snapshot.py --selftest`
Expected: FAIL——`NameError: write_livescan`

- [ ] **Step 3: 实现（含 CLI 子命令）**

```python
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
    scan = {"at": _now_iso(), **scan}
    day = day or date.today().isoformat()
    path = _TRENDS_DIR / f"{day}-livescan.json"
    _TRENDS_DIR.mkdir(parents=True, exist_ok=True)
    doc = {"date": day, "type": "livescan", "schemaVersion": SCHEMA_VERSION, "scans": []}
    if path.exists():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log("trends", f"当日livescan文件损坏，重置（{path.name}）")
    doc["scans"].append(scan)
    atomic_write_json(path, doc)
    log("trends", f"livescan {len(scan['matches'])} 场 → {path.name}")
    return path
```

`__main__` 块扩展为：

```python
if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif len(sys.argv) >= 3 and sys.argv[1] == "livescan":
        write_livescan(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
    else:
        print(__doc__)
```

- [ ] **Step 4: 跑自测确认通过**

Run: `cd /Users/zhangshensheng/Documents/GitHub/football/engine/scripts && python3 trends_snapshot.py --selftest`
Expected: 五行 OK 全绿

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/trends_snapshot.py
git commit -m "feat(trends): write_livescan校验落盘(trigger/threat枚举+matchId必填)+CLI"
```

---

### Task 6: sporttery_fetch.py 钩子接线（①odds ②intel）

**Files:**
- Modify: `engine/scripts/sporttery_fetch.py`（主流程 L409-410 写盘后 + `cmd_insight` L70-75 落盘后）

**Interfaces:**
- Consumes: Task 3 `write_snapshot(extracted, trigger)`、Task 1 `extract_odds(matches)`、Task 4 `write_intel_entry(payload, code)`
- Produces: 刷新/insight 即自动落盘 05-trends（运行时行为，无新接口）

- [ ] **Step 1: 主流程接线（钩子①）**

在 `sporttery_fetch.py` 顶部 import 区（`from common import ...` 之后）加：

```python
from trends_snapshot import extract_odds as ts_extract_odds, write_snapshot
from trends_snapshot import write_intel_entry
```

在 `main()` 中 `OUT.write_text(...)` 与 `log("sporttery", f"{len(out_matches)} 场 → ...")` 两行之后（即"编号:"打印之前）插入：

```python
    # 钩子①：刷新即快照（intel-timeline 设计 §5；失败不阻断主流程）
    try:
        write_snapshot(ts_extract_odds(out_matches), "run.py update" if "-snapshot-marker" not in sys.argv else "run.py snapshot")
    except Exception as e:
        log("trends", f"odds快照失败(不阻断): {e}")
```

（注：trigger 来源此处统一写 `"run.py update"` 即可——直跑 `sporttery_fetch.py` 的场景绝大多数是 update/all 链；`run.py snapshot` 子命令的独立 trigger 由 Task 7 通过环境变量传递更繁琐，简化为同一枚举值 `"run.py update"` 不损失数据。**最终采用**：本处 trigger 固定 `"run.py update"`，Task 7 的 snapshot 子命令带环境变量 `TRENDS_TRIGGER=run.py snapshot`，此处读取 `os.environ.get("TRENDS_TRIGGER", "run.py update")`。实现按环境变量方案，需在文件头补 `import os`。）

- [ ] **Step 2: cmd_insight 接线（钩子②）**

在 `cmd_insight()` 的 `out.write_text(...)` 行之后插入：

```python
    # 钩子②：情报摘要落时序（intel-timeline 设计 §5；失败不阻断）
    try:
        write_intel_entry(payload, payload["match"]["code"])
    except Exception as e:
        log("trends", f"intel摘要失败(不阻断): {e}")
```

- [ ] **Step 3: 集成验证（真实 API）**

Run: `cd /Users/zhangshensheng/Documents/GitHub/football/engine/scripts && python3 sporttery_fetch.py && ls -la ../../data/05-trends/`
Expected: 刷新成功 + `2026-08-30-odds.json` 生成（base 版，~40+ 场五池）

再拉一场 insight 验证钩子②：
Run: `python3 sporttery_fetch.py insight 2041147 && python3 -c "import json;print(json.load(open('../../data/05-trends/2026-08-30-intel.json'))['entries'][-1]['code'])"`
Expected: `周六027`

再刷一次验证 diff：
Run: `python3 sporttery_fetch.py && python3 -c "import json;d=json.load(open('../../data/05-trends/2026-08-30-odds.json'));print('snaps:',len(d['snapshots']),'last changes:',len(d['snapshots'][-1].get('changes',[])))"`
Expected: `snaps: 2`，changes 为空或个位数（两次刷新间隔内调价稀疏）

- [ ] **Step 4: Commit**

```bash
git add engine/scripts/sporttery_fetch.py data/05-trends/
git commit -m "feat(trends): sporttery_fetch双钩子接线——刷新即odds快照/insight即摘要(失败不阻断)"
```

---

### Task 7: run.py snapshot 子命令

**Files:**
- Modify: `engine/scripts/run.py`（`main()` 子命令分支 + `__doc__` 用法行）

**Interfaces:**
- Consumes: `sporttery_fetch.py` CLI（刷新含钩子①）+ `engine/cache/sporttery_matches.json`（code→matchId 查询）
- Produces: `python run.py snapshot [--insight 周日004,周一002]`（手动补拍入口）

- [ ] **Step 1: 加子命令分支（`elif cmd == "all":` 之前插入）**

```python
    elif cmd == "snapshot":
        # intel-timeline 手动补拍：刷新触发钩子①（odds 全量快照）；--insight 按编号拉情报触发钩子②
        import os
        os.environ["TRENDS_TRIGGER"] = "run.py snapshot"
        sh("sporttery_fetch.py")
        if rest and rest[0] == "--insight" and len(rest) >= 2:
            cache = json.loads((ROOT_CACHE := __import__("pathlib").Path(__file__).parent.parent / "cache" /
                                "sporttery_matches.json").read_text(encoding="utf-8"))
            ids = {m["code"]: m["matchId"] for m in cache.get("matches") or [] if m.get("code")}
            for code in rest[1].split(","):
                mid = ids.get(code.strip())
                if mid:
                    sh("sporttery_fetch.py", "insight", str(mid))
                else:
                    log("run", f"{code} 不在售/无 matchId，跳过")
```

（文件头补 `import json`；`ROOT_CACHE` 的路径推导 = `engine/cache/`。实现时把海象表达式拆成普通两行赋值，可读性优先。）

`__doc__` 用法列表加一行：

```
  python run.py snapshot [--insight 周日004,周一002]  # 情报时序手动补拍（odds全量快照+可选情报）
```

- [ ] **Step 2: 集成验证（连跑两次验证 diff 链）**

Run: `cd /Users/zhangshensheng/Documents/GitHub/football && python3 engine/scripts/run.py snapshot && python3 engine/scripts/run.py snapshot && python3 -c "
import json
d = json.load(open('data/05-trends/2026-08-30-odds.json'))
print('版本数:', len(d['snapshots']))
for s in d['snapshots']:
    print(s['at'], s['trigger'], 'base' if s['base'] else f\"changes={len(s.get('changes', []))} removed={len(s.get('removed', []))}\")"`
Expected: 版本数 ≥ 2；末版 trigger=`run.py snapshot`，changes=0 或个位数

- [ ] **Step 3: Commit**

```bash
git add engine/scripts/run.py
git commit -m "feat(trends): run.py snapshot子命令——odds全量补拍+--insight按编号拉情报"
```

---

### Task 8: backfill.py 赛果对齐桥

**Files:**
- Modify: `engine/scripts/backfill.py`（`apply_pin_close` 调用后两处 + 新函数）

**Interfaces:**
- Consumes: Task 2 `replay_odds`；05-trends 文件命名约定
- Produces: `find_pre_snapshots(code: str, d: str) -> dict | None`——在预测日 ±1 天的 odds/intel 时序文件中找该场，返回 `{"matchId": int, "lastOddsAt": str, "lastIntelAt": str | None}`。backfill 回填成功处把它挂到 `rec["preSnapshots"]`。

- [ ] **Step 1: 写失败的自测（临时目录构造跨日场景）**

在 `trends_snapshot.py` 的 selftest 末尾追加（桥函数放 trends_snapshot 里，backfill 只调用——纯函数归属时序库，回填侧零逻辑）：

```python
    # ---- find_pre_snapshots 桥（跨日窗口: d-1/d/d+1）----
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
            assert find_pre_snapshots("不存在", "2026-08-29") is None
        finally:
            _set_trends_dir(real_dir4)
    print("[selftest] find_pre_snapshots OK")
```

（`real_dir4` 同前取法。断言要点：lastOddsAt 取的是 **d+1? 不——跨日窗口 d-1/d/d+1 里最后出现的版本**，本例 d=2026-08-29（预测日），08-30 的版本更晚且在窗口内 → lastOddsAt=08-30T00:30，即"赛前最后状态"。）

- [ ] **Step 2: 跑自测确认失败**

Run: `cd /Users/zhangshensheng/Documents/GitHub/football/engine/scripts && python3 trends_snapshot.py --selftest`
Expected: FAIL——`NameError: find_pre_snapshots`

- [ ] **Step 3: 在 trends_snapshot.py 实现**

```python
def find_pre_snapshots(code, d):
    """赛果对齐桥：在预测日 ±1 天的 odds/intel 时序里找该场赛前最后状态。

    匹配键=matchId（体彩编号每周复用，ADR D8）：odds 日首版带 code→matchId 映射，
    先解析 matchId 再对 intel 精确匹配。返回 {matchId, lastOddsAt, lastIntelAt} 或 None。
    开发者 sszhang
    """
    from datetime import timedelta
    base = date.fromisoformat(d)
    match_id, last_odds = None, None
    for delta in (-1, 0, 1):
        path = _TRENDS_DIR / f"{(base + timedelta(days=delta)).isoformat()}-odds.json"
        if not path.exists():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for s in doc.get("snapshots") or []:
            hit = (any(m.get("code") == code for m in s.get("matches") or [])
                   or any(c.get("code") == code for c in s.get("changes") or []))
            if hit:
                last_odds = s["at"]
                for m in s.get("matches") or []:
                    if m.get("code") == code and m.get("matchId"):
                        match_id = m["matchId"]
                for c in s.get("changes") or []:
                    if c.get("code") == code and c.get("matchId"):
                        match_id = c["matchId"]
    if match_id is None:
        return None
    last_intel = None
    for delta in (-1, 0, 1):
        path = _TRENDS_DIR / f"{(base + timedelta(days=delta)).isoformat()}-intel.json"
        if not path.exists():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for e in doc.get("entries") or []:
            if e.get("matchId") == match_id:
                last_intel = e["at"]
    return {"matchId": match_id, "lastOddsAt": last_odds, "lastIntelAt": last_intel}
```

- [ ] **Step 4: 跑自测确认通过**

Run: `cd /Users/zhangshensheng/Documents/GitHub/football/engine/scripts && python3 trends_snapshot.py --selftest`
Expected: 六行 OK 全绿

- [ ] **Step 5: backfill.py 挂桥（两处回填成功点）**

`backfill.py` 头部 import 区加：

```python
from trends_snapshot import find_pre_snapshots
```

链路 1（体彩对票成功）`apply_pin_close(rec, sp.get("matchDate") or d, ...)` 行之后加：

```python
            ps = find_pre_snapshots(rec.get("code"), d)
            if ps:
                rec.setdefault("preSnapshots", ps)   # 幂等：不覆盖已有指针
```

链路 2（ESPN 兜底成功）`apply_pin_close(rec, d, ...)` 行之后加同样三行。

（铁律 7：只增补字段不动锁定字段；`setdefault` 幂等不覆盖。）

- [ ] **Step 6: 集成验证（用已回填的历史轮重跑 backfill 的本地补算路径）**

Run: `cd /Users/zhangshensheng/Documents/GitHub/football && python3 engine/scripts/backfill.py 2026-08-29 && python3 -c "
import json
d = json.load(open('data/02-results/2026-08-29.json'))
ms = [m for m in d.get('matches', []) if m.get('preSnapshots')]
print('挂桥场次数:', len(ms))
for m in ms[:3]: print(m['code'], m['preSnapshots'])"`
Expected: 已完赛且 05-trends 有记录的场次挂上 `preSnapshots`（今晚 T010 四场若已完赛且在时序库中应有输出；未完赛场次跳过属正常）

- [ ] **Step 7: Commit**

```bash
git add engine/scripts/trends_snapshot.py engine/scripts/backfill.py data/02-results/
git commit -m "feat(trends): 赛果对齐桥——find_pre_snapshots(matchId键·±1天窗口)+backfill双链路挂preSnapshots"
```

---

### Task 9: SKILL.md + CLAUDE.md 纪律固化

**Files:**
- Modify: `skill/SKILL.md`（Step 1 / Step 6.5 / 文件存储路径表 三处）
- Modify: `.claude/CLAUDE.md`（目录结构 `data/05-trends/` 描述行）

**Interfaces:**
- Consumes: Task 5 CLI `python engine/scripts/trends_snapshot.py livescan <scan.json>`、Task 7 `run.py snapshot`
- Produces: 纪律文本（无代码接口）

- [ ] **Step 1: SKILL.md 文件存储路径表（05-trends 行替换）**

原行（`| \`data/05-trends/\` | 趋势发现 JSON | ...` 或相近）替换为：

```markdown
| `data/05-trends/` | **赛前情报时序库**（intel-timeline，docs/2026-08-30-intel-timeline-design.html）：`{date}-odds.json` 五池赔率 diff 增量链（刷新自动落盘）/ `{date}-intel.json` 情报摘要（insight 拉取自动落盘）/ `{date}-livescan.json` 临场扫描事件（skill 录入，`trends_snapshot.py livescan` 校验通道）；2026-08-29 前的 livescan 为 legacy 格式双格式兼容 | `YYYY-MM-DD-{odds\|intel\|livescan}.json` |
```

- [ ] **Step 2: SKILL.md Step 1 末尾加一行**

```markdown
- **刷新即快照 ★ v5.4**：任何路径刷新 `sporttery_matches.json` / 拉取 insight 自动落盘 `data/05-trends/` 时序（钩子内嵌，失败不阻断）；手动补拍 `python engine/scripts/run.py snapshot [--insight 编号,编号]`
```

- [ ] **Step 3: SKILL.md Step 6.5 修订纪律小节加一条**

```markdown
- **扫描必落盘 ★ v5.4**：临场扫描结果必须经 `python engine/scripts/trends_snapshot.py livescan <scan.json>` 录入 `data/05-trends/`（结构化 signals + threat 枚举 + matchId 必填），**禁止只留在对话里**——赔率状态停售后不可再生，扫描判断是将来因子验证的原始对账材料
```

- [ ] **Step 4: 项目 CLAUDE.md 目录结构 05-trends 行同步**

`.claude/CLAUDE.md` 中 `- \`data/05-trends/\`: 趋势发现 JSON` 替换为：

```markdown
- `data/05-trends/`: 赛前情报时序库 intel-timeline（odds 五池 diff 链/intel 摘要/livescan 扫描事件；刷新自动落盘+回填挂 preSnapshots 桥；设计=docs/2026-08-30-intel-timeline-design.html）
```

- [ ] **Step 5: 今晚 legacy livescan 追加 formatNote（不改历史字段，只补指针说明）**

`data/05-trends/2026-08-29-livescan.json` 的 `formatNote` 值末尾追加：`·正式schema=write_livescan通道(2026-08-30起)`（Edit 工具精确替换，不动其他字段）。

- [ ] **Step 6: 验证文本一致性**

Run: `grep -n "刷新即快照\|扫描必落盘\|intel-timeline" skill/SKILL.md .claude/CLAUDE.md`
Expected: 三处纪律文本 + 两处目录描述全部命中，无拼写漂移

- [ ] **Step 7: Commit**

```bash
git add skill/SKILL.md .claude/CLAUDE.md data/05-trends/2026-08-29-livescan.json
git commit -m "docs(skill): intel-timeline纪律固化——刷新即快照/扫描必落盘(livescan校验通道)/目录描述v5.4"
```

---

## 收尾（执行完所有 Task 后）

1. 全量自测：`python3 engine/scripts/trends_snapshot.py --selftest` → 六行 OK
2. 端到端：`python3 engine/scripts/run.py snapshot` → 05-trends 三类文件齐备可回放
3. 提交说明里引用 spec：`docs/2026-08-30-intel-timeline-design.html`

## Self-Review 记录（计划自审时填写）

- Spec 覆盖：§4.1 odds↔Task1-3 / §4.2 intel↔Task4 / §4.3 livescan↔Task5 / §5#2 钩子↔Task6 / §5#3 snapshot↔Task7 / §5#4 桥↔Task8 / §5#5 纪律↔Task9；§6 错误边界（损坏降级↔Task3、不阻断↔Task6、找不到省略↔Task8 setdefault-None 路径）；§7 测试（三情形↔Task2 selftest、连跑两次↔Task7 集成、桥↔Task8、校验↔Task5）；§8 跨年归档为运维策略非本期代码（spec §8 明示）。无缺口。
- 类型一致性：`extract_odds/replay_odds/diff_odds/write_snapshot/atomic_write_json/extract_intel/write_intel_entry/write_livescan/find_pre_snapshots` 各任务间签名一致（已逐一核对 Consumes/Produces 块）。
- 占位符：Task 4 Step 1 的"故意错的探针"注明了修正方式（以 keyPlayer 口径为准），非 TBD；Task 6/7 各有一处实现注记（环境变量方案/海象拆行），指令明确。
