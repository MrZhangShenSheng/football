# 假设驱动出票引擎 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 撤销拦判断的概率闸门、新增假设层作为唯一拦截机制,让引擎按「大胆假设→小心验证→最后看赔率」出票。

**Architecture:** 闸门分两类处理——拦判断的(赔率上限/带宽上限/无库跳过)撤销,拦模型错误的(`DIVERGENCE_LIMIT`)降级为卡面标注。新增 `hypothesis` 字段挂在每条候选腿上,`verdict: refuted` 的腿不进候选。DC 与 freq_band 模板均降级为验证工具,不作推荐源。保底档显式关档。

**Tech Stack:** Python 3、pytest、既有 `engine/scripts/boldplay.py` + `freq_band.py` + `dc_predict.py`

**Spec:** `docs/superpowers/specs/2026-09-16-hypothesis-driven-engine-design.md`

## Global Constraints

- 赔率下限 `4.0`,上限 `float("inf")`
- `DIVERGENCE_LIMIT = 0.05` 常量**保留**(仍用于计算标注值),仅移除据此排除的分支
- 无库场次标 `modelSupport: "none"`;有 DC 标 `"dc"`;仅模板标 `"template"`
- 卡面按场次分组,组内赔率降序
- `verdict` 取值仅 `survived` / `refuted` / `pending`;`pending` 与 `refuted` 均不得出票
- 预算纪律不动:`ROUND_REDLINE = 30.0` 保持原值不改
- 新流程前 3 轮只出影子票
- 每个任务结束跑全量 `python -m pytest engine/tests -q`,332 个既有测试的净变化必须可解释

## 对 Spec 的一处修正

Spec §1.4 称「下限提到 4.0 后保底档选不出 5 条合格腿,自然关闭」。**该判断错误。**

`_base_legs`(`boldplay.py:523-544`)有自己独立的赔率门槛 `if min(o3) < 1.10: continue`,**不读 `ODDS_RANGE`**。改 `ODDS_RANGE` 不影响保底档。

因此保底档关档必须**显式实现**(Task 5),不是 Task 1 的副作用。Spec 该处措辞在 Task 5 完成后一并更正。

## File Structure

| 文件 | 责任 | 本计划中的改动 |
|:---|:---|:---|
| `engine/scripts/boldplay.py` | 出票主引擎:闸门常量、三档构建、卡面渲染 | 闸门撤销/降级、保底关档、假设层字段、排序 |
| `engine/scripts/freq_band.py` | 比分层频率模板与形状带 | `BAND_DEFAULT` 上限撤销 |
| `engine/scripts/hypothesis.py` | **新建**:假设层数据结构与校验 | 全新 |
| `engine/tests/test_boldplay_mix.py` | A-MIX 路径测试 | 断言反转 |
| `engine/tests/test_hypothesis.py` | **新建**:假设层测试 | 全新 |
| `engine/tests/test_boldplay.py` | 主引擎测试(含保底档自检) | 保底关档断言 |
| `engine/tests/test_freq_band.py` | 频率模板测试 | 带宽相关断言排查 |

假设层单独成文件的理由:它是全新机制、与既有闸门逻辑无耦合,且 `boldplay.py` 已超千行。

---

### Task 1: 赔率域闸门(撤上限、下限提 4.0)

**Files:**
- Modify: `engine/scripts/boldplay.py:43`
- Modify: `engine/scripts/freq_band.py:25`
- Test: `engine/tests/test_boldplay_mix.py:24`

**Interfaces:**
- Consumes: 无
- Produces: `boldplay.ODDS_RANGE = (4.0, float("inf"))`、`freq_band.BAND_DEFAULT = (4.0, float("inf"))`

- [ ] **Step 1: 反转既有测试的上限断言**

`engine/tests/test_boldplay_mix.py:24` 现有测试名为 `test_mix_odds_range_and_no_dc_skip`,先只改赔率部分(无库放行留给 Task 3)。把该测试重命名并改写:

```python
def test_mix_odds_range_lower_bound_only():
    """赔率域:上限已撤(175/550 级长尾放行),下限 4.0 挡低赔腿。"""
    day = _odds_day()
    day["matches"][0]["crs"]["4:0"] = 550.0    # 高赔长尾:现在必须放行
    day["matches"][0]["crs"]["1:1"] = 3.2      # 低于下限 4.0:必须被挡
    zh = {"皇马": "real-madrid", "社会": "real-sociedad"}

    def fake_dc(m, z):
        return (1.8, 0.9, -0.1) if m["matchNumStr"] == "001" else None

    legs = mix_candidates(day, {}, zh, {}, dc_params_fn=fake_dc)
    assert all(l["odds"] >= 4.0 for l in legs)
    assert boldplay.ODDS_RANGE[1] == float("inf")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest engine/tests/test_boldplay_mix.py::test_mix_odds_range_lower_bound_only -v`
Expected: FAIL —— `assert boldplay.ODDS_RANGE[1] == float("inf")` 失败,实际为 `40.0`

- [ ] **Step 3: 改两处常量**

`engine/scripts/boldplay.py:43`:

```python
ODDS_RANGE = (4.0, float("inf"))  # 单腿赔率域：上限撤销（2026-09-16 拍板：以小博大，高赔长尾放行——
# 阿布艾因 4:0@175 曾被 40 上限拦截）；下限 4.0 挡方向层低赔腿（HAD 主胜 1.31/1.44 赔不动）。
# 撤上限的已知代价：8-25 探针的 4:0@550 EV+845% 假阳性会重新出现，与真机会数学上不可区分，
# 防噪声职责转移给分歧标注 + 假设层人工否证（spec §1.3.1）
```

