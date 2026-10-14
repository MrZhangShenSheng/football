# 比分端三层降级链分批回测（3/6/12个月）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对比分端生产链（三层降级：市场CRS去水→market_lambda反解→V3W兜底）做防泄漏的分批回测（W1=3月/W2=6月/W3=12月裁决窗），以真实体彩赔率结算收益率，主判据=W3 窗 A3(V3W) vs A1(市场) top1 回收率配对差 bootstrap CI 下限>0。

**Architecture:** 独立回测编排器 `engine/scripts/backtest_crs_batch.py` + 预注册判据文件（跑前冻结）。四臂强制路由（A0 封盘 null 天花板 / A1 开盘 devig / A2 强制层2反解 / A3 强制层3 V3W / A4 生产降级复刻），结算器与预测器物理分离。复用 strength_chain_eval 的 as-of 组件与 bootstrap 工具，不改动生产链任何文件。

**Tech Stack:** Python3 + 现有模块（market_lambda / strength_loaders / strength_chain_eval 内部函数 / score_matrix）+ pytest（engine/tests/ 惯例）。

**Spec:** 本计划自带完整设计（brainstorming 会话 2026-10-13 产出+review 修正两处：消融臂改强制路由、W3 裁决窗改 2024-10~2025-09 防参数污染）。设计正文见本文件"设计冻结"节——**执行者须先读该节**。

## Global Constraints

- **判据跑前冻结**：`engine/cache/batch-backtest-prereg.json` 必须在任何回测执行前 commit（Task 1）；跑后禁改口径（铁律 14②）
- **防泄漏三道闸**：模型输入只看 as_of−2天 前联赛库（`team_state_on(dc_rolling=True)` 内建 lag）；模型输入价=crsOpen 开盘价（A3/A4 决策时点=开赛日前一天）；结算器只读赛果、永不回写 ctx
- **真实赔率结算**：成交价一律=封盘 `crs` 字段；未挂牌比分落 `other_*` 键（`_score_facts` 已处理）
- **配对完整性**：缺 crsOpen（<20 项）的场次四臂同跳，禁单臂跳（防选择偏倚）
- **多重比较锁定**：主判据唯一（W3·A3 vs A1·top1·CI 下限>0）；其余输出全部标 `exploratory: true`
- **正收益红线**：任何臂 W 窗回收率>100% → 必须先跑 `--leak-audit`（as_of 再提前2天 A/B）+ 随机基线自证伪，通过才进报告结论
- 变量命名 camelCase；魔法值入常量 UPPER_SNAKE_CASE；开发者注释署名 sszhang

## 设计冻结（执行者必读）

### 数据源
`engine/cache/hist_odds/crs_hist_2023-09-01_2025-09-30.json`（8575 场）+ `crs_hist_2025-10-01_2026-09-28.json`（4999 场）。每场字段：`matchId, code, date, league, home, away, score("h:a"), halfScore, had{h,d,a}, single, crs(封盘31项), crsOpen(开盘), crsSnapshots, ttg`。crs 键格式：`"5:0"` / `"other_h"` / `"other_d"` / `"other_a"`（已归一，与生产 pick 键同构）。

### 四臂定义（强制路由，非生产降级语义）
| 臂 | 输入 | 输出排序 | 回答 |
|:---|:---|:---|:---|
| A0 | 封盘 crs → `mkl.crs_power_devig` | 去水概率降序 | null 天花板（自检≈84%） |
| A1 | 开盘 crsOpen → `mkl.crs_power_devig` | 同上 | 开盘信息价值 |
| A2 | 封盘 ttg+had → `mkl.lam_sum_from_ttg`+`mkl.solve_lamh` → `sm.dc_matrix(λ, RHO_ZERO)` | 矩阵概率降序 | 反解结构增益 |
| A3 | `se._predict_match(hid, aid, date-1, ctx, memo, beta=0.05)` → matrix | 矩阵概率降序 | V3W 纯模型臂（主判据） |
| A4 | `pick_source({"crs": crsOpen归一, "ttg": ttg, "had": had})` → None 则 A3 | 各 src 排序 | 生产降级链一致性（预期≈A1） |

已知限制（报告必标）：A2/A4 层2 输入用封盘 ttg/had（hist_odds 无 ttgOpen/hadOpen）；A3/A4 决策时点=开赛前一日（生产=临场）；W1/W2 含 V3W 参数回望（HST 10956 场含近期）——仅 W3（2024-10~2025-09）为真时间外。

