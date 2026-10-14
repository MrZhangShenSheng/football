# 右尾肥尾场次模型（tail-model）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建大比分/冷门场次数据集（13515 场·骨架三轴标签+四族特征），训练 logistic 多头模型，以肥尾事件族 log-loss 对拍市场池聚合（判据预注册先行），产出可复评的 tail-model-report。

**Architecture:** 三层解耦——`tail_dataset.py`（构建器，幂等+版本指纹，产出 dataset.jsonl 凭证）→ `tail_train.py`（expanding walk-forward 11 段，logistic 三头 [tg softmax / 方向|tg / 零封|tg,方向] + HistGB exploratory 对照，配对 log-loss bootstrap CI）→ 判 PASS 才激活 tail_pick/影子层（本计划不含 tail_pick，二期）。防泄漏三道闸：as_of=开赛前一日、league 库时间线 lag=2 天、特征市场价只用 crsOpen（ttg/had 封盘只作对手盘）。

**Tech Stack:** Python3 + numpy + sklearn 1.9.1（LogisticRegression / HistGradientBoostingClassifier）+ pytest（engine/tests/ 惯例）。复用：`backtest_crs_batch.load_hist_window`（hist 装载）、`market_lambda.crs_power_devig`（CRS power 去水）、`strength_loaders.zh_to_id`（中文队名映射）。

**Spec:** `docs/2026-10-13-tail-model-design.html`（§0 死路边界 / §2 数据集 / §3 判据预注册 / §6 风险清单——执行者必读）

## Global Constraints

- **判据跑前冻结**：`engine/cache/tail-model-prereg.json` 必须先于一切模型代码 commit（Task 1）；跑后禁改口径（铁律 14②）；`tail_train.py` 启动时 `check_prereg()` 校验指纹一致才允许出报告
- **防泄漏三道闸**：as_of = 开赛日 + `DECISION_DAY_OFFSET=-1`；league/hst/elo 时间线只计 `date ≤ as_of − LEAK_LAG_DAYS(=2)` 天（elo 除外：elo_*_pre 是赛前值，`date ≤ as_of` 即可见，lag=0）；特征市场价只用 `crsOpen`，**ttg/had 封盘禁入特征**（留作判据对手盘）
- **主判据唯一**：E1=总进球≥5，logistic 三头 vs 市场 ttg devig 聚合（s5+s6+s7），全测试段配对 log-loss 差 bootstrap 95% CI 下限>0；E2=12 精确格 \|h−a\|≥3、E3=3 精确格 0:3/0:4/0:5 为次判据（**双侧同 0..5 网格口径**，outcome 同网格）；HistGB 只 exploratory 不进任何判据
- **变量命名 camelCase，常量 UPPER_SNAKE_CASE，开发者注释署名 sszhang**；null 特征=训练窗均值填充 + 缺失计数 2 特征（不逐列 mask）
- dataset.jsonl 与 meta.json 纳入 git（凭证纪律同 qcache）；内部文件 JSON/纯文本（铁律 9），报告 HTML 带 `<meta charset="utf-8">`（铁律 6.5）
- 数据集行为源：`backtest_crs_batch.load_hist_window(date_lo, date_hi)`（已存在，行结构 `{date, code, league, home, away, score, had, ttg, crs, crsOpen}`，crs/crsOpen 均 ≥20 项才收）

## 数据源速查（执行者必读）

| 源 | 路径 | 结构 |
|:---|:---|:---|
| hist_odds | `engine/cache/hist_odds/crs_hist_*.json` | `{matches:[{date, code, league(中文), home/away(中文), score "h:a", had{h,d,a}, ttg{"0".."7"}, crs(封盘31项), crsOpen}]}`，crs 键 `"5:0"`/`"other_h"` |
| league 库 | `data/02-results/league/{lg}_matches.json` | **list 直存** `[{date, home, away, hg, ag}]`，队名=规范ID（如 "fulham"），34 库 43259 场 |
| hst 射正 | `engine/cache/hst_{lg}.json` | `{rows:[{date, home, away, hst, ast}]}`，16 库 20825 场 |
| elo | `engine/cache/elo_history_{lg}_GLOBAL.json` | `{rows:[{date, home, away, elo_home_pre, elo_away_pre, result}]}`，赛前值 lag=0 干净 |
| 队名映射 | `strength_loaders.load_aliases()` + `sl.zh_to_id(aliases)` | hist 中文名 → 规范ID（实测映射率 86.8%） |

**复用函数签名**：
- `backtest_crs_batch.load_hist_window(date_lo, date_hi, *, hist_dir=None) -> list[dict]`
- `market_lambda.crs_power_devig(crs: dict) -> dict`（输入 pick 键池 → 输出 **sXXsYY 键**分布，other→`s1sh/s1sd/s1sa`；池 <20 项返回 `{}`）
- `strength_chain_eval._boot_ci_diff(diffs, seed, n_boot) -> tuple[float, float]`（配对差均值 bootstrap 95% CI）

---

### Task 1: 预注册判据文件冻结

**Files:**
- Create: `engine/cache/tail-model-prereg.json`

**Interfaces:**
- Produces: prereg JSON——`tail_train.py` 的 `check_prereg()` 校验源（Task 6 消费）；`frozenParams` 各值即下方 Global Constraints 常量

- [ ] **Step 1: 写 prereg JSON（全文如下，逐字落盘）**

```json
{
  "registeredAt": "2026-10-13",
  "spec": "右尾肥尾场次模型 v1（docs/2026-10-13-tail-model-design.html）",
  "dataset": {
    "source": "engine/cache/hist_odds crs_hist_*.json 全量",
    "loader": "backtest_crs_batch.load_hist_window（crs+crsOpen 均≥20项）",
    "range": "2023-09-01 ~ 2026-09-28"
  },
  "labels": {
    "tgBin": "min(h+a,7) 共8档",
    "direction": "主胜0/平1/客胜2",
    "cleanSheet": "客零封0(h=0,a>0)/主零封1(a=0,h>0)/无2(双方进球或0:0)"
  },
  "features": {
    "marketAnchor": "crsOpen 派生 11 维——唯一市场特征源；ttg/had 封盘禁入特征（留作对手盘）",
    "teamAsof": "近10/38场 6统计(gf/ga/tg4率/blowout率/零封率/被零封率)×3数(主/客/差) + rest 2维 = 38维",
    "hstElo": "hst 近20场场均射正 for/against 4维 + elo_diff 1维",
    "ctx": "联赛模板3维(大球率/屠杀率/客零封大胜率) + 月份sin/cos 2维",
    "impute": "null→训练窗列均值填充 + 缺失计数2特征(nNullTeam/nNullOther)"
  },
  "leakage": {
    "decisionDayOffset": -1,
    "leagueLagDays": 2,
    "eloLagDays": 0,
    "featurePrice": "crsOpen only"
  },
  "folds": {
    "scheme": "expanding walk-forward",
    "testSpanMonths": 2,
    "firstTest": "2025-01",
    "lastTest": "2026-09-28",
    "nFolds": 11,
    "scalerFit": "train-only",
    "standardize": true
  },
  "primaryCriterion": {
    "event": "E1_tg_ge5",
    "model": "logistic_heads",
    "baseline": "market_ttg_power_devig(s5+s6+s7)",
    "metric": "paired log-loss diff (market − model，正=模型更准)",
    "ci": "bootstrap95 B=2000",
    "pass": "CI 下限 > 0",
    "pooling": "全测试段逐场配对汇总"
  },
  "secondary": [
    {
      "event": "E2_exact_diff3_cells",
      "cells": ["3:0", "4:0", "4:1", "5:0", "5:1", "5:2", "0:3", "0:4", "1:4", "0:5", "1:5", "2:5"],
      "model": "logistic_heads 骨架连乘合成（同12格）",
      "baseline": "market crs_power_devig 封盘同12格聚合",
      "outcome": "赛果比分∈同12格（双侧同 0..5 网格口径）",
      "metric": "同主判据"
    },
    {
      "event": "E3_exact_away_cs_cells",
      "cells": ["0:3", "0:4", "0:5"],
      "model": "logistic_heads 合成（同3格）",
      "baseline": "market 同3格聚合",
      "outcome": "赛果比分∈同3格",
      "metric": "同主判据"
    }
  ],
  "exploratoryOnly": ["HistGB E1 对照", "featMask 分层（映射/未映射）", "回收率模拟", "分联赛", "分月", "other 全口径"],
  "redlines": [
    "任何回收率模拟为正 → 先 leak-audit+随机基线自证伪（铁律14③）再上报",
    "主判据 FAIL → tail_pick/影子层不激活，数据集保留为资产，负结果归档",
    "主判据 PASS → 影子层 50 票 forward，不直接入生产链（铁律15 准入制）"
  ],
  "frozenParams": {
    "C": 1.0,
    "max_iter": 2000,
    "leak_lag_days": 2,
    "decision_day_offset": -1,
    "boot_B": 2000,
    "seed": 20261014,
    "stats_windows": [10, 38],
    "min_window_n": 5,
    "hst_window": 20,
    "min_pool_items": 20,
    "logloss_clip": 1e-06,
    "histgb": {"max_iter": 300, "learning_rate": 0.06}
  }
}
```

- [ ] **Step 2: Commit（判据凭证，先于一切模型代码）**

```bash
git add engine/cache/tail-model-prereg.json
git commit -m "prereg(右尾): 肥尾场次模型判据冻结——E1主判据CI下限>0·E2/E3精确格同网格·HistGB仅exploratory"
```

---

### Task 2: 骨架标签 + 市场锚特征（tail_dataset.py 第一块）

**Files:**
- Create: `engine/scripts/tail_dataset.py`
- Test: `engine/tests/test_tail_dataset.py`

**Interfaces:**
- Consumes: `market_lambda.crs_power_devig`（crsOpen pick 键池 → sXXsYY 键分布）
- Produces: `label_skeleton(score: str) -> dict`（`{tg, tgBin, direction, cleanSheet}`，int 值）；`market_anchor_features(row: dict) -> dict`（11 维 float，池不足返回 `{}`）；常量 `TG_BIN_MAX=7, MIN_POOL_ITEMS=20`（后续任务 import）

- [ ] **Step 1: 写失败测试**