`engine/scripts/freq_band.py:25`:

```python
BAND_DEFAULT = (4.0, float("inf"))   # CRS 形状带：上限撤销、下限降 4（2026-09-16 拍板，spec §1.1）；
# 原 (10.0, 28.0) 桂林-梅州合并带降级为参考标签，不再过滤
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest engine/tests/test_boldplay_mix.py -v`
Expected: 新测试 PASS

- [ ] **Step 5: 跑全量测试,记录失败清单**

Run: `python -m pytest engine/tests -q`

预期会有失败——`SHAPES` 带宽相关、`freq_legs` 带宽相关的断言可能锁死旧值。**逐条判断:锁死旧行为的改,仍然有效的保留。** 把失败清单记进 commit message。

- [ ] **Step 6: 提交**

```bash
git add engine/scripts/boldplay.py engine/scripts/freq_band.py engine/tests/test_boldplay_mix.py
git commit -m "feat(gate): 赔率域撤上限·下限提4.0

ODDS_RANGE (2.0,40.0)->(4.0,inf)；BAND_DEFAULT (10.0,28.0)->(4.0,inf)
撤上限依据：阿布艾因4:0@175曾被40上限拦截(spec §1.1)
下限4.0挡方向层低赔腿(HAD主胜1.31/1.44)
已知代价：550级假阳性重现，防噪声转移给分歧标注+假设层(spec §1.3.1)"
```

---

### Task 2: `DIVERGENCE_LIMIT` 降级为标注

**Files:**
- Modify: `engine/scripts/boldplay.py:272`、`:282`、`:296`、`:663`
- Test: `engine/tests/test_boldplay_mix.py`

**Interfaces:**
- Consumes: Task 1 的 `ODDS_RANGE`
- Produces: 每条候选腿新增 `divergence: float`(模型概率与市场去水概率之差,pp 绝对值)与 `divergenceFlag: bool`(是否 ≥ `DIVERGENCE_LIMIT`)

- [ ] **Step 1: 写失败测试**

```python
def test_divergence_annotated_not_excluded():
    """分歧超 5pp 的腿必须入选并带标注(原为排除)。"""
    day = _odds_day()
    zh = {"皇马": "real-madrid", "社会": "real-sociedad"}

    def fake_dc(m, z):
        return (2.6, 0.4, -0.1) if m["matchNumStr"] == "001" else None

    legs = mix_candidates(day, {}, zh, {}, dc_params_fn=fake_dc)
    assert legs, "分歧腿不应被滤光"
    assert all("divergence" in l and "divergenceFlag" in l for l in legs)
    assert any(l["divergenceFlag"] for l in legs), "λ=2.6 对市场应产生 >5pp 分歧腿"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest engine/tests/test_boldplay_mix.py::test_divergence_annotated_not_excluded -v`
Expected: FAIL —— `KeyError: 'divergence'` 或 `legs` 为空(被熔断滤光)

- [ ] **Step 3: 改四处过滤点为标注**

`boldplay.py:272` CRS 池,原为 `if abs(...) < DIVERGENCE_LIMIT:` 包裹 `offer(...)`,改为无条件 offer 并带标注:

```python
                d = abs(crs_p[(x, y)] - p_mkt)
                offer(crs_p[(x, y)] * o - 1,
                      {"play": "crs", "pick": k, "odds": o,
                       "source": "dc-reweighted" if adj else "dc",
                       "divergence": round(d, 4), "divergenceFlag": d >= DIVERGENCE_LIMIT})
```

`boldplay.py:282` TTG 池同构:去掉 `and abs(p_dc[i] - p_mkt[i]) < DIVERGENCE_LIMIT`,保留赔率域判断,在 leg 字典加同样两个字段。

`boldplay.py:296` HAFU 池同构。

`boldplay.py:663` 彩票档 P0-3 熔断,原为 `if abs(p - p_mkt[k]) >= DIVERGENCE_LIMIT: continue`(不入档),改为记录不跳过:

```python
                d = abs(p - p_mkt[k])
                # P0-3 熔断降级为标注（2026-09-16，spec §1.2）：该熔断原拦 DC 升班马污染
                # （弗洛西诺 p_dc=94% EV+63% 假阳性）。5pp 分歧无法区分「敢跟市场对赌」与
                # 「DC 参数算坏」，故不再排除，改由卡面标注 + 假设层否证
```

保留 `blocked` 参数回传机制,但语义从「被熔断的腿」改为「高分歧腿清单」。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest engine/tests/test_boldplay_mix.py -v`
Expected: PASS

- [ ] **Step 5: 跑全量测试**

Run: `python -m pytest engine/tests -q`

`boldplay.py:1044-1046` 有注释说「分歧旗全候选化后翻身腿被滤光→closed 属合法常态」,该测试的宽松分支现在可能反而选出腿,需确认断言仍成立。

- [ ] **Step 6: 提交**

```bash
git add engine/scripts/boldplay.py engine/tests/test_boldplay_mix.py
git commit -m "feat(gate): DIVERGENCE_LIMIT 四处过滤降级为卡面标注

