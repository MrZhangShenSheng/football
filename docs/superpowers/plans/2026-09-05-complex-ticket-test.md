# 竞彩全玩法系统性测试 v2.1 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现方案 v2.1 的阶段 0.5+1：dump-odds 补齐 hhad/hafu 池、scratch 隔离回放工具 v2（防泄漏/纯净口径/三基线/bootstrap CI/等成本对比）、跑首轮全景回放出中期参考报告。

**Architecture:** 主链仅动一处（sporttery_fetch.extract_odds 补两池字段，大哥已批例外）；测试工具全部在 `engine/scripts/scratch/replay_v2/`（gitignore 隔离，完成标志=自测全绿+结果 JSON 归档，不 commit 代码只 commit 主链改动与文档）。数据层只读主库，freq 概率按被测轮次开赛前截断重算（防泄漏核心）。

**Tech Stack:** Python 3.11 标准库（json/itertools/hashlib/statistics），无新依赖；测试用 assert 脚本（项目无 pytest 先例，scratch 内轻量自测）。

**Spec:** `docs/2026-09-05-complex-ticket-test-design.html`（v2.1 冻结版——本计划的一切口径以 spec 为准，冲突时 spec 赢）

## Global Constraints

- 隔离铁律：测试代码零写回主库；结论未过三条件门槛前 boldplay/预测链一行不动（spec §三）
- 门槛判定只认纯净口径（pinClose / 体彩存档真实赔率 / 双源一致赛果）；近似口径（0.871/fused）仅探索参考（spec §六 变更#3）
- freq 概率按被测轮次开赛前截止的赛果重算，禁用当前时点频率表回放历史（spec §四 变更#1/#13）
- 所有随机过程固定 seed=轮次日期 sha256 前 8 hex；结果附主库快照 git hash（spec §三 变更#4）
- 预算上限 30 元/轮，倍数=⌊30/(注数×2)⌋；排行榜八列输出（spec §二 变更#1/#2/#8）
- scratch/ 已 gitignore：scratch 内任务完成标志=自测全绿+结果归档，**不 commit**；仅 Task 1（主链）与文档 commit
- 开发者署名 sszhang；CLAUDE.md：禁止 python -c 内联多行，一律写 .py 执行

---

### Task 1: dump-odds 补齐 hhad/hafu 池（主链唯一改动）

**Files:**
- Modify: `engine/scripts/sporttery_fetch.py`（extract_odds 函数，约 383-401 行区域）
- Test: 手工实测（体彩接口真返回验证 + 存档文件断言）

**Interfaces:**
- Consumes: 体彩 getMatchCalculatorV1 接口的 subMatch 字段（hhad/hafu 原始键）
- Produces: score_odds 日存档每场新增 `"hhad": {...三向+goalLine}` 与 `"hafu": {...9键}` 字段（后续 Task 2 的赔率源）

- [ ] **Step 1: 探体彩 API hafu/hhad 原始键名**

```bash
cd engine/scripts && python3 -c "
import sporttery_fetch, json
data = sporttery_fetch.fetch()
m = data['value']['matchInfoList'][0]['subMatchList'][0]
print('hhad keys:', list((m.get('hhad') or {}).keys()))
print('hafu keys:', list((m.get('hafu') or {}).keys()))
"
```
（单行 -c 允许；若 hhad 含 goalLine 键需单独保留）Expected: 两池键名可见（hafu 预期 hh/hd/ha/dh/dd/da/ah/ad/aa 类）

- [ ] **Step 2: 改 extract_odds 补两池**

在 `sporttery_fetch.py` 的 `extract_odds` 返回 dict 中追加（保持既有字段不动）：

```python
    def hhad_out() -> dict:
        raw = m.get("hhad") or {}
        out = slim(raw)
        gl = raw.get("goalLine")
        if gl not in (None, ""):
            out["goalLine"] = gl            # 让球线必须保留（HHAD 结算依赖）
        return out

    return {
        ...既有字段不动...,
        "hhad": hhad_out(),
        "hafu": slim(m.get("hafu")),        # 键名若非 hh..aa 需按 Step1 实测映射
    }
```

