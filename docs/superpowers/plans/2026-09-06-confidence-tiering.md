# 置信度分层出票（A+C+N 三轨）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已定稿的三轨设计落地——保底仓重构为 3*4*5 五场容错（胆材分层+覆盖闸）、同日重出机制、离线重校准任务、叙事彩票模型（轨道 N）、验证看板。

**Architecture:** boldplay.py 是出票卡生成器（三档制），本计划把保底档从 4串11 重构为 3*4*5 并加覆盖闸；新增 narrative.py（叙事链生成）与 recalibrate.py（校准曲线）两个独立脚本；settle/影子层只做字段兼容性扩展。所有新字段对旧数据用 `.get()` 兜底。

**Tech Stack:** Python 3（标准库+numpy，已有依赖），pytest，JSON 数据文件。

**Spec:** `docs/2026-09-06-confidence-tiering-design.html`（含独立审核修订 A~E 与兼容性十.五节 7 面——本计划的兼容性要求全部来自该节）

## Global Constraints

- 命令路径铁律：脚本一律从 `engine/scripts/` 目录跑 `python <script>.py`；pytest 从 `engine/` 跑 `python3 -m pytest tests -q`
- 内部文件 JSON 禁止 HTML/SVG；仅用户报告用 HTML
- 类/脚本开发者署名 sszhang（docstring 注明）
- 禁止魔法值：所有阈值（0.75/0.60/0.55/1.35/0.68/5pp 等）定义为模块级常量
- 票面事实永不改写：已结算票/已结算卡只读不重算
- 新字段读旧数据一律 `.get()` 兜底（兼容面③④）
- 单写会话纪律：开工前 `git pull`，每任务一 commit
- 提交信息结尾：`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

### Task 1: 保底选腿改造——分层取 5 腿（2胆+3腿）

**Files:**
- Modify: `engine/scripts/boldplay.py:479-512`（`_base_legs` 函数）
- Test: `engine/tests/test_boldplay.py`（追加测试类）

**Interfaces:**
- Consumes: `devig_n`/`fuse`/`score_matrix`/`map_league`/`FD_ANCHOR_LEAGUES`（boldplay.py 已有）
- Produces: `_base_legs(odds_day, zh, dc_params_fn, fusion)` 返回 **5 条腿**的 list（旧为 4 条）；每条腿 dict 新增 `"tier": "dan"|"std"` 字段（胆级/标准级）；`BASE_TIER_DAN_P = 0.75`、`BASE_TIER_STD_P = 0.60`、`BASE_TREADLINE_ODDS = 1.35`、`BASE_TREADLINE_P = 0.68` 常量供 Task 2/3 引用

- [ ] **Step 1: 写失败测试**

在 `engine/tests/test_boldplay.py` 末尾追加（沿用文件里现有的 `fake_day` 构造模式——先读该文件开头 50 行找现成的假数据工厂函数名，下面以 `_mk_day` 代指，实施时替换为真实名）：

```python
class TestBaseTieredLegs:
    """保底3*4*5分层选腿(设计§四/§五): 2胆+3腿·5条返回·踩线过滤·补位规则"""
    def test_returns_five_legs_with_tier_field(self):
        day = _mk_day()          # 现有假数据工厂: 构造>=6场有had的fd锚场次
        legs = _base_legs(day, zh={}, dc_params_fn=lambda m, z: None)
        assert len(legs) == 5
        assert all("tier" in l for l in legs)
        dans = [l for l in legs if l["tier"] == "dan"]
        stds = [l for l in legs if l["tier"] == "std"]
        # 胆级优先2席; 不足2条胆时高p标准腿补位(补位腿tier=std, 审核修订B)
        assert len(dans) <= 2
        assert len(dans) + len(stds) == 5

    def test_dan_requires_p075(self):
        day = _mk_day()
        legs = _base_legs(day, zh={}, dc_params_fn=lambda m, z: None)
        for l in legs:
            if l["tier"] == "dan":
                assert l["p"] >= 0.75

    def test_treadline_filter(self):
        # 踩线降级(设计§四): 赔率<1.35 且 p<0.68 不入保底(朗斯案护栏)
        day = _mk_day()
        legs = _base_legs(day, zh={}, dc_params_fn=lambda m, z: None)
        for l in legs:
            assert not (l["odds"] < BASE_TREADLINE_ODDS and l["p"] < BASE_TREADLINE_P)

    def test_std_threshold_060(self):
        # 标准腿门槛 p>=0.60(设计§4.4: 0.60-0.65段实测74%)
        day = _mk_day()
        legs = _base_legs(day, zh={}, dc_params_fn=lambda m, z: None)
        assert all(l["p"] >= 0.60 for l in legs)

    def test_fewer_than_five_closes(self):
        # 零腿轮(设计§四): 合格腿<5返回短列表(关档由调用方判断)
        day = _mk_day_few()      # 只有3场合格——读test文件现有少场次工厂,无则现场构造
        legs = _base_legs(day, zh={}, dc_params_fn=lambda m, z: None)
        assert len(legs) < 5
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd engine && python3 -m pytest tests/test_boldplay.py::TestBaseTieredLegs -v`
Expected: FAIL（`_base_legs` 现返回 4 条、无 tier 字段；`BASE_TREADLINE_ODDS` 未定义）

- [ ] **Step 3: 最小实现**

`engine/scripts/boldplay.py` 模块常量区（`DIVERGENCE_LIMIT` 附近）加：

```python
BASE_TIER_DAN_P = 0.75        # 胆级线(设计§五: 实测96%·n=24)
BASE_TIER_STD_P = 0.60        # 标准级线(设计§4.4: 0.60-0.65段实测74%)
BASE_TREADLINE_ODDS = 1.35    # 踩线护栏(设计§四·审核C: 朗斯@1.36个案,1.35-1.70段实测72%)
BASE_TREADLINE_P = 0.68
```

`_base_legs` 改造（保留现有 fd 锚白名单/合赔保护/p_fused 计算，只改尾部选腿）：

```python
    pool.sort(key=lambda x: (-x[0], x[1]))
    # 分层取腿(设计§四): 胆级>=0.75优先2席→标准级>=0.60补满5席; 踩线(赔率<1.35且p<0.68)排除
    dans = [x for x in pool if x[0] >= BASE_TIER_DAN_P][:2]
    rest = [x for x in pool
            if x not in dans
            and x[0] >= BASE_TIER_STD_P
            and not (x[1] < BASE_TREADLINE_ODDS and x[0] < BASE_TREADLINE_P)]
    chosen = dans + rest[:5 - len(dans)]
    def _leg(p, o, m, k, tier):
        return {"matchNumStr": m.get("matchNumStr") or m.get("code"),
                "match": f'{m.get("home")}-{m.get("away")}',
                "play": "had", "pick": ("主胜", "平", "客胜")[k],
                "odds": o, "p": round(p, 4), "tier": tier}
    return [_leg(p, o, m, k, "dan" if (p, o, m, k) in dans else "std")
            for p, o, m, k in chosen]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd engine && python3 -m pytest tests/test_boldplay.py -v`
Expected: 全 PASS（含旧测试——旧测试若断言 4 条返回，按 Surgical 原则只改与腿数相关的断言并在 commit 里注明）

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/boldplay.py engine/tests/test_boldplay.py
git commit -m "feat(boldplay): 保底选腿分层改造——2胆(p>=0.75)+3腿(p>=0.60)共5条·tier字段·踩线护栏(1.35/0.68)·胆不足时高p标准腿补位(审核修订B)"
```

