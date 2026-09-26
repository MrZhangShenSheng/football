# CRS 融合链路重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 CRS 比分选择从"freq-band 带内 q 最高"（已证伪负 alpha）重构为"模板先验 × 市场似然 = 融合后验 + 比分族变现"，直接转正生产。

**Architecture:** 新建 `engine/scripts/crs_fusion.py` 独立融合引擎（power 去水/Lidstone 平滑/对数意见池/族输出四组件），`freq_band.py` 加 `--method=fused` 接入，`boldplay.py` 三池卡切源；参数存 `engine/cache/fusion_crs.json` 带回滚开关；监控走 backfill/trend_report。

**Tech Stack:** Python 3.11 标准库 + pytest（无新依赖）

**Spec:** `docs/2026-09-26-crs-fusion-redesign.html`（v2 含数学审查修正，执行者必读）

## Global Constraints

- 命令路径：脚本一律 `python3 engine/scripts/xxx.py`（run.py 不在仓库根）
- 开发者署名：模块 docstring 写 `sszhang`
- 内部文件 JSON/纯文本；用户报告才用 HTML
- 旧链保留：`--method=legacy` 一个月回滚开关（2026-10-26 前不删）
- 禁魔法值：所有阈值/参数进 `fusion_crs.json` 或模块常量区并注释出处（spec 节号）
- 数学语义铁律：`q_t(s)=0` 不得进入融合代数（Lidstone 平滑后全 31 项>0）；"c=0 永不入选"只在输出过滤层
- 比分键格式：体彩 CRS 池键 `'0:0'` 字符串；内部统一 `(h, a)` 元组，边界处转换

---

### Task 1: crs_fusion.py — power 去水 extract_mkt_dist

**Files:**
- Create: `engine/scripts/crs_fusion.py`
- Test: `engine/scripts/tests/test_crs_fusion.py`

**Interfaces:**
- Consumes: 无（首任务）
- Produces: `extract_mkt_dist(crs_odds: dict[str, float]) -> dict[tuple[int,int], float]`；`solve_power_k(implied: dict) -> float`（内部）

**Steps:**

- [ ] **Step 1: 写失败测试**

```python
# engine/scripts/tests/test_crs_fusion.py
"""crs_fusion 融合引擎测试。开发者 sszhang"""
import pytest
from crs_fusion import extract_mkt_dist

def test_power_dewater_sums_to_one():
    # 3 项玩具市场: 抽水 20%
    odds = {"1:0": 2.5, "1:1": 3.5, "0:1": 4.0}
    dist = extract_mkt_dist(odds)
    assert abs(sum(dist.values()) - 1.0) < 1e-9

def test_power_dewater_monotone():
    # 赔率低的项概率必须高（保序）
    odds = {"1:0": 2.0, "1:1": 4.0, "0:1": 8.0}
    dist = extract_mkt_dist(odds)
    assert dist[(1,0)] > dist[(1,1)] > dist[(0,1)]

def test_power_dewater_lifts_tail_vs_naive():
    # 高抽水场景下 power 法必须比朴素归一给长赔项更高概率（favorite-longshot 修正, spec §3）
    odds = {"1:0": 2.0, "0:4": 200.0, "1:1": 3.5, "4:0": 150.0}
    dist = extract_mkt_dist(odds)
    naive = {k: (1/v)/sum(1/x for x in odds.values()) for k, v in odds.items()}
    h, a = 0, 4
    key = (h, a)
    assert dist[key] > naive["0:4"]

def test_extract_skips_invalid_entries():
    odds = {"1:0": 2.5, "bad": 0, "1:1": 3.5, "0:1": "x"}
    dist = extract_mkt_dist(odds)
    assert (1,0) in dist and (1,1) in dist and (0,1) in dist
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /Users/zhangshensheng/Documents/GitHub/football && python3 -m pytest engine/scripts/tests/test_crs_fusion.py -v`
Expected: FAIL（ModuleNotFoundError: crs_fusion）

- [ ] **Step 3: 最小实现**