```python
# engine/tests/test_tail_dataset.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from tail_dataset import label_skeleton, market_anchor_features


def _full_pool(hi=8.0):
    pool = {f"{i}:{j}": hi for i in range(3) for j in range(3)}
    pool.update({"other_h": 40.0, "other_d": 40.0, "other_a": 40.0})
    pool.update({f"{i}:{j}": 30.0 for i in range(3, 6) for j in range(3)})
    pool.update({f"{i}:{j}": 30.0 for i in range(3) for j in range(3, 6)})
    return pool


def test_label_skeleton_axes():
    assert label_skeleton("2:1") == {"tg": 3, "tgBin": 3, "direction": 0, "cleanSheet": 2}
    assert label_skeleton("0:5") == {"tg": 5, "tgBin": 5, "direction": 2, "cleanSheet": 0}
    assert label_skeleton("3:0") == {"tg": 3, "tgBin": 3, "direction": 0, "cleanSheet": 1}
    assert label_skeleton("0:0") == {"tg": 0, "tgBin": 0, "direction": 1, "cleanSheet": 2}  # 0:0 归"无"
    assert label_skeleton("2:6")["tgBin"] == 7        # tg=8 截断到 7+


def test_market_anchor_dimensions_and_direction():
    row = {"crsOpen": _full_pool()}
    f = market_anchor_features(row)
    assert len(f) == 11
    assert 0.0 < f["mkt_p_home_open"] < 1.0
    assert f["mkt_p_tg_ge5_open"] > 0.0
    # 0:0 赔率调最低 → 三向里平局聚合应最大
    row2 = {"crsOpen": {**_full_pool(), "0:0": 1.5}}
    f2 = market_anchor_features(row2)
    assert f2["mkt_p_draw_open"] > f2["mkt_p_home_open"]


def test_market_anchor_insufficient_pool():
    assert market_anchor_features({"crsOpen": {"1:1": 7.0}}) == {}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest engine/tests/test_tail_dataset.py -v`
Expected: FAIL（ModuleNotFoundError: tail_dataset）

- [ ] **Step 3: 实现（文件头+常量+两函数）**

```python
# engine/scripts/tail_dataset.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tail_dataset.py — 右尾肥尾场次数据集构建器（spec docs/2026-10-13-tail-model-design.html）。

防泄漏三道闸：as_of=开赛前一日；league/hst 时间线 lag=2 天（elo 赛前值 lag=0）；
特征市场价只用 crsOpen——ttg/had 封盘只作判据对手盘，禁入特征。
判据预注册 engine/cache/tail-model-prereg.json（跑前冻结·铁律14②）。
开发者 sszhang"""
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import market_lambda as mkl

TG_BIN_MAX = 7                 # tgBin = min(h+a, 7)，7 档=7+球
MIN_POOL_ITEMS = 20            # 与 backtest_crs_batch 同源
LEAK_LAG_DAYS = 2              # league/hst 时间线滞后（铁律14①语义）
DECISION_DAY_OFFSET = -1       # 决策时点=开赛前一日
FEATURE_VERSION = "tail-v1"    # 特征清单版本（meta.json 冻结用）


def label_skeleton(score: str) -> dict:
    """比分 → 骨架三轴标签。cleanSheet：0=客零封 1=主零封 2=无（0:0 归无·口径冻结 prereg）。
    开发者 sszhang"""
    h, a = (int(x) for x in score.split(":"))
    tg = h + a
    if h > a:
        direction, cs = 0, (1 if a == 0 else 2)
    elif h == a:
        direction, cs = 1, 2
    else:
        direction, cs = 2, (0 if h == 0 else 2)
    return {"tg": tg, "tgBin": min(tg, TG_BIN_MAX), "direction": direction, "cleanSheet": cs}


def market_anchor_features(row: dict) -> dict:
    """crsOpen 全套派生 11 维（唯一市场特征源·开盘口径=决策时点合法）。

    三向聚合含 other 归侧；tg 矩/肥尾质量/熵只计精确键（other 不摊·口径冻结）。"""
    pool = row.get("crsOpen") or {}
    if len(pool) < MIN_POOL_ITEMS:
        return {}
    p = mkl.crs_power_devig(pool)
    if not p:
        return {}
    exact, p_home, p_draw, p_away, p_other = [], 0.0, 0.0, 0.0, 0.0
    ent = 0.0
    for k, v in p.items():
        if k in ("s1sh", "s1sd", "s1sa"):
            p_other += v
            if k == "s1sh":
                p_home += v
            elif k == "s1sd":
                p_draw += v
            else:
                p_away += v
        elif k.startswith("s") and len(k) == 6 and k[3] == "s":
            h, a = int(k[1:3]), int(k[4:6])
            exact.append((h, a, v))
            if h > a:
                p_home += v
            elif h == a:
                p_draw += v
            else:
                p_away += v
        if v > 0:
            ent -= v * math.log(v)
    if not exact:
        return {}
    e_tg = sum((h + a) * v for h, a, v in exact)
    p_ge4 = sum(v for h, a, v in exact if h + a >= 4)
    p_ge5 = sum(v for h, a, v in exact if h + a >= 5)
    p_diff3 = sum(v for h, a, v in exact if abs(h - a) >= 3)
    p_away_big = sum(v for h, a, v in exact if h == 0 and a >= 3)
    return {
        "mkt_e_tg_open": e_tg,
        "mkt_p_tg_ge4_open": p_ge4,
        "mkt_p_tg_ge5_open": p_ge5,
        "mkt_p_diff_ge3_open": p_diff3,
        "mkt_p_away_cs_big_open": p_away_big,
        "mkt_p_home_open": p_home,
        "mkt_p_draw_open": p_draw,
        "mkt_p_away_open": p_away,
        "mkt_p_max_side_open": max(p_home, p_draw, p_away),
        "mkt_entropy_open": ent,
        "mkt_other_mass_open": p_other,
    }
```

- [ ] **Step 4: 跑测试通过**

Run: `python3 -m pytest engine/tests/test_tail_dataset.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/tail_dataset.py engine/tests/test_tail_dataset.py
git commit -m "feat(右尾): 骨架三轴标签+市场锚特征11维——crsOpen唯一市场源·口径冻结prereg"
```

---

### Task 3: LeagueIndex + 球队 as-of 特征（tail_dataset.py 第二块）

**Files:**
- Modify: `engine/scripts/tail_dataset.py`（追加）
- Test: `engine/tests/test_tail_dataset.py`（追加）

**Interfaces:**
- Consumes: league 库 `data/02-results/league/{lg}_matches.json`（list 直存 `[{date, home, away, hg, ag}]`）
- Produces: `LeagueIndex(league_dir: Path, gf_field="hg", ga_field="ag")`（`.window(team, as_of, n) -> list[dict]`，行 `{date, gf, ga, isHome}`，lag 内建）；`team_asof_features(idx, home_id, away_id, as_of) -> dict`（38 维，值可 None）；常量 `STATS_WINDOWS=(10,38), MIN_WINDOW_N=5`

- [ ] **Step 1: 写失败测试**

```python
# 追加到 engine/tests/test_tail_dataset.py
import json as _json
from tail_dataset import LeagueIndex, team_asof_features, LEAK_LAG_DAYS


def _mini_league(tmp_path):
    rows = []
    # arsenal 主队 3 场：10-01 大胜 4:0、10-08 大球 3:3、10-15 零封 2:0
    for d, (hg, ag) in [("2025-10-01", (4, 0)), ("2025-10-08", (3, 3)), ("2025-10-15", (2, 0))]:
        rows.append({"date": d, "home": "arsenal", "away": "chelsea", "hg": hg, "ag": ag})
    # chelsea 主场 2025-10-05 被 0:3
    rows.append({"date": "2025-10-05", "home": "chelsea", "away": "liverpool", "hg": 0, "ag": 3})
    p = tmp_path / "england-premier_matches.json"
    p.write_text(_json.dumps(rows), encoding="utf-8")
    return tmp_path


def test_window_respects_lag_and_sides(tmp_path):
    idx = LeagueIndex(_mini_league(tmp_path))
    # as_of=10-16，lag=2 → cutoff=10-14，arsenal 可见 10-01/10-08 两场
    ms = idx.window("arsenal", "2025-10-16", 10)
    assert [m["date"] for m in ms] == ["2025-10-01", "2025-10-08"]
    assert ms[0]["gf"] == 4 and ms[0]["isHome"]
    # chelsea 同 cutoff：10-01(客 0:4→gf0 ga4) + 10-05(主 0:3)
    mc = idx.window("chelsea", "2025-10-16", 10)
    assert len(mc) == 2 and mc[1]["gf"] == 0 and mc[1]["ga"] == 3


def test_team_asof_features_dims_and_null(tmp_path):
    idx = LeagueIndex(_mini_league(tmp_path))
    # 窗不足 MIN_WINDOW_N=5 → 38 维全 None 但键齐
    f = team_asof_features(idx, "arsenal", "chelsea", "2025-10-16")
    assert len(f) == 38
    assert all(v is None for v in f.values())
    # 造足 6 场后统计非空：arsenal 打了 3 场不够——用 liverpool 视角测正常路径
    rows = [{"date": f"2025-09-{i:02d}", "home": "arsenal", "away": "chelsea", "hg": 2, "ag": 1}
            for i in range(1, 7)]
    p2 = tmp_path / "spain-laliga_matches.json"
    p2.write_text(_json.dumps(rows + [{"date": "2025-09-07", "home": "x", "away": "arsenal", "hg": 0, "ag": 5}]), encoding="utf-8")
    f2 = team_asof_features(idx, "chelsea", "arsenal", "2025-09-08")
    assert f2["h_gf_w10"] is not None and abs(f2["h_gf_w10"] - 1.0) < 1e-9   # chelsea 6 场全客负 1 球
    assert f2["a_gf_w10"] is not None                                       # arsenal 6+1 场
    assert f2["d_gf_w10"] == f2["h_gf_w10"] - f2["a_gf_w10"]
```

- [ ] **Step 2: 跑确认失败**

Run: `python3 -m pytest engine/tests/test_tail_dataset.py -v`
Expected: 新增 2 条 FAIL（ImportError: LeagueIndex）

- [ ] **Step 3: 实现（追加到 tail_dataset.py）**