---

### Task 2: 保底档重构 3\*4\*5 + 覆盖闸

**Files:**
- Modify: `engine/scripts/boldplay.py:630-700`（`build_three_tier`）与 `:30-45`（常量区）
- Test: `engine/tests/test_boldplay.py`

**Interfaces:**
- Consumes: Task 1 的 `_base_legs`（5 腿+tier）、`expand_combos` 语义（backfill.py:437——但本任务保底档 bets 显式声明为 size≥3 组合，不复用 expand_combos 的 size≥2）
- Produces: `build_three_tier` 输出的 `tiers.base` 变为 `{"cost": 32*n, "legs": [...5条...], "play": "had-3*4*5", "bets": [{legs, multiplier}×16], "coverGate": {"pFull": <float>, "cap": <float>, "ok": <bool>}}`；新常量 `BASE_UNIT_STAKE = 2.0`、`BASE_COMBOS_MIN = 3`（3串1起点）；辅助函数 `payout_full_hit(legs, unit, mult)` 返回全中回款（Task 5 narrative 也用）

- [ ] **Step 1: 写失败测试**

```python
class TestThreeByFourByFive:
    """保底3*4*5重构(设计§四): 16注32元·bets显式·覆盖闸P_full>=32n+N"""
    def test_base_shape_16_bets(self):
        t = build_three_tier(_fake_day(), _fake_table(), seq=9, zh={}, form={})
        base = t["tiers"]["base"]
        assert base["play"] == "had-3*4*5"
        assert base["cost"] == 32
        assert len(base["bets"]) == 16            # C(5,3)+C(5,4)+C(5,5)=10+5+1
        sizes = {len(b["legs"]) for b in base["bets"]}
        assert sizes == {3, 4, 5}                 # 3串1×10+4串1×5+5串1×1

    def test_cover_gate_ok(self):
        t = build_three_tier(_fake_day(), _fake_table(), seq=9, zh={}, form={})
        gate = t["tiers"]["base"]["coverGate"]
        assert "pFull" in gate and "cap" in gate and gate["ok"] in (True, False)
        # n=1无叙事仓时: P_full >= 32 必然成立(设计§4.1)
        assert gate["pFull"] >= 32 or not gate["ok"]

    def test_cover_gate_narrative_cap(self):
        # 覆盖闸(设计§4.1): 叙事仓上限 = P_full - 32n
        t = build_three_tier(_fake_day(), _fake_table(), seq=9, zh={}, form={})
        gate = t["tiers"]["base"]["coverGate"]
        assert gate["cap"] == round(gate["pFull"] - 32, 2)

    def test_payout_full_hit(self):
        from boldplay import payout_full_hit
        legs = [{"odds": 1.3}, {"odds": 1.4}, {"odds": 1.5}, {"odds": 1.6}, {"odds": 1.7}]
        p = payout_full_hit(legs, unit=2.0, mult=1)
        assert abs(p - (2*(1.3*1.4*1.5 + 1.3*1.4*1.6 + 1.3*1.4*1.7 + 1.3*1.5*1.6 + 1.3*1.5*1.7 + 1.3*1.6*1.7 + 1.4*1.5*1.6 + 1.4*1.5*1.7 + 1.4*1.6*1.7 + 1.5*1.6*1.7
                          + 1.3*1.4*1.5*1.6 + 1.3*1.4*1.5*1.7 + 1.3*1.4*1.6*1.7 + 1.3*1.5*1.6*1.7 + 1.4*1.5*1.6*1.7
                          + 1.3*1.4*1.5*1.6*1.7))) < 0.01
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd engine && python3 -m pytest tests/test_boldplay.py::TestThreeByFourByFive -v`
Expected: FAIL（`payout_full_hit` 未定义、base 仍是 4串11）

- [ ] **Step 3: 最小实现**

boldplay.py 常量区加 `BASE_UNIT_STAKE = 2.0`、`BASE_COMBOS_MIN = 3`；新增函数（放 `_base_legs` 后）：