### 窗口
| 窗 | 日期区间 | 预期场次 | 定位 |
|:---|:---|:--:|:---|
| W1 | 2026-07-01 ~ 2026-09-28 | ~1000 | 管道验证（结论不作依据） |
| W2 | 2026-04-01 ~ 2026-09-28 | ~2400 | 参考（含参数回望） |
| W3 | 2024-10-01 ~ 2025-09-30 | ~2900 | **裁决窗**（真时间外） |

### 结算
- 口径一（主判据）：逐场 top1~4 单关、每注 2 元、封盘价成交。`回收率 = Σ派彩 / Σ(2×k)`；派彩=命中时 `2 × odds[pick]`
- 口径二（参考）：每日按 A3 排序取前 4 场 × 每场 top2 双选组 12 注 24 元模拟票（复刻生产出卡），标 `exploratory`
- 配对差：同场同 k 的逐注派彩差 → `_boot_ci_diff(diffs)` 95% CI

---

### Task 1: 预注册判据文件冻结

**Files:**
- Create: `engine/cache/batch-backtest-prereg.json`
- Create: `docs/2026-10-13-crs-batch-backtest-design.html`（spec 归档，HTML 按 charset utf-8 规范）

**Interfaces:**
- Produces: prereg JSON schema——回测执行时 `check_prereg()` 校验指纹一致才允许出报告（Task 7 消费）

- [ ] **Step 1: 写 prereg JSON（判据全文冻结）**

```json
{
  "registeredAt": "2026-10-13",
  "spec": "比分端三层降级链分批回测 v1",
  "windows": {
    "W1": {"from": "2026-07-01", "to": "2026-09-28", "role": "pipeline-check"},
    "W2": {"from": "2026-04-01", "to": "2026-09-28", "role": "reference-param-lookback"},
    "W3": {"from": "2024-10-01", "to": "2025-09-30", "role": "adjudication"}
  },
  "arms": ["A0_close_null", "A1_open_devig", "A2_lambda_reverse", "A3_v3w_model", "A4_prod_fallback"],
  "primaryCriterion": {
    "window": "W3", "arm": "A3_v3w_model", "baseline": "A1_open_devig",
    "metric": "top1 回收率逐场配对差", "ci": "bootstrap95 B=2000",
    "pass": "CI 下限 > 0"
  },
  "secondary": [
    {"window": "W3", "arm": "A2_lambda_reverse", "baseline": "A1_open_devig", "metric": "同上"},
    {"window": "W3", "arm": "A4_prod_fallback", "baseline": "A1_open_devig", "metric": "同上·预期≈0"}
  ],
  "exploratoryOnly": ["W1", "W2", "top2-4", "口径二模拟实票", "联赛分层"],
  "redlines": [
    "任何臂窗口回收率>1.0 → 先 --leak-audit(A/B as_of-3天)+随机基线，不过不进结论",
    "A0 top1 回收率窗内偏离 0.80~0.88 → 管道 bug 中断"
  ],
  "frozenParams": {"beta": 0.05, "lag_days": 2, "decision_day_offset": -1,
                   "min_pool_items": 20, "k_max": 4, "boot_B": 2000, "seed": 20261013}
}
```

- [ ] **Step 2: 写 spec HTML**（内容=本计划"设计冻结"节转 HTML，含四臂表/窗口表/判据/限制节，`<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">` 开头）

- [ ] **Step 3: Commit（判据凭证，先于一切代码执行）**

```bash
git add engine/cache/batch-backtest-prereg.json docs/2026-10-13-crs-batch-backtest-design.html
git commit -m "prereg(回测): 分批回测判据冻结——W3裁决窗A3vsA1主判据·四臂强制路由·红线双条"
```

### Task 2: hist_odds 装载器 + 键转换

**Files:**
- Create: `engine/scripts/backtest_crs_batch.py`
- Test: `engine/tests/test_backtest_crs_batch.py`

**Interfaces:**
- Produces: `load_hist_window(date_lo: str, date_hi: str) -> list[dict]`（行结构：`{date, code, league, home, away, score, had, ttg, crs, crsOpen}`，两价池均 ≥MIN_POOL_ITEMS 项）；`matrix_key_to_pick(k: str) -> str`（`'s01s00'→'1:0'`，other 三键直通）

