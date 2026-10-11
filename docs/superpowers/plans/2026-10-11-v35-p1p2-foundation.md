# v35 地基工程 P1+P2 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 生产链装上九步证据链仪表（P1）+ 五源数据质量账本（P2），并行交付，各自独立验收。

**Architecture:** evidence.py 轻量账本（fail-open·幂等·原子写）为核心；dc_predict 挂 S3/S5/S6、strength_chain_prod 挂 S2/S4/S5/S7、run.py update 挂 S1；S8/S9 走 CLI 会话登记。P2 独立脚本 data_ledger.py 扫已有档案出账本 JSON，run.py update 末尾自动刷新。

**Tech Stack:** Python 3（python3 运行）· pytest 9.1.1（engine/scripts/tests/ 已有 conftest 注入 sys.path）· 无新第三方依赖。

**Spec:** `docs/2026-10-11-v35-foundation-design.html`（本计划从设计档论证；执行者须读两者）。P3/P4 不在本计划——各阶段 GO/NO-GO 后另立计划。

## Global Constraints（全任务隐含遵守）

- **fail-open**：仪表任何异常只警告不中断——预测主流程永不为 logging 挂掉（设计档 P1 铁设计①）
- **幂等**：按（开赛日, matchKey, step）键去重，重跑覆盖同键（铁设计②）
- **键口径=开赛日**（物理比赛日）；matchKey 优先级 matchId > 编号(matchNumStr) > "联赛|主|客"；文件名禁 `:`（Windows 双机）
- **无魔法值**：步骤名/池名一律常量（STEP_*/POOL_*）；业务逻辑禁字符串字面量
- **schema 带 version 字段**：加字段兼容，改语义升版
- **命令路径**：python3，工作目录=仓库根；测试 `python3 -m pytest engine/scripts/tests/<file> -v`
- 注释中文、开发者 sszhang（匹配仓库风格）；提交信息中文、带 Co-Authored-By 尾注
- 每任务一个 commit；单写会话纪律（开工先 git pull，收工 commit）

## 探测已确认的锚点（执行者不必重新探测）

- `engine/scripts/dc_predict.py:254` main()：单场 CLI（league/home/away + 可选 --market）；`lh/la` λ、`dc["rho"]`、result dict 含 lambdaHome/lambdaAway/market/p_fused/fusion(a,b)；打印前（line ~345 `print(json.dumps(result...))` 之前）为挂钩点
- `engine/scripts/strength_chain_prod.py:88-98`：entry 组装处（entry 含 code/matchId/league/lam/had/crs_top5/boomP/water）；`pred = sce._predict_match(...)` 在 line 80；doc params `boom/had_hot` 在 line 102
- `engine/scripts/run.py:78`：`sh("sporttery_fetch.py", "dump-odds")` —— S1 接线点
- score_odds 存档形状：`{fetchedAt, source, matchDays: [{businessDate, matches: [{matchNumStr, league, home, away, had/crs/ttg/hhad/hafu(各为 dict), oddsUpdatedAt}]}]}`，**无 matchId**，键=matchNumStr
- strength 链 ctx：`{"timeline", "dc", "elo", "xg", "hst"(按联赛代码), "unmapped"}`；`pred.get("league")` 是内部联赛代码
- lineups 时序：`data/05-trends/{date}-lineups.json` = `{date, type, schemaVersion, protocol, matches{matchId: entry}}`
- 别名表：`data/01-teams/_aliases.json` dict 49 条
- HST 文件：`hst_{lg}.json` glob `engine/cache/**/hst_*.json` 兜底（不锁死目录）
- fd Pinnacle 生产消费物 = `data/02-results/*.json` 的 `matches[].pinClose`（账本按此统计锚覆盖率）

---

## P1 · 证据链仪表化

### Task 1: evidence.py 核心（record + 常量 + 原子写）

**Files:**
- Create: `engine/scripts/evidence.py`
- Test: `engine/scripts/tests/test_evidence.py`

**Interfaces:**
- Produces: `EVIDENCE_ROOT: Path`（模块级，测试可换）、`SCHEMA_VERSION = 1`、步骤常量 `STEP_ODDS/S2...S9`（值 "S1_odds"…"S9_scheme"）、`norm_key(match_id=None, code=None, league=None, home=None, away=None) -> str`、`record(match_day: str, match_key: str, step: str, payload: dict, source: str = "code") -> bool`

- [ ] **Step 1: 写失败测试**