```python
# ── league/hst 时间线索引（lag 内建·bisect 查窗）────────────────────────────
import bisect

STATS_WINDOWS = (10, 38)
MIN_WINDOW_N = 5
TEAM_STAT_KEYS = ("gf", "ga", "tg4", "blow", "clean", "beCleaned")


class LeagueIndex:
    """league 库（或 hst 缓存）→ per-team 按日期索引；window() 内建 LEAK_LAG_DAYS 截断。

    gf_field/ga_field 参数化：league 库用 hg/ag（进球），hst 缓存用 hst/ast（射正）。"""

    def __init__(self, league_dir: Path, gf_field: str = "hg", ga_field: str = "ag"):
        self._by_team: dict[str, dict] = {}
        for f in sorted(Path(league_dir).glob("*_matches.json")):
            doc = json.loads(f.read_text(encoding="utf-8"))
            rows = doc if isinstance(doc, list) else (doc.get("rows") or [])
            for r in rows:
                d = str(r.get("date") or "")
                gf, ga = r.get(gf_field), r.get(ga_field)
                if not d or gf is None or ga is None:
                    continue
                for team, gfor, gaga, is_home in ((r.get("home"), gf, ga, True),
                                                  (r.get("away"), ga, gf, False)):
                    slot = self._by_team.setdefault(team, {"dates": [], "rows": []})
                    slot["dates"].append(d)
                    slot["rows"].append({"date": d, "gf": int(gfor), "ga": int(gaga),
                                         "isHome": is_home})
        for slot in self._by_team.values():
            order = sorted(range(len(slot["dates"])), key=lambda i: slot["dates"][i])
            slot["dates"] = [slot["dates"][i] for i in order]
            slot["rows"] = [slot["rows"][i] for i in order]

    def window(self, team: str, as_of: str, n: int) -> list[dict]:
        """as_of（含）前 LEAK_LAG_DAYS 天内可见的最近 n 场（铁律14①语义）。"""
        cutoff = (date.fromisoformat(as_of) - timedelta(days=LEAK_LAG_DAYS)).isoformat()
        slot = self._by_team.get(team)
        if not slot:
            return []
        hi = bisect.bisect_right(slot["dates"], cutoff)
        return slot["rows"][max(0, hi - n):hi]

    def last_date_before(self, team: str, as_of: str) -> str | None:
        """最近一场日期（rest 天数用；同 lag 截断）。"""
        w = self.window(team, as_of, 1)
        return w[-1]["date"] if w else None


def _window_stats(matches: list[dict]) -> dict | None:
    n = len(matches)
    if n < MIN_WINDOW_N:
        return None
    tg4 = sum(1 for m in matches if m["gf"] + m["ga"] >= 4)
    blow = sum(1 for m in matches if abs(m["gf"] - m["ga"]) >= 3)
    clean = sum(1 for m in matches if m["ga"] == 0)
    be_cleaned = sum(1 for m in matches if m["gf"] == 0)
    return {"gf": sum(m["gf"] for m in matches) / n,
            "ga": sum(m["ga"] for m in matches) / n,
            "tg4": tg4 / n, "blow": blow / n,
            "clean": clean / n, "beCleaned": be_cleaned / n}


def team_asof_features(idx: LeagueIndex, home_id: str | None, away_id: str | None,
                       as_of: str) -> dict:
    """两队近10/38场 6统计×(主/客/差) + rest 2维 = 38 维。任一队窗不足→该窗段全 None
    （训练侧 train-mean 填充+nNullTeam 计数·prereg features.impute）。开发者 sszhang"""
    out: dict[str, float | None] = {}
    stats = {}
    for side, team in (("h", home_id), ("a", away_id)):
        stats[side] = {}
        for w in STATS_WINDOWS:
            stats[side][w] = _window_stats(idx.window(team, as_of, w)) if team else None
    for w in STATS_WINDOWS:
        for k in TEAM_STAT_KEYS:
            hs, as_ = stats["h"][w], stats["a"][w]
            hv = hs[k] if hs else None
            av = as_[k] if as_ else None
            out[f"h_{k}_w{w}"] = hv
            out[f"a_{k}_w{w}"] = av
            out[f"d_{k}_w{w}"] = (hv - av) if (hv is not None and av is not None) else None
    for side, team in (("h", home_id), ("a", away_id)):
        last = idx.last_date_before(team, as_of) if team else None
        out[f"rest_{side}"] = ((date.fromisoformat(as_of) - date.fromisoformat(last)).days
                               if last else None)
    return out
```

- [ ] **Step 4: 跑测试通过**

Run: `python3 -m pytest engine/tests/test_tail_dataset.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/tail_dataset.py engine/tests/test_tail_dataset.py
git commit -m "feat(右尾): LeagueIndex时间线(lag内建)+球队as-of特征38维——窗不足诚实None"
```

---

### Task 4: hst/elo/上下文特征（tail_dataset.py 第三块）

**Files:**
- Modify: `engine/scripts/tail_dataset.py`（追加）
- Test: `engine/tests/test_tail_dataset.py`（追加）

**Interfaces:**
- Consumes: `engine/cache/hst_{lg}.json`（rows {date, home, away, hst, ast}）；`engine/cache/elo_history_{lg}_GLOBAL.json`（rows {date, home, away, elo_home_pre, elo_away_pre}，赛前值 lag=0）
- Produces: `build_hst_index(cache_dir) -> LeagueIndex`（gf_field="hst", ga_field="ast"）；`build_elo_index(cache_dir) -> EloIndex`（`.elo(team, as_of) -> float | None`）；`TAIL_LEAGUE_MAP: dict[str, str]`（hist 中文名→库 key）；`ctx_features(lg_idx, league_zh, as_of) -> dict`（5 维）；常量 `HST_WINDOW=20`

- [ ] **Step 1: 写失败测试**

```python
# 追加
from tail_dataset import (build_hst_index, build_elo_index, EloIndex,
                          ctx_features, TAIL_LEAGUE_MAP)


def test_hst_index_reads_shots(tmp_path):
    (tmp_path / "hst_england-premier.json").write_text(_json.dumps(
        {"rows": [{"date": "2025-10-01", "home": "arsenal", "away": "chelsea", "hst": 8, "ast": 1}]}),
        encoding="utf-8")
    idx = build_hst_index(tmp_path)
    ms = idx.window("arsenal", "2025-10-05", 5)
    assert ms and ms[0]["gf"] == 8 and ms[0]["ga"] == 1   # gf=射正


def test_elo_index_pre_match_value(tmp_path):
    (tmp_path / "elo_history_england-premier_GLOBAL.json").write_text(_json.dumps(
        {"rows": [{"date": "2025-10-01", "home": "arsenal", "away": "chelsea",
                   "elo_home_pre": 1600.0, "elo_away_pre": 1500.0}]}), encoding="utf-8")
    ei = build_elo_index(tmp_path)
    assert ei.elo("arsenal", "2025-10-01") == 1600.0     # 当日赛前值可见（lag=0）
    assert ei.elo("arsenal", "2025-09-30") is None       # 无更早值 → None
    assert ei.elo("chelsea", "2025-10-03") == 1500.0


def test_ctx_features_template_and_month(tmp_path):
    _mini_league(tmp_path)                                # arsenal/chelsea 4 场小库
    idx = LeagueIndex(tmp_path)
    f = ctx_features(idx, "英超", "2025-10-16")
    assert f["lg_p_tg4"] is not None and 0.0 <= f["lg_p_tg4"] <= 1.0
    assert f["lg_p_blow"] is not None
    assert -1.0 <= f["ctx_month_sin"] <= 1.0
    # 未映射联赛（如澳超）→ 模板 None 但月份特征仍在
    f2 = ctx_features(idx, "澳超", "2025-10-16")
    assert f2["lg_p_tg4"] is None and f2["ctx_month_sin"] is not None
    assert "英超" in TAIL_LEAGUE_MAP
```

- [ ] **Step 2: 跑确认失败**

Run: `python3 -m pytest engine/tests/test_tail_dataset.py -v`
Expected: 新增 3 FAIL（ImportError: build_hst_index）

- [ ] **Step 3: 实现（追加）**