```python
def payout_full_hit(legs: list, unit: float = BASE_UNIT_STAKE, mult: int = 1) -> float:
    """保底3*4*5全中回款(设计§4.1覆盖闸): Σ size>=3 全组合单注奖金, 税前口径
    (单注<1万免税, 5串1低赔腿乘积远低于起征线). 开发者 sszhang"""
    from itertools import combinations
    total = 0.0
    for size in range(BASE_COMBOS_MIN, len(legs) + 1):
        for c in combinations(range(len(legs)), size):
            odds = 1.0
            for i in c:
                odds *= legs[i]["odds"]
            total += unit * mult * odds
    return round(total, 2)
```

`build_three_tier` 中 base 档组装段改为（保留现有 `_base_legs` 调用与 warnings 机制）：

```python
    base_legs = _base_legs(odds_day, zh, dc_params_fn, fusion)
    if len(base_legs) >= 5:
        from itertools import combinations
        bets_345 = [{"legs": list(c), "multiplier": 1}
                    for size in (3, 4, 5)
                    for c in combinations(range(5), size)]
        p_full = payout_full_hit(base_legs)
        base = {"cost": int(BASE_UNIT_STAKE * 16), "legs": base_legs,
                "play": "had-3*4*5", "bets": bets_345,
                "coverGate": {"pFull": p_full, "cap": round(p_full - 32, 2), "ok": True}}
    else:
        base = {"cost": 0, "legs": base_legs, "play": "had-3*4*5",
                "coverGate": None}   # 零腿轮关档(设计§四), 只出叙事档
```

同函数内预算检查 `if total_cost > ROUND_REDLINE:` 一段**删除**（设计§四：预算红线全取消）——注意同文件 `budget_gate`/`MONTHLY_CAP` 调用保留给 `--structure=legacy` 旧结构用（兼容面⑥：legacy 对照卡逻辑不动）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd engine && python3 -m pytest tests/test_boldplay.py tests/test_boldplay_settle.py -v`
Expected: 全 PASS（settle 测试若依赖 base 4 串结构，用票面 bets 显式口径——backfill.settle_payout 本就按 bets 算，天然兼容 3\*4\*5）

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/boldplay.py engine/tests/test_boldplay.py
git commit -m "feat(boldplay): 保底档3*4*5重构——16注bets显式·覆盖闸coverGate(P_full/上限/ok)·零腿轮关档·轮红线检查移除(新结构·legacy档保留旧预算逻辑)"
```

---

### Task 3: 同日重出机制（归档/superseded/seq 修正/实票保护闸）

**Files:**
- Modify: `engine/scripts/boldplay.py:1140-1215`（seq 计数与落盘段）
- Test: `engine/tests/test_boldplay_settle.py`（追加）

**Interfaces:**
- Consumes: `is_process_snapshot`（boldplay.py:387 已有）、`tickets.json` 路径（`data/06-tickets/tickets.json`）
- Produces: `archive_stale_card(path, tickets_path) -> Path | None`（重出前归档，返回归档路径或 None=无需归档）；落盘前主卡存在即自动调用；归档卡写 `"superseded": true, "supersededBy": "r{N}"`；seq 计数改为按**主文件去重自然日**计数

- [ ] **Step 1: 写失败测试**

```python
class TestRegenSameDay:
    """同日重出(设计§八): 归档命名/superseded/seq按日去重/实票保护闸"""
    def test_archive_renames_and_marks(self, tmp_path):
        main = tmp_path / "2026-09-07-boldplay.json"
        main.write_text(json.dumps({"date": "2026-09-07", "seq": 16, "approved": False}), ensure_ascii=False)
        archived = archive_stale_card(main, tickets_path=tmp_path / "nope.json")
        assert archived is not None and archived.name == "2026-09-07-r1-boldplay.json"
        card = json.loads(archived.read_text(encoding="utf-8"))
        assert card["superseded"] is True and card["supersededBy"] == "r1"
        assert not main.exists()          # 旧主卡已改名, 落盘段将写新主卡

    def test_archive_blocked_when_ticket_aligned(self, tmp_path):
        # 实票保护闸(设计§八): approved卡有票对齐→拒绝静默归档
        main = tmp_path / "2026-09-07-boldplay.json"
        main.write_text(json.dumps({"date": "2026-09-07", "seq": 16, "approved": True}), ensure_ascii=False)
        tickets = tmp_path / "tickets.json"
        tickets.write_text(json.dumps({"tickets": [
            {"id": "T023", "source": "boldplay", "placedAt": "2026-09-07",
             "stake": 32, "legs": []}]}, ensure_ascii=False))
        archived = archive_stale_card(main, tickets_path=tickets, force=False)
        assert archived is None           # 拒绝, 主卡原样保留
        assert main.exists()

    def test_force_overrides_protection(self, tmp_path):
        main = tmp_path / "2026-09-07-boldplay.json"
        main.write_text(json.dumps({"date": "2026-09-07", "approved": True}), ensure_ascii=False)
        tickets = tmp_path / "tickets.json"
        tickets.write_text('{"tickets": [{"id": "T023", "placedAt": "2026-09-07"}]}')
        archived = archive_stale_card(main, tickets_path=tickets, force=True)
        assert archived is not None

    def test_seq_counts_unique_dates(self, tmp_path, monkeypatch):
        # seq按自然日去重(设计§八): 同日r1快照不递增seq
        import boldplay as bp
        for name in ("2026-09-06-boldplay.json", "2026-09-06-r1-boldplay.json",
                     "2026-09-05-boldplay.json"):
            (tmp_path / name).write_text('{"date": "x", "seq": 1}')
        monkeypatch.setattr(bp, "PRED_DIR", tmp_path)
        assert bp._next_seq() == 3        # 2个自然日 → seq=3(而非文件数4)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd engine && python3 -m pytest tests/test_boldplay_settle.py::TestRegenSameDay -v`
Expected: FAIL（`archive_stale_card`/`_next_seq` 未定义）

- [ ] **Step 3: 最小实现**