- [ ] **Step 1: 写失败测试**

```python
# engine/tests/test_backtest_crs_batch.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from backtest_crs_batch import load_hist_window, matrix_key_to_pick

def test_matrix_key_to_pick():
    assert matrix_key_to_pick("s01s00") == "1:0"
    assert matrix_key_to_pick("s05s03") == "5:3"
    assert matrix_key_to_pick("other_h") == "other_h"

def test_load_window_filters_and_keeps_pairs(tmp_path, monkeypatch):
    # 造 2 场迷你库：1 场两价齐、1 场缺 crsOpen → 只留前者
    fake = {"range": "x", "count": 2, "matches": [
        {"date": "2025-03-01", "code": "周六001", "league": "英超", "home": "A", "away": "B",
         "score": "2:1", "had": {"h": 1.5, "d": 4.0, "a": 5.0}, "ttg": {"s0": 3.0},
         "crs": {f"{i}:{j}": 8.0 for i in range(3) for j in range(3)},
         "crsOpen": {f"{i}:{j}": 8.5 for i in range(3) for j in range(3)}},
        {"date": "2025-03-02", "code": "周六002", "league": "英超", "home": "C", "away": "D",
         "score": "1:1", "had": {"h": 2.0, "d": 3.0, "a": 3.5}, "ttg": {},
         "crs": {f"{i}:{j}": 8.0 for i in range(3) for j in range(3)}, "crsOpen": {}},
    ]}
    p = tmp_path / "crs_hist_fake.json"; p.write_text(__import__("json").dumps(fake), encoding="utf-8")
    monkeypatch.setattr("backtest_crs_batch.HIST_DIR", tmp_path)
    rows = load_hist_window("2025-03-01", "2025-03-31")
    assert len(rows) == 1 and rows[0]["code"] == "周六001"
```

- [ ] **Step 2: 跑测试确认失败**：`python3 -m pytest engine/tests/test_backtest_crs_batch.py -v` → ImportError

- [ ] **Step 3: 实现**

```python
# engine/scripts/backtest_crs_batch.py 文件头 + 常量 + 装载器
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
```

- [ ] **Step 4: 跑测试通过** → PASS
- [ ] **Step 5: Commit** `git add -A && git commit -m "feat(回测): hist窗口装载器+矩阵键转换——配对完整性过滤"`

### Task 3: 结算器（封盘成交 + _score_facts 复用）

**Files:**
- Modify: `engine/scripts/backtest_crs_batch.py`
- Test: `engine/tests/test_backtest_crs_batch.py`（追加）

**Interfaces:**
- Consumes: `strength_chain_eval._score_facts(score, priced_keys)` → `(matrix_key, tg, side)`
- Produces: `settle_row(picks_ranked: list[str], row: dict, k: int) -> dict`——`{"picks": [...前k], "hit": pick|None, "odds": float, "payout": float, "dropped": int}`；无价 pick 记 dropped 废注

- [ ] **Step 1: 失败测试**

```python
def test_settle_row_hit_and_unpriced():
    from backtest_crs_batch import settle_row
    row = {"score": "2:1", "crs": {"2:1": 9.5, "1:1": 7.0, "1:0": 8.0, "other_h": 40.0}}
    r = settle_row(["2:1", "1:1", "1:0"], row, 2)
    assert r["hit"] == "2:1" and r["odds"] == 9.5 and r["payout"] == 2 * 9.5
    r2 = settle_row(["9:9", "2:1"], row, 2)   # 9:9 无价 → dropped，次选命中
    assert r2["dropped"] == 1 and r2["hit"] == "2:1"

def test_settle_row_unlisted_score_falls_to_other():
    from backtest_crs_batch import settle_row
    row = {"score": "7:0", "crs": {"other_h": 45.0, "1:1": 7.0}}
    r = settle_row(["other_h"], row, 1)
    assert r["hit"] == "other_h" and r["odds"] == 45.0
```

- [ ] **Step 2: 跑确认失败** → NameError: settle_row
- [ ] **Step 3: 实现**