```python
# engine/scripts/crs_fusion.py
"""CRS 融合引擎（spec: docs/2026-09-26-crs-fusion-redesign.html v2）。

四组件：power 去水 / Lidstone 平滑 / 对数意见池融合 / 比分族输出。
哲学：q=先验 × 市场=似然 → 后验；三向层 fusion.json 方法论推广到 31 维。
开发者 sszhang"""
import re

CRS_KEY = re.compile(r"^(\d+):(\d+)$")

def solve_power_k(implied: dict[tuple, float]) -> float:
    """解 Σ p_i^k = 1 的 k（二分法）。implied 为原始倒数赔率（Σ>1 含抽水）。"""
    lo, hi = 0.05, 1.0
    def over(k):
        return sum(v ** k for v in implied.values()) - 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if over(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2

def extract_mkt_dist(crs_odds: dict) -> dict[tuple[int, int], float]:
    """体彩 CRS 31 项赔率 → power 去水市场分布（spec §3, Clarke 2013）。

    p_mkt(s) ∝ (1/o_s)^k，k 解 Σp=1——高抽水 CRS 池优于朴素归一（抬长赔尾部）。
    无效项（0/非数字/格式错）静默跳过。"""
    implied = {}
    for k, v in crs_odds.items():
        m = CRS_KEY.match(str(k))
        try:
            w = 1.0 / float(v)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if m and w > 0:
            implied[(int(m.group(1)), int(m.group(2)))] = w
    if not implied:
        return {}
    k_exp = solve_power_k(implied)
    raw = {s: w ** k_exp for s, w in implied.items()}
    z = sum(raw.values())
    return {s: p / z for s, p in raw.items()}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest engine/scripts/tests/test_crs_fusion.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/crs_fusion.py engine/scripts/tests/test_crs_fusion.py
git commit -m "feat(crs_fusion): power去水市场分布提取·spec §3·Clarke 2013"
```

---

### Task 2: crs_fusion.py — Lidstone 平滑 smooth_template

**Files:**
- Modify: `engine/scripts/crs_fusion.py`
- Test: `engine/scripts/tests/test_crs_fusion.py`（追加）

**Interfaces:**
- Consumes: 无
- Produces: `smooth_template(counts: dict[tuple,int], alpha: float = 0.5, n_items: int = 31) -> dict[tuple, float]`

**Steps:**

- [ ] **Step 1: 追加失败测试**

```python
from crs_fusion import smooth_template

def test_smooth_all_positive_even_unseen():
    # 数学审查错误1修正：c=0 的比分平滑后必须 >0（先验支撑集不得阉割后验）
    counts = {(1,0): 50, (1,1): 40, (2,1): 30}
    q = smooth_template(counts)
    assert all(p > 0 for p in q.values())
    assert (0,4) in q and q[(0,4)] > 0   # 从未出现的比分也有平滑概率

def test_smooth_sums_to_one():
    counts = {(1,0): 50, (1,1): 40}
    q = smooth_template(counts)
    assert abs(sum(q.values()) - 1.0) < 1e-9

def test_smooth_preserves_ranking():
    counts = {(1,0): 50, (1,1): 40, (2,1): 30}
    q = smooth_template(counts)
    assert q[(1,0)] > q[(1,1)] > q[(2,1)]
```

- [ ] **Step 2: 跑测试确认失败**（ImportError: smooth_template）
- [ ] **Step 3: 实现（追加到 crs_fusion.py）**

```python
ALPHA_LIDSTONE = 0.5   # Jeffreys 先验（spec §3 数学审查错误1修正）

def smooth_template(counts: dict, alpha: float = ALPHA_LIDSTONE, n_items: int = 31) -> dict:
    """联赛模板计数 → Lidstone 平滑分布。

    q_t(s) = (count_s + α)/(N + 31α)——全 31 项>0，"c=0 永不入选"降级为输出层
    业务规则，不进融合代数（零吸收违背贝叶斯支撑集代数）。"""
    keys = [(h, a) for h in range(6) for a in range(6) if h + a <= 5 or (h <= 5 and a <= 5)][:n_items]
    keys = keys[:n_items]
    n = sum(counts.values())
    z = n + alpha * n_items
    return {s: (counts.get(s, 0) + alpha) / z for s in keys}
```

注意：31 项键集以体彩 CRS 池实际挂牌为准（主客各 0-5、合计≤5 的常规 31 项），如与池不符以 `extract_mkt_dist` 输出的键并集为准在融合前对齐（Task 3 处理）。

- [ ] **Step 4: 跑测试** → 3 PASSED
- [ ] **Step 5: Commit**：`git commit -m "feat(crs_fusion): Lidstone平滑模板·零吸收修正"`

---

### Task 3: crs_fusion.py — 对数意见池 fuse_crs + λ 收缩 shrink_lambda