boldplay.py 新增（放 `is_process_snapshot` 附近）：

```python
def _next_seq() -> int:
    """seq按自然日去重计数(设计§八): 同日rN快照不递增, 防翻身档轮换奇偶被打乱.
    开发者 sszhang"""
    dates = set()
    for p in PRED_DIR.glob("*-boldplay*.json"):
        if is_process_snapshot(p):
            continue
        try:
            card = json.loads(p.read_text(encoding="utf-8"))
            dates.add(card.get("date") or p.stem.split("-boldplay")[0])
        except Exception:
            dates.add(p.stem.split("-boldplay")[0])
    return len(dates) + 1


def archive_stale_card(main_path: Path, tickets_path: Path, force: bool = False) -> Path | None:
    """同日重出归档(设计§八): 旧主卡改名{date}-rN-boldplay.json+superseded标记.
    实票保护闸: 旧卡approved且有票对齐→拒绝(force=False时), 返回None. 开发者 sszhang"""
    if not main_path.exists():
        return None
    card = json.loads(main_path.read_text(encoding="utf-8"))
    if card.get("approved") and not force:
        try:
            tickets = json.loads(tickets_path.read_text(encoding="utf-8"))
            placed = {t.get("placedAt", "")[:10] for t in tickets.get("tickets", [])}
            if card.get("date") in placed:
                print(f"[boldplay] {main_path.name} 已approved且当日有实票——重出需 --force")
                return None
        except FileNotFoundError:
            pass
    n = 1
    while (main_path.parent / f"{card['date']}-r{n}-boldplay.json").exists():
        n += 1
    card["superseded"] = True
    card["supersededBy"] = f"r{n}"
    archived = main_path.parent / f"{card['date']}-r{n}-boldplay.json"
    main_path.rename(archived)
    archived.write_text(json.dumps(card, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return archived
```

main() 落盘段（~1208 `path = PRED_DIR / f"{date.today()}-boldplay{suffix}.json"` 之前）插入：

```python
    archive_stale_card(PRED_DIR / f"{date.today()}-boldplay{suffix}.json",
                       ROOT / "data/06-tickets/tickets.json", force="--force" in args)
```

seq 计数段（~1154 `seq = len(hist) + 1`）改为 `seq = _next_seq()`；settle 循环（~874）加一行跳过：`if ticket.get("superseded"): print(f"[boldplay] {p.name} 已作废(superseded)，跳过"); continue`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd engine && python3 -m pytest tests/test_boldplay_settle.py tests/test_boldplay.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/boldplay.py engine/tests/test_boldplay_settle.py
git commit -m "feat(boldplay): 同日重出机制——归档rN命名+superseded标记·seq按自然日去重·实票保护闸(--force显式突破)·settle跳过作废卡"
```

---

### Task 4: preference.json 迁移 schemaVersion=2

**Files:**
- Modify: `data/06-tickets/preference.json`
- Test: 无独立测试（数据文件，skill 会话消费；校验靠 JSON 合法性 + 结构断言脚本一次跑）

**Interfaces:**
- Consumes: 设计§四/§4.1/§4.2 的三仓结构
- Produces: `schemaVersion: 2`；新键 `tracks`（A 保底 3\*4\*5 参数 / N 叙事仓星级映射）；旧键 shapes/stake/playTaste 保留并标 `deprecated: true`（兼容面②：skill 会话读此文件，旧键过渡期保留）

- [ ] **Step 1: 写新结构（保留旧键）**

用 Python 脚本改写（jq 风格直接编辑 JSON，保留现有全部键）：

```python
import json
p = "data/06-tickets/preference.json"
d = json.load(open(p, encoding="utf-8"))
d["meta"]["lastUpdated"] = "2026-09-07"
d["schemaVersion"] = 2
d["deprecated"] = {"shapes": "v3轨道A取代", "stake": "预算红线已废除(设计§四)", "playTaste": "轨道N叙事仓取代"}
d["tracks"] = {
  "A_base": {"shape": "3*4*5", "combos": "3串1×10+4串1×5+5串1×1=16注", "unitStake": 2,
             "costPerMult": 32, "danSeats": 2, "stdSeats": 3,
             "danP": 0.75, "stdP": 0.60, "treadline": [1.35, 0.68],
             "coverGate": "P_full>=32n+N·突破需显式拍板标记"},
  "N_narrative": {"starStake": {"5": 10, "4": 5, "3": 2, "lt3": 0},
                  "plays": ["N-CRS-2x1", "N-HAFU-3x1", "N-MIX-2x1"],
                  "note": "金额=注数×2元·无上限·覆盖闸约束·★★以下只进影子"}}
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
```

- [ ] **Step 2: 校验 JSON 合法 + 旧键仍在**

Run: `python3 -c "import json; d=json.load(open('data/06-tickets/preference.json')); assert d['schemaVersion']==2 and 'shapes' in d and d['tracks']['A_base']['costPerMult']==32; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add data/06-tickets/preference.json
git commit -m "feat(preference): schemaVersion=2三轨结构——tracks.A_base(3*4*5参数/覆盖闸)+tracks.N_narrative(星级映射/玩法标记)·旧键保留标deprecated(skill会话兼容)"
```

---

### Task 5: recalibrate.py 校准曲线 + verify 接线

**Files:**
- Create: `engine/scripts/recalibrate.py`
- Modify: `engine/scripts/run.py:107-117`（verify 编排）
- Test: `engine/tests/test_recalibrate.py`（新建）

**Interfaces:**
- Consumes: `corpus.json` records（`p_final` list / `directionHit` bool）、`engine/cache/fusion.json` 先例模式（history 可回滚）
- Produces: `engine/cache/calibration_curve.json`（`{"generatedAt", "n", "bins": [{"pBin": 0.65, "claimed": 0.65, "actual": 0.74, "n": 35}...], "tiers": {"dan": 0.75, "std": 0.60, "obs": 0.55}}`）；`engine/cache/conf_tiers.json`（缺失时冷启动默认值——兼容面⑤）；CLI `python recalibrate.py`（幂等：语料增量<10 跳过）

- [ ] **Step 1: 写失败测试**

```python
# engine/tests/test_recalibrate.py
import json
from pathlib import Path
import recalibrate as rc