```python
# engine/scripts/tests/test_evidence.py
# -*- coding: utf-8 -*-
"""v35 P1 证据链核心测试。开发者 sszhang"""
import json

import evidence


def _swap_root(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "EVIDENCE_ROOT", tmp_path)


def test_record_roundtrip(tmp_path, monkeypatch):
    _swap_root(tmp_path, monkeypatch)
    assert evidence.record("2026-10-12", "周日001", evidence.STEP_DC,
                           {"lambdaHome": 1.4}, source="code") is True
    doc = json.loads((tmp_path / "2026-10-12" / "周日001.json").read_text(encoding="utf-8"))
    assert doc["schema"] == evidence.SCHEMA_VERSION
    assert doc["steps"][evidence.STEP_DC]["payload"]["lambdaHome"] == 1.4


def test_record_idempotent_overwrite(tmp_path, monkeypatch):
    _swap_root(tmp_path, monkeypatch)
    evidence.record("2026-10-12", "k", evidence.STEP_DC, {"v": 1})
    evidence.record("2026-10-12", "k", evidence.STEP_DC, {"v": 2})   # 同键覆盖不重复
    doc = json.loads((tmp_path / "2026-10-12" / "k.json").read_text(encoding="utf-8"))
    assert list(doc["steps"]) == [evidence.STEP_DC]
    assert doc["steps"][evidence.STEP_DC]["payload"]["v"] == 2


def test_record_fail_open(tmp_path, monkeypatch):
    # EVIDENCE_ROOT 指向文件（非目录）→ 写必败 → 只返回 False 绝不抛
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(evidence, "EVIDENCE_ROOT", blocker)
    assert evidence.record("2026-10-12", "k", evidence.STEP_DC, {"v": 1}) is False


def test_norm_key_priority(tmp_path):
    assert evidence.norm_key(match_id="12345") == "12345"
    assert evidence.norm_key(code="周日003") == "周日003"
    assert evidence.norm_key(league="英超", home="A", away="B") == "英超|A|B"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest engine/scripts/tests/test_evidence.py -v`
Expected: FAIL（ModuleNotFoundError: evidence）

- [ ] **Step 3: 最小实现**

```python
# engine/scripts/evidence.py
# -*- coding: utf-8 -*-
"""v35 P1 证据链账本——每场预测九步输入输出落盘（设计 docs/2026-10-11-v35-foundation-design.html）。

铁设计：fail-open（仪表异常只警告不中断主流程）/ 幂等（开赛日+matchKey+step 同键覆盖）/
原子写（tmp+replace）/ 键口径=开赛日（物理比赛日）。开发者 sszhang
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = ROOT / "engine" / "cache" / "evidence"
SCHEMA_VERSION = 1

# 九步常量（禁魔法值）
STEP_ODDS = "S1_odds"            # 五池赔率快照引用
STEP_PROFILE = "S2_profiles"     # 画像引用（elo/xg/HST 指针+新鲜度）
STEP_DC = "S3_dc"                # DC 攻防参数 + rho
STEP_LAMBDA = "S4_lambda"        # V3W λ
STEP_DEVIG = "S5_devig"          # 市场去水
STEP_FUSION = "S6_fusion"        # 融合系数 + p_fused
STEP_MATRIX = "S7_matrix"        # 比分矩阵
STEP_HYPO = "S8_hypothesis"      # 假设层（会话登记）
STEP_SCHEME = "S9_scheme"        # 挡位方案（会话登记）
ALL_STEPS = (STEP_ODDS, STEP_PROFILE, STEP_DC, STEP_LAMBDA, STEP_DEVIG,
             STEP_FUSION, STEP_MATRIX, STEP_HYPO, STEP_SCHEME)

SOURCE_CODE = "code"
SOURCE_SESSION = "session"


def norm_key(match_id=None, code=None, league=None, home=None, away=None) -> str:
    """matchKey 优先级：matchId > 体彩编号 > 联赛|主|客（禁 ':'，Windows 双机兼容）。"""
    if match_id:
        return str(match_id)
    if code:
        return str(code)
    return f"{league}|{home}|{away}"


def record(match_day: str, match_key: str, step: str, payload: dict,
           source: str = SOURCE_CODE) -> bool:
    """幂等落盘一条步骤记录。fail-open：任何异常打印警告返回 False，绝不抛出。"""
    try:
        day_dir = EVIDENCE_ROOT / match_day
        day_dir.mkdir(parents=True, exist_ok=True)
        f = day_dir / f"{match_key}.json"
        doc = (json.loads(f.read_text(encoding="utf-8")) if f.exists()
               else {"schema": SCHEMA_VERSION, "day": match_day, "matchKey": match_key, "steps": {}})
        doc["steps"][step] = {"source": source,
                              "recordedAt": datetime.now().isoformat(timespec="seconds"),
                              "payload": payload}
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(f)                    # 原子写
        return True
    except Exception as exc:              # noqa: BLE001 —— fail-open 铁设计
        print(f"[evidence] 警告: {step}@{match_day}/{match_key} 记录失败 {exc}（不中断）",
              file=sys.stderr)
        return False
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest engine/scripts/tests/test_evidence.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/evidence.py engine/scripts/tests/test_evidence.py
git commit -m "feat(v35-P1): evidence.py 证据链核心——fail-open·幂等·原子写·九步常量"
```

---

### Task 2: 完整性检查 + 控制台打印机 + S1 批量登记 + CLI