boldplay.py:272/282/296 mix_candidates 三池 + :663 彩票档 P0-3 熔断
改为计算 divergence/divergenceFlag 写入 leg，不再据此排除
依据 spec §1.2：5pp 分歧无法区分'敢跟市场对赌'与'DC参数算坏'
  前者(阿布艾因4:0@175)该放行，后者(弗洛西诺p_dc=94%)拦不住也要标出来
常量 DIVERGENCE_LIMIT 保留用于计算标注阈值"
```

---

### Task 3: 无库场次放行(两处)

**Files:**
- Modify: `engine/scripts/boldplay.py:247`、`:631`
- Test: `engine/tests/test_boldplay_mix.py`

**Interfaces:**
- Consumes: Task 2 的标注字段
- Produces: 每条腿新增 `modelSupport: "dc" | "template" | "none"`;无库场次的腿不含 `divergence`(无模型概率可比)

- [ ] **Step 1: 写失败测试(两处跳过点各一)**

```python
def test_no_library_match_admitted_amix():
    """无 DC 场次必须入选并标 modelSupport:none(A-MIX 路径, boldplay.py:247)。"""
    day = _odds_day()
    zh = {"皇马": "real-madrid", "社会": "real-sociedad"}

    def fake_dc(m, z):
        return (1.8, 0.9, -0.1) if m["matchNumStr"] == "001" else None

    legs = mix_candidates(day, {}, zh, {}, dc_params_fn=fake_dc)
    codes = {l["matchNumStr"] for l in legs}
    assert "002" in codes, "无 DC 场次 002 必须放行"
    no_lib = [l for l in legs if l["matchNumStr"] == "002"]
    assert all(l["modelSupport"] == "none" for l in no_lib)
    assert all("divergence" not in l for l in no_lib), "无模型概率则无分歧值"
    has_lib = [l for l in legs if l["matchNumStr"] == "001"]
    assert all(l["modelSupport"] == "dc" for l in has_lib)


def test_no_library_match_admitted_lottery():
    """无 DC 场次必须入选(彩票档路径, boldplay.py:631)。"""
    day = _odds_day()
    zh = {"皇马": "real-madrid", "社会": "real-sociedad"}

    def fake_dc(m, z):
        return (1.8, 0.9, -0.1) if m["matchNumStr"] == "001" else None

    legs = boldplay._lottery_legs(day, zh=zh, dc_params_fn=fake_dc)
    codes = {l["matchNumStr"] for l in legs}
    assert "002" in codes, "彩票档同样须放行无库场次"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest engine/tests/test_boldplay_mix.py -k no_library -v`
Expected: FAIL —— `"002" in codes` 失败(两处均被 `continue` 跳过)

- [ ] **Step 3: 改两处跳过分支**

`boldplay.py:245-248` 原为:

```python
        params = dc_params_fn(m, zh)
        if not params:
            continue  # 无 DC 缓存/队名未入库 → 该场不入 A-MIX
```

改为放行,无库时跳过概率相关计算、只按赔率入选:

```python
        params = dc_params_fn(m, zh)
        # 2026-09-16 拍板放行无库场次（spec §1.6）：亚冠阿布艾因 4:0@175 曾因此永不入卡。
        # 无 DC → 不给概率只给赔率，标 modelSupport:none，选腿依据交假设层
        if not params:
            legs.extend(_odds_only_legs(m))
            continue
```

新增辅助函数(放在 `mix_candidates` 之前):

```python
def _odds_only_legs(m: dict) -> list:
    """无 DC 场次的候选腿：只有赔率、无概率无 EV 无分歧值（spec §1.6）。
    选腿依据交假设层——主客强弱差/远征距离时差/赛制压力/伤停/轮换动机。开发者 sszhang"""
    mid = m.get("matchNumStr") or m.get("code")
    match = f'{m.get("home")}-{m.get("away")}'
    out = []
    for k, v in (m.get("crs") or {}).items():
        if ":" not in k:
            continue
        o = float(v)
        if o < ODDS_RANGE[0]:
            continue
        out.append({"play": "crs", "pick": k, "odds": o, "modelSupport": "none",
                    "matchNumStr": mid, "match": match})
    out.sort(key=lambda l: -l["odds"])
    return out
```

有库场次的腿在 Task 2 已加的字典里补 `"modelSupport": "dc"`。

`boldplay.py:631` 彩票档原为 `if not (mid and had and params): continue`,拆开——缺 `mid`/`had` 仍跳过(没赔率无从下注),仅缺 `params` 时放行:

```python
        if not (mid and had):
            continue
        if not params:                      # 无库放行（spec §1.6），只按赔率不算 p_fused
            legs.extend(_odds_only_had_legs(m, mid, had))
            continue
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest engine/tests/test_boldplay_mix.py -k no_library -v`
Expected: PASS

- [ ] **Step 5: 跑全量测试**

Run: `python -m pytest engine/tests -q`

`test_boldplay_mix.py:36` 原断言 `all(l["matchNumStr"] == "001" ...)` 已在 Step 1 被新测试取代,确认旧断言已删净。

- [ ] **Step 6: 提交**

```bash
git add engine/scripts/boldplay.py engine/tests/test_boldplay_mix.py
git commit -m "feat(gate): 无库场次放行(两处跳过点)