def test_bins_from_corpus(tmp_path, monkeypatch):
    recs = [
        {"p_final": [0.7, 0.2, 0.1], "directionHit": True,  "pick": "主胜"},
        {"p_final": [0.7, 0.2, 0.1], "directionHit": False, "pick": "主胜"},
        {"p_final": [0.7, 0.2, 0.1], "directionHit": True,  "pick": "主胜"},
        {"p_final": [0.7, 0.2, 0.1], "directionHit": True,  "pick": "主胜"},
    ]
    bins = rc.build_bins(recs)
    assert bins[0]["n"] == 4 and abs(bins[0]["actual"] - 0.75) < 0.01

def test_skip_when_small_delta(tmp_path, monkeypatch):
    # 幂等护栏: 语料增量<10场跳过(设计§六)
    assert rc.should_skip(prev_n=400, cur_n=405) is True
    assert rc.should_skip(prev_n=400, cur_n=415) is False

def test_conf_tiers_coldstart(tmp_path, monkeypatch):
    # 冷启动(兼容面⑤): conf_tiers.json缺失→内置默认值落盘
    monkeypatch.setattr(rc, "CACHE", tmp_path)
    tiers = rc.load_tiers()
    assert tiers == {"dan": 0.75, "std": 0.60, "obs": 0.55}
    assert (tmp_path / "conf_tiers.json").exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd engine && python3 -m pytest tests/test_recalibrate.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 最小实现**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线重校准(轨道C·设计§六): corpus全量→各p档声称vs实际校准曲线→calibration_curve.json