**Files:**
- Modify: `engine/scripts/evidence.py`（追加）
- Test: `engine/scripts/tests/test_evidence.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 record/常量
- Produces: `check(match_day: str) -> dict`（{matchKey: [缺失步骤]}，含无文件日返回 {}）；`show(match_day: str, match_key: str) -> None`（控制台打表）；`record_s1_from_archive(archive_day: str) -> int`（从 score_odds/{day}.json 逐场登记 S1，返回登记数）；CLI 子命令 `record/s1/check/show`

- [ ] **Step 1: 追加失败测试**

```python
def test_check_missing_steps(tmp_path, monkeypatch):
    _swap_root(tmp_path, monkeypatch)
    evidence.record("2026-10-12", "k", evidence.STEP_ODDS, {})
    missing = evidence.check("2026-10-12")["k"]
    assert set(missing) == set(evidence.ALL_STEPS) - {evidence.STEP_ODDS}


def test_check_empty_day(tmp_path, monkeypatch):
    _swap_root(tmp_path, monkeypatch)
    assert evidence.check("1999-01-01") == {}


def test_record_s1_from_archive(tmp_path, monkeypatch):
    _swap_root(tmp_path, monkeypatch)
    arch = tmp_path / "arch.json"
    arch.write_text(json.dumps({"fetchedAt": "T", "source": "sporttery", "matchDays": [
        {"businessDate": "2026-10-12", "matches": [
            {"matchNumStr": "周日001", "league": "英超", "home": "A", "away": "B",
             "had": {"h": 2.0, "d": 3.0, "a": 3.5}, "oddsUpdatedAt": "t1"}]}]},
        ensure_ascii=False), encoding="utf-8")
    n = evidence.record_s1_from_archive(str(arch))
    assert n == 1
    doc = json.loads((tmp_path / "2026-10-12" / "周日001.json").read_text(encoding="utf-8"))
    p = doc["steps"][evidence.STEP_ODDS]["payload"]
    assert p["pools"] == ["had"] and p["oddsUpdatedAt"] == "t1"