**Files:**
- Modify: `engine/scripts/crs_fusion.py`
- Test: `engine/scripts/tests/test_crs_fusion.py`（追加）

**Interfaces:**
- Consumes: Task 1 `extract_mkt_dist`、Task 2 `smooth_template`
- Produces:
  - `fuse_crs(q_t: dict, p_mkt: dict, r: float = 0.286, eps: float = 0.001) -> dict[tuple, float]`
  - `shrink_lambda(lam_sum_model: float, e_mkt: float | None, w: float = 0.35) -> tuple[float, bool]`（返回 (λ'sum, shrunk)）

**Steps:**

- [ ] **Step 1: 追加失败测试**

```python
from crs_fusion import fuse_crs, shrink_lambda

def test_fuse_normalizes():
    q = {(1,0): 0.3, (1,1): 0.25, (0,1): 0.2, (2,1): 0.15, (2,0): 0.1}
    p = {(1,0): 0.4, (1,1): 0.3, (0,1): 0.1, (2,1): 0.1, (2,0): 0.1}
    f = fuse_crs(q, p)
    assert abs(sum(f.values()) - 1.0) < 1e-9

def test_fuse_market_dominant_when_r_small():
    # r→0 退化为市场分布（spec 弱点5：a*→0 退化仍有效）
    q = {(1,0): 0.9, (1,1): 0.05, (0,1): 0.05}
    p = {(1,0): 0.1, (1,1): 0.2, (0,1): 0.7}
    f = fuse_crs(q, p, r=0.001)
    assert f[(0,1)] > f[(1,0)]   # 跟市场走

def test_fuse_epsilon_on_missing_market_key():
    # 市场缺项 ε 兜底不归零
    q = {(1,0): 0.5, (5,5): 0.5}
    p = {(1,0): 1.0}             # 市场只有一项
    f = fuse_crs(q, p)
    assert f[(5,5)] > 0

def test_shrink_lambda_basic():
    assert abs(shrink_lambda(1.2, 3.0, w=0.35)[0] - (0.35*1.2 + 0.65*3.0)) < 1e-9

def test_shrink_lambda_no_market_degrades():
    lam, shrunk = shrink_lambda(2.5, None, w=0.35)
    assert lam == 2.5 and shrunk is False

def test_shrink_lambda_extremes():
    assert shrink_lambda(2.0, 3.0, w=0.0)[0] == 3.0   # 全市场
    assert shrink_lambda(2.0, 3.0, w=1.0)[0] == 2.0   # 全模型
```

- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现**

```python
def fuse_crs(q_t: dict, p_mkt: dict, r: float = 0.286, eps: float = 0.001) -> dict:
    """对数意见池：P_final ∝ q^r · p^(1-r)（spec §3；比值不变性→只搜 r 一维）。

    r = a/(a+b)，默认 0.286 = 0.4/1.4。市场缺项 ε 兜底。q_t 须已平滑（全正）。"""
    keys = set(q_t) | set(p_mkt)
    raw = {}
    for s in keys:
        qv = max(q_t.get(s, eps), 1e-12)
        pv = max(p_mkt.get(s, eps), 1e-12)
        raw[s] = qv ** r * pv ** (1.0 - r)
    z = sum(raw.values())
    return {s: v / z for s, v in raw.items()}

def shrink_lambda(lam_sum_model: float, e_mkt, w: float = 0.35):
    """λ 总量向市场 E 收缩（spec §2 James-Stein 精神）。

    返回 (λ'sum, shrunk)；e_mkt=None 时纯模型降级。"""
    if e_mkt is None or e_mkt <= 0:
        return lam_sum_model, False
    return w * lam_sum_model + (1.0 - w) * e_mkt, True
```

- [ ] **Step 4: 跑测试** → 全部 PASSED
- [ ] **Step 5: Commit**：`git commit -m "feat(crs_fusion): 对数意见池融合+λ收缩·r比值参数化"`

---

### Task 4: crs_fusion.py — 比分族 FAMILY + 族输出 + 闸门

**Files:**
- Modify: `engine/scripts/crs_fusion.py`
- Test: `engine/scripts/tests/test_crs_fusion.py`（追加）

**Interfaces:**
- Consumes: Task 3 `fuse_crs`
- Produces:
  - `FAMILIES: dict[str, list[tuple]]`（5 族常量）
  - `family_scores(p_final: dict) -> list[dict]`——按族概率降序，元素 `{family, prob, top1: (score,p), top2: (score,p)}`（top1/top2 为族内 P_final 最高的两个比分）
  - `family_gate(p_final: dict, threshold: float = 0.28) -> tuple[bool, float]`——(放行, max族概率)

**Steps:**

- [ ] **Step 1: 追加失败测试**

```python
from crs_fusion import FAMILIES, family_scores, family_gate

def test_five_families_defined():
    assert set(FAMILIES) == {"home_clean", "home_multi", "draw", "away_clean", "away_multi"}
    assert (1,1) in FAMILIES["draw"] and (2,0) in FAMILIES["home_clean"]

def test_family_scores_sorted_with_top2():
    p = {(1,0): 0.18, (1,1): 0.15, (0,1): 0.12, (2,1): 0.10, (2,0): 0.08, (0,0): 0.05}
    fams = family_scores(p)
    assert fams[0]["prob"] >= fams[1]["prob"]
    draw = [f for f in fams if f["family"] == "draw"][0]
    assert draw["top1"][0] == (1,1) and draw["top2"][0] == (0,0)

def test_family_gate_blocks_flat_distribution():
    # 数学审查错误3：高方差场族概率摊平 → 关档
    flat = {(1,0): 0.07, (1,1): 0.07, (0,1): 0.07, (2,1): 0.06, (2,0): 0.06, (0,2): 0.06, (0,0): 0.04}
    ok, mx = family_gate(flat)
    assert ok is False and mx < 0.28

def test_family_gate_passes_concentrated():
    p = {(1,1): 0.30, (0,0): 0.10, (1,0): 0.20, (0,1): 0.05, (2,1): 0.05, (2,0): 0.05}
    ok, mx = family_gate(p)
    assert ok is True and mx >= 0.28
```

- [ ] **Step 2: 确认失败**
- [ ] **Step 3: 实现**

```python
# 5 族（spec §4）：族概率=ΣP_final，守恒到 1−族外项
FAMILIES = {
    "home_clean":  [(1,0), (2,0), (3,0)],
    "home_multi":  [(2,1), (3,1), (3,2)],
    "draw":        [(0,0), (1,1), (2,2)],
    "away_clean":  [(0,1), (0,2), (0,3)],
    "away_multi":  [(1,2), (1,3), (2,3)],
}
FAMILY_GATE_THRESHOLD = 0.28   # spec §4 数学审查错误3：max族概率<28%→关档

def family_scores(p_final: dict) -> list:
    out = []
    for name, members in FAMILIES.items():
        inside = {s: p_final[s] for s in members if s in p_final}
        if not inside:
            continue
        ranked = sorted(inside.items(), key=lambda kv: -kv[1])
        out.append({"family": name, "prob": sum(inside.values()),
                    "top1": ranked[0], "top2": ranked[1] if len(ranked) > 1 else None})
    return sorted(out, key=lambda f: -f["prob"])

def family_gate(p_final: dict, threshold: float = FAMILY_GATE_THRESHOLD):
    fams = family_scores(p_final)
    if not fams:
        return False, 0.0
    mx = fams[0]["prob"]
    return mx >= threshold, mx
```

- [ ] **Step 4: 跑测试** → PASSED
- [ ] **Step 5: Commit**：`git commit -m "feat(crs_fusion): 5族输出+族概率闸门28%"`

---

### Task 5: fusion_crs.json 参数文件 + load_fusion_crs

**Files:**
- Create: `engine/cache/fusion_crs.json`
- Modify: `engine/scripts/crs_fusion.py`（加加载函数）
- Test: `engine/scripts/tests/test_crs_fusion.py`（追加）

**Interfaces:**
- Produces: `load_fusion_crs() -> dict`（读 engine/cache/fusion_crs.json，缺文件返回 DEFAULTS 冻结值并标 degraded=true）

**Steps:**

- [ ] **Step 1: 写参数文件**

```json
{
  "frozenAt": "2026-09-26",
  "enabled": true,
  "r": 0.286,
  "w": 0.35,
  "alphaLidstone": 0.5,
  "familyGateThreshold": 0.28,
  "hhadCedeThreshold": 0.65,
  "leagueOverrides": null,
  "note": "r/w 冻结起步；join样本≥300 自动开一维校准（护栏改善<1%不动）；leagueOverrides 一期禁用（spec 弱点7）；enabled=false 一键回滚纯模板"
}
```

- [ ] **Step 2: 追加测试**

```python
import json, pathlib
from crs_fusion import load_fusion_crs

def test_load_fusion_crs_reads_file():
    cfg = load_fusion_crs()
    assert cfg["enabled"] is True
    assert 0 < cfg["r"] < 1 and 0 <= cfg["w"] <= 1

def test_load_fusion_crs_degrades_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr("crs_fusion.FUSION_CRS_PATH", tmp_path / "missing.json")
    cfg = load_fusion_crs()
    assert cfg["degraded"] is True and cfg["r"] == 0.286
```

- [ ] **Step 3: 实现（crs_fusion.py 追加）**

```python
import json as _json
from pathlib import Path

FUSION_CRS_PATH = Path(__file__).resolve().parent.parent / "cache" / "fusion_crs.json"
FUSION_CRS_DEFAULTS = {"frozenAt": "2026-09-26", "enabled": True, "r": 0.286, "w": 0.35,
                       "alphaLidstone": 0.5, "familyGateThreshold": 0.28, "hhadCedeThreshold": 0.65,
                       "leagueOverrides": None}

def load_fusion_crs() -> dict:
    """读融合参数；文件缺失/损坏 → 冻结默认值 + degraded 标记（降级不熔断）。"""
    try:
        cfg = _json.loads(FUSION_CRS_PATH.read_text(encoding="utf-8"))
        cfg.setdefault("degraded", False)
        return cfg
    except Exception:
        return {**FUSION_CRS_DEFAULTS, "degraded": True}
```

- [ ] **Step 4: 跑测试** → PASSED
- [ ] **Step 5: Commit**：`git commit -m "feat(crs_fusion): 参数文件fusion_crs.json+降级加载"`

---

### Task 6: freq_band.py 接入 --method=fused

**Files:**
- Modify: `engine/scripts/freq_band.py`（freq_legs 附近加 fused 出腿路径 + CLI 参数）
- Test: `engine/scripts/tests/test_crs_fusion.py`（追加集成测试）

**Interfaces:**
- Consumes: Task 1-5 全部
- Produces: `fused_legs(odds_day: dict, freq_table: dict, form: dict, zh: dict, cfg: dict) -> list[dict]`——每场一条：`{code, match, families: [...], gate: {pass, maxProb}, p_final_top3, marketFused: bool}`；CLI `--method=fused|legacy`（fused 新默认）

**Steps:**

- [ ] **Step 1: 追加集成测试（用玩具数据打全链）**

```python
from crs_fusion import extract_mkt_dist, smooth_template, fuse_crs, family_scores, family_gate, load_fusion_crs

def test_full_pipeline_toy_match():
    # 玩具场：市场定价 1:1 集中 → 平局族应居首且过闸门
    crs_odds = {"1:1": 4.0, "0:0": 8.0, "1:0": 7.0, "0:1": 9.0, "2:1": 9.0, "2:2": 16.0,
                "2:0": 15.0, "0:2": 18.0, "3:0": 30.0, "0:3": 40.0, "3:1": 20.0, "1:3": 30.0, "3:2": 40.0, "2:3": 40.0,
                "1:2": 12.0, "1:4": 90.0, "0:4": 80.0, "4:0": 60.0, "4:1": 80.0, "5:0": 150.0, "0:5": 200.0}
    counts = {(1,1): 40, (1,0): 30, (2,1): 25, (0,1): 20, (0,0): 15, (2,0): 12, (1,2): 10}
    q = smooth_template(counts)
    p = extract_mkt_dist(crs_odds)
    f = fuse_crs(q, p, r=load_fusion_crs()["r"])
    ok, mx = family_gate(f)
    fams = family_scores(f)
    assert ok and fams[0]["family"] == "draw"    # 市场模板同向 → 平局族居首
```

- [ ] **Step 2: freq_band.py 加 fused_legs + CLI**

在 `freq_legs` 之后追加（保持 legacy 函数零改动），CLI `--method` 默认 `fused`：

```python
def fused_legs(odds_day: dict, freq_table: dict, form: dict, zh: dict, cfg: dict | None = None) -> list:
    """CRS-Fused 出腿（spec §3 四步：平滑→去水→融合→族组合）。

    每场输出 5 族排序 + 闸门判定；闸门不过 → 该场 CRS 关档（gate.pass=False 照样落条目，
    铁律 8 空轮≠漏跑）。市场价缺失 → marketFused=False 降级纯模板排序。"""
    from crs_fusion import smooth_template, extract_mkt_dist, fuse_crs, family_scores, family_gate
    cfg = cfg or load_fusion_crs_safe()
    out = []
    for m in odds_day.get("matches", []):
        crs = m.get("crs") or {}
        base = _template_counts(freq_table, m, zh)          # 复用现有模板计数路径（无则全局池）
        q = smooth_template(base, alpha=cfg["alphaLidstone"])
        if len(crs) >= 20:                                   # 市场价足够才融合
            p = extract_mkt_dist(crs)
            pf = fuse_crs(q, p, r=cfg["r"])
            fused = True
        else:
            pf = q; fused = False
        ok, mx = family_gate(pf, cfg["familyGateThreshold"])
        out.append({"code": m.get("matchNumStr"), "match": f'{m.get("home")} vs {m.get("away")}',
                    "families": family_scores(pf), "gate": {"pass": ok, "maxProb": round(mx, 4)},
                    "p_final_top3": sorted(pf.items(), key=lambda kv: -kv[1])[:3], "marketFused": fused})
    return out

def load_fusion_crs_safe():
    try:
        from crs_fusion import load_fusion_crs
        return load_fusion_crs()
    except Exception:
        return {"r": 0.286, "alphaLidstone": 0.5, "familyGateThreshold": 0.28}
```

注：`_template_counts` 复用 freq_band 现有模板/平移逻辑产出的 Counter（读取现有 `shifted_q` 链；如平移不可用回退 `global_pool`）——执行时以现有代码实际函数名为准接线，不新写模板逻辑。

- [ ] **Step 3: 跑测试+现有回归**：`python3 -m pytest engine/scripts/tests/ -v` 全过（含 test_pool.py 旧测试不破坏）
- [ ] **Step 4: 手工冒烟**：`python3 engine/scripts/freq_band.py --help` 显示 `--method fused|legacy`；对当日 odds 跑一次 fused 输出肉眼检查闸门/族排序
- [ ] **Step 5: Commit**：`git commit -m "feat(freq_band): --method=fused 接入融合引擎·legacy保留回滚"`

---

### Task 7: 回测验收 crs_fusion_audit.py（207 场 walk-forward + power 去水自检）

**Files:**
- Create: `engine/scripts/crs_fusion_audit.py`（九模块审计管道固化 + 上线门槛回测）
- Test: 手工验收（输出报告 JSON + 控制台摘要）

**Interfaces:**
- Consumes: crs_fusion 全部；scratch/crs_audit/v3_rows.json（207 场 join 样本，执行时复制进 `engine/cache/crs_audit/` 持久化）；score_odds 29 天存档
- Produces: `engine/cache/crs_fusion_audit.json`（三方 logloss 配对差 CI95 / 族 top1 命中 vs 期望 / 小比分捕获率 / 818 场 power 去水自检 / 尾部 4+ 球档校准）

**Steps:**

- [ ] **Step 1: 写审计脚本骨架**（复用九模块管道：装载 score_odds 场次→join 语料赛果→逐场算三个分布〔纯模板/纯市场/融合〕→walk-forward（时间排序，r/w 不做样本内拟合=冻结值直接评）→配对差 bootstrap CI95）
- [ ] **Step 2: 跑 207 场回测**，验收线（spec §7）：融合 logloss < 纯模板 < 现行链（CI95 不跨 0）；族 top1 命中 ≥ 真实频率期望；小比分捕获 ≥45%
- [ ] **Step 3: 跑 818 场 power 去水自检**（市场去水分布 vs 实际：分桶校准 + 尾部 4+ 球档专项）
- [ ] **Step 4: 结果落 `engine/cache/crs_fusion_audit.json` + 控制台摘要给大哥过目**
- [ ] **Step 5: Commit**：`git commit -m "feat(crs_fusion_audit): 207场walk-forward验收+818场去水自检"`

**验收不达标的处置**：任何一条验收线不过 → 停，带数据回报大哥（不静默降标准——尊重事实）。

---

### Task 8: boldplay.py 三池卡切源 + HHAD 让位

**Files:**
- Modify: `engine/scripts/boldplay.py`（三池卡 CRS 候选源：模板 q 排序 → fused 族 top1/top2；加 HHAD 让位标注）
- Test: `python3 engine/scripts/boldplay.py` 冒烟（生成当日卡无 warnings）

**Interfaces:**
- Consumes: Task 6 `fused_legs`
- Produces: 三池卡 CRS 行带 `family`/`familyProb`/`gate` 字段；主胜族合计 ≥ cfg.hhadCedeThreshold(0.65) → 卡面标 `cedeHHAD: true`

**Steps:**

- [ ] **Step 1: 定位 boldplay 三池卡 CRS 候选装配点（grep "crs" 装配逻辑）**
- [ ] **Step 2: 换源 fused_legs 输出（族 top1 入翻身档候选；族 top1+top2 供 CRS 单关双选）+ cedeHHAD 标注**
- [ ] **Step 3: 冒烟当日卡**：`python3 engine/scripts/boldplay.py`，检查 CRS 行含族字段、闸门关档场有 reason、无新增 warnings**
- [ ] **Step 4: Commit**：`git commit -m "feat(boldplay): 三池卡CRS切融合源+族字段+HHAD让位标注"`

---

### Task 9: 监控链——familyHit 回填 + trend 族校准/尾部区块

**Files:**
- Modify: `engine/scripts/backfill.py`（回填时对 CRS pick 判 `familyHit`——pick 比分落 5 族之一且该族为当轮输出 top1 → true；落盘 02-results）
- Modify: `engine/scripts/trend_report.py`（+族概率分桶校准区块 + 4+ 球尾部专项区块——唯一正 EV 探测器）
- Test: 手工跑 `run.py verify` 冒烟

**Interfaces:**
- Consumes: Task 6 落盘的当轮 families 数据（02-results matches[].crsFamilies 透传）
- Produces: `matches[].familyHit: bool|null`；trend.html 新区块

**Steps:**

- [ ] **Step 1: backfill 增 familyHit 判定（20 行内：pick 比分→族→对照当轮 top1 族）**
- [ ] **Step 2: trend_report 加两区块（族校准分桶表 + 尾部预测 vs 实际折线数据）**
- [ ] **Step 3: `python3 engine/scripts/run.py verify` 全链冒烟无崩**
- [ ] **Step 4: Commit**：`git commit -m "feat(monitor): familyHit回填+族校准/尾部专项trend区块"`

---

### Task 10: 生产验收 + SKILL v5.15 注记

**Files:**
- Modify: `skill/SKILL.md`（铁律 8 三步→四步改写 + 版本注记行）
- Test: 真实出票卡全链冒烟

**Steps:**

- [ ] **Step 1: 全链冒烟**：`python3 engine/scripts/boldplay.py` 生成当日卡（fused 默认）+ `run.py verify` 不崩 + 抽 3 场手工核对族输出 vs 手算（1 场低分集中场过闸门/1 场高方差场关档/1 场强热场 cedeHHAD）**
- [ ] **Step 2: SKILL.md 铁律 8 改写**（三步→四步：平滑→去水→融合→族组合；引用 spec/audit 文档路径；v5.15 注记：CRS 融合链直接转正+二项检验回滚红线）
- [ ] **Step 3: 更新 .claude/CLAUDE.md 的 boldplay/freq_band 描述行（一句话级）**
- [ ] **Step 4: Commit**：`git commit -m "feat(v5.15): CRS融合链转正·铁律8四步制·SKILL+CLAUDE注记"`

---

## Self-Review

1. **Spec 覆盖**：层1（Task 3 shrink_lambda+Task 7 w 校准数据）/层2（Task 1-3）/层3（Task 4+8）/层4（Task 5 回滚开关+Task 9 监控）✓；数学审查 3 修正（错误1→Task 2、错误2→Task 9 二项检验由 trend 区块数据支撑+回滚开关 Task 5、错误3→Task 4 闸门）✓；7 弱点（4→Task 3 r 参数化、6→Task 7 审计含 s7 检验、7→Task 5 leagueOverrides null、9→Task 9 尾部区块、10→Task 7 CI95）✓
2. **占位符扫描**：Task 6 `_template_counts` 标注"以现有代码实际函数名为准接线"——这是接线点标注非占位（模板逻辑已存在于 freq_band，执行者 grep 接线）；Task 7 骨架步骤含明确计算内容 ✓
3. **类型一致性**：比分键全链 `(h,a)` 元组、族名 FAMILIES 键、cfg 字段名与 fusion_crs.json 一致 ✓