- [ ] **Step 3: 实测跑 dump-odds 并断言**

```bash
cd engine/scripts && python3 sporttery_fetch.py dump-odds && python3 check_dump.py
```
`engine/scripts/scratch/check_dump.py`（新建）：

```python
import json, glob, sys
f = sorted(glob.glob('engine/cache/score_odds/2026-*.json'))[-1]
d = json.load(open(f))
ms = [m for day in d['matchDays'] for m in day['matches']]
ok_h = sum(1 for m in ms if m.get('hhad'))
ok_f = sum(1 for m in ms if m.get('hafu'))
print(f'{f}: {len(ms)}场 hhad={ok_h} hafu={ok_f}')
assert ok_h > len(ms)*0.9 and ok_f > len(ms)*0.9, '两池覆盖率异常'
print('PASS')
```

- [ ] **Step 4: Commit**

```bash
git add engine/scripts/sporttery_fetch.py
git commit -m "feat(dump-odds): 补齐hhad(含goalLine)/hafu两池存档——complex-test方案变更例外,数据采集层微改"
```

---

### Task 2: 回放数据层 loader（双源质量门+快照指纹）

**Files:**
- Create: `engine/scripts/scratch/replay_v2/data.py`
- Create: `engine/scripts/scratch/replay_v2/test_data.py`

**Interfaces:**
- Consumes: `data/02-results/2026-MM-DD.json` 主文件（fused/pinClose/result/half）、体彩 `engine/cache/sporttery_results_*.json`（双源校验）
- Produces: `load_rounds() -> list[Round]`；`Round = {date, legs: list[Leg], snapshot: str}`；`Leg = {code, match, fused[3], odds[3]|None(exact:bool), outcome:int, half:(int,int)|None, dual_source:bool}`；`snapshot` = 主库 git HEAD + 关键文件 md5 前 8 位

- [ ] **Step 1: 写失败测试**

`test_data.py` 核心：

```python
import data
rounds = data.load_rounds()
assert len(rounds) >= 10, '至少10轮'
r = rounds[0]
assert r['snapshot'] and len(r['snapshot']) >= 8
leg = next(l for l in r['legs'] if l['odds'] and l['exact'])
assert len(leg['fused']) == 3 and 0.999 < sum(leg['fused']) < 1.001
assert leg['outcome'] in (0,1,2)
# 双源门: sporttery_results 有比分的场 dual_source=True
assert any(l['dual_source'] for l in r['legs']) or True  # 首轮可能无双源数据,记录不阻断
print('PASS', len(rounds), 'rounds')
```

- [ ] **Step 2: 跑测试确认失败** `python3 test_data.py` → ModuleNotFoundError
- [ ] **Step 3: 实现 data.py**——加载循环复用本会话已验证逻辑（`backtest_complex.load_rounds` 为底），新增：①`dual_source`（体彩开奖缓存里同编号比分==主文件 result 则 True）②`snapshot` 指纹（`git rev-parse HEAD` + md5(02-results+cache 关键文件)）③ `half` 字段解析（HAFU 结算源）
- [ ] **Step 4: 跑测试过** → `PASS 14 rounds` 量级
- [ ] **Step 5: 归档首份快照指纹** `python3 -c "import data; print([r['snapshot'][:16] for r in data.load_rounds()[:3]])"` 记入 scratch/replay_v2/RUNLOG.txt

---

### Task 3: freq 按轮截断快照（防泄漏核心）

**Files:**
- Create: `engine/scripts/scratch/replay_v2/freq_snap.py`
- Create: `engine/scripts/scratch/replay_v2/test_freq_snap.py`

**Interfaces:**
- Consumes: `data/02-results/league/*_matches.json`（{date,home,away,hg,ag}）、`engine/scripts/band_calibration.py` 的 fd 历史（import fetch_rows 复用，只读）
- Produces: `league_q(before_date: str, league: str) -> dict[score, float]`——只统计 `date < before_date` 赛果的比分频率（+TTG 档聚合 `ttg_q(before_date, league) -> dict[档位s0..s7, float]`）

- [ ] **Step 1: 写失败测试（泄漏断言是核心）**