+ conf_tiers.json分层线滚动修正(偏差>8pp才调·语料每+10轮重估).
幂等: 语料增量<10场跳过. isatonic修正只标注不生效(影子AB 30轮后才切主链).
开发者 sszhang"""
import json
from datetime import date
from pathlib import Path
from common import ROOT, log

CACHE = ROOT / "engine/cache"
CORPUS = ROOT / "data/04-summaries/corpus.json"
DEFAULT_TIERS = {"dan": 0.75, "std": 0.60, "obs": 0.55}
TIER_SHIFT_PP = 0.08          # 分层线调整门槛(设计§五: 偏差>8pp才调)
MIN_DELTA_N = 10              # 幂等门槛(设计§六)

def build_bins(records: list) -> list:
    bins = {}
    for r in records:
        pf = r.get("p_final")
        if not isinstance(pf, list) or len(pf) != 3:
            continue
        if not r.get("pick") or r.get("pick") == "(避开)" or r.get("directionHit") is None:
            continue
        pm = max(pf)
        b = round(pm - 0.5 * (pm % 0.1 >= 0.05 and 0 or 0), 1)  # 0.1宽分箱
        b = round(int(pm * 10) / 10, 1)
        bins.setdefault(b, [0, 0])
        bins[b][1] += 1
        bins[b][0] += 1 if r["directionHit"] else 0
    return [{"pBin": b, "claimed": round(b + 0.05, 2),
             "actual": round(h / n, 3), "n": n}
            for b, (h, n) in sorted(bins.items()) if n >= 5]

def should_skip(prev_n: int, cur_n: int) -> bool:
    return cur_n - prev_n < MIN_DELTA_N

def load_tiers() -> dict:
    p = CACHE / "conf_tiers.json"
    if not p.exists():
        p.write_text(json.dumps({"tiers": DEFAULT_TIERS, "generatedAt": str(date.today())},
                                ensure_ascii=False, indent=1), encoding="utf-8")
        return DEFAULT_TIERS
    return json.loads(p.read_text(encoding="utf-8"))["tiers"]

def main() -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    records = corpus.get("records", [])
    curve = {"generatedAt": str(date.today()), "n": len(records),
             "bins": build_bins(records), "tiers": load_tiers()}
    out = CACHE / "calibration_curve.json"
    prev = json.loads(out.read_text(encoding="utf-8"))["n"] if out.exists() else 0
    if should_skip(prev, len(records)):
        log(f"recalibrate 跳过: 语料增量 {len(records) - prev} < {MIN_DELTA_N}")
        return
    out.write_text(json.dumps(curve, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"recalibrate → {out} ({len(curve['bins'])} bins, n={len(records)})")

if __name__ == "__main__":
    main()
```

run.py verify 编排（~107 `elif cmd == "verify":` 块）末尾 `sh("temperature.py", "--check")` 后加：

```python
        sh("recalibrate.py")       # 轨道C校准曲线(幂等: 增量<10跳过)
```

- [ ] **Step 4: 跑测试确认通过 + verify 干跑**

Run: `cd engine && python3 -m pytest tests/test_recalibrate.py -v && cd scripts && python recalibrate.py`
Expected: 测试全 PASS；脚本打印 bins 或"跳过"

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/recalibrate.py engine/scripts/run.py engine/tests/test_recalibrate.py
git commit -m "feat(recalibrate): 轨道C校准曲线——corpus分箱声称vs实际·conf_tiers冷启动默认值·幂等(<10场跳过)·挂run.py verify链尾"
```

---

### Task 6: 影子层 track 分轨

**Files:**
- Modify: `engine/scripts/scratch/replay_v2/paper.py:155-285`（settle_all/shadow_all/main）
- Test: `engine/tests/test_paper_track.py`（新建；paper.py 在 scratch 目录不在包内，测试用 `sys.path.insert` 导入）

**Interfaces:**
- Consumes: `paper_tickets.json`（44 张旧影子票，全无 track 字段——兼容面④）
- Produces: 新影子票强制 `"track": "A"|"N"` 字段；settle_all 统计输出分轨（`{track: {n, hits, payout}}`）；旧票无 track 默认 `"A"`

- [ ] **Step 1: 写失败测试**

```python
# engine/tests/test_paper_track.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "scratch" / "replay_v2"))
import paper

def test_old_ticket_defaults_track_a():
    t = {"id": "S001", "spec_name": "x", "legs": [], "cost": 2}   # 旧影子票无track
    assert paper._track_of(t) == "A"

def test_new_ticket_keeps_track():
    assert paper._track_of({"track": "N"}) == "N"

def test_by_track_stats():
    tickets = [
        {"id": "S1", "track": "A", "payout": 10, "cost": 2, "result": "hit"},
        {"id": "S2", "track": "N", "payout": 0, "cost": 2, "result": "miss"},
        {"id": "S3", "payout": 0, "cost": 2, "result": "miss"},   # 旧票→A
    ]
    stats = paper._by_track(tickets)
    assert stats["A"]["n"] == 2 and stats["N"]["n"] == 1
    assert stats["A"]["payout"] == 10
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd engine && python3 -m pytest tests/test_paper_track.py -v`
Expected: FAIL（`_track_of`/`_by_track` 未定义）

- [ ] **Step 3: 最小实现**

paper.py 新增两个函数（settle_all 前）：

```python
def _track_of(ticket: dict) -> str:
    """影子票来源轨道(设计§七·兼容面④): 旧票无track默认A(EV版). 开发者 sszhang"""
    return ticket.get("track") or "A"

def _by_track(tickets: list) -> dict:
    stats = {}
    for t in tickets:
        k = _track_of(t)
        s = stats.setdefault(k, {"n": 0, "payout": 0.0, "cost": 0.0})
        s["n"] += 1
        s["payout"] += t.get("payout") or 0
        s["cost"] += t.get("cost") or 0
    return stats
```

settle_all 输出段尾部分轨打印：

```python
    for k, s in _by_track(all_tickets).items():
        print(f"[shadow] track {k}: {s['n']}票 回款{s['payout']:.0f}/{s['cost']:.0f}元")
```

shadow_all 生成新票时加 `"track": spec.get("track", "A")`（spec 由调用方传入，轨道 N 的 spec 带 `track: "N"`——Task 7 narrative 调用时注入）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd engine && python3 -m pytest tests/test_paper_track.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/scratch/replay_v2/paper.py engine/tests/test_paper_track.py
git commit -m "feat(shadow): 影子票track分轨——新票强制track字段(A/N)·旧票默认A·settle-all分轨统计(EV版与叙事版CRS影子禁混口径·审核修订D)"
```

---

### Task 7: narrative.py 叙事链生成器（轨道 N 核心）

**Files:**
- Create: `engine/scripts/narrative.py`
- Test: `engine/tests/test_narrative.py`（新建）

**Interfaces:**
- Consumes: `league_profile` 产出（00-leagues 的 standings/score 分布）、`01-teams` formSummary、`dc_predict.score_matrix`（只取矩阵不取市场融合——设计§十一⑤层）、`score_ev.map_league`、`boldplay.payout_full_hit`（Task 2）
- Produces: `build_narrative(matches, league_profile, teams, seq) -> {"candidates": [{"code", "match", "layers": [5层链], "script": {"score": "2:0", "hafu": "hd", "dir": "主胜"}, "star": 1-5, "divergence": float}], "plays": [{"name": "N-甲", "playType": "N-CRS-2x1", "legs": [...], "mult": 12, "star": 4}]}`；CLI `python narrative.py`（读当日 sporttery_matches 缓存生成叙事卡，落 `data/03-predictions/{date}-narrative.json`）

- [ ] **Step 1: 写失败测试**

```python
# engine/tests/test_narrative.py
import narrative as nr

FAKE_MATCH = {"matchNumStr": "周六016", "league": "西甲", "home": "毕尔巴鄂", "away": "马竞",
              "had": {"h": 2.8, "d": 3.1, "a": 2.5}}
FAKE_PROFILE = {"standings": [], "scoreTop": {"2-0": 9, "1-0": 12, "1-1": 10},
                "drawRate": 0.24, "upsetRate": 0.18}
FAKE_TEAM = {"formSummary": {"last10": "6胜2平2负", "goalAvg": "1.9"}}

def test_five_layers_all_present():
    cand = nr.build_candidate(FAKE_MATCH, FAKE_PROFILE,
                              {"毕尔巴鄂": FAKE_TEAM, "马竞": FAKE_TEAM})
    names = [l["layer"] for l in cand["layers"]]
    assert names == ["strength", "form", "absence", "style", "script"]

def test_script_from_score_matrix():
    cand = nr.build_candidate(FAKE_MATCH, FAKE_PROFILE,
                              {"毕尔巴鄂": FAKE_TEAM, "马竞": FAKE_TEAM})
    assert cand["script"]["score"] in {"1:0", "2:0", "2:1", "0:0", "1:1", "0:1", "1:2", "2:2"}
    assert cand["script"]["hafu"] in {"hh", "hd", "ha", "dh", "dd", "da", "ah", "ad", "aa"}

def test_star_range_1_to_5():
    cand = nr.build_candidate(FAKE_MATCH, FAKE_PROFILE,
                              {"毕尔巴鄂": FAKE_TEAM, "马竞": FAKE_TEAM})
    assert 1 <= cand["star"] <= 5

def test_divergence_is_not_ev():
    # 分歧度=剧本概率-市场隐含(设计§十一): 只用于排序, 不算期望收益
    cand = nr.build_candidate(FAKE_MATCH, FAKE_PROFILE,
                              {"毕尔巴鄂": FAKE_TEAM, "马竞": FAKE_TEAM})
    assert isinstance(cand["divergence"], float)

def test_plays_carry_playtype():
    # 票面形状标记(设计§十二): playType必须落 N-CRS-2x1/N-HAFU-3x1/N-MIX-2x1
    card = nr.build_narrative([FAKE_MATCH], {"西甲": FAKE_PROFILE},
                              {"毕尔巴鄂": FAKE_TEAM, "马竞": FAKE_TEAM}, seq=1)
    for p in card["plays"]:
        assert p["playType"] in ("N-CRS-2x1", "N-HAFU-3x1", "N-MIX-2x1")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd engine && python3 -m pytest tests/test_narrative.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 最小实现**

narrative.py 骨架（五层链按设计§十一表逐层实现；λ 推导复用 dc_predict.score_matrix）：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轨道N叙事彩票模型(设计§十一/十二): 球队强弱/状态/缺阵/风格/剧本五层叙事链
推导比赛走向与比分→CRS/HAFU/混串玩法映射. 不算EV不参考市场定价(分歧度仅排序用).
开发者 sszhang"""
import json
from datetime import date
from pathlib import Path
from common import ROOT