```python
# ── hst / elo / 上下文 ────────────────────────────────────────────────────
HST_WINDOW = 20
HST_KEY = "hst_{lg}.json"
ELO_GLOB = "elo_history_*_GLOBAL.json"
# hist 中文名 → 库 key（模板特征路由；未收录→模板 None 诚实降级）
TAIL_LEAGUE_MAP = {
    "英超": "england-premier", "英冠": "england-championship", "西甲": "spain-laliga",
    "西乙": "spain-liga2", "意甲": "italy-serie-a", "意乙": "italy-serie-b",
    "德甲": "germany-bundesliga", "德乙": "germany-bundesliga2", "法甲": "france-ligue1",
    "法乙": "france-ligue2", "荷甲": "netherlands-eredivisie", "葡超": "portugal-primeira",
    "土超": "turkey-super-lig", "希超": "greece-super", "比甲": "belgium-first-a",
    "日职": "japan", "韩职": "korea", "美职": "usa", "挪超": "norway", "瑞超": "sweden",
    "丹超": "denmark", "沙特": "saudi", "巴西": "brazil", "欧冠": "uefa-champions",
    "欧联": "uefa-europa", "欧国联": "uefa-nations", "世预赛": "world-cup-qual",
    "世界杯": "world-cup", "解放者杯": "libertadores", "亚冠精英": "asian-u23",
}


def build_hst_index(cache_dir: Path) -> LeagueIndex:
    """hst_{lg}.json 族 → LeagueIndex（gf=射正 hst·ga=被射正 ast）。

    文件名 pattern 与 LeagueIndex 的 *_matches.json glob 不同——重命名临时副本不可取，
    改为直接扫 hst_*.json 装载（复用 LeagueIndex 的行装载逻辑，注入文件清单）。"""
    files = sorted(Path(cache_dir).glob("hst_*.json"))
    tmp_pairs = []
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        rows = doc.get("rows") or []
        lg = f.stem.removeprefix("hst_")
        for r in rows:
            d = str(r.get("date") or "")
            if d and r.get("hst") is not None and r.get("ast") is not None:
                tmp_pairs.append({"date": d, "home": r.get("home"), "away": r.get("away"),
                                  "hg": r.get("hst"), "ag": r.get("ast")})
    return _LeagueIndexFromRows(tmp_pairs), None if False else _LeagueIndexFromRows(tmp_pairs)


def _LeagueIndexFromRows(rows: list[dict]) -> LeagueIndex:
    """由内存行构造 LeagueIndex（绕过文件 glob；hst 装载用）。"""
    idx = LeagueIndex.__new__(LeagueIndex)
    idx._by_team = {}
    LeagueIndex._ingest_rows(idx, rows)
    return idx


class EloIndex:
    """elo_history_*_GLOBAL.json → per-team (dates, elo_pre)。赛前值不含该场赛果
    （strength_loaders 口径注释：lag=0 无答案泄漏）。"""

    def __init__(self, rows: list[dict]):
        self._by_team: dict[str, dict] = {}
        for r in rows:
            d = str(r.get("date") or "")
            if not d:
                continue
            for team, elo in ((r.get("home"), r.get("elo_home_pre")),
                              (r.get("away"), r.get("elo_away_pre"))):
                if team and elo is not None:
                    slot = self._by_team.setdefault(team, {"dates": [], "elos": []})
                    slot["dates"].append(d)
                    slot["elos"].append(float(elo))
        for slot in self._by_team.values():
            order = sorted(range(len(slot["dates"])), key=lambda i: slot["dates"][i])
            slot["dates"] = [slot["dates"][i] for i in order]
            slot["elos"] = [slot["elos"][i] for i in order]

    def elo(self, team: str, as_of: str) -> float | None:
        slot = self._by_team.get(team)
        if not slot:
            return None
        hi = bisect.bisect_right(slot["dates"], as_of)
        return slot["elos"][hi - 1] if hi > 0 else None


def build_elo_index(cache_dir: Path) -> EloIndex:
    rows = []
    for f in sorted(Path(cache_dir).glob(ELO_GLOB)):
        doc = json.loads(f.read_text(encoding="utf-8"))
        rows.extend(doc.get("rows") or [])
    return EloIndex(rows)


def ctx_features(lg_idx: LeagueIndex, league_zh: str, as_of: str) -> dict:
    """联赛 as-of 模板 3 维（大球率/屠杀率/客零封大胜率·全库行无 league 维故用
    TAIL_LEAGUE_MAP 路由到主库口径=该库全部场次聚合·as_of 前可见）+ 月份 sin/cos 2 维。

    模板聚合走 LeagueIndex 全队窗口代价高——直接对库文件按日期重扫（轻量·一次性/场）。
    开发者 sszhang"""
    out: dict[str, float | None] = {}
    lg = TAIL_LEAGUE_MAP.get(league_zh)
    if lg:
        f = ROOT / "data" / "02-results" / "league" / f"{lg}_matches.json"
        if f.exists():
            doc = json.loads(f.read_text(encoding="utf-8"))
            rows = doc if isinstance(doc, list) else (doc.get("rows") or [])
            cutoff = (date.fromisoformat(as_of) - timedelta(days=LEAK_LAG_DAYS)).isoformat()
            visible = [r for r in rows if str(r.get("date") or "") <= cutoff
                       and r.get("hg") is not None and r.get("ag") is not None]
            if visible:
                n = len(visible)
                out["lg_p_tg4"] = sum(1 for r in visible if int(r["hg"]) + int(r["ag"]) >= 4) / n
                out["lg_p_blow"] = sum(1 for r in visible
                                       if abs(int(r["hg"]) - int(r["ag"])) >= 3) / n
                out["lg_p_away_big"] = sum(1 for r in visible
                                           if int(r["hg"]) == 0 and int(r["ag"]) >= 3) / n
    out.setdefault("lg_p_tg4", None)
    out.setdefault("lg_p_blow", None)
    out.setdefault("lg_p_away_big", None)
    m = date.fromisoformat(as_of).month
    angle = 2 * math.pi * (m - 1) / 12
    out["ctx_month_sin"] = math.sin(angle)
    out["ctx_month_cos"] = math.cos(angle)
    return out
```

同时给 `LeagueIndex.__init__` 抽出行装载（在 Task 3 的类里加类方法）：

```python
# LeagueIndex 内追加（Task 3 已建类，本任务补 ingest 钩子）
    @classmethod
    def _ingest_rows(cls, idx: "LeagueIndex", rows: list[dict]) -> None:
        """内存行装载（hst/league 复用；见 build_hst_index）。"""
        for r in rows:
            d = str(r.get("date") or "")
            gf, ga = r.get("hg"), r.get("ag")
            if not d or gf is None or ga is None:
                continue
            for team, gfor, gaga, is_home in ((r.get("home"), gf, ga, True),
                                              (r.get("away"), ga, gf, False)):
                slot = idx._by_team.setdefault(team, {"dates": [], "rows": []})
                slot["dates"].append(d)
                slot["rows"].append({"date": d, "gf": int(gfor), "ga": int(gaga),
                                     "isHome": is_home})
        for slot in idx._by_team.values():
            order = sorted(range(len(slot["dates"])), key=lambda i: slot["dates"][i])
            slot["dates"] = [slot["dates"][i] for i in order]
            slot["rows"] = [slot["rows"][i] for i in order]
```

（重构纪律：`__init__` 的装载循环体改为调 `self._ingest_rows(loaded_rows)`，行为不变——Task 3 测试回归守护。）

修正 `build_hst_index` 收尾两行（上面第一版笔误，定稿）：

```python
def build_hst_index(cache_dir: Path) -> LeagueIndex:
    files = sorted(Path(cache_dir).glob("hst_*.json"))
    rows = []
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        for r in doc.get("rows") or []:
            d = str(r.get("date") or "")
            if d and r.get("hst") is not None and r.get("ast") is not None:
                rows.append({"date": d, "home": r.get("home"), "away": r.get("away"),
                             "hg": r.get("hst"), "ag": r.get("ast")})
    return _LeagueIndexFromRows(rows)
```

hst/elo 特征组装函数（追加）：

```python
def hst_elo_features(hst_idx: LeagueIndex, elo_idx: EloIndex,
                     home_id: str | None, away_id: str | None, as_of: str) -> dict:
    """hst 近 HST_WINDOW 场场均射正 for/against（主客各2维）+ elo_diff = 5 维。"""
    out: dict[str, float | None] = {}
    for side, team in (("h", home_id), ("a", away_id)):
        ms = hst_idx.window(team, as_of, HST_WINDOW) if team else []
        if len(ms) >= MIN_WINDOW_N:
            out[f"{side}_hst_for"] = sum(m["gf"] for m in ms) / len(ms)
            out[f"{side}_hst_against"] = sum(m["ga"] for m in ms) / len(ms)
        else:
            out[f"{side}_hst_for"] = None
            out[f"{side}_hst_against"] = None
    eh = elo_idx.elo(home_id, as_of) if home_id else None
    ea = elo_idx.elo(away_id, as_of) if away_id else None
    out["elo_diff"] = (eh - ea) if (eh is not None and ea is not None) else None
    return out
```

（`ctx_features` 测试里 `_mini_league` 的库文件名是 `england-premier_matches.json` 且 TAIL_LEAGUE_MAP["英超"] 命中——测试预期成立。）

- [ ] **Step 4: 跑测试通过**

Run: `python3 -m pytest engine/tests/test_tail_dataset.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/tail_dataset.py engine/tests/test_tail_dataset.py
git commit -m "feat(右尾): hst射正+elo赛前值+联赛模板/月份上下文特征——LeagueIndex行装载复用"
```

---

### Task 5: 数据集组装 + jsonl 落盘 + CLI build

**Files:**
- Modify: `engine/scripts/tail_dataset.py`（追加）
- Test: `engine/tests/test_tail_dataset.py`（追加）

**Interfaces:**
- Consumes: `backtest_crs_batch.load_hist_window`、`strength_loaders.load_aliases`/`zh_to_id`、本文件全部特征函数
- Produces: `build_rows(date_lo, date_hi, *, hist_dir=None, league_dir=None, cache_dir=None) -> tuple[list[dict], dict]`（行 `{date, code, league, homeId, awayId, score, label{...}, features{...全维}, nNullTeam, nNullOther}`，meta=`{count, mappedRate}`）；`FEATURE_NAMES_ORDER(rows) -> list[str]`（首行特征键序冻结）；`write_dataset(rows, out_dir) -> dict`（dataset.jsonl + meta.json，meta 含 sha256 指纹）；CLI `python tail_dataset.py build`

- [ ] **Step 1: 写失败测试（迷你 hist + 迷你库端到端）**

```python
# 追加
from tail_dataset import build_rows, write_dataset


def _mini_hist(tmp_path):
    pool = {f"{i}:{j}": 8.0 for i in range(3) for j in range(3)}
    pool.update({"other_h": 40.0, "other_d": 40.0, "other_a": 40.0,
                 **{f"{i}:{j}": 30.0 for i in range(3, 6) for j in range(6)},
                 **{f"{i}:{j}": 30.0 for i in range(6) for j in range(3)}})
    matches = [
        {"date": "2025-10-16", "code": "四001", "league": "英超", "home": "阿森纳",
         "away": "切尔西", "score": "2:1", "had": {"h": 1.5, "d": 4.0, "a": 5.0},
         "ttg": {str(i): 3.0 for i in range(8)}, "crs": dict(pool), "crsOpen": dict(pool)},
        {"date": "2025-10-17", "code": "四002", "league": "英超", "home": "利物浦",
         "away": "曼城", "score": "0:5", "had": {"h": 2.2, "d": 3.2, "a": 2.5},
         "ttg": {str(i): 3.0 for i in range(8)}, "crs": dict(pool), "crsOpen": dict(pool)},
    ]
    p = tmp_path / "crs_hist_mini.json"
    p.write_text(_json.dumps({"matches": matches}), encoding="utf-8")
    return tmp_path


def test_build_rows_end_to_end(tmp_path):
    hist_dir = _mini_hist(tmp_path)
    lg_dir = _mini_league(tmp_path)                       # arsenal/chelsea 小库
    rows, meta = build_rows("2025-10-01", "2025-10-31",
                            hist_dir=hist_dir, league_dir=lg_dir, cache_dir=tmp_path)
    assert len(rows) == 2
    r0 = rows[0]
    assert r0["homeId"] == "arsenal" and r0["label"] == {"tg": 3, "tgBin": 3, "direction": 0, "cleanSheet": 2}
    assert r0["crs"] and r0["ttg"]                              # 对拍快照顶层挂载
    assert r0["features"]["mkt_p_tg_ge5_open"] > 0
    assert r0["features"]["h_gf_w10"] is None             # 窗不足 MIN_WINDOW_N
    assert r0["nNullTeam"] > 0                            # 缺失计数入特征
    assert 0.0 <= meta["mappedRate"] <= 1.0


def test_write_dataset_fingerprint_stable(tmp_path):
    hist_dir = _mini_hist(tmp_path)
    lg_dir = _mini_league(tmp_path)
    rows, _ = build_rows("2025-10-01", "2025-10-31",
                         hist_dir=hist_dir, league_dir=lg_dir, cache_dir=tmp_path)
    meta1 = write_dataset(rows, tmp_path / "out1")
    meta2 = write_dataset(rows, tmp_path / "out2")
    assert meta1["fingerprint"] == meta2["fingerprint"]
    assert (tmp_path / "out1" / "dataset.jsonl").exists()
    assert meta1["featureNames"][0] == "mkt_e_tg_open"    # 键序冻结·市场锚打头
    lines = (tmp_path / "out1" / "dataset.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
```