```

- [ ] **Step 2: 跑测试确认新用例失败**

Run: `python3 -m pytest engine/scripts/tests/test_evidence.py -v`
Expected: 新 3 例 FAIL（check/record_s1 不存在），旧 4 例 PASS

- [ ] **Step 3: 实现（追加到 evidence.py）**

```python
def check(match_day: str) -> dict:
    """完整性检查：每场缺哪些步（缺步标注不补造——设计档 P1 铁设计）。"""
    day_dir = EVIDENCE_ROOT / match_day
    if not day_dir.exists():
        return {}
    out = {}
    for f in sorted(day_dir.glob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                  # noqa: BLE001 —— 脏文件不炸检查
            out[f.stem] = ["<文件损坏>"]
            continue
        out[f.stem] = [s for s in ALL_STEPS if s not in doc.get("steps", {})]
    return out


def show(match_day: str, match_key: str) -> None:
    """控制台打表：这场怎么算的（大哥要求直出不切窗口）。"""
    f = EVIDENCE_ROOT / match_day / f"{match_key}.json"
    if not f.exists():
        print(f"[evidence] 无记录: {f}")
        return
    doc = json.loads(f.read_text(encoding="utf-8"))
    print(f"=== 证据链 {match_day} {match_key} (schema {doc['schema']}) ===")
    for step in ALL_STEPS:
        rec = doc["steps"].get(step)
        if not rec:
            print(f"  {step:14s} ✗ 缺")
            continue
        body = json.dumps(rec["payload"], ensure_ascii=False)
        print(f"  {step:14s} ✓ [{rec['source']}] {rec['recordedAt']} · {body[:160]}")


POOL_KEYS = ("had", "crs", "ttg", "hhad", "hafu")


def record_s1_from_archive(archive_path_or_day: str) -> int:
    """从 score_odds 日存档逐场登记 S1（键=matchNumStr·开赛日=存档文件日）。"""
    p = Path(archive_path_or_day)
    if not p.exists():
        p = ROOT / "engine" / "cache" / "score_odds" / f"{archive_path_or_day}.json"
        if not p.exists():
            print(f"[evidence] S1 源不存在: {archive_path_or_day}")
            return 0
    day = p.stem
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:              # noqa: BLE001
        print(f"[evidence] S1 存档解析失败 {exc}")
        return 0
    n = 0
    for blk in doc.get("matchDays") or []:
        for m in blk.get("matches") or []:
            pools = [k for k in POOL_KEYS if m.get(k)]
            payload = {"archive": p.name, "fetchedAt": doc.get("fetchedAt"),
                       "pools": pools, "oddsUpdatedAt": m.get("oddsUpdatedAt"),
                       "sellStatus": m.get("sellStatus")}
            n += record(day, str(m.get("matchNumStr") or ""), STEP_ODDS, payload)
    return n


def _cli() -> None:
    args = sys.argv[1:]
    if len(args) >= 5 and args[0] == "record":       # 会话登记 S8/S9 走此口
        ok = record(args[1], args[2], args[3], json.loads(args[4]), source=SOURCE_SESSION)
        print("[evidence] 会话登记 ✓" if ok else "[evidence] 会话登记 失败(警告)")
    elif len(args) >= 2 and args[0] == "s1":
        print(f"[evidence] S1 登记 {record_s1_from_archive(args[1])} 场")
    elif len(args) >= 2 and args[0] == "check":
        r = check(args[1])
        for k, miss in r.items():
            tag = "齐" if not miss else f"缺 {'/'.join(miss)}"
            print(f"  {k}: {tag}")
        print(f"[evidence] {len(r)} 场检查完毕（缺步标注不补造）")
    elif len(args) >= 3 and args[0] == "show":
        show(args[1], args[2])
    else:
        print("用法: evidence.py record <日> <键> <步> '<json>' | s1 <存档日> | "
              "check <日> | show <日> <键>")


if __name__ == "__main__":
    _cli()
```

- [ ] **Step 4: 跑测试确认全通过**

Run: `python3 -m pytest engine/scripts/tests/test_evidence.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/evidence.py engine/scripts/tests/test_evidence.py
git commit -m "feat(v35-P1): 证据链 check/show 打印机 + S1 存档批量登记 + 会话登记 CLI"
```

---

### Task 3: dc_predict 挂钩（S3/S5/S6 + --mid/--day 选项）

**Files:**
- Modify: `engine/scripts/dc_predict.py`（main() 参数区 line ~255-263 + 打印前 line ~345）

**Interfaces:**
- Consumes: evidence.record / norm_key / STEP_DC / STEP_DEVIG / STEP_FUSION
- Produces: dc_predict 支持 `--mid <matchId>` `--day <开赛日>`（缺省=今天）；无 --mid 时 matchKey 退化为 "联赛|主|客"

- [ ] **Step 1: 参数区追加解析（line ~259 --market 块后）**

```python
    mid = None
    if "--mid" in sys.argv:
        mid = sys.argv[sys.argv.index("--mid") + 1]
    mday = None
    if "--day" in sys.argv:
        mday = sys.argv[sys.argv.index("--day") + 1]
```

- [ ] **Step 2: 打印前挂钩（`print(json.dumps(result...))` 之前插入）**

```python
    # v35 证据链 hook（fail-open·record 内部再兜一层）
    try:
        import evidence
        from datetime import date as _date
        _key = evidence.norm_key(match_id=mid, league=league, home=h_key, away=a_key)
        _day = mday or _date.today().isoformat()
        evidence.record(_day, _key, evidence.STEP_DC,
                        {"league": league, "dcFile": f"{league}_dc.json",
                         "lambdaHome": round(lh, 3), "lambdaAway": round(la, 3),
                         "rho": dc.get("rho")})
        if market:
            evidence.record(_day, _key, evidence.STEP_DEVIG,
                            {"method": "1/o", "market": market, "p_mkt": result["market"]})
            evidence.record(_day, _key, evidence.STEP_FUSION,
                            {"a": fus_a, "b": fus_b, "p_fused": result["p_fused"]})
    except Exception as _ev:              # noqa: BLE001 —— fail-open
        print(f"[dc_predict] evidence 警告: {_ev}", file=sys.stderr)
```

- [ ] **Step 3: 真实冒烟（选一个已有 DC 缓存联赛）**

Run: `ls engine/cache/*_dc.json | head -3` 取一联赛与两队名，然后：
`python3 engine/scripts/dc_predict.py <联赛> "<主>" "<客>" --market 2.0,3.0,3.5 --mid 99999 --day 2026-10-12`
Expected: 正常输出 result JSON；随后
`python3 engine/scripts/evidence.py show 2026-10-12 99999` 打出 S3/S5/S6 三步 ✓

- [ ] **Step 4: 冒烟后清理测试残留**

Run: `rm -rf engine/cache/evidence/2026-10-12`
（99999 是冒烟假键，不入库；真实预测自然生成）

- [ ] **Step 5: Commit**

```bash
git add engine/scripts/dc_predict.py
git commit -m "feat(v35-P1): dc_predict 挂证据链 S3/S5/S6——--mid/--day 选项·fail-open"
```

---

### Task 4: strength_chain_prod 挂钩（S2/S4/S5/S7）

**Files:**
- Modify: `engine/scripts/strength_chain_prod.py`（entry 组装后 line ~98 `cands.append(entry)` 附近）

**Interfaces:**
- Consumes: evidence.record / norm_key / STEP_PROFILE / STEP_LAMBDA / STEP_DEVIG / STEP_MATRIX
- Produces: 每场成功预测自动落四步；args.day 即开赛日（与 {date}-crs.json 同口径）

- [ ] **Step 1: entry 组装后插入 hook（`cands.append(entry)` 之前）**

```python
        # v35 证据链 hook（fail-open·外层兜底）
        try:
            import evidence
            _key = evidence.norm_key(match_id=m.get("matchId"), code=m.get("code"),
                                     home=m["home"], away=m["away"])
            evidence.record(args.day, _key, evidence.STEP_LAMBDA,
                            {"lam": entry["lam"], "beta": 0.05})
            evidence.record(args.day, _key, evidence.STEP_PROFILE,
                            {"hid": hid, "aid": aid, "leagueCode": pred.get("league"),
                             "hstRows": len(ctx["hst"].get(pred.get("league") or "", []),
                             "unmappedLeague": (pred.get("league") or "") in ctx["unmapped"]})
            evidence.record(args.day, _key, evidence.STEP_DEVIG,
                            {"method": "V3W-dewater-v2", "boom": BOOM_V2,
                             "hadHot": HAD_HOT_V2, "water": is_water,
                             "boomP": entry["boomP"], "had": entry["had"]})
            evidence.record(args.day, _key, evidence.STEP_MATRIX,
                            {"top5": entry["crs_top5"], "lam": entry["lam"]})
        except Exception:                 # noqa: BLE001 —— fail-open
            pass
```

- [ ] **Step 2: 单元测试验证 hook 落盘（不跑全链重放）**

追加到 `engine/scripts/tests/test_evidence.py`：

```python
def test_scp_hook_payload_shape(tmp_path, monkeypatch):
    """hook 逻辑抽取验证：四步 payload 形状（直接构造 entry 形状调 record）。"""
    _swap_root(tmp_path, monkeypatch)
    entry = {"lam": [1.4, 1.1], "crs_top5": [{"pick": "s1s0", "p": 0.12}],
             "boomP": 0.03, "water": False, "had": {"h": 0.5}}
    evidence.record("2026-10-12", "k", evidence.STEP_LAMBDA, {"lam": entry["lam"], "beta": 0.05})
    evidence.record("2026-10-12", "k", evidence.STEP_MATRIX,
                    {"top5": entry["crs_top5"], "lam": entry["lam"]})
    doc = json.loads((tmp_path / "2026-10-12" / "k.json").read_text(encoding="utf-8"))
    assert evidence.STEP_LAMBDA in doc["steps"] and evidence.STEP_MATRIX in doc["steps"]
```

Run: `python3 -m pytest engine/scripts/tests/test_evidence.py -v`
Expected: 8 passed

- [ ] **Step 3: 冒烟（fixture 喂场+无卡历史日——禁 --force 重跑真实日防覆写预测归档）**

1. 选无 crs.json 的历史日：`ls data/03-predictions/*-crs.json`，取其中不存在的最近一日（预期 2026-10-05；已存在则往前一日）
2. 找两个可映射队名：`python3 -c "import sys; sys.path.insert(0,'engine/scripts'); import strength_loaders as sl; print(list(sl.zh_to_id().items())[:8])"`（取前两队；若冒烟 skip=no_data 换下一对）
3. 写 fixture `/tmp/v35_feed.json`（**注意 --feed 期望 list 形态**）：
   `[{"matchId": "900001", "code": "周六901", "home": "<队1>", "away": "<队2>", "league": "<联赛名>", "kickoff": "2026-10-05 20:00"}]`
4. Run: `python3 engine/scripts/strength_chain_prod.py <该历史日> --feed /tmp/v35_feed.json`
5. Run: `python3 engine/scripts/evidence.py check <该历史日> | tail -3`
   Expected: 900001 场 S2/S4/S5/S7 ✓（S1/S3/S6/S8/S9 缺=正常——各归各脚本/会话）
6. 清理: `rm data/03-predictions/<该历史日>-crs.json /tmp/v35_feed.json && rm -rf engine/cache/evidence/<该历史日>`

- [ ] **Step 4: Commit**

```bash
git add engine/scripts/strength_chain_prod.py engine/scripts/tests/test_evidence.py
git commit -m "feat(v35-P1): strength_chain_prod 挂证据链 S2/S4/S5/S7——λ/画像引用/去水v2/矩阵"
```

---

### Task 5: run.py update 接线 S1 + 账本刷新占位

**Files:**
- Modify: `engine/scripts/run.py:78`（dump-odds 调用后）

**Interfaces:**
- Consumes: evidence.record_s1_from_archive（经 CLI `s1`）
- Produces: 每次 run.py update 自动登记当日 S1（score_odds 日存档）

- [ ] **Step 1: line 78 后追加一行**

```python
        sh("evidence.py", "s1 " + date.today().isoformat())   # v35-P1: 证据链 S1 自动登记
```

（若 run.py 未 import date，按文件头既有 import 风格补 `from datetime import date`；已 import 则跳过）

- [ ] **Step 2: 验证**

Run: `python3 -c "import ast; ast.parse(open('engine/scripts/run.py').read())"` 
Expected: 无输出（语法通过）；接线行为由 Task 7 验收覆盖（不真跑 update——会拉网络）

- [ ] **Step 3: Commit**

```bash
git add engine/scripts/run.py
git commit -m "feat(v35-P1): run.py update 末尾自动登记证据链 S1"
```

---

### Task 6: 协议登记（S8/S9 会话纪律入 CLAUDE.md 使用协议）

**Files:**
- Modify: `.claude/CLAUDE.md`（"帮我预测"行）

**Interfaces:**
- Consumes: evidence CLI record（S8/S9）/ check（出票前）

- [ ] **Step 1: "帮我预测"行的流程链尾部（git commit 之前）插入短语**

锚点（**唯一**·勿用 `→ git commit |`，它在协议表出现 4 次）：在该行 `→ git commit | 摘要卡（方案/赔率/仓位·**闯关票面自动附后**）` 的 `→ git commit` 前插入。追加内容：
`→ **证据链收口：S8/S9 会话登记（`evidence.py record <开赛日> <编号> S8_hypothesis|S9_scheme '<json>'`·假设 verdict+挡位+最终腿）+ 出票前完整性检查（`evidence.py check <开赛日>`·缺步标注不补造·S1/S3/S5/S6 代码 hook 自动落·S2/S4/S7 strength_chain_prod 自动落）** `

- [ ] **Step 2: Commit**

```bash
git add .claude/CLAUDE.md
git commit -m "feat(v35-P1): S8/S9 会话登记纪律入预测协议——出票前完整性检查缺步标注不补造"
```

---

### Task 7: P1 验收（GO/NO-GO 闸）

**Files:** 无新文件（验收跑既有件）

- [ ] **Step 1: 全测试**

Run: `python3 -m pytest engine/scripts/tests/test_evidence.py -v`
Expected: 8 passed（fail-open/幂等/check/show/S1 全绿）

- [ ] **Step 2: 端到端演示（同 Task 3 Step 3 冒烟 + check）**

dc_predict 真实一场 + `evidence.py check` 输出 S3/S5/S6 ✓；演示后 `rm -rf engine/cache/evidence/2026-10-12`

- [ ] **Step 3: 证据链目录纳入 git（凭证链纪律·条件式——冒烟已清理则目录空不提交）**

Run: `git add engine/cache/evidence/ && git status --short engine/cache/evidence/ | grep -q . && git commit -m "feat(v35-P1): P1 验收闸 GO——证据链仪表上线（fail-open·幂等·check/show 交付）" || echo "[v35] 证据链目录空·首次真实预测自然入库（凭证链纪律已立）"`

**GO 条件**（设计档 §六）：fail-open 单测 ✓ + 幂等重跑 ✓ + 冒烟 3 场九步可查 ✓ → 进 P3 前向积累期（每日预测自然落链）

---

## P2 · 数据地基账本（与 P1 并行，互不依赖）

### Task 8: data_ledger.py 五源扫描器

**Files:**
- Create: `engine/scripts/data_ledger.py`
- Test: `engine/scripts/tests/test_data_ledger.py`

**Interfaces:**
- Produces: `scan_sporttery(dir) -> dict`、`scan_pinclose(dir) -> dict`、`scan_hst() -> dict`、`scan_lineups(dir) -> dict`、`scan_aliases() -> dict`、`INCIDENTS`（静态事件史列表）、`build() -> dict`、main 写 `data/04-summaries/data-quality-ledger.json`（`--audit` 抽样复算 10 条）

- [ ] **Step 1: 写失败测试（纯函数·fixture 喂形状）**

```python
# engine/scripts/tests/test_data_ledger.py
# -*- coding: utf-8 -*-
"""v35 P2 数据账本测试。开发者 sszhang"""
import json

import data_ledger as dl


def test_scan_sporttery(tmp_path):
    (tmp_path / "2026-10-11.json").write_text(json.dumps(
        {"fetchedAt": "t", "matchDays": [{"businessDate": "2026-10-12",
          "matches": [{"matchNumStr": "周日001", "had": {"h": 2}, "oddsUpdatedAt": "t1"},
                      {"matchNumStr": "周日002", "had": {"h": 2}}]}]}, ensure_ascii=False), encoding="utf-8")
    r = dl.scan_sporttery(tmp_path)
    assert r["archives"] == 1 and r["matches"] == 2
    assert r["oddsUpdatedMatches"] == 1          # 调价率分子


def test_scan_pinclose(tmp_path):
    (tmp_path / "2026-10-11.json").write_text(json.dumps(
        {"matches": [{"pinClose": {"h": 2.0}}, {}, {"pinClose": {"h": 3.0}}]}), encoding="utf-8")
    r = dl.scan_pinclose(tmp_path)
    assert r["files"] == 1 and r["matches"] == 3 and r["withPinClose"] == 2


def test_scan_lineups(tmp_path):
    (tmp_path / "2026-10-13-lineups.json").write_text(json.dumps(
        {"matches": {"111": {"lineup": {"home": ["a"]}, "injuries": {"home": ["x"]}},
                     "222": {"lineup": None}}}, ensure_ascii=False), encoding="utf-8")
    r = dl.scan_lineups(tmp_path)
    assert r["files"] == 1 and r["matchEntries"] == 2
    assert r["lineupPublished"] == 1 and r["injuryEvents"] == 1


def test_scan_hst(tmp_path):
    (tmp_path / "hst_eng1.json").write_text(json.dumps(
        {"rows": [{"home": "A", "hst": 5, "date": "2026-10-01"},
                  {"home": "A", "hst": 4, "date": "2026-09-01"}]}), encoding="utf-8")
    r = dl.scan_hst(str(tmp_path))
    assert r["eng1"]["rows"] == 2 and r["eng1"]["lastDate"] == "2026-10-01"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest engine/scripts/tests/test_data_ledger.py -v`
Expected: FAIL（ModuleNotFoundError: data_ledger）

- [ ] **Step 3: 实现**

```python
# engine/scripts/data_ledger.py
# -*- coding: utf-8 -*-
"""v35 P2 数据地基账本——五源质量指标自动生成（设计 docs/2026-10-11-v35-foundation-design.html §三）。

产物 data/04-summaries/data-quality-ledger.json·随 run.py update 自动刷新（防过期档案）。
开发者 sszhang
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER_OUT = ROOT / "data" / "04-summaries" / "data-quality-ledger.json"

# 静态事件史（口径/断粮事故——账本须记伤疤不记功绩）
INCIDENTS = [
    {"date": "2026-10-11", "source": "fd", "event": "Psh/Psch 口径混价：2425/2526 静默用赛前价·已修复 g(PPCH,Psch) 重拉 51 文件 100% 对账"},
    {"date": "2026-09-07", "source": "shadow", "event": "scratch 目录清理误删影子账本——凭证禁放 scratch 纪律起点"},
]


def scan_sporttery(d: Path) -> dict:
    files = sorted(d.glob("*.json")) if d.exists() else []
    matches = updated = 0
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                  # noqa: BLE001
            continue
        for blk in doc.get("matchDays") or []:
            for m in blk.get("matches") or []:
                matches += 1
                if m.get("oddsUpdatedAt"):
                    updated += 1
    return {"archives": len(files), "dayMin": files[0].stem if files else None,
            "dayMax": files[-1].stem if files else None,
            "matches": matches, "oddsUpdatedMatches": updated,
            "调价率": round(updated / matches, 4) if matches else None}


def scan_pinclose(d: Path) -> dict:
    files = sorted(d.glob("*.json")) if d.exists() else []
    n = with_pc = 0
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                  # noqa: BLE001
            continue
        for m in doc.get("matches") or []:
            n += 1
            if m.get("pinClose"):
                with_pc += 1
    return {"files": len(files), "matches": n, "withPinClose": with_pc,
            "锚覆盖率": round(with_pc / n, 4) if n else None}


def scan_hst(glob_root: str = None) -> dict:
    """glob 兜底扫 hst_{lg}.json（不锁死目录）。"""
    root = Path(glob_root) if glob_root else ROOT / "engine" / "cache"
    out = {}
    for f in sorted(root.rglob("hst_*.json")):
        try:
            rows = json.loads(f.read_text(encoding="utf-8")).get("rows") or []
        except Exception:                  # noqa: BLE001
            continue
        dates = [r.get("date") or r.get("dt") or r.get("day") for r in rows
                 if isinstance(r, dict)]
        dates = [x for x in dates if x]
        out[f.stem.replace("hst_", "")] = {
            "rows": len(rows),
            "lastDate": max(dates) if dates else None,
            "fileMtime": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="days")}
    return out


def scan_lineups(d: Path) -> dict:
    files = sorted(d.glob("*-lineups.json")) if d.exists() else []
    entries = published = inj = 0
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                  # noqa: BLE001
            continue
        for m in (doc.get("matches") or {}).values():
            entries += 1
            if m.get("lineup"):
                published += 1
            i = m.get("injuries") or m.get("injury") or {}
            inj += len(i) if isinstance(i, (list, dict)) else 0
    return {"files": len(files), "matchEntries": entries,
            "lineupPublished": published, "injuryEvents": inj,
            "伤停轨判据进度": f"{entries}/300场·{inj}/30变更"}


def scan_aliases() -> dict:
    f = ROOT / "data" / "01-teams" / "_aliases.json"
    try:
        n = len(json.loads(f.read_text(encoding="utf-8")))
    except Exception:                      # noqa: BLE001
        n = None
    return {"entries": n}


def build() -> dict:
    return {"generatedAt": datetime.now().isoformat(timespec="seconds"),
            "sources": {
                "sporttery": scan_sporttery(ROOT / "engine" / "cache" / "score_odds"),
                "pinnacle锚": scan_pinclose(ROOT / "data" / "02-results"),
                "hst": scan_hst(),
                "lineup伤停轨": scan_lineups(ROOT / "data" / "05-trends"),
                "aliases": scan_aliases()},
            "incidents": INCIDENTS}


def audit(ledger: dict) -> str:
    """抽样对账：账本数字 vs 源文件直算（≥10 条）。"""
    checks = []
    sp = ledger["sources"]["sporttery"]
    live = scan_sporttery(ROOT / "engine" / "cache" / "score_odds")
    checks.append(("sporttery.archives", sp["archives"], live["archives"]))
    checks.append(("sporttery.matches", sp["matches"], live["matches"]))
    checks.append(("sporttery.调价率", sp["调价率"], live["调价率"]))
    pc = ledger["sources"]["pinnacle锚"]
    live_pc = scan_pinclose(ROOT / "data" / "02-results")
    checks += [("pinClose.withPinClose", pc["withPinClose"], live_pc["withPinClose"]),
               ("pinClose.锚覆盖率", pc["锚覆盖率"], live_pc["锚覆盖率"])]
    lu = ledger["sources"]["lineup伤停轨"]
    live_lu = scan_lineups(ROOT / "data" / "05-trends")
    checks += [("lineup.files", lu["files"], live_lu["files"]),
               ("lineup.matchEntries", lu["matchEntries"], live_lu["matchEntries"]),
               ("lineup.lineupPublished", lu["lineupPublished"], live_lu["lineupPublished"]),
               ("lineup.injuryEvents", lu["injuryEvents"], live_lu["injuryEvents"])]
    al = ledger["sources"]["aliases"]
    checks.append(("aliases.entries", al["entries"], scan_aliases()["entries"]))
    hst = ledger["sources"]["hst"]
    live_hst = scan_hst()
    for lg in list(hst)[:2]:
        checks.append((f"hst.{lg}.rows", hst[lg]["rows"], live_hst.get(lg, {}).get("rows")))
    bad = [(k, a, b) for k, a, b in checks if a != b]
    return f"PASS · {len(checks)} 条抽样一致" if not bad else f"FAIL——{bad[:3]}"


def main() -> None:
    if "--audit" in sys.argv:
        print(audit(json.loads(LEDGER_OUT.read_text(encoding="utf-8"))))
        return
    ledger = build()
    LEDGER_OUT.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_OUT.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[ledger] 五源账本 → {LEDGER_OUT}")
    print(json.dumps(ledger["sources"], ensure_ascii=False, indent=1)[:800])


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest engine/scripts/tests/test_data_ledger.py -v`
Expected: 4 passed

- [ ] **Step 5: 真跑首版账本**

Run: `python3 engine/scripts/data_ledger.py`
Expected: 打印五源摘要 + data-quality-ledger.json 落盘

- [ ] **Step 6: Commit**

```bash
git add engine/scripts/data_ledger.py engine/scripts/tests/test_data_ledger.py data/04-summaries/data-quality-ledger.json
git commit -m "feat(v35-P2): data_ledger 五源质量账本——体彩/pin锚/HST/伤停轨/别名+事件史"
```

---

### Task 9: 账本自动刷新接线 + 抽样对账验收

**Files:**
- Modify: `engine/scripts/run.py`（Task 5 的 s1 行后）

- [ ] **Step 1: run.py update 末尾追加**

```python
        sh("data_ledger.py", "")               # v35-P2: 数据账本随 update 自动刷新
```

- [ ] **Step 2: 语法验证**

Run: `python3 -c "import ast; ast.parse(open('engine/scripts/run.py').read())"`
Expected: 无输出

- [ ] **Step 3: 抽样对账（P2 验收=10 条）**

Run: `python3 engine/scripts/data_ledger.py --audit`
Expected: `PASS · ≥10 条抽样一致`

- [ ] **Step 4: Commit**

```bash
git add engine/scripts/run.py
git commit -m "feat(v35-P2): 数据账本随 run.py update 自动刷新 + 抽样对账 PASS"
```

---

### Task 10: P1+P2 收口 commit + GO/NO-GO 报告

- [ ] **Step 1: 全量测试回归**

Run: `python3 -m pytest engine/scripts/tests/test_evidence.py engine/scripts/tests/test_data_ledger.py -v`
Expected: 12 passed

- [ ] **Step 2: 收口 commit（若有零散未提交件）**

```bash
git add -A engine/scripts/ engine/cache/evidence/ data/04-summaries/
git commit -m "feat(v35-P1P2): 地基工程前两阶段交付——证据链仪表+数据账本·GO 报告待大哥过目"
```

- [ ] **Step 3: 向大哥报 GO/NO-GO**（控制台直出：fail-open 测试结果/幂等/账本五源摘要/抽样对账/下一步=P3 立项）

---

## Self-Review 记录（写完已查）

1. **Spec 覆盖**：设计档 P1 九步——S1=Task5、S2/S4/S5/S7=Task4、S3/S5/S6=Task3、S8/S9=Task6 会话登记、check/show=Task2、fail-open/幂等/原子写=Task1；P2 五源+自动刷新+抽样对账=Task8/9；验收闸=Task7/10。✓ 无遗漏
2. **占位符**：无 TBD/「适当处理」类步骤；全部代码块完整可粘贴
3. **类型一致**：record(day, key, step, payload, source) 全任务同签名；STEP_* 常量唯一来源 evidence.py；data_ledger 函数名与测试/audit 调用一致 ✓
4. **风险交底**：strength_chain_prod hook 用 pred.get("league")（内部联赛代码）索引 ctx["hst"]——若键不齐 hstRows=0 如实落账（不炸）；hst 行日期键三候选兜底；run.py sh() 调用风格须与既有 sh 一致（执行时看一眼上下文再插）