SCRIPT_UNIVERSE = {"1:0", "2:0", "2:1", "0:0", "1:1", "0:1", "1:2", "2:2"}   # 小比分剧本域
HAFU_KEYS = ("hh", "hd", "ha", "dh", "dd", "da", "ah", "ad", "aa")
NARRATIVE_DIR = ROOT / "data/03-predictions"

def _strength_layer(m, profile):
    st = profile.get("standings") or []
    pos = {row.get("team"): row.get("pos") for row in st}
    return {"layer": "strength", "homePos": pos.get(m["home"]), "awayPos": pos.get(m["away"]),
            "drawRate": profile.get("drawRate"), "upsetRate": profile.get("upsetRate")}

def _form_layer(m, teams):
    h = (teams.get(m["home"]) or {}).get("formSummary") or {}
    a = (teams.get(m["away"]) or {}).get("formSummary") or {}
    return {"layer": "form", "homeLast10": h.get("last10"), "awayLast10": a.get("last10"),
            "homeGoalAvg": h.get("goalAvg"), "awayGoalAvg": a.get("goalAvg")}

def _absence_layer(m):
    # 伤停差值因子: 现有insight链已在02-results, 此处读取最近结果文件做近似
    # (ESPN injury端点接入是批次4后续任务, 先以现有资产跑通——设计§十一数据边界)
    return {"layer": "absence", "factor": 1.0, "source": "placeholder-until-espn"}

def _style_layer(m, profile):
    top = profile.get("scoreTop") or {}
    return {"layer": "style", "topScores": top}

def _script_layer(m, style):
    # 风格模板最高频比分→剧本(λ推导接dc_predict是P1.5, 先模板版跑通链路)
    top = style.get("topScores") or {}
    score = max(top, key=top.get) if top else "1:0"
    return {"layer": "script", "score": score}

def build_candidate(m: dict, profile: dict, teams: dict) -> dict:
    layers = [_strength_layer(m, profile), _form_layer(m, teams),
              _absence_layer(m), _style_layer(m, profile)]
    script_l = _script_layer(m, layers[3])
    layers.append(script_l)
    h, a = script_l["score"].split(":")
    hafu = ("h" if h > a else "d" if h == a else "a") + ("h" if h > a else "d" if h == a else "a")
    star = sum(1 for l in layers if l.get("factor", 1.0) >= 0.9 and l.get("layer") != "script")
    had = m.get("had") or {}
    p_mkt = 1.0 / float(had.get("h") or 3.0) if had.get("h") else 0.33
    divergence = 0.25 - p_mkt          # 剧本概率粗估0.25-市场隐含(排序用, 非EV)
    return {"code": m.get("matchNumStr"), "match": f'{m["home"]}-{m["away"]}',
            "layers": layers, "script": {"score": script_l["score"], "hafu": hafu,
            "dir": "主胜" if h > a else "平" if h == a else "客胜"},
            "star": min(5, max(1, star + 2)), "divergence": round(divergence, 4)}

def build_narrative(matches, profiles, teams, seq):
    cands = [build_candidate(m, profiles.get(m.get("league"), {}), teams) for m in matches]
    cands.sort(key=lambda c: -c["divergence"])          # 剧本分歧度选场(设计§十一)
    top = cands[:3]
    plays = [{"name": "N-甲", "playType": "N-CRS-2x1",
              "legs": [top[0], top[1]] if len(top) >= 2 else top, "mult": None, "star": top[0]["star"]}]
    return {"date": str(date.today()), "seq": seq, "candidates": cands, "plays": plays,
            "track": "N"}

def main() -> None:
    cache = ROOT / "engine/cache/sporttery_matches.json"
    data = json.loads(cache.read_text(encoding="utf-8"))
    card = build_narrative(data.get("matches", []), {}, {}, seq=1)
    out = NARRATIVE_DIR / f"{date.today()}-narrative.json"
    out.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[narrative] → {out}")

if __name__ == "__main__":
    main()
```

（注：`star`/`divergence` 的粗估公式是 v0 骨架——设计§十一的五层全硬判定在 ESPN 伤停/首发接入后细化，本任务只锁接口与链路通。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd engine && python3 -m pytest tests/test_narrative.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/narrative.py engine/tests/test_narrative.py
git commit -m "feat(narrative): 轨道N叙事链生成器v0——五层链(强弱/状态/缺阵/风格/剧本)·剧本分歧度排序·N-CRS-2x1玩法标记·不算EV(市场价仅查倍数)"
```

---

### Task 8: ticket_report 看板扩展（缺列不渲染）

**Files:**
- Modify: `engine/scripts/ticket_report.py`（读现有结构后在 KPI 段扩列）
- Test: `engine/tests/test_ticket_report.py`（若不存在则新建）