- [ ] **Step 2: 跑确认失败**

Run: `python3 -m pytest engine/tests/test_tail_dataset.py -v`
Expected: 新增 2 FAIL（ImportError: build_rows）

- [ ] **Step 3: 实现（追加）**

```python
# ── 组装与落盘 ────────────────────────────────────────────────────────────
import hashlib
import strength_loaders as sl
from backtest_crs_batch import load_hist_window

CTX_KEYS = ("lg_p_tg4", "lg_p_blow", "lg_p_away_big", "ctx_month_sin", "ctx_month_cos")


def _zh_alias_maps():
    aliases = sl.load_aliases()
    return sl.zh_to_id(aliases), aliases


def build_rows(date_lo: str, date_hi: str, *, hist_dir: Path | None = None,
               league_dir: Path | None = None, cache_dir: Path | None = None
               ) -> tuple[list[dict], dict]:
    """hist 窗口 → 标签+四族特征行。映射失败队→homeId/awayId=None+球队特征全 None
    （featMask 语义·诚实降级不剔除）。开发者 sszhang"""
    lg_dir = league_dir or (ROOT / "data" / "02-results" / "league")
    c_dir = cache_dir or (ROOT / "engine" / "cache")
    lg_idx = LeagueIndex(lg_dir)
    hst_idx = build_hst_index(c_dir)
    elo_idx = build_elo_index(c_dir)
    zh, aliases = _zh_alias_maps()

    def one(name: str):
        return zh.get(name) or sl._norm_team(name, aliases)

    out, mapped, total = [], 0, 0
    for row in load_hist_window(date_lo, date_hi, hist_dir=hist_dir):
        hid, aid = one(row["home"]), one(row["away"])
        total += 1
        if hid and aid:
            mapped += 1
        as_of = (date.fromisoformat(row["date"])
                 + timedelta(days=DECISION_DAY_OFFSET)).isoformat()
        feats: dict[str, float | None] = {}
        feats.update(market_anchor_features(row))          # 池不足→空dict（11维缺失）
        team = team_asof_features(lg_idx, hid, aid, as_of)
        feats.update(team)
        feats.update(hst_elo_features(hst_idx, elo_idx, hid, aid, as_of))
        feats.update(ctx_features(lg_idx, row["league"], as_of))
        n_team = sum(1 for v in team.values() if v is None)
        n_other = sum(1 for k in (*feats,)
                      if k.startswith(("mkt_", "h_", "a_", "d_", "rest_", "lg_"))
                      or k.endswith(("_hst_for", "_hst_against")) or k == "elo_diff")
        n_other = n_other - n_team if n_other > n_team else 0
        feats["nNullTeam"] = n_team
        feats["nNullOther"] = n_other
        # ttg/had/crs 顶层快照：tail_train.market_event_probs 对拍消费（封盘价=市场对手盘）
        out.append({"date": row["date"], "code": row["code"], "league": row["league"],
                    "homeId": hid, "awayId": aid, "score": row["score"],
                    "ttg": row["ttg"], "had": row["had"], "crs": row["crs"],
                    "label": label_skeleton(row["score"]), "features": feats})
    return out, {"count": len(out), "mappedRate": round(mapped / total, 4) if total else None}


def _feature_names_ordered(sample: dict) -> list[str]:
    """键序冻结：市场锚(11)→球队(38)→hst/elo(5)→ctx(5)→计数(2)——由插入序保证。"""
    return list(sample.keys())


def write_dataset(rows: list[dict], out_dir: Path) -> dict:
    """dataset.jsonl + meta.json（fingerprint=行规范化 sha256 前16位·幂等重建可 diff）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    names = _feature_names_ordered(rows[0]["features"]) if rows else []
    lines = []
    for r in rows:
        r2 = dict(r)
        r2["features"] = {k: r["features"].get(k) for k in names}
        lines.append(json.dumps(r2, ensure_ascii=False, sort_keys=True))
    blob = "\n".join(lines)
    meta = {"version": FEATURE_VERSION, "count": len(rows),
            "dateRange": [rows[0]["date"], rows[-1]["date"]] if rows else None,
            "featureNames": names, "nFeatures": len(names),
            "fingerprint": hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]}
    (out_dir / "dataset.jsonl").write_text(blob + "\n", encoding="utf-8")
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    return meta


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="右尾肥尾场次数据集构建器")
    ap.add_argument("cmd", choices=["build"])
    ap.add_argument("--from", dest="date_lo", default="2023-09-01")
    ap.add_argument("--to", dest="date_hi", default="2026-09-28")
    ap.add_argument("--out", default=str(ROOT / "engine" / "cache" / "tail_dataset"))
    args = ap.parse_args()
    rows, stat = build_rows(args.date_lo, args.date_hi)
    meta = write_dataset(rows, Path(args.out))
    print(json.dumps({**meta, "mappedRate": stat["mappedRate"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试通过**

Run: `python3 -m pytest engine/tests/test_tail_dataset.py -v`
Expected: 10 PASS

- [ ] **Step 5: 全库构建（真数据·约1~2分钟）+ Commit**

Run: `python3 engine/scripts/tail_dataset.py build`
Expected: stdout 打印 meta（count≈13515、nFeatures≈61、mappedRate≈0.87、fingerprint 十六进制）

```bash
git add engine/scripts/tail_dataset.py engine/tests/test_tail_dataset.py engine/cache/tail_dataset/
git commit -m "feat(右尾): 数据集组装器+jsonl落盘+版本指纹——全库13515场构建入库"
```

---

### Task 6: walk-forward 训练链 + 判据对拍（tail_train.py 主体）

**Files:**
- Create: `engine/scripts/tail_train.py`
- Test: `engine/tests/test_tail_train.py`

**Interfaces:**
- Consumes: `engine/cache/tail_dataset/dataset.jsonl`（Task 5 产出·meta.featureNames 定列序）、`market_lambda.crs_power_devig`、`strength_chain_eval._boot_ci_diff`、prereg JSON
- Produces:
  - `load_dataset(dir) -> tuple[list[dict], dict]`（行+meta）
  - `time_folds() -> list[dict]`（`{testFrom, testTo}`，expanding，11 段）
  - `fit_scaler(X_train) -> tuple[np.ndarray, np.ndarray]`（nan-safe mean/std）/ `apply_scaler(X, mu, sd) -> np.ndarray`（nan→mu 即 scale 后 0）
  - `heads_matrix(head, X, onehot_vals, n_slots) -> np.ndarray`（X + one-hot 拼接，tg 档/方向条件化用）
  - `train_heads(X, y_tg, Xd, y_dir, Xc, y_cs) -> dict`（`{"tg": LogisticRegression, "dir": ..., "cs": ...}`；cs 只训 tgBin≥2 样本）
  - `predict_event_probs(heads, X) -> np.ndarray`（N×3，列=E1/E2/E3 模型概率，向量化批预测）
  - `market_event_probs(row) -> tuple[float, float, float] | None`（E1=ttg devig s5+s6+s7；E2/E3=crs 封盘 power devig 同格聚合；池不足 None）
  - `event_outcomes(score) -> tuple[int, int, int]`（E1=tg≥5；E2/E3=赛果∈冻结格）
  - `run_folds(rows, meta, *, model="logistic") -> dict`（判据产物：perEvent `{n, diffMean, ci95, verdict}` + 分段明细）
  - `check_prereg() -> None`（指纹闸：prereg 文件存在且 frozenParams 与模块常量一致，否则 SystemExit 2）
  - CLI `python tail_train.py run`（产出 `data/04-summaries/tail-model/run.json`）
- 冻结格常量：`E2_CELLS = [(3,0),(4,0),(4,1),(5,0),(5,1),(5,2),(0,3),(0,4),(1,4),(0,5),(1,5),(2,5)]`、`E3_CELLS = [(0,3),(0,4),(0,5)]`

- [ ] **Step 1: 写失败测试**

```python
# engine/tests/test_tail_train.py
import sys, pathlib, json
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from tail_train import (time_folds, fit_scaler, apply_scaler, event_outcomes,
                        market_event_probs, train_heads, predict_event_probs,
                        E2_CELLS, E3_CELLS, CS_NO_INFO_TG)


def test_time_folds_eleven_expanding():
    folds = time_folds()
    assert len(folds) == 11
    assert folds[0] == {"testFrom": "2025-01-01", "testTo": "2025-02-28"}
    assert folds[-1]["testFrom"] == "2026-09-01" and folds[-1]["testTo"] == "2026-09-28"


def test_scaler_nan_safe():
    X = np.array([[1.0, np.nan], [3.0, 5.0], [np.nan, 7.0]])
    mu, sd = fit_scaler(X)
    assert np.isclose(mu[0], 2.0) and np.isclose(mu[1], 6.0)
    Z = apply_scaler(X, mu, sd)
    assert not np.isnan(Z).any()
    assert np.isclose(Z[0, 1], 0.0)                      # nan→均值→scale 后 0


def test_event_outcomes_grid():
    assert event_outcomes("2:3") == (0, 0, 0)
    assert event_outcomes("0:5") == (1, 1, 1)            # tg≥5 且 ∈E2 且 ∈E3
    assert event_outcomes("4:1") == (1, 1, 0)
    assert event_outcomes("6:0") == (1, 0, 0)            # 6:0 不在 0..5 网格→E2=0（口径冻结）