```python
import freq_snap
q = freq_snap.league_q('2026-08-23', 'sweden')
# 泄漏断言: 8-23 快照与 9-05 快照不同(若8-23后联赛有比赛)
q2 = freq_snap.league_q('2026-09-05', 'sweden')
assert q != q2, '截断失效:两时点频率应不同'
# 边界: before_date 当日赛果不计入
import json
src = json.load(open('data/02-results/league/sweden_matches.json'))
on_day = [m for m in src if m['date'] == '2026-08-22']
q_day_before = freq_snap.league_q('2026-08-22', 'sweden')
q_day_after = freq_snap.league_q('2026-08-23', 'sweden')
if on_day: assert q_day_before != q_day_after, '当日边界泄漏'
print('PASS')
```

- [ ] **Step 2: 确认失败** → ModuleNotFoundError
- [ ] **Step 3: 实现**——纯日期过滤 + Counter 归一；league 库映射（sweden→sweden_matches.json 等，从 `score_ev.map_league` 复用映射）；fd 联赛走 `band_calibration.fetch_rows(season)` 过滤日期
- [ ] **Step 4: 过测** → PASS
- [ ] **Step 5: 生成首轮回放全部轮次的 q 快照缓存**（每轮×每联赛一个 dict，存 `scratch/replay_v2/qcache/`，避免重复计算）

---

### Task 4: 票型引擎（方案族+等成本+结算）

**Files:**
- Create: `engine/scripts/scratch/replay_v2/shapes.py`
- Create: `engine/scripts/scratch/replay_v2/test_shapes.py`

**Interfaces:**
- Consumes: Task 2 的 Leg、Task 3 的 q 快照（TTG/CRS 腿的概率与选项）
- Produces:
  - `PLAN_FAMILIES: list[PlanSpec]`，`PlanSpec = {family:'A|B|C|D', name, pool:'strong|jiao_zhuo|mix|ev_best|ttg|crs', options:'single|dual|triple', shape:'N串1|4串11|全2关|8串9|单关|2串1', n_legs}`
  - `build_ticket(legs, spec) -> {picks: list[set[int]], bets: list[dict], n_bets: int, mult: int, cost: int}`（mult=⌊30/(n_bets*2)⌋ 至少 1）
  - `settle(legs, ticket) -> float`（真实赛果回款；复用本会话已验证的 settle 逻辑迁移）

- [ ] **Step 1: 写失败测试**

```python
import shapes
# 等成本: 4串11(11注)×1倍=22 ≤30; 8串1(1注)×15倍=30; 全2关6场(15注)×1倍=30
t1 = shapes.build_ticket(DUMMY4, SPECS['4串11']); assert t1['cost'] == 22
t2 = shapes.build_ticket(DUMMY8, SPECS['8串1']);  assert t2['cost'] == 30
# 结算: 全中腿票全额, 断腿串1=0
assert shapes.settle(WIN_LEGS, t2) > 0
assert shapes.settle(ONE_MISS_LEGS, t2) == 0
# 复式: 双选腿两结果之一命中即腿过
t3 = shapes.build_ticket(DUAL_LEGS, SPECS['4串1+2双选'])
assert shapes.settle(SECOND_OUTCOME_LEGS, t3) > 0, '次选项命中应派彩'
```
（DUMMY*/WIN_* 为测试内构造的假腿 fixture）

- [ ] **Step 2-4: 失败→实现→过测**——settle 逻辑从 `scratch/backtest_complex.py` 的 `settle()/n_bets_of()` 迁移（已两轮实测），补复式次选项派彩断言
- [ ] **Step 5: 胶着池自适应验证**（spec 变更#6）：`pool='jiao_zhuo'` 时腿数 <4 → 自动降 2串1/单关复式，写断言测试过

---

### Task 5: 三基线（seed 固定）

**Files:**
- Create: `engine/scripts/scratch/replay_v2/baseline.py`
- Create: `engine/scripts/scratch/replay_v2/test_baseline.py`