```python
from strength_chain_eval import _score_facts

def settle_row(picks_ranked: list[str], row: dict, k: int) -> dict:
    """前 k pick 按封盘价结算（2元/注）。赛果经 _score_facts 落归一键；无价 pick=废注。"""
    crs = row["crs"]
    facts = _score_facts(row["score"], set(crs.keys()))
    actual_pick = facts[0] and matrix_key_to_pick(facts[0]) if facts else None
    picks, dropped, hit, odds = [], 0, None, 0.0
    for p in picks_ranked:
        if len(picks) >= k:
            break
        if p not in crs:
            dropped += 1; continue          # 无价废注（不入 k 计数）
        picks.append(p)
        if hit is None and p == actual_pick:
            hit, odds = p, float(crs[p])
    payout = 2.0 * odds if hit else 0.0
    return {"picks": picks, "hit": hit, "odds": odds, "payout": payout, "dropped": dropped}
```

- [ ] **Step 4: 测试通过**（注意 `test_settle_row_unlisted_score_falls_to_other` 里 `_score_facts("7:0", keys)` 应返回 `other_h` 矩阵键——若断言失败先打印 facts 调口径，矩阵键转 pick 后即 other_h）
- [ ] **Step 5: Commit `feat(回测): 结算器——封盘成交+未挂牌落other+无价废注`**

### Task 4: A0/A1/A2 臂（市场侧三臂）

**Files:**
- Modify: `engine/scripts/backtest_crs_batch.py`
- Test: 追加

**Interfaces:**
- Consumes: `market_lambda` 模块（`import market_lambda as mkl`）、`score_matrix as sm`、`mkl.RHO_ZERO`
- Produces: `arm_a0(row)->list[str]` / `arm_a1(row)->list[str]` / `arm_a2(row)->list[str]`（均返回按概率降序的 pick 键全排序）

- [ ] **Step 1: 失败测试**

```python
def test_arm_a0_orders_by_devig_prob():
    from backtest_crs_batch import arm_a0
    row = {"crs": {"1:1": 7.0, "1:0": 8.0, "0:0": 9.0}, "crsOpen": {}, "had": {}, "ttg": {}}
    ranked = arm_a0(row)                     # 1:1 赔最低 → 去水概率最高
    assert ranked[0] == "1:1"

def test_arm_a2_lambda_reverse_orders():
    from backtest_crs_batch import arm_a2
    row = {"crs": {}, "crsOpen": {}, "ttg": {f"s{i}": 3.0 for i in range(8)},
           "had": {"h": 1.4, "d": 4.5, "a": 6.5}}
    ranked = arm_a2(row)                     # 强主 λ→矩阵 top 应为主胜小比分
    assert ranked[0] in ("1:0", "2:0", "2:1", "3:0")
```

- [ ] **Step 2: 确认失败** → ImportError
- [ ] **Step 3: 实现**

```python
sys.path.insert(0, str(Path(__file__).resolve().parent))
import market_lambda as mkl
import score_matrix as sm

def _devig_ranked(pool: dict) -> list[str]:
    p = mkl.crs_power_devig(pool)
    return [k for k, _ in sorted(p.items(), key=lambda kv: -kv[1])] if p else []

def arm_a0(row: dict) -> list[str]:
    """null 天花板：封盘 CRS power 去水降序。开发者 sszhang"""
    return _devig_ranked(row["crs"])

def arm_a1(row: dict) -> list[str]:
    """开盘信息臂：开盘 CRS power 去水降序。"""
    return _devig_ranked(row["crsOpen"])

def arm_a2(row: dict) -> list[str]:
    """强制层2：封盘 ttg+had 反解 λ(ρ=0) → 泊松矩阵降序（测反解结构增益，与 A0 同信息源）。"""
    ttg, had = row["ttg"], row["had"]
    if len(ttg) < 6 or not (had.get("h") and had.get("d") and had.get("a")):
        return []
    lam_sum = mkl.lam_sum_from_ttg(ttg)
    p3 = mkl.had_devig(had["h"], had["d"], had["a"])
    lh = mkl.solve_lamh(lam_sum, p3[0])
    mat = sm.dc_matrix(lh, lam_sum - lh, mkl.RHO_ZERO)
    ranked = sorted(mat.items(), key=lambda kv: -kv[1])
    return [matrix_key_to_pick(k) for k, _ in ranked]
```

- [ ] **Step 4: 测试通过**
- [ ] **Step 5: Commit `feat(回测): A0/A1/A2市场侧三臂`**

### Task 5: A3/A4 臂（V3W + 生产降级复刻）

**Files:**
- Modify: `engine/scripts/backtest_crs_batch.py`
- Test: 追加