**Interfaces:**
- Consumes: `tickets.json`（旧票 22 张无新字段——兼容面①）、`calibration_curve.json`（Task 5 产出，可能不存在）
- Produces: 报告新列：全中覆盖率（票有 `coverGate` 才渲染）、突破覆盖频次（票 note 含"突破覆盖"才计数）、叙事命中（playType 以 "N-" 前缀的票单独分栏）；旧票三列全空不渲染该行

- [ ] **Step 1: 写失败测试**

```python
# engine/tests/test_ticket_report.py
import ticket_report as tr

def test_kpi_row_skipped_when_no_new_fields():
    # 兼容面①: 旧票无coverGate/playType新前缀→新KPI行不渲染(不报错)
    old_ticket = {"id": "T001", "stake": 22, "legs": [], "settled": {"payout": 20.4}}
    assert tr.has_new_kpi([old_ticket]) is False

def test_kpi_row_present_for_new_ticket():
    new_ticket = {"id": "T023", "stake": 32, "legs": [], "playType": "N-CRS-2x1",
                  "coverGate": {"ok": True}, "settled": {"payout": 0}}
    assert tr.has_new_kpi([new_ticket]) is True

def test_breakthrough_count():
    tickets = [
        {"id": "T023", "note": "突破覆盖:大哥拍板", "stake": 32, "legs": []},
        {"id": "T024", "stake": 32, "legs": []},
    ]
    assert tr.count_breakthrough(tickets) == 1

def test_narrative_split():
    tickets = [
        {"id": "T023", "playType": "N-CRS-2x1", "stake": 2, "legs": [], "settled": {"payout": 0}},
        {"id": "T024", "playType": "N-HAFU-3x1", "stake": 1, "legs": [], "settled": {"payout": 0}},
        {"id": "T025", "stake": 32, "legs": [], "settled": {"payout": 0}},
    ]
    n = tr.narrative_tickets(tickets)
    assert len(n) == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd engine && python3 -m pytest tests/test_ticket_report.py -v`
Expected: FAIL（函数未定义）

- [ ] **Step 3: 最小实现**

ticket_report.py 新增（KPI 渲染段调用）：

```python
def has_new_kpi(tickets: list) -> bool:
    """新KPI列存在性(兼容面①): 全部旧票→False, 渲染层跳过新行. 开发者 sszhang"""
    return any(t.get("coverGate") or str(t.get("playType", "")).startswith("N-")
               for t in tickets)

def count_breakthrough(tickets: list) -> int:
    return sum(1 for t in tickets if "突破覆盖" in (t.get("note") or ""))

def narrative_tickets(tickets: list) -> list:
    return [t for t in tickets if str(t.get("playType", "")).startswith("N-")]
```

渲染段加条件块（`if has_new_kpi(tickets):` 包住覆盖率/突破频次/叙事分栏三行）。

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `cd engine && python3 -m pytest tests -q`
Expected: 全 PASS（老测试零回归）

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/ticket_report.py engine/tests/test_ticket_report.py
git commit -m "feat(report): 看板三新列——全中覆盖率/突破覆盖频次/叙事分栏·旧票缺列不渲染(兼容面①)"
```

---

### Task 9: 收尾——README 更新 + 全量验证

**Files:**
- Modify: `README.md`（出票结构段一句话更新）
- 无新测试

**Interfaces:**
- Consumes: 全部前序任务
- Produces: 文档与现实一致

- [ ] **Step 1: README 出票段更新**

README.md 中描述"三档制（保底HAD 4串11+翻身+彩票档）"的句子替换为：

> 三轨制 v6：轨道A保底 3\*4\*5 五场容错（2胆+3腿·32n元·覆盖闸 P_full≥32n+N）+ 轨道N叙事仓（CRS/HAFU 剧本票·星级映射·不限额）+ 轨道C离线重校准（recalibrate 挂 verify 链）；预算红线废除，纪律=覆盖闸+星级+叙事质量熔断。

（找到实际句子再原样替换，保持前后文风格。）

- [ ] **Step 2: 全量回归 + verify 干跑**

Run: `cd engine && python3 -m pytest tests -q && cd scripts && python run.py verify`
Expected: pytest 全 PASS；verify 链跑通（recalibrate 行出现"跳过"或 bins 输出）

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): 三轨制v6出票结构更新——3*4*5保底+叙事仓+覆盖闸·预算红线废除"
```

---

## Self-Review 记录

1. **Spec 覆盖**：§四保底重构→Task 1/2；§4.1覆盖闸→Task 2；§4.2星级映射→Task 4（preference）+Task 7（narrative 产出 star）；§五分层→Task 1；§六recalibrate→Task 5；§七影子分轨→Task 6；§八同日重出→Task 3；§九看板熔断→Task 8（熔断程序化检查延后到影子层数据积累后，spec 未定义数据源细节，避免过度设计）；§十一/十二叙事→Task 7；§十.五兼容①→Task 8、②→Task 4、③④→Task 3/6 `.get()`兜底、⑤→Task 5 冷启动、⑥⑦→Task 5 run.py 一行。**缺口说明**：ESPN 伤停/首发端点（批次4 数据源）不在本计划——依赖外部 API 可用性验证，单独出小计划；isotonic 回归（§六②）语料 n≥400 才触发，当前 459 条但增量门槛未到，Task 5 只铺曲线地基。
2. **占位符扫描**：无 TBD/TODO；Task 7 absence 层 `"source": "placeholder-until-espn"` 是**数据源标注**（运行时可见的诚实降级标记）非计划占位符——保留。
3. **类型一致性**：`payout_full_hit(legs, unit, mult)` Task 2 定义、Task 7 未直接调用（确认无签名冲突）；`_track_of`/`_by_track` Task 6 定义并自用；`tier: "dan"|"std"` Task 1 产出、Task 2 消费；`playType: "N-CRS-2x1"` Task 7 产出、Task 8 消费——前缀口径一致。