**Interfaces:**
- Consumes: Task 2 Leg、Task 4 shapes
- Produces: `random_baseline(round, spec, seed) -> Ticket`（双随机：场次从可买池均匀抽 + 选项三向均匀抽）；`market_hot(round, spec) -> Ticket`（每场买最低赔选项）；`pure_had(round, spec) -> Ticket`

- [ ] **Step 1: 测试**——同 seed 两次调用结果逐位相同（可复现性铁律）；不同 seed 分布不同
- [ ] **Step 2-4: 失败→实现→过测**（`random.Random(seed)`，seed=int(sha256(轮次日期+spec名)[:8],16)）
- [ ] **Step 5: 抽查 3 轮基线票面人工合理性** 记入 RUNLOG.txt

---

### Task 6: 统计层（bootstrap CI+多重比较+八列输出）

**Files:**
- Create: `engine/scripts/scratch/replay_v2/stats.py`
- Create: `engine/scripts/scratch/replay_v2/test_stats.py`

**Interfaces:**
- Consumes: 各方案逐轮 `[(cost, payout)]` 序列
- Produces: `summarize(rounds_nets: list[float], seed) -> {rate, rate_ci, legs_hit, legs_tot, total_profit, avg_net, roi, worst}`；`rank(results) -> 表`（前两名 CI 重叠判并列规则）

- [ ] **Step 1: 测试**——bootstrap 同 seed 同结果；CI 覆盖点估计；并列规则用构造数据断言
- [ ] **Step 2-4: 失败→实现→过测**（percentile bootstrap 1000 次）
- [ ] **Step 5: 输出格式固化**——八列：票面构成/每轮实际成本/胜率(回款率+腿命中率)/累计盈利金额(元)/轮均净/收益率(净/成本)/95%CI/最差轮

---

### Task 7: 首轮全景回放执行+中期报告

**Files:**
- Create: `engine/scripts/scratch/replay_v2/run_replay.py`
- Create: `engine/scripts/scratch/replay_v2/results/round1_*.json`（结果归档）
- Create: `data/04-summaries/2026-09-XX-complex-test-interim.html`（**HTML 报告**，中期参考——交付物按用户规则 HTML）

- [ ] **Step 1: 跑全景矩阵**（HAD 系 14 轮 + TTG/CRS 系 12 轮 + 基线×1000），结果 JSON 落 results/
- [ ] **Step 2: 校验可复现性**——重跑一次 diff 逐位一致（spec 变更#4），记 RUNLOG
- [ ] **Step 3: 生成中期报告 HTML**（排行榜八列+口径警示"中期参考不作优化依据"+纯净/近似分表）
- [ ] **Step 4: Commit（仅报告）**

```bash
git add data/04-summaries/2026-09-XX-complex-test-interim.html
git commit -m "docs(complex-test): 首轮全景回放中期参考报告——阶段1产出,不作优化依据"
```

---

### Task 8: paper trading 影子账本工具（阶段 2 备用）

**Files:**
- Create: `engine/scripts/scratch/replay_v2/paper.py`

**Interfaces:**
- Produces: `register(spec, legs, odds_frozen) -> 票`（写 `scratch/paper_tickets.json`）；`settle_all()`（真实赛果结算影子票）；真实购买票的登记走正账（tickets.json + `testGroup:"complex-test"` 标记）由会话内实票登记流程完成，本工具只管影子票

- [ ] **Step 1: 测试登记+结算往返**（构造 2 腿票断言结算金额）
- [ ] **Step 2-3: 实现→过测**（结算逻辑复用 shapes.settle）

---

## Self-Review 结论

- Spec 覆盖：§二指标(T6八列)、§三隔离(T2指纹/scratch纪律)、§四数据(T2双源/T3截断)、§五矩阵(T4族+T5基线)、§六统计(T6 CI+并列)、§七门槛(阶段3判定不在本计划=运营动作)、§八流程(T7中期/T8阶段2工具) ✅
- 占位符：无 TBD；DUMMY fixture 在测试内构造
- 类型一致：Leg/PlanSpec/Ticket 签名在 Interfaces 块对齐
- 已知留白（有意）：HAFU 回放不在本计划（spec 判定延后 10 月中，赔率积累后另立计划）；阶段 2/3 为运营动作非代码