**Interfaces:**
- Consumes: `strength_chain_eval._predict_match(hid, aid, as_of, ctx, memo, beta)`；`strength_loaders.build_ctx(leagues)`；`strength_chain_prod.pick_source(odds)`；队名→规范ID 映射经 `_aliases.json`（`strength_loaders._norm_team`）
- Produces: `build_ctx_cached() -> dict`（34 库全量 ctx，进程级缓存）；`arm_a3(row, ctx, memo) -> list[str]`（V3W 矩阵降序转 pick 键；队未映射返回 `[]`）；`arm_a4(row, ctx, memo) -> list[str]`（`pick_source` 层1/2 → None 走 A3）

- [ ] **Step 1: 失败测试**

```python
def test_arm_a3_unmapped_returns_empty():
    from backtest_crs_batch import arm_a3
    row = {"home": "不存在的队名XYZ", "away": "同样不存在ABC", "date": "2025-03-01"}
    assert arm_a3(row, {}, {}) == []

def test_arm_a4_prefers_market_when_open_full():
    from backtest_crs_batch import arm_a4
    row = {"crs": {}, "crsOpen": {f"{i}:{j}": 8.0 for i in range(4) for j in range(4)},
           "ttg": {f"s{i}": 3.0 for i in range(8)}, "had": {"h": 1.5, "d": 4.0, "a": 5.0},
           "home": "X", "away": "Y", "date": "2025-03-01"}
    ranked = arm_a4(row, {}, {})             # 开盘 crs 齐 → 层1，排序=开盘 devig 序
    assert ranked and isinstance(ranked, list)
```

- [ ] **Step 2: 确认失败** → ImportError
- [ ] **Step 3: 实现**

```python
from datetime import date, timedelta
import strength_loaders as sl
from strength_chain_eval import _predict_match
from strength_chain_prod import pick_source

BETA = 0.05
_CTX_CACHE: dict = {}

def build_ctx_cached() -> dict:
    """34 库全量 ctx·进程级缓存（四臂三窗共享·防交叉污染即共享同一事实源）。"""
    if "ctx" not in _CTX_CACHE:
        leagues = sorted({p.stem.removesuffix("_matches")
                          for p in (ROOT / "data" / "02-results" / "league").glob("*_matches.json")})
        _CTX_CACHE["ctx"] = sl.build_ctx(leagues)
    return _CTX_CACHE["ctx"]

def _team_ids(row: dict):
    aliases = sl.load_aliases()
    return (sl._norm_team(row["home"], aliases), sl._norm_team(row["away"], aliases))

def arm_a3(row: dict, ctx: dict, memo: dict) -> list[str]:
    """强制层3：V3W 全链·as_of=开赛前一日（决策时点防泄漏）。未映射→[]。开发者 sszhang"""
    hid, aid = _team_ids(row)
    if not hid or not aid:
        return []
    as_of = date.fromisoformat(row["date"]) + timedelta(days=DECISION_DAY_OFFSET)
    try:
        pred = _predict_match(hid, aid, as_of, ctx, memo, BETA)
    except Exception:
        return []
    if not pred:
        return []
    mat = pred["matrix"]
    return [matrix_key_to_pick(k) for k, _ in sorted(mat.items(), key=lambda kv: -kv[1])]

def arm_a4(row: dict, ctx: dict, memo: dict) -> list[str]:
    """生产降级复刻：开盘价入 pick_source（层1 CRS去水→层2 反解）·None→A3 兜底。"""
    odds = {"crs": row["crsOpen"], "ttg": row["ttg"], "had": row["had"]}
    src = pick_source(odds)
    if src:
        return [x["pick"] for x in src["top5"]] if src["src"] == "market_crs" else \
               [matrix_key_to_pick(k) for k, _ in sorted(
                   sm.dc_matrix(src["lam"][0], src["lam"][1], mkl.RHO_ZERO).items(),
                   key=lambda kv: -kv[1])]
    return arm_a3(row, ctx, memo)
```

注：`DECISION_DAY_OFFSET = -1` 入模块常量（prereg frozenParams 同值）；`pick_source` 返回的 `top5` 已按概率降序，但层1 返回键即 pick 键、层2 的 lam 需重建矩阵。

- [ ] **Step 4: 测试通过**（A3 未映射 [] 、A4 走层1）
- [ ] **Step 5: Commit `feat(回测): A3 V3W臂+A4 生产降级复刻`**