def test_market_event_probs_ttg_and_crs():
    row = {"ttg": {"5": 6.0, "6": 12.0, "7": 30.0, **{str(i): 4.0 for i in range(5)}},
           "crs": {f"{i}:{j}": 12.0 for i in range(6) for j in range(6)},
           "crsOpen": {f"{i}:{j}": 12.0 for i in range(6) for j in range(6)}}
    e1, e2, e3 = market_event_probs(row)
    assert 0.0 < e1 < 0.5
    assert 0.0 < e2 < 0.4 and 0.0 < e3 < 0.2
    assert e2 > e3                                       # E2 格集是 E3 超集
    assert market_event_probs({"ttg": {}, "crs": {}, "crsOpen": {}}) is None


def test_train_predict_heads_synthetic():
    rng = np.random.default_rng(7)
    N = 400
    z = rng.normal(size=N)                                # 潜变量：大球倾向
    y_tg = np.where(z + rng.normal(scale=0.7, size=N) > 0.9, 5, 2)
    X = np.column_stack([z + rng.normal(scale=0.3, size=N), rng.normal(size=N)])
    # 方向/零封标签由 tg 推（结构自洽）
    y_dir = np.array([{5: 2, 2: 0}[t] for t in y_tg])
    y_cs = np.array([0 if (t == 5 and d == 2) else 2 for t, d in zip(y_tg, y_dir)])
    tg1h = np.eye(8)[y_tg]
    heads = train_heads(X, y_tg, np.hstack([X, tg1h]), y_dir,
                        np.hstack([X, tg1h, np.eye(3)[y_dir]]), y_cs)
    P = predict_event_probs(heads, X[:20])
    assert P.shape == (20, 3)
    assert ((P > 0) & (P < 1)).all()
    # z 大 → E1 概率高（模型学到了信号）
    Xhi = np.column_stack([np.full(10, 2.0), np.zeros(10)])
    Xlo = np.column_stack([np.full(10, -2.0), np.zeros(10)])
    assert predict_event_probs(heads, Xhi)[:, 0].mean() > predict_event_probs(heads, Xlo)[:, 0].mean()
```

- [ ] **Step 2: 跑确认失败**

Run: `python3 -m pytest engine/tests/test_tail_train.py -v`
Expected: FAIL（ModuleNotFoundError: tail_train）

- [ ] **Step 3: 实现（文件头+常量+全部函数）**

```python
# engine/scripts/tail_train.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tail_train.py — 右尾肥尾场次模型训练评估链（spec docs/2026-10-13-tail-model-design.html §3）。