boldplay.py:247 A-MIX + :631 彩票档——两处必须同改否则两路径行为不一致
无库场次经 _odds_only_legs 只按赔率入选，标 modelSupport:none，不给概率/EV/分歧值
触发：周二006 亚冠阿布艾因4:0利雅胜利(CRS 4:0@175)因亚冠无库从未进卡(spec §1.6)"
```

---

### Task 4: 假设层

**Files:**
- Create: `engine/scripts/hypothesis.py`
- Create: `engine/tests/test_hypothesis.py`

**Interfaces:**
- Consumes: 无(纯数据结构与校验,不依赖前序任务)
- Produces:
  - `VERDICTS = ("survived", "refuted", "pending")`
  - `make_hypothesis(assumption: str, checks: list[dict] | None = None, verdict: str = "pending") -> dict`
  - `validate_hypothesis(h: dict) -> list[str]` 返回问题清单,空列表=通过
  - `is_buyable(h: dict) -> bool` 仅 `verdict == "survived"` 且校验通过时为 True
  - `filter_buyable(legs: list) -> tuple[list, list]` 返回 (可买腿, 被挡腿)
  - `check_shared_legs(bets: list) -> list[str]` 共用腿跨注复用告警

- [ ] **Step 1: 写失败测试**

```python
"""假设层测试（spec §二）。开发者 sszhang"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from hypothesis import (make_hypothesis, validate_hypothesis, is_buyable,
                        filter_buyable, check_shared_legs)


def _h2h_check(gapless=True, matches=4):
    return {"kind": "h2h", "matches": matches, "seasonComplete": gapless,
            "gaps": [] if gapless else ["E1 2526 缺 2026-01-05 之后"],
            "finding": "米堡主场两次仅进1球"}


def test_refuted_leg_not_buyable():
    """verdict:refuted 的腿不进候选——米堡2:1 反面案例(spec §二)。"""
    h = make_hypothesis("米堡主场场均进3球所以能打出2:1",
                        checks=[_h2h_check()], verdict="refuted")
    assert not is_buyable(h)


def test_pending_leg_not_buyable():
    """verdict 未填写标 pending，同样不得出票。"""
    h = make_hypothesis("奥萨苏纳客场零封")
    assert h["verdict"] == "pending"
    assert not is_buyable(h)


def test_survived_leg_buyable():
    h = make_hypothesis("奥萨苏纳客场零封", checks=[_h2h_check()], verdict="survived")
    assert validate_hypothesis(h) == []
    assert is_buyable(h)


def test_missing_h2h_check_reports_problem():
    """checks[] 缺 H2H 完整度记录时报错(2026-09-15 残缺缓存事故)。"""
    h = make_hypothesis("奥萨苏纳客场零封",
                        checks=[{"kind": "form", "finding": "近5场2胜"}],
                        verdict="survived")
    probs = validate_hypothesis(h)
    assert any("h2h" in p for p in probs)
    assert not is_buyable(h)


def test_h2h_check_must_record_completeness():
    """H2H 项须同时记录场次数、赛季完整度、缺口区间。"""
    h = make_hypothesis("奥萨苏纳客场零封",
                        checks=[{"kind": "h2h", "matches": 4}],   # 缺 seasonComplete/gaps
                        verdict="survived")
    probs = validate_hypothesis(h)
    assert any("seasonComplete" in p or "gaps" in p for p in probs)


def test_invalid_verdict_rejected():
    h = make_hypothesis("测试", checks=[_h2h_check()], verdict="maybe")
    assert any("verdict" in p for p in probs := validate_hypothesis(h))


def test_filter_buyable_splits_legs():
    ok = {"pick": "4:0", "hypothesis": make_hypothesis(
        "阿布艾因主场强于远征的利雅胜利", checks=[_h2h_check()], verdict="survived")}
    bad = {"pick": "2:1", "hypothesis": make_hypothesis(
        "米堡主场场均3球", checks=[_h2h_check()], verdict="refuted")}
    buyable, blocked = filter_buyable([ok, bad])
    assert [l["pick"] for l in buyable] == ["4:0"]
    assert [l["pick"] for l in blocked] == ["2:1"]


def test_shared_leg_across_bets_warns():
    """共用腿跨注复用告警——T033 三注共用米堡2:1=伪分散(spec §二)。"""
    bets = [{"legs": ["周三006 马竞4:0", "周二010 米堡2:1"]},
            {"legs": ["周三006 马竞3:0", "周二010 米堡2:1"]},
            {"legs": ["周三006 马竞2:0", "周二010 米堡2:1"]}]
    warns = check_shared_legs(bets)
    assert any("米堡2:1" in w and "3" in w for w in warns)


def test_independent_bets_no_warning():
    bets = [{"legs": ["A", "B"]}, {"legs": ["C", "D"]}]
    assert check_shared_legs(bets) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest engine/tests/test_hypothesis.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'hypothesis'`

注意:PyPI 有同名包 `hypothesis`。若环境已装,`sys.path.insert(0, ...)` 使本地 `engine/scripts/hypothesis.py` 优先。若冲突,改名 `hypo_layer.py` 并同步测试导入。Step 2 若报 `ImportError: cannot import name 'make_hypothesis'` 而非 `ModuleNotFoundError`,即为撞包,立即改名。

- [ ] **Step 3: 实现假设层**

```python
"""假设层（spec §二 2026-09-16 拍板）：撤销自动闸门后唯一的拦截机制。