### Task 6: 窗口编排 + 配对 bootstrap + 随机基线 + 泄漏A/B

**Files:**
- Modify: `engine/scripts/backtest_crs_batch.py`
- Test: 追加（用合成小窗端到端）

**Interfaces:**
- Consumes: `strength_chain_eval._boot_ci_diff(diffs, seed, n_boot)`、`_random_shuffle_baseline`
- Produces: `run_window(win_name: str, arms: dict[str, callable], *, k_max=4) -> dict`——每臂每 k 的 `{n, stake, payout, recovery, dropped}` + 主/次判据配对 CI + `unmappedRate`；CLI：`python backtest_crs_batch.py run W1|W2|W3` / `--leak-audit`（A3 决策日再 −2 天 A/B 对比）

- [ ] **Step 1: 失败测试（合成 3 场窗端到端）**

```python
def test_run_window_end_to_end(tmp_path, monkeypatch):
    from backtest_crs_batch import run_window
    fake = {"matches": [
        {"date": "2025-03-01", "code": f"六00{i}", "league": "英超", "home": "阿森纳", "away": "切尔西",
         "score": "2:1", "had": {"h": 1.5, "d": 4.0, "a": 5.0}, "ttg": {f"s{i}": 3.0 for i in range(8)},
         "crs": {f"{i}:{j}": 8.0 for i in range(4) for j in range(4)} | {"other_h": 40.0},
         "crsOpen": {f"{i}:{j}": 9.0 for i in range(4) for j in range(4)} | {"other_h": 45.0}}
        for i in range(3)]}
    p = tmp_path / "crs_hist_fake.json"; p.write_text(__import__("json").dumps(fake), encoding="utf-8")
    monkeypatch.setattr("backtest_crs_batch.HIST_DIR", tmp_path)
    out = run_window("WX", {"A0": lambda r, c, m: __import__("backtest_crs_batch").arm_a0(r)}, k_max=1)
    a0 = out["arms"]["A0"]["k1"]
    assert a0["n"] == 3 and a0["stake"] == 6.0 and 0.0 <= a0["recovery"] <= 10.0
```

- [ ] **Step 2: 确认失败** → ImportError run_window
- [ ] **Step 3: 实现**

```python
BOOT_B, SEED, K_MAX, DECISION_DAY_OFFSET = 2000, 20261013, 4, -1

def run_window(win_name: str, arms: dict, *, k_max: int = K_MAX, hist_dir: Path | None = None) -> dict:
    """窗口编排：装载→四臂逐场排序→逐 k 结算→聚合+配对 CI。arm 签名 (row, ctx, memo)->list。
    判据仅按 prereg 主次口径输出，其余标 exploratory。开发者 sszhang"""
    import strength_chain_eval as se
    from collections import defaultdict
    win = load_window_range(win_name)                      # 读 prereg windows
    rows = load_hist_window(win["from"], win["to"], hist_dir=hist_dir)
    ctx = build_ctx_cached()
    memo: dict = {}
    per = {arm: {k: {"n": 0, "stake": 0.0, "payout": 0.0, "dropped": 0} for k in range(1, k_max + 1)}
           for arm in arms}
    pay_detail = defaultdict(lambda: defaultdict(list))    # arm -> k -> 逐注派彩（配对用）
    unmapped = {arm: 0 for arm in arms}
    for row in rows:
        for arm, fn in arms.items():
            ranked = fn(row, ctx, memo)
            if not ranked:
                unmapped[arm] += 1; continue
            for k in range(1, k_max + 1):
                st = settle_row(ranked, row, k)
                per[arm][k]["n"] += 1
                per[arm][k]["stake"] += 2.0 * k
                per[arm][k]["payout"] += st["payout"]
                per[arm][k]["dropped"] += st["dropped"]
                pay_detail[arm][k].append(st["payout"])
    report = {"window": win_name, "rows": len(rows),
              "arms": {a: {f"k{k}": {**v, "recovery": round(v["payout"] / v["stake"], 4) if v["stake"] else None}
                           for k, v in ks.items()} for a, ks in per.items()},
              "unmapped": unmapped}
    # 主判据/次判据（prereg 口径）
    for pair in prereg_pairs(win_name):
        hi, base, k = pair["arm"], pair["baseline"], 1
        diffs = [x - y for x, y in zip(pay_detail[hi][k], pay_detail[base][k])]
        ci = se._boot_ci_diff(diffs, seed=SEED, n_boot=BOOT_B)
        report.setdefault("criteria", []).append({**pair, "k": k, "diffMean": round(sum(diffs) / len(diffs), 4),
                                                  "ci95": [round(x, 4) for x in ci],
                                                  "verdict": "PASS" if ci[0] > 0 else "FAIL",
                                                  "exploratory": win_name not in ("W3",)})
    return report
```