判据预注册 engine/cache/tail-model-prereg.json（跑前冻结·check_prereg 指纹闸）。
主判据 E1=总进球≥5：logistic 三头 vs 市场 ttg devig 聚合，配对 log-loss 差 CI 下限>0。
防泄漏：特征已由 tail_dataset 保证（as_of/lag/crsOpen）；本脚本 folds 内 scaler/模型
只在训练窗 fit。开发者 sszhang"""
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import market_lambda as mkl
from strength_chain_eval import _boot_ci_diff

PREREG_PATH = ROOT / "engine" / "cache" / "tail-model-prereg.json"
DATASET_DIR = ROOT / "engine" / "cache" / "tail_dataset"
OUT_DIR = ROOT / "data" / "04-summaries" / "tail-model"

C_LR = 1.0
MAX_ITER_LR = 2000
BOOT_B = 2000
SEED = 20261014
LOGLLOSS_CLIP = 1e-6
CS_NO_INFO_TG = 2                       # tgBin<2 的零封由结构决定，cs 头不训（tg=1 主胜=必 1:0）

E2_CELLS = [(3, 0), (4, 0), (4, 1), (5, 0), (5, 1), (5, 2),
            (0, 3), (0, 4), (1, 4), (0, 5), (1, 5), (2, 5)]
E3_CELLS = [(0, 3), (0, 4), (0, 5)]
TTG_KEYS = tuple(str(i) for i in range(8))


def time_folds() -> list[dict]:
    """expanding walk-forward：2025-01 起每 2 月一段共 11 段，末段截 2026-09-28。"""
    folds = []
    y, m = 2025, 1
    while (y, m) <= (2026, 9):
        ny, nm = (y, m + 1) if m < 12 else (y + 1, 1)
        if m == 2:
            to = f"{y}-02-28"
        elif m in (1, 3, 5, 7, 8, 10, 12):
            to = f"{ny if m == 12 else y}-{nm:02d}-01" if m == 12 else f"{y}-{m + 1:02d}-01"
            to = (date_str_month_end(y, m))
        else:
            to = date_str_month_end(y, m)
        folds.append({"testFrom": f"{y}-{m:02d}-01", "testTo": to})
        y, m = ny, nm
    folds[-1]["testTo"] = "2026-09-28"
    return folds


def date_str_month_end(y: int, m: int) -> str:
    """月末字符串（段 testTo=下月1日前一日）。"""
    import calendar
    return f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]}"


def load_dataset(ds_dir: Path = DATASET_DIR) -> tuple[list[dict], dict]:
    meta = json.loads((Path(ds_dir) / "meta.json").read_text(encoding="utf-8"))
    rows = [json.loads(x) for x in
            (Path(ds_dir) / "dataset.jsonl").read_text(encoding="utf-8").strip().split("\n")]
    return rows, meta


def feature_matrix(rows: list[dict], names: list[str]) -> np.ndarray:
    """行→矩阵（None→nan；scaler 前置）。"""
    return np.array([[float(r["features"].get(k)) if r["features"].get(k) is not None else np.nan
                      for k in names] for r in rows], dtype=float)


def fit_scaler(X_train: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """nan-safe 列均值/标准差（std=0→1 防除零）。train-only fit（防泄漏）。"""
    with np.errstate(invalid="ignore"):
        mu = np.nanmean(X_train, axis=0)
        sd = np.nanstd(X_train, axis=0)
    mu = np.where(np.isnan(mu), 0.0, mu)
    return mu, np.where(sd < 1e-12, 1.0, sd)


def apply_scaler(X: np.ndarray, mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    """nan→mu（scale 后为 0）；再标准化。"""
    Xf = np.where(np.isnan(X), mu, X)
    return (Xf - mu) / sd


def _onehot(vals: np.ndarray, n_slots: int) -> np.ndarray:
    z = np.zeros((len(vals), n_slots), dtype=float)
    z[np.arange(len(vals)), vals] = 1.0
    return z


def train_heads(X, y_tg, Xd, y_dir, Xc, y_cs) -> dict:
    """logistic 三头：tg 8类 / 方向|tg档one-hot / 零封|tg+方向（仅 tgBin≥CS_NO_INFO_TG 样本）。"""
    kw = dict(C=C_LR, max_iter=MAX_ITER_LR, random_state=SEED)
    h_tg = LogisticRegression(**kw).fit(X, y_tg)
    h_dir = LogisticRegression(**kw).fit(Xd, y_dir)
    mask = np.array([t >= CS_NO_INFO_TG for t in y_tg])
    h_cs = LogisticRegression(**kw).fit(Xc[mask], y_cs[mask]) if mask.sum() >= 30 else None
    return {"tg": h_tg, "dir": h_dir, "cs": h_cs}


def predict_event_probs(heads: dict, X: np.ndarray) -> np.ndarray:
    """骨架三轴连乘 → 每场 (E1, E2, E3)。

    条件化向量化：dir/cs 头输入 = X + 候选 tg 档 one-hot（+方向 one-hot），
    一次批量喂 8N / 24N 行避免逐格调用。开发者 sszhang"""
    N = len(X)
    p_tg = heads["tg"].predict_proba(X)                     # N×8（列序=classes_）
    tg_classes = list(heads["tg"].classes_)
    # dir：每场×8 档
   Xd_block = np.hstack([np.repeat(X, 8, axis=0), np.tile(np.eye(8), (N, 1))])
    p_dir_all = heads["dir"].predict_proba(Xd_block)        # 8N×3
    dir_classes = list(heads["dir"].classes_)
    # cs：每场×8档×3方向
    tri = np.array([(t, d) for t in range(8) for d in range(3)])
    Xc_block = np.hstack([np.repeat(X, 24, axis=0),
                          np.tile(np.eye(8)[tri[:, 0]], (N, 1)),
                          np.tile(np.eye(3)[tri[:, 1]], (N, 1))])
    p_cs_all = heads["cs"].predict_proba(Xc_block) if heads["cs"] else None
    cs_classes = list(heads["cs"].classes_) if heads["cs"] else []
    idx_tg = {c: i for i, c in enumerate(tg_classes)}
    idx_dir = {c: i for i, c in enumerate(dir_classes)}
    idx_cs = {c: i for i, c in enumerate(cs_classes)}

    def cell_p(i_row: int, h: int, a: int) -> float:
        tg = min(h + a, 7)
        d = 0 if h > a else (1 if h == a else 2)
        cs = 2
        if h > a and a == 0:
            cs = 1
        elif h < a and h == 0:
            cs = 0
        pt = p_tg[i_row, idx_tg[tg]]
        pd = p_dir_all[i_row * 8 + tg, idx_dir[d]]
        if p_cs_all is None or tg < CS_NO_INFO_TG:
            pc = 1.0 if cs == 2 else 0.0                    # 无 cs 头：非零封=1、零封=0（保守）
        else:
            pc = p_cs_all[i_row * 24 + tg * 3 + d, idx_cs[cs]]
        return float(pt * pd * pc)

    out = np.zeros((N, 3))
    for i in range(N):
        out[i, 0] = sum(p_tg[i, idx_tg[t]] for t in (5, 6, 7))
        out[i, 1] = sum(cell_p(i, h, a) for h, a in E2_CELLS)
        out[i, 2] = sum(cell_p(i, h, a) for h, a in E3_CELLS)
    return out


def market_event_probs(row: dict) -> tuple[float, float, float] | None:
    """判据对手盘（封盘价·市场最终定价）：E1=ttg power devig s5+s6+s7；
    E2/E3=crs power devig 封盘同格聚合。任一池不足→None（双侧同跳）。"""
    ttg = row.get("ttg") or {}
    ks = [k for k in TTG_KEYS if ttg.get(k) and float(ttg[k]) > 1.0]
    if len(ks) < 6:
        return None
    p_t = mkl._power_devig_probs(np.array([1.0 / float(ttg[k]) for k in ks]))
    pm = dict(zip(ks, p_t))
    e1 = float(sum(pm.get(k, 0.0) for k in ("5", "6", "7")))
    crs = mkl.crs_power_devig(row.get("crs") or {})
    if not crs:
        return None
    e2 = float(sum(crs.get(f"s{h:02d}s{a:02d}", 0.0) for h, a in E2_CELLS))
    e3 = float(sum(crs.get(f"s{h:02d}s{a:02d}", 0.0) for h, a in E3_CELLS))
    return e1, e2, e3


def event_outcomes(score: str) -> tuple[int, int, int]:
    """赛果→三事件 0/1（E2/E3 同 0..5 网格口径·prereg secondary）。"""
    h, a = (int(x) for x in score.split(":"))
    return (int(h + a >= 5), int((h, a) in set(E2_CELLS)), int((h, a) in set(E3_CELLS)))


def _logloss(p: float, y: int) -> float:
    p = min(max(p, LOGLLOSS_CLIP), 1 - LOGLLOSS_CLIP)
    return -np.log(p) if y else -np.log(1 - p)


def run_folds(rows: list[dict], meta: dict, *, model: str = "logistic") -> dict:
    """expanding 逐段：train 窗 fit scaler+三头 → test 段逐场 (p_model, p_market, y)
    → 全段汇总配对 log-loss 差（market − model，正=模型更准）+ bootstrap CI。"""
    names = meta["featureNames"]
    diffs = {e: [] for e in ("E1", "E2", "E3")}
    seg_detail = []
    for k, fold in enumerate(time_folds()):
        train = [r for r in rows if r["date"] < fold["testFrom"]]
        test = [r for r in rows if fold["testFrom"] <= r["date"] <= fold["testTo"]]
        if len(train) < 500 or not test:
            continue
        Xtr = feature_matrix(train, names)
        mu, sd = fit_scaler(Xtr)
        Ztr = apply_scaler(Xtr, mu, sd)
        y_tg = np.array([r["label"]["tgBin"] for r in train])
        y_dir = np.array([r["label"]["direction"] for r in train])
        y_cs = np.array([r["label"]["cleanSheet"] for r in train])
        tg1h = _onehot(y_tg, 8)
        heads = train_heads(Ztr, y_tg,
                            np.hstack([Ztr, tg1h]), y_dir,
                            np.hstack([Ztr, tg1h, _onehot(y_dir, 3)]), y_cs)
        Zte = apply_scaler(feature_matrix(test, names), mu, sd)
        P = predict_event_probs(heads, Zte)
        # 对拍：test 行需回查 hist 原 row（ttg/crs 封盘）——dataset 行自带 had/ttg/crs 快照
        n_seg = 0
        for i, r in enumerate(test):
            mk = market_event_probs(r)
            if mk is None:
                continue
            y = event_outcomes(r["score"])
            for j, e in enumerate(("E1", "E2", "E3")):
                diffs[e].append(_logloss(mk[j], y[j]) - _logloss(P[i, j], y[j]))
            n_seg += 1
        seg_detail.append({"fold": k, "testFrom": fold["testFrom"], "n": n_seg})
    out = {"model": model, "segments": seg_detail}
    for e in ("E1", "E2", "E3"):
        d = diffs[e]
        if len(d) >= 30:
            ci = _boot_ci_diff(d, seed=SEED, n_boot=BOOT_B)
            out[e] = {"n": len(d), "diffMean": round(float(np.mean(d)), 4),
                      "ci95": [round(float(x), 4) for x in ci],
                      "verdict": "PASS" if ci[0] > 0 else "FAIL"}
        else:
            out[e] = {"n": len(d), "verdict": "INSUFFICIENT_N"}
    return out


def check_prereg() -> dict:
    """指纹闸：prereg 存在且 frozenParams 与模块常量一致（铁律15 语义·跑前校验）。"""
    if not PREREG_PATH.exists():
        raise SystemExit(f"[prereg] 缺 {PREREG_PATH}——判据未冻结禁跑（铁律14②）")
    pr = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    fp = pr["frozenParams"]
    expect = {"C": C_LR, "max_iter": MAX_ITER_LR, "boot_B": BOOT_B, "seed": SEED,
              "logloss_clip": LOGLLOSS_CLIP}
    for k, v in expect.items():
        if fp.get(k) != v:
            raise SystemExit(f"[prereg] frozenParams.{k}={fp.get(k)} ≠ 模块常量 {v}")
    return pr


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="右尾肥尾模型训练评估链")
    ap.add_argument("cmd", choices=["run"])
    args = ap.parse_args()
    check_prereg()
    rows, meta = load_dataset()
    result = run_folds(rows, meta)
    result["datasetMeta"] = {k: meta[k] for k in ("version", "count", "fingerprint")}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps({e: result[e] for e in ("E1", "E2", "E3")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

**实现注意（执行者必读）**：
1. `time_folds` 上面初稿的分支写乱了——**定稿用这个**：

```python
def time_folds() -> list[dict]:
    folds = []
    y, m = 2025, 1
    while (y, m) <= (2026, 9):
        folds.append({"testFrom": f"{y}-{m:02d}-01",
                      "testTo": date_str_month_end(y, m)})
        m += 1
        if m > 12:
            y, m = y + 1, 1
    # 两月一段：取奇数索引段并并窗
    merged = []
    i = 0
    monthly = folds
    while i + 1 < len(monthly):
        merged.append({"testFrom": monthly[i]["testFrom"], "testTo": monthly[i + 1]["testTo"]})
        i += 2
    if i < len(monthly):                                   # 2026-09 单月尾巴
        merged.append(monthly[i])
    merged[-1]["testTo"] = "2026-09-28"
    return merged
```

（11 段=2025 年 6 段 + 2026 年 1-8 月 4 段 + 2026-09 单月 1 段；测试断言第一条 `(2025-01-01, 2025-02-28)`。）
2. `predict_event_probs` 定稿版把 `Xd_block` 行的行首空格去掉（初稿缩进笔误）；`cs` 头样本 mask 语义=`tgBin>=CS_NO_INFO_TG`。
3. `run_folds` 里 `market_event_probs(r)` 直接吃 dataset 行——**Task 5 的 build_rows 必须把 `ttg/had/crs` 原样挂到输出行**（对拍需要）。Task 5 Step 3 的 out.append 里补 `"ttg": row["ttg"], "had": row["had"], "crs": row["crs"]`（features 之外的顶层字段；测试同步加断言 `r0["crs"]` 非空）。**执行 Task 5 时先做这个补充**，Task 6 才能跑通。

- [ ] **Step 4: 跑测试通过**

Run: `python3 -m pytest engine/tests/test_tail_train.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/tail_train.py engine/tests/test_tail_train.py
git commit -m "feat(右尾): walk-forward训练链——logistic三头+骨架连乘合成+市场对手盘对拍+配对logloss CI"
```

---

### Task 7: HistGB 对照 + featMask 分层 + HTML 报告 + run 全流程

**Files:**
- Modify: `engine/scripts/tail_train.py`（追加）
- Test: `engine/tests/test_tail_train.py`（追加）

**Interfaces:**
- Consumes: Task 6 全部；`sklearn.ensemble.HistGradientBoostingClassifier`
- Produces: `run_histgb_e1(rows, meta) -> dict`（exploratory E1 对照，同 folds 同 impute）；`split_by_mapping(rows) -> tuple[list, list]`（featMask 分层：nNullTeam==球队维全缺 vs 有值——用 `homeId and awayId` 判映射）；`render_report(result_all: dict) -> str`（HTML·charset utf-8·SVG CI 图）；CLI 扩展 `run-all`（logistic 主+HistGB+分层+报告落盘 `data/04-summaries/tail-model/tail-model-report.html` + run.json）

- [ ] **Step 1: 写失败测试**

```python
# 追加
from tail_train import split_by_mapping, render_report


def test_split_by_mapping(tmp_path):
    rows = [{"homeId": "arsenal", "awayId": "chelsea", "date": "2025-01-01", "score": "1:1",
             "label": {"tgBin": 2, "direction": 1, "cleanSheet": 2}, "features": {}},
            {"homeId": None, "awayId": None, "date": "2025-01-02", "score": "2:2",
             "label": {"tgBin": 4, "direction": 1, "cleanSheet": 2}, "features": {}}]
    mapped, unmapped = split_by_mapping(rows)
    assert len(mapped) == 1 and len(unmapped) == 1


def test_render_report_html_and_svg():
    fake = {"logistic": {"E1": {"n": 4000, "diffMean": 0.012, "ci95": [0.002, 0.022],
                                "verdict": "PASS"},
                         "E2": {"n": 4000, "diffMean": -0.004, "ci95": [-0.01, 0.004],
                                "verdict": "FAIL"},
                         "E3": {"n": 4000, "diffMean": 0.001, "ci95": [-0.005, 0.007],
                                "verdict": "FAIL"}},
            "histgbE1": {"n": 4000, "diffMean": 0.006, "ci95": [-0.002, 0.014],
                         "verdict": "FAIL"},
            "mappedLayer": {"E1": {"n": 3000, "diffMean": 0.01, "ci95": [0.0, 0.02],
                                   "verdict": "FAIL"}},
            "unmappedLayer": {"E1": {"n": 1000, "diffMean": 0.02, "ci95": [-0.01, 0.05],
                                     "verdict": "FAIL"}},
            "datasetMeta": {"version": "tail-v1", "count": 13515, "fingerprint": "abc123"}}
    html = render_report(fake)
    assert html.startswith("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">")
    assert "主判据" in html and "PASS" in html and "<svg" in html
    assert "exploratory" in html
```

- [ ] **Step 2: 跑确认失败**

Run: `python3 -m pytest engine/tests/test_tail_train.py -v`
Expected: 新增 2 FAIL（ImportError: split_by_mapping）

- [ ] **Step 3: 实现（追加到 tail_train.py）**

```python
# ── HistGB exploratory 对照 + featMask 分层 + 报告 ──────────────────────────
from sklearn.ensemble import HistGradientBoostingClassifier

HISTGB_PARAMS = {"max_iter": 300, "learning_rate": 0.06, "random_state": SEED}


def run_histgb_e1(rows: list[dict], meta: dict) -> dict:
    """HistGB tg 头 E1 对照（exploratory·不进判据）。同 folds 同 scaler 同 impute，
    逐段累计配对 log-loss 差。HistGB 原生吃 nan（不填均值），impute 口径差异在报告标注。"""
    names = meta["featureNames"]
    diffs = []
    for fold in time_folds():
        train = [r for r in rows if r["date"] < fold["testFrom"]]
        test = [r for r in rows if fold["testFrom"] <= r["date"] <= fold["testTo"]]
        if len(train) < 500 or not test:
            continue
        Xtr, Xte = feature_matrix(train, names), feature_matrix(test, names)
        y_tg = np.array([r["label"]["tgBin"] for r in train])
        clf = HistGradientBoostingClassifier(**HISTGB_PARAMS).fit(Xtr, y_tg)
        p_tg = clf.predict_proba(Xte)
        cols = {c: i for i, c in enumerate(clf.classes_)}
        for i, r in enumerate(test):
            mk = market_event_probs(r)
            if mk is None:
                continue
            y1 = event_outcomes(r["score"])[0]
            p_m = sum(p_tg[i, cols[t]] for t in (5, 6, 7) if t in cols)
            diffs.append(_logloss(mk[0], y1) - _logloss(p_m, y1))
    if len(diffs) < 30:
        return {"n": len(diffs), "verdict": "INSUFFICIENT_N", "exploratory": True}
    ci = _boot_ci_diff(diffs, seed=SEED, n_boot=BOOT_B)
    return {"n": len(diffs), "diffMean": round(float(np.mean(diffs)), 4),
            "ci95": [round(float(x), 4) for x in ci],
            "verdict": "PASS" if ci[0] > 0 else "FAIL", "exploratory": True}


def split_by_mapping(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """featMask 分层（红线②）：映射成功（两队均有 ID）vs 降级（任一 None）。"""
    mapped = [r for r in rows if r.get("homeId") and r.get("awayId")]
    unmapped = [r for r in rows if not (r.get("homeId") and r.get("awayId"))]
    return mapped, unmapped


def _ci_svg_block(result_all: dict) -> str:
    """三事件族+对照+分层 CI 误差条（内嵌 SVG·currentColor 适配主题）。"""
    items = [("E1 主判据·logistic", result_all.get("logistic", {}).get("E1")),
             ("E2 次判据", result_all.get("logistic", {}).get("E2")),
             ("E3 次判据", result_all.get("logistic", {}).get("E3")),
             ("E1·HistGB(exploratory)", result_all.get("histgbE1")),
             ("E1·映射层", (result_all.get("mappedLayer") or {}).get("E1")),
             ("E1·未映射层", (result_all.get("unmappedLayer") or {}).get("E1"))]
    rows_svg = []
    scale = 200.0   # 每侧最大渲染幅度
    for i, (label, d) in enumerate(items):
        y = 30 + i * 34
        if not d or "ci95" not in d:
            rows_svg.append(f'<text x="10" y="{y + 4}" font-size="12" fill="currentColor">{label}: n={d.get("n", 0) if d else 0} 样本不足</text>')
            continue
        lo, hi = d["ci95"]
        x0, x1 = 430 + max(-scale, min(scale, lo * 1000)), 430 + max(-scale, min(scale, hi * 1000))
        color = "#16a085" if d["verdict"] == "PASS" else "#c0392b"
        rows_svg.append(f'<line x1="{x0:.0f}" y1="{y}" x2="{x1:.0f}" y2="{y}" stroke="{color}" stroke-width="4"/>'
                        f'<circle cx="{430 + d["diffMean"] * 1000:.0f}" cy="{y}" r="4" fill="{color}"/>'
                        f'<text x="10" y="{y + 4}" font-size="12" fill="currentColor">{label}: Δ={d["diffMean"]:+.4f} CI[{lo:+.4f},{hi:+.4f}] {d["verdict"]}</text>')
    body = "".join(rows_svg)
    return (f'<svg viewBox="0 0 880 240" width="100%" xmlns="http://www.w3.org/2000/svg">'
            f'<line x1="430" y1="10" x2="430" y2="230" stroke="#888" stroke-dasharray="4,4"/>'
            f'<text x="434" y="18" font-size="10" fill="#888">0（平手线·右侧=模型更准）</text>{body}</svg>')


def render_report(result_all: dict) -> str:
    """tail-model-report.html（charset utf-8·主判据置顶·exploratory 全标注·限制节）。"""
    e1 = result_all.get("logistic", {}).get("E1", {})
    meta = result_all.get("datasetMeta", {})
    lines = []
    lines.append("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">")
    lines.append("<title>右尾肥尾场次模型报告</title></head><body>")
    lines.append("<h1>右尾肥尾场次模型 · 判据裁决报告</h1>")
    lines.append(f"<p>主判据 E1（总进球≥5·logistic vs 市场ttg去水·配对log-loss差 CI 下限&gt;0）：<b>{e1.get('verdict')}</b>"
                 f"（Δ={e1.get('diffMean')}, CI={e1.get('ci95')}, n={e1.get('n')}）</p>")
    lines.append(f"<p>数据集 {meta.get('version')} · {meta.get('count')} 场 · 指纹 {meta.get('fingerprint')}</p>")
    lines.append("<h2>CI 总览</h2>")
    lines.append(_ci_svg_block(result_all))
    lines.append("<h2>口径与限制</h2><ul>"
                 "<li>E2/E3 为 0..5 精确格口径（双侧同网格·outcome 同网格）；other 全口径未进判据（exploratory）</li>"
                 "<li>HistGB 原生吃 nan 与 logistic 的 train-mean 填充 impute 口径不同，仅 exploratory</li>"
                 "<li>特征市场价只用 crsOpen；ttg/had/crs 封盘只作对手盘（防泄漏口径）</li>"
                 "<li>featMask 分层为红线②要求（防『只在易映射联赛赢』假象）</li>"
                 "<li>主判据 FAIL → tail_pick/影子层不激活（prereg 红线）</li></ul>")
    lines.append("<p style=\"color:#888;font-size:12px\">开发者 sszhang · 判据预注册 engine/cache/tail-model-prereg.json · 铁律 14②/15</p>")
    lines.append("</body></html>")
    return "".join(lines)
```

main 扩展（替换 Task 6 的 main）：

```python
def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="右尾肥尾模型训练评估链")
    ap.add_argument("cmd", choices=["run", "run-all"])
    args = ap.parse_args()
    check_prereg()
    rows, meta = load_dataset()
    result = {"logistic": run_folds(rows, meta)}
    if args.cmd == "run-all":
        result["histgbE1"] = run_histgb_e1(rows, meta)
        mapped, unmapped = split_by_mapping(rows)
        result["mappedLayer"] = {"E1": run_folds(mapped, meta)["E1"]} if len(mapped) > 500 else {"E1": {"n": 0, "verdict": "SKIP"}}
        result["unmappedLayer"] = {"E1": run_folds(unmapped, meta)["E1"]} if len(unmapped) > 500 else {"E1": {"n": 0, "verdict": "SKIP"}}
    result["datasetMeta"] = {k: meta[k] for k in ("version", "count", "fingerprint")}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    (OUT_DIR / "tail-model-report.html").write_text(render_report(result), encoding="utf-8")
    print(json.dumps({e: result["logistic"][e] for e in ("E1", "E2", "E3")}, ensure_ascii=False))
    return 0
```

- [ ] **Step 4: 跑测试通过 + 全量 run-all**

Run: `python3 -m pytest engine/tests/test_tail_train.py engine/tests/test_tail_dataset.py -v`
Expected: 全 PASS

Run: `python3 engine/scripts/tail_train.py run-all`
Expected: stdout 打印 E1/E2/E3 verdict（主判据 PASS/FAIL 落 run.json + tail-model-report.html）

- [ ] **Step 5: Commit + 归档**

```bash
git add engine/scripts/tail_train.py engine/tests/test_tail_train.py data/04-summaries/tail-model/
git commit -m "test(右尾): run-all裁决——HistGB对照+featMask分层+HTML报告·主判据E1落verdict"
```

---

## Self-Review 记录

- **Spec 覆盖**：§2 四族特征（Task 2/3/4）✓ §2.5 jsonl+指纹（Task 5）✓ §3 walk-forward+判据（Task 1/6）✓ §3.3 双模型（Task 7 HistGB）✓ 红线② featMask 分层（Task 7）✓ §4 tail_pick=**明示 out of scope**（prereg 红线"主判据 FAIL 不激活"；PASS 后另立项）✓ 红线①回收率模拟自证伪=无回收率模拟代码（连模拟都不做，更不可能虚报）——比 spec 更保守，合规
- **占位符**：Task 6 给出 time_folds/predict_event_probs 的定稿修正版（初稿笔误显式标注"定稿用这个"），无 TBD
- **类型一致**：`LeagueIndex.window -> list[dict]`（Task 3 定义，Task 4 hst 复用经 `_LeagueIndexFromRows`）✓ `market_event_probs -> tuple|None`（Task 6 定义，Task 7 消费）✓ dataset 行顶层 `ttg/had/crs` 补挂要求已写入 Task 6 实现注意第 3 条（Task 5 执行时补）✓ prereg frozenParams ↔ 模块常量（C/MAX_ITER_LR/BOOT_B/SEED/LOGLLOSS_CLIP 在 check_prereg 校验；STATS_WINDOWS/MIN_WINDOW_N/HST_WINDOW 在 tail_dataset 侧，不校验但由测试+meta.featureNames 冻结）✓
- **已知风险**：①`_boot_ci_diff` 签名按四臂回测用法 `(diffs, seed=, n_boot=)` 假定——执行者首步先 grep 确认签名，不符则以实际为准适配调用（不改函数本体）②13515 场 × 11 段 expanding 重训 logistic 三次方级耗时≈10~20 分钟可接受；若超 40 分钟，段内 train 采样上限 8000 行（prereg 未冻结训练容量·允许，报告标注）③`crs_power_devig` 对 crsOpen 的 31 项池要求 ≥20——load_hist_window 已保证双池 ≥20，market_anchor_features 不会空跑（保险分支保留）