三段式 assumption / checks[] / verdict。verdict=refuted 或 pending 的腿不得出票。
拦的是没做功课，不是拦胆量——反例：米堡2:1「主场场均3球所以能打2:1」，
H2H 四场米堡主场两次仅进1球、米尔沃尔防守型，数据明确反对。开发者 sszhang"""

VERDICTS = ("survived", "refuted", "pending")


def make_hypothesis(assumption: str, checks: list | None = None,
                    verdict: str = "pending") -> dict:
    return {"assumption": assumption, "checks": list(checks or []), "verdict": verdict}


def validate_hypothesis(h: dict) -> list:
    """返回问题清单，空列表=通过。"""
    probs = []
    if not str(h.get("assumption") or "").strip():
        probs.append("assumption 为空：须写一句可证伪的判断")
    v = h.get("verdict")
    if v not in VERDICTS:
        probs.append(f"verdict 非法：{v!r}，仅允许 {VERDICTS}")
    checks = h.get("checks") or []
    h2h = [c for c in checks if c.get("kind") == "h2h"]
    if not h2h:
        probs.append("checks 缺 h2h 项：2026-09-15 残缺缓存曾致漏判(spec §二)")
    for c in h2h:
        if "seasonComplete" not in c:
            probs.append("h2h 项缺 seasonComplete：须记录数据源赛季完整度")
        if "gaps" not in c:
            probs.append("h2h 项缺 gaps：须记录缺口区间")
        if "matches" not in c:
            probs.append("h2h 项缺 matches：须记录交手场次数")
    return probs


def is_buyable(h: dict) -> bool:
    return h.get("verdict") == "survived" and not validate_hypothesis(h)


def filter_buyable(legs: list) -> tuple:
    """(可买腿, 被挡腿)。无 hypothesis 字段的腿视为 pending → 被挡。"""
    buyable, blocked = [], []
    for l in legs:
        h = l.get("hypothesis") or make_hypothesis("")
        (buyable if is_buyable(h) else blocked).append(l)
    return buyable, blocked


def check_shared_legs(bets: list) -> list:
    """共用腿跨注复用告警（spec §二）：T033 三注共用米堡2:1，表面3注实为1个失效点。"""
    from collections import Counter
    cnt = Counter(leg for b in bets for leg in (b.get("legs") or []))
    return [f"⚠腿「{leg}」被 {n} 注复用：表面 {len(bets)} 注实为伪分散，"
            f"该腿一断则 {n} 注同灭(T033 教训)"
            for leg, n in cnt.items() if n > 1]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest engine/tests/test_hypothesis.py -v`
Expected: 9 passed

- [ ] **Step 5: 跑全量测试**

Run: `python -m pytest engine/tests -q`
Expected: 既有 332 个测试不受影响(新模块尚未接入 `boldplay.py`)

- [ ] **Step 6: 提交**

```bash
git add engine/scripts/hypothesis.py engine/tests/test_hypothesis.py
git commit -m "feat(hypothesis): 新增假设层——撤销闸门后唯一的拦截机制

三段式 assumption/checks[]/verdict；refuted 与 pending 均不得出票
validate_hypothesis 强制 checks 含 h2h 项且记录 matches/seasonComplete/gaps
  依据：2026-09-15 据 260/552 残缺缓存断言'H2H只有3场'，漏 2026-04-03 米堡主场1:2
check_shared_legs 告警共用腿跨注复用(T033 三注共用米堡2:1=伪分散，1个失效点)
反面案例：米堡2:1「主场场均3球」——H2H四场两次仅进1球，数据明确反对(spec §二)"
```

---

### Task 5: 保底档显式关档

**Files:**
- Modify: `engine/scripts/boldplay.py:709-725`
- Modify: `engine/tests/test_boldplay.py:1036-1041`(即 `boldplay.py` 内嵌自检,见下)
- Modify: `docs/superpowers/specs/2026-09-16-hypothesis-driven-engine-design.md` §1.4

**Interfaces:**
- Consumes: 无
- Produces: `BASE_TIER_ENABLED = False` 常量;保底档恒走关档分支,`tiers.base.coverGate is None`

- [ ] **Step 1: 写失败测试**

先确认自检代码位置。`boldplay.py:1034-1042` 是模块内自检函数(非 pytest),`engine/tests/test_boldplay.py` 另有测试。两处都要改。

新增 pytest 测试:

```python
def test_base_tier_closed_by_default():
    """保底档关档(spec §1.4)：大哥「资金小量不保本无所谓」，保底档目的即保本，故关。"""
    day = _full_odds_day()      # 复用既有 fixture，含 5+ 场 had 齐全的锚联赛场次
    t = boldplay.build_three_tier(day, {}, seq=1, zh=_zh(), form={})
    base = t["tiers"]["base"]
    assert base["cost"] == 0
    assert base["coverGate"] is None
    assert "关档" in base["note"]
    assert not base.get("bets"), "关档不产生注"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest engine/tests/test_boldplay.py::test_base_tier_closed_by_default -v`
Expected: FAIL —— `base["cost"] == 32`,`coverGate` 为 dict

**若意外 PASS**:说明 fixture 本就选不出 5 腿,该测试无效。改用真实 odds 缓存或补足 fixture 场次,确保「未关档时会失败」。

- [ ] **Step 3: 显式关档**

`boldplay.py` 常量区加:

```python
BASE_TIER_ENABLED = False   # 保底档开关（2026-09-16 拍板，spec §1.4）：大哥「资金小量不保本
# 其实无所谓」——保底档设计目的即保本，与此冲突故关档。注意：改 ODDS_RANGE 不会使其自动关闭，
# _base_legs 有独立门槛 min(o3)<1.10（:543）不读 ODDS_RANGE，故须显式关。
# coverGate 机制保留：置 True 即复活保本档
```

`boldplay.py:710` 条件改为:

```python
    if BASE_TIER_ENABLED and len(base_legs) >= 5:
```

关档分支的 note 覆盖两种成因:

```python
        note = ("保底档关档（2026-09-16 拍板：不保本无所谓，spec §1.4）"
                if not BASE_TIER_ENABLED
                else f"保底关档（合格腿{len(base_legs)}<5，不硬凑）")
        base = {"cost": 0, "legs": base_legs, "play": "had-3*4*5",
                "note": note, "coverGate": None}
```

- [ ] **Step 4: 改模块内自检**

`boldplay.py:1036-1042` 四处断言改为关档态:

```python
    base = t["tiers"]["base"]
    assert base["cost"] == 0 and base["coverGate"] is None    # 保底档关档(spec §1.4)
    assert base["play"] == "had-3*4*5"
    assert "关档" in base["note"]
```

删除 `assert len(base["bets"]) == 16` 与 `payout_full_hit` 相关断言(关档无注)。`payout_full_hit` 函数本身保留——`BASE_TIER_ENABLED=True` 时仍需要。

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest engine/tests -q`
Expected: 新测试 PASS;保底档相关旧断言已同步改完

- [ ] **Step 6: 更正 spec §1.4 的错误措辞**

Spec 原文称「下限提到 4.0 后它选不出 5 条合格腿,自然关闭」。改为:

```markdown
**关档须显式实现,不是下限的副作用。** `_base_legs`(`boldplay.py:523-544`)有独立赔率门槛
`min(o3) < 1.10`,不读 `ODDS_RANGE`——改赔率域不影响保底档。故新增 `BASE_TIER_ENABLED = False`
开关显式关闭。置 `True` 即复活保本档。
```

- [ ] **Step 7: 提交**

```bash
git add engine/scripts/boldplay.py engine/tests/test_boldplay.py docs/superpowers/specs/2026-09-16-hypothesis-driven-engine-design.md
git commit -m "feat(base): 保底档显式关档

大哥「资金小量不保本其实无所谓」——保底档设计目的即保本，冲突故关(spec §1.4)
新增 BASE_TIER_ENABLED=False 开关；coverGate 机制保留，置True即复活

同时更正 spec §1.4 错误措辞：原称'下限4.0后自然关闭'不成立——
_base_legs(:543) 有独立门槛 min(o3)<1.10 不读 ODDS_RANGE，须显式关档
同步改模块内自检 :1036-1041 三处断言为关档态；payout_full_hit 保留"
```

---

### Task 6: 假设层接入出票路径

**Files:**
- Modify: `engine/scripts/boldplay.py`(`build_three_tier` 与 `mix_candidates` 出口)
- Test: `engine/tests/test_boldplay_mix.py`

**Interfaces:**
- Consumes: Task 4 的 `filter_buyable`、`check_shared_legs`;Task 3 的 `modelSupport`
- Produces: 卡内每条腿带 `hypothesis` 字段(默认 `pending`);`tiers.*.blockedByHypothesis` 记录被挡腿;`warnings` 追加共用腿告警

- [ ] **Step 1: 写失败测试**

```python
def test_legs_carry_pending_hypothesis_by_default():
    """新生成的腿默认带 pending 假设，未经验证不得出票(spec §三)。"""
    day = _odds_day()
    zh = {"皇马": "real-madrid", "社会": "real-sociedad"}
    legs = mix_candidates(day, {}, zh, {}, dc_params_fn=lambda m, z: (1.8, 0.9, -0.1))
    assert all(l["hypothesis"]["verdict"] == "pending" for l in legs)


def test_shared_leg_warning_in_card():
    """共用腿跨注复用须在卡 warnings 中告警(T033 教训)。"""
    t = {"tiers": {"upset": {"bets": [{"legs": ["A", "X"]}, {"legs": ["B", "X"]}]}},
         "warnings": []}
    boldplay.annotate_hypothesis_warnings(t)
    assert any("X" in w and "伪分散" in w for w in t["warnings"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest engine/tests/test_boldplay_mix.py -k hypothesis -v`
Expected: FAIL —— `KeyError: 'hypothesis'` 与 `AttributeError: annotate_hypothesis_warnings`

- [ ] **Step 3: 接入**

`boldplay.py` 顶部导入:

```python
from hypothesis import make_hypothesis, filter_buyable, check_shared_legs
```

`mix_candidates` 出口(`boldplay.py:301-302` 附近)与 `_odds_only_legs` 每条腿补默认假设:

```python
            legs.append({**leg, "matchNumStr": mid, "match": f'{m.get("home")}-{m.get("away")}',
                         "ev": round(ev, 4), "hypothesis": make_hypothesis("")})
```

新增卡级函数:

```python
def annotate_hypothesis_warnings(t: dict) -> None:
    """卡级假设层告警（spec §二/§三）：共用腿跨注复用 + pending 腿提示。开发者 sszhang"""
    t.setdefault("warnings", [])
    for name, tier in (t.get("tiers") or {}).items():
        if not isinstance(tier, dict):
            continue
        for w in check_shared_legs(tier.get("bets") or []):
            t["warnings"].append(f"[{name}] {w}")
        buyable, blocked = filter_buyable(tier.get("legs") or [])
        if blocked:
            tier["blockedByHypothesis"] = [
                {"pick": l.get("pick"), "verdict": (l.get("hypothesis") or {}).get("verdict")}
                for l in blocked]
            t["warnings"].append(
                f"[{name}] {len(blocked)} 腿假设未通过（pending/refuted），出票前须逐条验证")
```

在 `build_three_tier` 返回前调用。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest engine/tests/test_boldplay_mix.py -k hypothesis -v`
Expected: PASS

- [ ] **Step 5: 跑全量测试**

Run: `python -m pytest engine/tests -q`

所有腿现在默认 `pending`,若有测试断言「卡可直接出票」会失败——那正是设计意图,改为断言 warnings 存在。

- [ ] **Step 6: 提交**

```bash
git add engine/scripts/boldplay.py engine/tests/test_boldplay_mix.py
git commit -m "feat(hypothesis): 假设层接入出票路径

每条腿默认带 pending 假设，未经第2步验证不得出票
annotate_hypothesis_warnings 卡级告警：共用腿跨注复用 + pending/refuted 腿清单
tiers.*.blockedByHypothesis 记录被挡腿及其 verdict"
```

---

### Task 7: 卡面排序与呈现

**Files:**
- Modify: `engine/scripts/boldplay.py:778`(`render_ticket`)
- Test: `engine/tests/test_boldplay.py`

**Interfaces:**
- Consumes: Task 2 的 `divergenceFlag`、Task 3 的 `modelSupport`、Task 6 的 `hypothesis`
- Produces: 卡面按场次分组、组内赔率降序;每行带 `modelSupport` 与分歧标注

- [ ] **Step 1: 写失败测试**

```python
def test_card_groups_by_match_odds_desc():
    """卡面按场次分组、组内赔率降序(spec §三)：排序用赔率决定先看到哪条，
    选腿用假设决定买不买。不定义排序则'只看最上面几条'会成隐性门槛。"""
    legs = [{"matchNumStr": "001", "match": "A-B", "play": "crs", "pick": "1:0",
             "odds": 6.5, "modelSupport": "dc", "divergenceFlag": False,
             "hypothesis": {"verdict": "pending"}},
            {"matchNumStr": "001", "match": "A-B", "play": "crs", "pick": "4:0",
             "odds": 175.0, "modelSupport": "none", "divergenceFlag": False,
             "hypothesis": {"verdict": "pending"}}]
    txt = boldplay.render_legs_grouped(legs)
    assert txt.index("4:0") < txt.index("1:0"), "组内须赔率降序"
    assert "modelSupport" in txt or "无模型" in txt
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest engine/tests/test_boldplay.py::test_card_groups_by_match_odds_desc -v`
Expected: FAIL —— `AttributeError: render_legs_grouped`

- [ ] **Step 3: 实现分组渲染**

```python
def render_legs_grouped(legs: list) -> str:
    """按场次分组、组内赔率降序渲染（spec §三）。排序用赔率决定阅读顺序，
    不决定入选资格——入选由假设层裁定。开发者 sszhang"""
    from itertools import groupby
    rows = sorted(legs, key=lambda l: (l.get("matchNumStr") or "", -float(l.get("odds") or 0)))
    out = []
    for code, grp in groupby(rows, key=lambda l: l.get("matchNumStr")):
        grp = list(grp)
        out.append(f"── {code} {grp[0].get('match', '')}")
        for l in grp:
            sup = {"dc": "DC", "template": "模板", "none": "无模型"}.get(
                l.get("modelSupport"), "?")
            flags = "⚠分歧 " if l.get("divergenceFlag") else ""
            vd = (l.get("hypothesis") or {}).get("verdict", "pending")
            mark = {"survived": "✓", "refuted": "✗", "pending": "○"}.get(vd, "○")
            out.append(f"   {mark} {l.get('play', ''):5} {str(l.get('pick', '')):6} "
                       f"@{float(l.get('odds', 0)):<7.2f} [{sup}] {flags}")
    return "\n".join(out)
```

在 `render_ticket` 的候选区调用该函数。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest engine/tests/test_boldplay.py -k grouped -v`
Expected: PASS

- [ ] **Step 5: 跑全量测试**

Run: `python -m pytest engine/tests -q`

`render_ticket` 的既有格式断言(`test_boldplay.py:1232` 等)可能因新增行失败,逐条判断。

- [ ] **Step 6: 提交**

```bash
git add engine/scripts/boldplay.py engine/tests/test_boldplay.py
git commit -m "feat(card): 卡面按场次分组·组内赔率降序·带 modelSupport 与分歧标注

依据 spec §三：撤过滤后候选腿达数百条，排序不定义则'只看最上面几条'成隐性门槛
明确区分：排序用赔率决定先看到哪条，选腿用假设决定买不买
每行标 verdict(✓survived/✗refuted/○pending) + modelSupport + ⚠分歧旗"
```

---

### Task 8: `preference.json` 同步实现状态 + 影子票约束

**Files:**
- Modify: `data/06-tickets/preference.json`
- Test: 无(数据文件)

**Interfaces:**
- Consumes: Task 1-7 的落地结果
- Produces: `candidatePool.*.implemented` 状态标记;`decisionFlow.shadowOnlyRounds`

- [ ] **Step 1: 标记实现状态**

前一轮写入 `preference.json` 的 `candidatePool` / `decisionFlow` 是纯文档、无代码读取。本步补上实现指针,避免再次出现「文档说改了、引擎没改」:

```python
d["candidatePool"]["cupNoLibrary"]["implemented"] = {
    "at": "2026-09-16", "commits": "Task 3",
    "code": ["boldplay.py:_odds_only_legs", "boldplay.py:247", "boldplay.py:631"],
    "test": "test_boldplay_mix.py::test_no_library_match_admitted_amix/_lottery"}
d["candidatePool"]["scoreLayerExpansion"]["implemented"] = {
    "at": "2026-09-16",
    "note": "改口:DC 未接默认路径。spec 审核判定'默认接DC'与假设驱动冲突,"
            "DC 与 freq_band 模板均降级为第2步验证工具,不作推荐源"}
d["decisionFlow"]["shadowOnlyRounds"] = {
    "rounds": 3, "startFrom": "首个走新流程的轮次",
    "why": "假设层是全新机制从未验证;用真钱验证未验证的逻辑属流程风险,"
           "与'不保本无所谓'的资金偏好是两件事(spec §五)",
    "baseline": ["S033A", "S033B"],
    "excluded": "X033 不得作有效性依据——中奖源于赔率差(175 vs 16)而非判断力"}
d["decisionFlow"]["roles"] = {
    "step1": "假设由 Claude 出、大哥审;大哥可追加自己的判断入单",
    "step2": "Claude 验证",
    "step3": "Claude 出方案、大哥定",
    "why": "Claude 快速查 H2H/主客分解/伤停,大哥定该不该信——"
           "09-15 大哥两次凭记忆纠正(米堡1:2、4月3日踢过)方向均对而缓存错",
    "weakestLink": "Claude 出假设时偷懒(米堡2:1 即实例),故第2步专用于否证自己"}
d["stake"]["note"] = ("2026-09-16:预算纪律本轮不动(spec §1.5)。闸门全撤后若预算同时不设限"
                      "则风险敞口无边界。三处状态矛盾待另轮理清:"
                      "boldplay.py:48 ROUND_REDLINE=30 仍在 / :773 注释称已废除 / "
                      "本文件 roundRedline 与 deprecated.stake 并存")
```

- [ ] **Step 2: 跑全量测试**

Run: `python -m pytest engine/tests -q`
Expected: 332+ passed(数据文件改动不影响测试)

- [ ] **Step 3: 提交**

```bash
git add data/06-tickets/preference.json
git commit -m "docs(preference): 同步实现状态·影子票约束·角色分工

补 implemented 指针(代码位置+测试名)——避免再现'文档说改了引擎没改'
  前一轮 candidatePool/decisionFlow 写入后无代码读取，引擎行为未变
标记改口:DC 未接默认路径(spec审核判定与假设驱动冲突，降级为验证工具)
新增 shadowOnlyRounds=3、roles 角色分工、stake.note 预算纪律待办"
```

---

## Self-Review

**1. Spec 覆盖检查**

| Spec 节 | 对应任务 |
|:---|:---|
| §1.1 撤销赔率上限/带宽上限 | Task 1 |
| §1.1 无库跳过两处 | Task 3 |
| §1.2 `DIVERGENCE_LIMIT` 改标注(四处) | Task 2 |
| §1.3 下限 4.0 作用域 | Task 1 |
| §1.4 保底档关档 | Task 5(含 spec 措辞更正) |
| §1.5 预算纪律不动 | Task 8(仅记录待办) |
| §1.6 无库场次放行 | Task 3 |
| §二 假设层三段式 | Task 4 |
| §二 角色分工 | Task 8 |
| §二 H2H 完整度检查 | Task 4 |
| §二 共用腿约束 | Task 4 + Task 6 |
| §二 DC 降级为验证工具 | Task 8(标记改口);代码层无需改动——Task 1-3 未把 DC 接默认路径 |
| §三 卡面排序 | Task 7 |
| §四 EV 与分歧值留卡面 | Task 2(分歧值)+ Task 7(渲染);EV 字段本就在,无需改动 |
| §五 影子票前 3 轮 | Task 8 |
| §六 测试反转 | Task 1-3 各自反转 + Task 4/6/7 新增 |

无遗漏。

**2. 占位符扫描**

已查:无 TBD/TODO/「类似 Task N」/「添加适当错误处理」。每个代码步骤均含可直接粘贴的实现。

**3. 类型一致性**

- `make_hypothesis` / `validate_hypothesis` / `is_buyable` / `filter_buyable` / `check_shared_legs` — Task 4 定义,Task 6 使用,签名一致
- `_odds_only_legs` — Task 3 定义并使用
- `_odds_only_had_legs` — Task 3 Step 3 彩票档分支引用,**但未给出实现**。补:与 `_odds_only_legs` 同构,遍历 `had` 三向而非 `crs`,产出 `{"play": "had", "pick": "h"|"d"|"a", ...}`,同样过 `ODDS_RANGE[0]` 下限、标 `modelSupport: "none"`。实现时照 `_odds_only_legs` 结构写。
- `annotate_hypothesis_warnings` — Task 6 定义并使用
- `render_legs_grouped` — Task 7 定义,`render_ticket` 调用
- `BASE_TIER_ENABLED` — Task 5 定义并使用
- `modelSupport` 取值三值在 Task 3 与 Task 7 渲染映射中一致

**4. 已知风险**

- Task 4 的模块名 `hypothesis` 与 PyPI 同名包冲突可能。Step 2 给了判别方法与改名预案。
- Task 1 Step 5、Task 2 Step 5、Task 7 Step 5 均可能有既有测试失败。这是设计意图(旧测试锁死了要撤的行为),但每条都须逐一判断而非批量删除。
- Task 5 Step 2 若测试意外 PASS,说明 fixture 无效,已给应对。