（`load_window_range` 读 prereg JSON 的 windows 段；`prereg_pairs` 读 primaryCriterion+secondary；两者各 8 行实现，读文件返回 dict/list。）

- [ ] **Step 4: 测试通过**（合成窗回收率数值域校验）
- [ ] **Step 5: Commit `feat(回测): 窗口编排+配对CI+prereg判据联动`**

### Task 7: W1 试跑 + 泄漏审计 + 报告落盘

**Files:**
- Modify: `engine/scripts/backtest_crs_batch.py`（main/报告生成）
- Produces: `data/04-summaries/crs-batch-backtest/{W1,W2,W3}.json` + `crs-batch-backtest-report.html`；CLI `run-all`

- [ ] **Step 1: 实现 main + HTML 报告**（charset utf-8、四臂×三窗×k1-4 矩阵表、主判据置顶、exploratory 标注、限制节三条：A2 封盘反解/决策时点偏前/W1W2 参数回望）

```python
def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "run-all", "leak-audit"])
    ap.add_argument("window", nargs="?")
    args = ap.parse_args()
    if args.cmd == "run":
        rep = run_window(args.window, ARM_FNS)
        out_dir = ROOT / "data" / "04-summaries" / "crs-batch-backtest"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{args.window}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(rep["criteria"], ensure_ascii=False))
        return 0
    if args.cmd == "leak-audit":
        # A3 决策日再 -2 天重跑指定窗，对比回收率与判据臂输出——差异大=泄漏警讯（红线）
        ...同 run 但 DECISION_DAY_OFFSET=-3、输出写 {window}-leakaudit.json、打印 A/B 回收率对照...
    ...
```

ARM_FNS = `{"A0_close_null": arm_a0-wrapper, ...}`——A0/A1/A2 包装为 `(row, ctx, memo)->list` 签名（忽略 ctx/memo）。

- [ ] **Step 2: W1 试跑**：`python3 engine/scripts/backtest_crs_batch.py run W1` → 校验 ①rows≈1000 ②**A0 top1 回收率 ∈ [0.80, 0.88]**（红线自检，出界即管道 bug 中断修完再跑）③dropped/unmapped 率打印

- [ ] **Step 3: 泄漏审计**：`python3 engine/scripts/backtest_crs_batch.py leak-audit W1` → A3(as_of-1) vs A3(as_of-3) 回收率差应小（|Δrecovery|<2pp 为过）；任何臂回收率>1.0 时必跑

- [ ] **Step 4: W2/W3 裁决跑**：`run-all` → 三窗齐 + HTML 报告生成 + 主判据 verdict 打印

- [ ] **Step 5: Commit + 报告归档** `git add -A && git commit -m "test(回测): W1管道自检+A0天花板校验+三窗分批+主判据裁决"`

---

## Self-Review 记录

- **Spec 覆盖**：四臂（Task 4/5）✓ 三窗（Task 6/7）✓ 双口径——口径二（模拟实票）**未入任务**：裁定为 W1 跑通后若主判据有信号再加（YAGNI·exploratory 地位），在 W1 报告 limitations 注明
- **占位符**：Task 7 leak-audit 分支用 `...` 省略——执行者按 run 分支同构实现（决策日偏移参数化），已在注释写明输出契约
- **类型一致**：arm 签名统一 `(row, ctx, memo)->list[str]`（A0/A1/A2 在 Task 7 包装）✓；`settle_row` 消费 `matrix_key_to_pick` 产物 ✓；prereg `frozenParams.decision_day_offset` ↔ 模块常量 `DECISION_DAY_OFFSET=-1` ✓
- **已知风险**：A0 天花板自检区间 [0.80,0.88] 为窗口子集容差（全库口径 84%）——若 W1 实测显著偏出需回查 power_devig 参数与 hist_odds 键完备性后再放行 W2/W3
