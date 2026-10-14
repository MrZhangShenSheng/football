# LLM 语境预测层（llm-predict）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建成 LLM 预测层四件套——数据卡生成器（自动层+语境层防泄漏过滤）、预测冻结工具（31 格校验/归一/hash 链）、对拍结算链（全分布主判据+右尾子域专项通道）、影子 spec 接入——每次"帮我预测"自动积累 forward 样本。

**Architecture:** 会话内 LLM 预测（非 API）：llm_card.py 装配数据卡（市场/情报/画像/首发自动层 + 会话联网检索语境层）→ 会话 LLM 按 SKILL.md 协议出 31 格分布 → llm_pred.py 冻结落盘（输入 hash 链）→ 回填时 llm_eval.py 对拍市场封盘 devig（复用 tail_train/market_lambda 数学）→ 影子层 family L forward。判据双分层：全分布主判据 + 右尾子域专项通道（n≥150 另立门）。

**Tech Stack:** Python3 标准库 + numpy + pytest。复用：`market_lambda.crs_power_devig`、`strength_chain_eval._boot_ci_diff`、`tail_dataset.TAIL_LEAGUE_MAP/zh 映射`、`sporttery_fetch.cmd_insight`（subprocess）、`tail_train.event_outcomes/_logloss`。

**Spec:** `docs/2026-10-13-llm-predict-design.html`（含 review 六修正：防赛后泄漏三道过滤/对拍键集口径/双判据分层/未检出续判/预测先于回填/检索限候选场——执行者必读 §2 §4）

## Global Constraints

- **判据跑前冻结**：`engine/cache/llm-predict-prereg.json` 先于一切代码 commit（Task 1）；跑后禁改口径（铁律 14②）；`llm_eval.py` 启动 `check_prereg()` 校验
- **forward only 红线**：LLM 见过历史赛果，一切样本只来自预注册后真实 forward；数据卡+预测均冻结落盘（时间戳+hash）防事后改；**预测步骤必须先于当日任何回填/赛果查询**（会话上下文污染）
- **防赛后泄漏三道过滤**（语境层）：检索词限定 preview/injury/team news 类；内容命中赛后特征词（FT/full-time/final score/终场/完场）整条丢弃；条目发布时间晚于数据卡 createdAt 不收
- **对拍键集口径**：以市场（开赛前最后可得快照）**有价键**为对拍键集，LLM 分布在该键集条件归一；log-loss clip=1e-6
- **双判据分层**：主判据=31 格全分布配对 log-loss 差（market−llm）CI 下限>0（n≥100 正式判）；右尾子域专项=E_tail 聚合事件（右尾键集=crs 有价键中赔率≥30 的格 ∪ {0:3,0:4}）二项 log-loss CI 下限>0 且 **n≥150** → "右尾选腿器"影子专项准入通道（另立 prereg）；n≥100 CI 跨 0 且点估计>0 → verdict=UNDETECTED 续判至 n=200
- 变量命名 camelCase、常量 UPPER_SNAKE_CASE、注释署名 sszhang；内部文件 JSON、报告 HTML 带 charset utf-8（铁律 6.5/9）
- 数据卡/预测文件纳 git（凭证纪律）；预测文件**幂等只读**：已存在直接返回，禁止任何覆写（与 qcache 的 --force 重写纪律不同——预测是凭证）

## 数据源速查（执行者必读）

| 源 | 路径/接口 | 结构 |
|:---|:---|:---|
| 体彩日存档 | `engine/cache/score_odds/{销售日}.json` | `{fetchedAt, source, matchDays:[{businessDate, matches:[{matchNumStr, league, home, away, matchTime, sellStatus, had, crs, ttg, hhad, hafu, oddsUpdatedAt}]}]}`——**市场层主源**（crs/ttg/had 键格式与 hist_odds 同：crs="s01s00" 或归一键？实测以文件为准，执行者先读一个真实文件确认键格式再写键转换） |
| 单场情报 | `python3 engine/scripts/sporttery_fetch.py insight <matchId>`（subprocess） | 落盘 `engine/cache/sporttery_insight_{matchId}.json`（伤停 slim/近10/排名/H2H/射手）；matchId 来源：体彩清单接口（grep sporttery_fetch.py 内的清单/matchId 拉取函数复用）；拿不到→intel 层标"未获取" |
| 联赛/球队画像 | `data/00-leagues/{lg}.json` + `data/01-teams/` | league 中文名→库 key 用 `tail_dataset.TAIL_LEAGUE_MAP`；队名 zh_to_id |
| 首发时序 | `data/05-trends/{开赛日}-lineups.json` | matchId 为键（结构先读 engine/scripts/lineup_watch.py 文件头注释确认）；对拍市场对手盘=该场 odds 快照链**最后快照**（最接近封盘），缺快照→用数据卡市场层价+`closeProxy: "cardTime"` 标记分层报 |
| 体彩赛果 | 回填流程已有（`data/02-results/{开赛日}.json`） | eval 消费 actual score |

**复用函数**：`market_lambda.crs_power_devig(pool) -> dict`（sXXsYY 键）、`strength_chain_eval._boot_ci_diff(diffs, seed=, n_boot=) -> list[float]`、`tail_train.event_outcomes(score) -> tuple`、`tail_dataset.zh 映射（sl.zh_to_id(aliases)）`。

---

### Task 1: 预注册判据文件冻结

**Files:**
- Create: `engine/cache/llm-predict-prereg.json`

**Interfaces:**
- Produces: prereg JSON——`llm_eval.py` 的 `check_prereg()` 校验源（Task 5 消费）

- [ ] **Step 1: 写 prereg JSON（逐字落盘）**

```json
{
  "registeredAt": "2026-10-13",
  "spec": "LLM 语境预测层 v1（docs/2026-10-13-llm-predict-design.html·含 review 六修正）",
  "predictor": "会话内 LLM·31 格比分分布·forward only",
  "card": {
    "layers": ["market", "intel", "profile", "lineup", "context"],
    "contextSearchScope": "快筛后候选场 6~10 场",
    "contextFilters": ["检索词限 preview/injury/team news", "赛后特征词整条丢弃", "发布时间晚于卡 createdAt 不收"]
  },
  "primaryCriterion": {
    "name": "E_full_31grid",
    "metric": "逐场配对 log-loss 差（market − llm，正=LLM 更准）",
    "marketSide": "开赛前最后可得快照 crs power devig（首选 lineup_watch 快照链末帧；缺则数据卡市场层+closeProxy 标记分层报）",
    "keyset": "市场有价键集·LLM 条件归一",
    "clip": 1e-06,
    "ci": "bootstrap95 B=2000",
    "pass": "CI 下限 > 0",
    "nGate": {"observe": 30, "formal": 100, "final": 200}
  },
  "tailChannel": {
    "name": "E_tail 右尾选腿器专项通道",
    "tailKeys": "crs 有价键中封盘赔率 ≥30 的格 ∪ {0:3, 0:4}",
    "metric": "E_tail 聚合二项 log-loss 配对差（每场计分·P(右尾键集求和)）",
    "pass": "CI 下限 > 0 且 n ≥ 150",
    "note": "主判据 FAIL 不封锁此通道；通过后另立专项 prereg 走影子准入"
  },
  "verdictRules": {
    "UNDETECTED": "n≥100 且 CI 跨 0 且 diffMean>0 → 续判至 n=200",
    "FALSIFIED": "CI 跨 0 且 diffMean≤0（n≥100）",
    "INSUFFICIENT": "n<100"
  },
  "descriptiveOnly": ["E1/E2/E3 事件族（同 tail-model 口径）", "分联赛/分月", "closeProxy 分层"],
  "redlines": [
    "forward only：历史回测=训练集考试=作弊，禁止",
    "预测步骤先于当日回填/赛果查询（会话污染）",
    "预测文件幂等只读禁覆写；数据卡语境层回写后重算 cardHash",
    "主判据 FAIL → LLM 不进任何整体预测叙事；右尾通道独立判定"
  ],
  "frozenParams": {"boot_B": 2000, "seed": 20261015, "logloss_clip": 1e-06, "min_pool_items": 20, "tail_odds_floor": 30.0, "tail_fixed_cells": ["0:3", "0:4"]}
}
```

- [ ] **Step 2: Commit（判据凭证，先于一切代码）**

```bash
git add engine/cache/llm-predict-prereg.json
git commit -m "prereg(llm预测): 判据冻结——31格全分布主判据+右尾子域专项通道(n≥150)+forward only红线+防赛后泄漏三道过滤"
```

---

### Task 2: 数据卡生成器——自动层装配 + 冻结

**Files:**
- Create: `engine/scripts/llm_card.py`
- Test: `engine/tests/test_llm_card.py`

**Interfaces:**
- Consumes: score_odds 存档、`sporttery_fetch.py insight`（subprocess）、`tail_dataset.TAIL_LEAGUE_MAP`、`data/05-trends/*-lineups.json`
- Produces:
  - `build_card(sale_date: str, match: dict, *, insight_path: Path | None, lineups: dict) -> dict`（卡结构 `{code, match, league, kickoff, layers:{market, intel, profile, lineup, context}, cardHash, createdAt}`，context 初值 `{"status": "pending", "entries": []}`）
  - `card_paths(sale_date: str) -> Path`（目录 `engine/cache/llm_cards/{销售日}/`）
  - `save_card(card: dict, sale_date: str) -> Path`（原子写；已存在→返回路径不覆写）
  - `card_hash(card: dict) -> str`（sha256 前 16 位，规范化 sort_keys）
  - CLI `python llm_card.py gen <销售日>`：扫 score_odds 存档当日场（sellStatus 在售）→ intel subprocess 拉取 → 存卡 → stdout 打印待检索清单（每场 1 条建议 query：`"{主队英文名} vs {客队英文名} injury news preview"`）

- [ ] **Step 1: 写失败测试（迷你 score_odds fixture）**

```python
# engine/tests/test_llm_card.py
import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from llm_card import build_card, save_card, card_hash, card_paths


def _mini_archive(tmp_path):
    m = {"matchNumStr": "周二001", "league": "英超", "home": "阿森纳", "away": "切尔西",
         "matchTime": "2026-10-14 03:45", "sellStatus": "ON_SALE",
         "had": {"h": 1.5, "d": 4.0, "a": 5.0},
         "crs": {"s01s00": 8.0, "s01s01": 7.0}, "ttg": {"s0": 3.0}, "hafu": {}, "hhad": {}}
    doc = {"fetchedAt": "2026-10-13T10:00:00", "source": "sporttery",
           "matchDays": [{"businessDate": "2026-10-13", "matches": [m]}]}
    p = tmp_path / "2026-10-13.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


def test_build_card_layers_and_hash(tmp_path):
    card = build_card("2026-10-13",
                      {"matchNumStr": "周二001", "league": "英超", "home": "阿森纳",
                       "away": "切尔西", "matchTime": "2026-10-14 03:45",
                       "had": {"h": 1.5, "d": 4.0, "a": 5.0},
                       "crs": {"s01s00": 8.0}, "ttg": {"s0": 3.0}},
                      insight_path=None, lineups={})
    assert card["code"] == "周二001"
    assert set(card["layers"]) == {"market", "intel", "profile", "lineup", "context"}
    assert card["layers"]["market"]["crs"] == {"s01s00": 8.0}
    assert card["layers"]["intel"]["status"] == "未获取"          # insight_path=None 诚实降级
    assert card["layers"]["context"] == {"status": "pending", "entries": []}
    assert len(card_hash(card)) == 16
    # 同卡同 hash（createdAt 固定注入时）
    c2 = dict(card); c2["layers"] = dict(card["layers"])
    assert card_hash(c2) == card_hash(card)


def test_save_card_idempotent_no_overwrite(tmp_path):
    card = build_card("2026-10-13",
                      {"matchNumStr": "周二001", "league": "英超", "home": "A", "away": "B",
                       "matchTime": "t", "had": {}, "crs": {}, "ttg": {}},
                      insight_path=None, lineups={})
    p1 = save_card(card, "2026-10-13", root=tmp_path)
    # 篡改后再存 → 不覆写，返回原路径
    card["layers"]["market"]["crs"] = {"HACKED": 1.0}
    p2 = save_card(card, "2026-10-13", root=tmp_path)
    assert p1 == p2
    assert "HACKED" not in json.loads(p1.read_text(encoding="utf-8"))["layers"]["market"]["crs"].__str__()
```

- [ ] **Step 2: 跑确认失败**

Run: `python3 -m pytest engine/tests/test_llm_card.py -v` → FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现（文件头+常量+四函数+CLI）**

```python
# engine/scripts/llm_card.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llm_card.py — LLM 预测数据卡生成器（spec docs/2026-10-13-llm-predict-design.html）。

五层：market(体彩存档)/intel(insight CLI)/profile(本地画像)/lineup(首发时序)/context(联网检索·会话回写)。
防泄漏三道过滤在 write_context（Task 3）；卡幂等只读（凭证纪律·预测链地基）。
判据预注册 engine/cache/llm-predict-prereg.json。开发者 sszhang"""
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tail_dataset import TAIL_LEAGUE_MAP          # league 中文名→库 key 复用

CARDS_ROOT = ROOT / "engine" / "cache" / "llm_cards"
ARCHIVE_ROOT = ROOT / "engine" / "cache" / "score_odds"
INSIGHT_TIMEOUT = 30


def card_paths(sale_date: str, *, root: Path | None = None) -> Path:
    return (root or CARDS_ROOT) / sale_date


def card_hash(card: dict) -> str:
    """规范化 sha256 前 16 位（不含 cardHash 自身字段）。"""
    c = {k: v for k, v in card.items() if k != "cardHash"}
    blob = json.dumps(c, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _profile_layer(league_zh: str) -> dict:
    lg = TAIL_LEAGUE_MAP.get(league_zh)
    if not lg:
        return {"status": "未映射", "league": league_zh}
    f = ROOT / "data" / "00-leagues" / f"{lg}.json"
    if not f.exists():
        return {"status": "未获取", "league": league_zh}
    doc = json.loads(f.read_text(encoding="utf-8"))
    keep = {k: doc.get(k) for k in ("leagueCn", "avgGoals", "homeWinRate", "upsetRate", "topScores")
            if doc.get(k) is not None}
    return {"status": "ok", "league": lg, **keep}


def _lineup_layer(code: str, lineups: dict) -> dict:
    for mid, e in (lineups or {}).items():
        if not isinstance(e, dict):
            continue
        if e.get("matchCode") == code or e.get("code") == code:
            return {"status": "ok", "lineupPublished": e.get("lineupPublished"),
                    "homeStarters": e.get("homeStarters"), "awayStarters": e.get("awayStarters"),
                    "matchId": mid}
    return {"status": "未获取"}


def build_card(sale_date: str, match: dict, *, insight_path: Path | None,
               lineups: dict, now: str | None = None) -> dict:
    """match=score_odds 存档行 → 五层数据卡。开发者 sszhang"""
    intel = {"status": "未获取"}
    if insight_path and Path(insight_path).exists():
        doc = json.loads(Path(insight_path).read_text(encoding="utf-8"))
        intel = {"status": "ok", **{k: doc.get(k) for k in ("injury", "recent", "standing", "h2h", "scorers")
                                    if doc.get(k) is not None}}
    card = {
        "code": match["matchNumStr"], "match": f'{match["home"]} vs {match["away"]}',
        "league": match["league"], "kickoff": match.get("matchTime"), "saleDate": sale_date,
        "layers": {
            "market": {"had": match.get("had") or {}, "crs": match.get("crs") or {},
                       "ttg": match.get("ttg") or {}, "oddsUpdatedAt": match.get("oddsUpdatedAt")},
            "intel": intel,
            "profile": _profile_layer(match["league"]),
            "lineup": _lineup_layer(match["matchNumStr"], lineups),
            "context": {"status": "pending", "entries": []},
        },
        "createdAt": now or datetime.now().isoformat(timespec="seconds"),
    }
    card["cardHash"] = card_hash(card)
    return card


def save_card(card: dict, sale_date: str, *, root: Path | None = None) -> Path:
    """幂等只读：已存在直接返回（凭证纪律·预测链防事后改）。"""
    d = card_paths(sale_date, root=root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f'{card["code"]}.json'
    if p.exists():
        return p
    p.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def gen(sale_date: str) -> int:
    """CLI 主流程：当日存档→逐场 intel→存卡→待检索清单。"""
    arc = ARCHIVE_ROOT / f"{sale_date}.json"
    if not arc.exists():
        raise SystemExit(f"[llm_card] 缺当日存档 {arc}（先跑 run.py update）")
    doc = json.loads(arc.read_text(encoding="utf-8"))
    lineups_f = ROOT / "data" / "05-trends" / f"{sale_date}-lineups.json"
    lineups = json.loads(lineups_f.read_text(encoding="utf-8")) if lineups_f.exists() else {}
    for day in doc.get("matchDays") or []:
        for m in day.get("matches") or []:
            if m.get("sellStatus") not in (None, "ON_SALE", "AUTO"):
                continue
            mid = m.get("matchId") or m.get("uniformMatchId")
            insight_path = None
            if mid:
                ip = ROOT / "engine" / "cache" / f"sporttery_insight_{mid}.json"
                if not ip.exists():
                    try:
                        subprocess.run([sys.executable, str(ROOT / "engine" / "scripts" / "sporttery_fetch.py"),
                                        "insight", str(mid)], timeout=INSIGHT_TIMEOUT, check=False)
                    except subprocess.TimeoutExpired:
                        pass
                insight_path = ip if ip.exists() else None
            card = build_card(sale_date, m, insight_path=insight_path, lineups=lineups)
            save_card(card, sale_date)
            print(f'[card] {card["code"]} {card["match"]} | 建议检索: "{card["match"]} injury news preview"')
    return 0


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="LLM 预测数据卡生成器")
    ap.add_argument("cmd", choices=["gen"])
    ap.add_argument("sale_date")
    args = ap.parse_args()
    return gen(args.sale_date)


if __name__ == "__main__":
    raise SystemExit(main())
```

（注：`build_card` 测试里 hash 稳定性依赖 createdAt 注入——测试用默认 now 时两次调用 createdAt 不同属正常，故测试断言的是"同 card 对象的 dict 拷贝 hash 相同"；lineup 层键名 `matchCode/homeStarters` 等以 lineup_watch.py 实际落盘为准，**执行者先读 lineup_watch.py 落盘函数**对齐字段名，测试 fixture 同步——这是有意的对齐点不是占位符。）

- [ ] **Step 4: 跑测试通过** → 2 PASS
- [ ] **Step 5: Commit**

```bash
git add engine/scripts/llm_card.py engine/tests/test_llm_card.py
git commit -m "feat(llm预测): 数据卡生成器五层装配——体彩存档市场层+insight情报+画像+首发·幂等只读凭证"
```

---

### Task 3: 语境层回写 + 防赛后泄漏三道过滤

**Files:**
- Modify: `engine/scripts/llm_card.py`（追加）
- Test: `engine/tests/test_llm_card.py`（追加）

**Interfaces:**
- Produces: `write_context(sale_date: str, code: str, entries: list[dict]) -> dict`——entries 每条 `{title, url, publishedAt, excerpt}`；返回 `{"accepted": n, "dropped": [{"reason": "...", "title": ...}]}`；写入后卡 `context.status="ok"`、重算 `cardHash`（**仅当该卡 context 仍为 pending 时允许写**——status=ok 的卡是终态禁止二次改写）；常量 `POST_MATCH_PATTERNS`、`QUERY_WORDS`

- [ ] **Step 1: 写失败测试**

```python
# 追加
from llm_card import write_context
import pytest


def _mk_card(tmp_path):
    card = build_card("2026-10-13",
                      {"matchNumStr": "周二001", "league": "英超", "home": "A", "away": "B",
                       "matchTime": "t", "had": {}, "crs": {}, "ttg": {}},
                      insight_path=None, lineups={}, now="2026-10-13T18:00:00")
    save_card(card, "2026-10-13", root=tmp_path)
    return card


def test_context_filters_post_match(tmp_path):
    _mk_card(tmp_path)
    entries = [
        {"title": "Team news: key midfielder doubts", "url": "u1",
         "publishedAt": "2026-10-13T12:00:00", "excerpt": "preview of rotation plans"},   # 收
        {"title": "FT: Arsenal 3-0 Chelsea", "url": "u2",
         "publishedAt": "2026-10-13T12:00:00", "excerpt": "full-time report"},           # 赛后词→丢
        {"title": "Preview piece", "url": "u3",
         "publishedAt": "2026-10-13T19:00:00", "excerpt": "fine"},                        # 晚于 createdAt→丢
    ]
    r = write_context("2026-10-13", "周二001", entries, root=tmp_path)
    assert r["accepted"] == 1 and len(r["dropped"]) == 2
    card = json.loads((tmp_path / "2026-10-13" / "周二001.json").read_text(encoding="utf-8"))
    assert card["layers"]["context"]["status"] == "ok"
    assert card["layers"]["context"]["entries"][0]["url"] == "u1"


def test_context_frozen_after_written(tmp_path):
    _mk_card(tmp_path)
    write_context("2026-10-13", "周二001", [{"title": "t", "url": "u",
                                             "publishedAt": "2026-10-13T10:00:00", "excerpt": "x"}],
                  root=tmp_path)
    with pytest.raises(RuntimeError):
        write_context("2026-10-13", "周二001", [{"title": "t2", "url": "u2",
                                                 "publishedAt": "2026-10-13T10:00:00", "excerpt": "x"}],
                      root=tmp_path)   # 终态禁二次改写
```

- [ ] **Step 2: 跑确认失败** → ImportError write_context
- [ ] **Step 3: 实现（追加）**

```python
# ── 语境层回写 + 防赛后泄漏三道过滤（spec review R1）───────────────────────
import re

POST_MATCH_PATTERNS = [
    r"\bFT\b", r"full[\s-]?time", r"final\s+score", r"match\s+report",
    r"终场", r"完场", r"全场结束", r"\d+\s*[-:]\s*\d+\s*(完|结束)",
]
QUERY_WORDS = ("injury news", "team news", "preview", "predicted lineup")   # 检索建议词（gen 已用）


def _is_post_match(entry: dict) -> bool:
    text = f'{entry.get("title") or ""} {entry.get("excerpt") or ""}'
    return any(re.search(p, text, re.IGNORECASE) for p in POST_MATCH_PATTERNS)


def write_context(sale_date: str, code: str, entries: list[dict], *,
                  root: Path | None = None) -> dict:
    """三道过滤：赛后词丢弃 / 发布时间晚于卡 createdAt 丢弃 / 缺 url 或 publishedAt 丢弃。
    写入后 context=终态（status=ok）禁止二次改写，cardHash 重算。开发者 sszhang"""
    p = card_paths(sale_date, root=root) / f"{code}.json"
    card = json.loads(p.read_text(encoding="utf-8"))
    ctx = card["layers"]["context"]
    if ctx.get("status") == "ok":
        raise RuntimeError(f"[write_context] {code} 语境层已终态，禁止改写（凭证纪律）")
    created = card.get("createdAt") or ""
    accepted, dropped = [], []
    for e in entries:
        if not (e.get("url") and e.get("publishedAt")):
            dropped.append({"reason": "缺url/publishedAt", "title": e.get("title")}); continue
        if str(e["publishedAt"]) > created:
            dropped.append({"reason": "发布晚于卡createdAt", "title": e.get("title")}); continue
        if _is_post_match(e):
            dropped.append({"reason": "赛后特征词", "title": e.get("title")}); continue
        accepted.append({"title": e.get("title"), "url": e["url"],
                         "publishedAt": e["publishedAt"],
                         "fetchedAt": datetime.now().isoformat(timespec="seconds"),
                         "excerpt": (e.get("excerpt") or "")[:600]})
    ctx["status"] = "ok" if accepted else "未获取"
    ctx["entries"] = accepted
    card["cardHash"] = card_hash(card)
    p.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"accepted": len(accepted), "dropped": dropped}
```

- [ ] **Step 4: 跑测试通过** → 4 PASS
- [ ] **Step 5: Commit** `feat(llm预测): 语境层回写+防赛后泄漏三道过滤——终态禁改写·hash重算`

---

### Task 4: 预测冻结工具 llm_pred.py（31 格校验/归一/hash 链）

**Files:**
- Create: `engine/scripts/llm_pred.py`
- Test: `engine/tests/test_llm_pred.py`

**Interfaces:**
- Consumes: 数据卡（`engine/cache/llm_cards/{销售日}/{code}.json`）
- Produces:
  - `validate_pred(pred: dict, market_keys: list[str]) -> tuple[bool, list[str]]`——每场 `{code, p_crs: dict, keyJudgments: [{judgment, evidence}], confidence}`；校验：code/p_crs 非空、键均∈market_keys、值 0≤p≤1、keyJudgments≥1 条且每条有 judgment+evidence、confidence∈{low,medium,high}
  - `normalize_to_keys(p_crs: dict, keys: list[str]) -> tuple[dict, float]`——条件归一（返回归一分布+原始和，原始和∉[0.5,2.0] 标质量警示由调用方记）
  - `freeze(sale_date: str, preds: list[dict]) -> Path`——写 `engine/cache/llm_preds/{销售日}.json`：`{frozenAt, preds: [{code, cardHash, p_crs, pRawSum, keyJudgments, confidence, issues}]}`；**幂等只读**：文件在→直接返回不覆写；校验失败任何一场→整批拒绝（SystemExit 2 列出 issues）

- [ ] **Step 1: 写失败测试**

```python
# engine/tests/test_llm_pred.py
import sys, json, pathlib, pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from llm_pred import validate_pred, normalize_to_keys, freeze


def test_validate_and_issues():
    keys = ["1:1", "1:0", "0:1"]
    ok = {"code": "周二001", "p_crs": {"1:1": 0.5, "1:0": 0.3, "0:1": 0.2},
          "keyJudgments": [{"judgment": "j", "evidence": "语境层#1"}], "confidence": "medium"}
    good, issues = validate_pred(ok, keys)
    assert good and not issues
    bad = dict(ok, p_crs={"1:1": 0.9, "9:9": 0.1})                    # 9:9 不在键集
    good2, issues2 = validate_pred(bad, keys)
    assert not good2 and any("9:9" in i for i in issues2)
    bad2 = dict(ok, keyJudgments=[])                                   # 无判断
    good3, issues3 = validate_pred(bad2, keys)
    assert not good3


def test_normalize_to_keys():
    p, raw_sum = normalize_to_keys({"1:1": 0.5, "1:0": 0.25, "0:1": 0.25, "2:2": 0.5},
                                   ["1:1", "1:0", "0:1"])
    assert abs(raw_sum - 1.5) < 1e-9
    assert abs(sum(p.values()) - 1.0) < 1e-9 and "2:2" not in p


def test_freeze_idempotent_and_strict(tmp_path, monkeypatch):
    monkeypatch.setattr("llm_pred.PREDS_ROOT", tmp_path)
    # 造卡（freeze 校验 cardHash 链）
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    from llm_card import build_card, save_card
    card = build_card("2026-10-13",
                      {"matchNumStr": "周二001", "league": "英超", "home": "A", "away": "B",
                       "matchTime": "t", "had": {}, "crs": {"s01s01": 7.0}, "ttg": {}},
                      insight_path=None, lineups={}, now="2026-10-13T18:00:00")
    save_card(card, "2026-10-13", root=tmp_path / "cards")
    monkeypatch.setattr("llm_pred.CARDS_ROOT", tmp_path / "cards")
    pred = {"code": "周二001", "p_crs": {"1:1": 0.5, "1:0": 0.3, "0:1": 0.2},
            "keyJudgments": [{"judgment": "j", "evidence": "e"}], "confidence": "medium"}
    p1 = freeze("2026-10-13", [pred])
    assert p1.exists()
    p2 = freeze("2026-10-13", [dict(pred, confidence="high")])     # 幂等不覆写
    doc = json.loads(p1.read_text(encoding="utf-8"))
    assert doc["preds"][0]["confidence"] == "medium"
    assert len(doc["preds"][0]["cardHash"]) == 16
```

- [ ] **Step 2: 跑确认失败** → ModuleNotFoundError
- [ ] **Step 3: 实现**

```python
# engine/scripts/llm_pred.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llm_pred.py — 会话内 LLM 预测的校验/归一/冻结工具（spec §3）。

31 格（市场有价键）分布；校验失败整批拒绝；冻结后幂等只读（凭证·forward only 地基）。
开发者 sszhang"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREDS_ROOT = ROOT / "engine" / "cache" / "llm_preds"
CARDS_ROOT = ROOT / "engine" / "cache" / "llm_cards"
CONF_LEVELS = ("low", "medium", "high")
RAW_SUM_WARN = (0.5, 2.0)


def validate_pred(pred: dict, market_keys: list[str]) -> tuple[bool, list[str]]:
    """结构校验：键∈市场键集 / 0≤p≤1 / 判断≥1 条带 evidence / confidence 枚举。"""
    issues = []
    if not pred.get("code"):
        issues.append("缺 code")
    p = pred.get("p_crs") or {}
    if not p:
        issues.append(f'{pred.get("code")}: p_crs 空')
    for k, v in (p or {}).items():
        if k not in market_keys:
            issues.append(f'{pred.get("code")}: 键 {k} 不在市场键集')
        if not (isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0):
            issues.append(f'{pred.get("code")}: {k} 值非法 {v}')
    kj = pred.get("keyJudgments") or []
    if not kj or any(not (x.get("judgment") and x.get("evidence")) for x in kj):
        issues.append(f'{pred.get("code")}: keyJudgments 缺失或不完整')
    if pred.get("confidence") not in CONF_LEVELS:
        issues.append(f'{pred.get("code")}: confidence 非法')
    return (not issues), issues


def normalize_to_keys(p_crs: dict, keys: list[str]) -> tuple[dict, float]:
    """条件归一到市场键集（spec·对拍键集口径），返回 (分布, 原始和)。"""
    raw_sum = sum(float(v) for v in p_crs.values())
    sub = {k: float(p_crs.get(k, 0.0)) for k in keys}
    z = sum(sub.values())
    if z <= 0:
        n = len(keys)
        return {k: 1.0 / n for k in keys}, raw_sum          # 全零兜底均匀（记录 issues 由调用方）
    return {k: v / z for k, v in sub.items()}, raw_sum


def freeze(sale_date: str, preds: list[dict]) -> Path:
    """逐场校验（键集=该场数据卡市场层有价键）→归一→写冻结文件。幂等只读。"""
    out = PREDS_ROOT / f"{sale_date}.json"
    if out.exists():
        return out
    frozen = {"frozenAt": datetime.now().isoformat(timespec="seconds"), "preds": []}
    for pred in preds:
        card_p = CARDS_ROOT / sale_date / f'{pred.get("code")}.json'
        if not card_p.exists():
            raise SystemExit(f'[llm_pred] 缺数据卡 {card_p}——先 gen 数据卡')
        card = json.loads(card_p.read_text(encoding="utf-8"))
        ctx = card["layers"]["context"]
        market = card["layers"]["market"]["crs"] or {}
        # 键转换：体彩存档 sXXsYY → pick 键（与 crs_key 反向）；已是 pick 键直通
        mk = {}
        for k, v in market.items():
            kk = k if (":" in k) else (
                f"{int(k[1:3])}:{int(k[4:6])}" if k.startswith("s") and len(k) == 6 and k[3] == "s"
                else ("other_h" if k == "s1sh" else "other_d" if k == "s1sd" else "other_a" if k == "s1sa" else None))
            if kk and isinstance(v, (int, float)) and float(v) > 1.0:
                mk[kk] = float(v)
        keys = list(mk)
        good, issues = validate_pred(pred, keys)
        if not good:
            raise SystemExit(f'[llm_pred] 校验失败拒冻结: {issues}')
        norm, raw_sum = normalize_to_keys(pred["p_crs"], keys)
        frozen["preds"].append({
            "code": pred["code"], "cardHash": card["cardHash"], "p_crs": norm,
            "pRawSum": round(raw_sum, 4),
            "rawSumWarn": not (RAW_SUM_WARN[0] <= raw_sum <= RAW_SUM_WARN[1]),
            "contextStatus": ctx.get("status"),
            "keyJudgments": pred["keyJudgments"], "confidence": pred["confidence"]})
    PREDS_ROOT.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(frozen, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
```

（注意体彩存档 crs 键可能是 `s01s01` 体彩原始键——Task 2 fixture 用了 `s01s00`；freeze 内置 sXXsYY→pick 转换双向兼容，**执行者以真实存档文件键格式为准**自测一条真实卡。）

- [ ] **Step 4: 跑测试通过** → 3 PASS
- [ ] **Step 5: Commit** `feat(llm预测): 预测冻结工具——31格校验/条件归一/cardHash链·整批严格·幂等只读`

---

### Task 5: 对拍结算链 llm_eval.py（主判据+右尾通道+descriptive）

**Files:**
- Create: `engine/scripts/llm_eval.py`
- Test: `engine/tests/test_llm_eval.py`

**Interfaces:**
- Consumes: `engine/cache/llm_preds/{销售日}.json`、数据卡（市场层键集）、lineup_watch 快照链（市场对手盘末帧）、`market_lambda.crs_power_devig`、`strength_chain_eval._boot_ci_diff`、prereg
- Produces:
  - `market_devig_on_keys(pool: dict, keys: list[str]) -> dict`——power devig 后取键交集（不足 20 项→{}）
  - `close_pool(kickoff_date: str, code: str, card: dict) -> tuple[dict, str]`——对手盘池：lineup_watch 该场 odds 快照链末帧 crs（键转 pick）；缺→数据卡市场层，proxy 标 `"closeProxy": "cardTime"`
  - `actual_key(score: str) -> str | None`——"h:a"→pick 键（>5 网格落 other_*）
  - `evaluate(days: list[str]) -> dict`——逐场配对差全分布 + E_tail 通道 + descriptive E1/E2/E3（复用 tail_train.event_outcomes）+ verdict 规则（PASS/FAIL/UNDETECTED/INSUFFICIENT）+ closeProxy 分层
  - `check_prereg() -> dict`（frozenParams：boot_B/seed/logloss_clip/min_pool_items/tail_odds_floor/tail_fixed_cells 对模块常量）
  - CLI `python llm_eval.py run [销售日...]`→`data/04-summaries/llm-predict/eval.json`
- 常量：`BOOT_B=2000, SEED=20261015, LOGLLOSS_CLIP=1e-6, MIN_POOL_ITEMS=20, TAIL_ODDS_FLOOR=30.0, TAIL_FIXED_CELLS=("0:3","0:4"), N_GATE={"observe":30,"formal":100,"final":200}`

- [ ] **Step 1: 写失败测试**

```python
# engine/tests/test_llm_eval.py
import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from llm_eval import (market_devig_on_keys, actual_key, evaluate,
                      TAIL_ODDS_FLOOR, LOGLLOSS_CLIP)


def _full_pool():
    pool = {f"{i}:{j}": (8.0 if (i + j) <= 3 else 30.0) for i in range(6) for j in range(6)}
    pool.update({"other_h": 60.0, "other_d": 60.0, "other_a": 60.0})
    return pool


def test_actual_key_grid_and_other():
    assert actual_key("2:1") == "2:1"
    assert actual_key("0:3") == "0:3"
    assert actual_key("6:0") == "other_h"
    assert actual_key("2:6") == "other_a"


def test_market_devig_keys():
    keys = ["1:1", "1:0", "0:1"]
    p = market_devig_on_keys(_full_pool(), keys)
    assert set(p) == set(keys) and abs(sum(p.values()) - 1.0) < 1e-6


def test_evaluate_verdict_machinery(tmp_path, monkeypatch):
    # 构造 2 个销售日的 preds + 赛果 fixture，验证 PASS/INSUFFICIENT 逻辑跑通
    import llm_eval as le
    pool = _full_pool()
    day_rows = []
    for i in range(35):                       # n≥30 观察门槛
        code = f"二{i:03d}"
        p = {k: (0.05 if k != "1:1" else 0.2) for k in pool}
        day_rows.append({"code": code, "cardHash": "x" * 16,
                         "p_crs": p, "pRawSum": 1.0, "rawSumWarn": False,
                         "contextStatus": "ok", "keyJudgments": [], "confidence": "medium",
                         "_marketPool": pool, "_actual": "1:1", "_closeProxy": "lineupLast"})
    monkeypatch.setattr(le, "_load_day_rows", lambda days: day_rows)
    out = evaluate(["2026-10-14"])
    assert out["primary"]["n"] == 35
    assert out["primary"]["verdict"] in ("PASS", "FAIL", "UNDETECTED", "INSUFFICIENT")
    assert "tailChannel" in out and "descriptive" in out
```

（`_load_day_rows(days)` 为 evaluate 的内部装载函数——返回行含下划线测试注入字段；生产实现从 llm_preds+卡+赛果装载后组装同结构。）

- [ ] **Step 2: 跑确认失败** → ModuleNotFoundError
- [ ] **Step 3: 实现（核心代码如下·生产装载函数按 Interfaces 描述实现）**

```python
# engine/scripts/llm_eval.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llm_eval.py — LLM 预测对拍结算（spec §4·prereg engine/cache/llm-predict-prereg.json）。

主判据=31 格全分布配对 log-loss（market−llm·CI 下限>0）；右尾通道=E_tail 聚合二项
（赔率≥30 键 ∪ {0:3,0:4}·CI 下限>0 且 n≥150）；descriptive=E1/E2/E3。
对手盘=开赛前最后可得快照（lineup_watch 末帧，缺则数据卡价+closeProxy 分层）。
开发者 sszhang"""
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import market_lambda as mkl
from strength_chain_eval import _boot_ci_diff
from tail_train import event_outcomes

PREREG_PATH = ROOT / "engine" / "cache" / "llm-predict-prereg.json"
PREDS_ROOT = ROOT / "engine" / "cache" / "llm_preds"
CARDS_ROOT = ROOT / "engine" / "cache" / "llm_cards"
TRENDS_ROOT = ROOT / "data" / "05-trends"
RESULTS_ROOT = ROOT / "data" / "02-results"
OUT_DIR = ROOT / "data" / "04-summaries" / "llm-predict"

BOOT_B, SEED, LOGLLOSS_CLIP, MIN_POOL_ITEMS = 2000, 20261015, 1e-6, 20
TAIL_ODDS_FLOOR = 30.0
TAIL_FIXED_CELLS = ("0:3", "0:4")
N_GATE = {"observe": 30, "formal": 100, "final": 200}


def actual_key(score: str) -> str | None:
    h, a = (int(x) for x in score.split(":"))
    if h <= 5 and a <= 5:
        return f"{h}:{a}"
    return "other_h" if h > a else ("other_d" if h == a else "other_a")


def market_devig_on_keys(pool: dict, keys: list[str]) -> dict:
    """power devig（s 键）→ pick 键交集。池<MIN_POOL_ITEMS→{}。"""
    if len([v for v in pool.values() if float(v) > 1.0]) < MIN_POOL_ITEMS:
        return {}
    s = mkl.crs_power_devig(pool)
    if not s:
        return {}
    back = {}
    for k, v in s.items():
        kk = (f"{int(k[1:3])}:{int(k[4:6])}" if k.startswith("s") and len(k) == 6 and k[3] == "s"
              else {"s1sh": "other_h", "s1sd": "other_d", "s1sa": "other_a"}.get(k))
        if kk:
            back[kk] = v
    return {k: back[k] for k in keys if k in back} if len(back) >= MIN_POOL_ITEMS else {}


def _ll(p: float, hit: bool) -> float:
    p = min(max(p, LOGLLOSS_CLIP), 1 - LOGLLOSS_CLIP)
    return -math.log(p) if hit else -math.log(1 - p)


def tail_keys_of(pool: dict) -> list[str]:
    """右尾键集=有价键中赔率≥30 ∪ 固定 {0:3,0:4}（有价才收）。"""
    out = {k for k, v in pool.items() if float(v) >= TAIL_ODDS_FLOOR}
    out |= {c for c in TAIL_FIXED_CELLS if pool.get(c) and float(pool[c]) > 1.0}
    return sorted(out)


def _load_day_rows(days: list[str]) -> list[dict]:
    """生产装载：llm_preds{d}.json + 数据卡市场层 + lineup 末帧 + 体彩赛果 → 逐场行。
    赛果优先 data/02-results/{开赛日}.json（score 字段）；无赛果场跳过（结算期未到）。"""
    rows = []
    for d in days:
        pf = PREDS_ROOT / f"{d}.json"
        if not pf.exists():
            continue
        preds = json.loads(pf.read_text(encoding="utf-8"))["preds"]
        lu = {}
        lu_f = TRENDS_ROOT / f"{d}-lineups.json"
        if lu_f.exists():
            lu = json.loads(lu_f.read_text(encoding="utf-8"))
        results = {}
        for rf in RESULTS_ROOT.glob("*.json"):
            try:
                doc = json.loads(rf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for m in (doc.get("matches") or doc.get("records") or []):
                if m.get("code") and (m.get("score") or m.get("finalScore")):
                    results[m["code"]] = str(m.get("score") or m.get("finalScore"))
        for pr in preds:
            card = json.loads((CARDS_ROOT / d / f'{pr["code"]}.json').read_text(encoding="utf-8"))
            market_pool = {}
            for k, v in (card["layers"]["market"]["crs"] or {}).items():
                kk = k if ":" in k else (
                    f"{int(k[1:3])}:{int(k[4:6])}" if k.startswith("s") and len(k) == 6 and k[3] == "s"
                    else {"s1sh": "other_h", "s1sd": "other_d", "s1sa": "other_a"}.get(k))
                if kk and isinstance(v, (int, float)) and float(v) > 1.0:
                    market_pool[kk] = float(v)
            pool, proxy = market_pool, "cardTime"
            for mid, e in lu.items():
                if isinstance(e, dict) and (e.get("matchCode") == pr["code"] or e.get("code") == pr["code"]):
                    snaps = e.get("oddsSnapshots") or e.get("odds") or []
                    last = snaps[-1] if isinstance(snaps, list) and snaps else None
                    if isinstance(last, dict) and last.get("crs"):
                        p2 = {}
                        for k, v in last["crs"].items():
                            kk = k if ":" in k else (
                                f"{int(k[1:3])}:{int(k[4:6])}" if k.startswith("s") and len(k) == 6 and k[3] == "s"
                                else {"s1sh": "other_h", "s1sd": "other_d", "s1sa": "other_a"}.get(k))
                            if kk and isinstance(v, (int, float)) and float(v) > 1.0:
                                p2[kk] = float(v)
                        if len(p2) >= MIN_POOL_ITEMS:
                            pool, proxy = p2, "lineupLast"
                    break
            sc = results.get(pr["code"])
            if not sc or ":" not in sc:
                continue                       # 赛果未回填，跳过
            rows.append({**pr, "_marketPool": pool, "_actual": actual_key(sc),
                         "_closeProxy": proxy, "_score": sc})
    return rows


def evaluate(days: list[str]) -> dict:
    rows = _load_day_rows(days)
    diffs_full, diffs_tail, desc = [], [], {"E1": [], "E2": [], "E3": []}
    proxy_split = {"cardTime": [], "lineupLast": []}
    for r in rows:
        keys = [k for k in r["p_crs"] if k in r["_marketPool"]]
        mkt = market_devig_on_keys(r["_marketPool"], keys)
        if not mkt or r["_actual"] not in mkt:
            continue                            # 池不足/键缺失→双侧同跳
        a = r["_actual"]
        llm = r["p_crs"]
        diffs_full.append(-math.log(max(mkt[a], LOGLLOSS_CLIP)) + math.log(max(llm.get(a, 0.0), LOGLLOSS_CLIP)))
        proxy_split.setdefault(r["_closeProxy"], []).append(diffs_full[-1])
        tk = tail_keys_of(r["_marketPool"])
        if tk:
            pl = sum(llm.get(k, 0.0) for k in tk)
            pm = sum(mkt.get(k, 0.0) for k in tk)
            hit = a in tk
            diffs_tail.append(_ll(pm, hit) - _ll(pl, hit))
        y = event_outcomes(r["_score"])
        for j, e in enumerate(("E1", "E2", "E3")):
            ev_keys = {"E1": None}.get(e)
            desc[e].append(_ll(sum(mkt.get(k, 0.0) for k in _event_keys(e, r["_marketPool"])), bool(y[j]))
                           - _ll(sum(llm.get(k, 0.0) for k in _event_keys(e, r["_marketPool"])), bool(y[j])))

    def verdict(diffs, gate_formal, gate_final):
        n = len(diffs)
        if n < N_GATE["observe"]:
            return {"n": n, "verdict": "INSUFFICIENT"}
        ci = _boot_ci_diff(diffs, seed=SEED, n_boot=BOOT_B)
        mean = float(np.mean(diffs))
        v = ("PASS" if ci[0] > 0 else
             "FAIL" if mean <= 0 and n >= gate_formal else
             "UNDETECTED" if mean > 0 and n < gate_final else "FAIL")
        return {"n": n, "diffMean": round(mean, 4), "ci95": [round(float(x), 4) for x in ci], "verdict": v}

    return {"days": days,
            "primary": verdict(diffs_full, N_GATE["formal"], N_GATE["final"]),
            "tailChannel": {**verdict(diffs_tail, N_GATE["formal"], N_GATE["final"]),
                            "gate": "CI 下限>0 且 n≥150 才开影子专项"},
            "descriptive": {e: ({"n": len(d), "diffMean": round(float(np.mean(d)), 4)}
                                if len(d) >= N_GATE["observe"] else {"n": len(d)})
                            for e, d in desc.items()},
            "closeProxySplit": {k: {"n": len(v), "diffMean": round(float(np.mean(v)), 4)}
                                for k, v in proxy_split.items() if v}}


def _event_keys(event: str, pool: dict) -> list[str]:
    """descriptive E1/E2/E3 的市场键聚合（E1 无市场直配键→用 E2 键集近似禁用——E1 走 ttg
    但 forward 场 ttg 池在卡里，此处按 spec descriptive 简化为 crs 键聚合口径并标注）。"""
    if event == "E1":
        return [k for k in pool if ":" in k and int(k[0]) + int(k[2]) >= 5] + \
               [k for k in ("other_h", "other_d", "other_a") if k in pool]
    cells = {"E2": [(3,0),(4,0),(4,1),(5,0),(5,1),(5,2),(0,3),(0,4),(1,4),(0,5),(1,5),(2,5)],
             "E3": [(0,3),(0,4),(0,5)]}
    return [f"{h}:{a}" for h, a in cells[event] if f"{h}:{a}" in pool]


def check_prereg() -> dict:
    if not PREREG_PATH.exists():
        raise SystemExit(f"[prereg] 缺 {PREREG_PATH}（铁律14②）")
    pr = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    fp = pr["frozenParams"]
    expect = {"boot_B": BOOT_B, "seed": SEED, "logloss_clip": LOGLOSS_CLIP,
              "min_pool_items": MIN_POOL_ITEMS, "tail_odds_floor": TAIL_ODDS_FLOOR,
              "tail_fixed_cells": list(TAIL_FIXED_CELLS)}
    for k, v in expect.items():
        if fp.get(k) != v:
            raise SystemExit(f"[prereg] frozenParams.{k}={fp.get(k)} ≠ 模块常量 {v}")
    return pr


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="LLM 预测对拍结算")
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("days", nargs="*")
    args = ap.parse_args()
    check_prereg()
    days = args.days or sorted(p.stem for p in PREDS_ROOT.glob("*.json"))
    result = evaluate(days)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "eval.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print(json.dumps({"primary": result["primary"], "tailChannel": result["tailChannel"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

（执行者注意两处真实数据对齐：①lineup_watch 快照结构以 engine/scripts/lineup_watch.py 落盘代码为准对齐 `oddsSnapshots` 字段名；②赛果装载扫 `data/02-results/*.json` 全目录较慢，可限当日±1 文件。）

- [ ] **Step 4: 跑测试通过** → 3 PASS
- [ ] **Step 5: Commit** `feat(llm预测): 对拍结算链——全分布主判据+E_tail右尾通道+descriptive+closeProxy分层+prereg指纹闸`

---

### Task 6: 影子层 family L + SKILL.md 流程挂接

**Files:**
- Modify: `engine/shadow/shapes.py`（PLAN_FAMILIES 追加 + L 族选腿分支）
- Modify: `skill/SKILL.md`（预测流程新增 LLM 预测步骤段）
- Test: `engine/tests/test_llm_pred.py`（追加 L 族 build_ticket 冒烟）

**Interfaces:**
- Consumes: `engine/cache/llm_preds/{销售日}.json`（冻结分布）、体彩在售价（shadow_all legs 口径）
- Produces: PLAN_FAMILIES 新条目 `{'family': 'L', 'name': 'llm-31格选腿', 'pool': 'llm_crs', 'shape': '2串1', 'n_legs': 2, 'options': 'dual', 'track': 'A'}`；`_pick_llm_legs(legs, qc)`——qc=llm_preds 映射 `{code: {crs: [(pick, p), ...] 右尾键内降序}}`，按 top1 概率降序取前 2 场、每场 top2 右尾腿（复用 build_ticket 的 crs 池价冻结路径）；SKILL.md 步骤文案（数据卡→联网检索语境层→31 格预测→llm_pred.freeze→回填时 llm_eval run）

- [ ] **Step 1: 写失败测试（L 族建票冒烟）**

```python
# 追加到 engine/tests/test_llm_pred.py
def test_llm_family_build_ticket(tmp_path):
    import importlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "shadow"))
    shapes = importlib.import_module("shapes")
    legs = [{"code": "周二001", "match": "A vs B", "fused": [0.5, 0.3, 0.2],
             "odds": [1.5, 4.0, 5.0],
             "pools": {"crs": {"s00s03": 40.0, "s00s04": 110.0, "s00s05": 350.0, "s01s01": 7.0}}},
            {"code": "周二002", "match": "C vs D", "fused": [0.4, 0.3, 0.3],
             "odds": [2.0, 3.4, 3.2],
             "pools": {"crs": {"s00s03": 35.0, "s00s04": 100.0, "s01s01": 7.5}}}]
    qc = {"周二001": {"crs": [("0:3", 0.030), ("0:4", 0.008)]},
          "周二002": {"crs": [("0:3", 0.020), ("0:4", 0.006)]}}
    spec = next(s for s in shapes.PLAN_FAMILIES if s.get("family") == "L")
    t = shapes.build_ticket(legs, spec, qc=qc)
    assert t is not None and len(t["tlegs"]) == 2
    assert t["tlegs"][0]["picks"] == ["0:3", "0:4"]          # 每场 top2 右尾腿
    assert t["n_bets"] == 4                                   # 2 场×双选=4 注 2串1
```

- [ ] **Step 2: 跑确认失败**（L 族不存在 → StopIteration）
- [ ] **Step 3: 实现**
  - shapes.py PLAN_FAMILIES 追加（预注册注释头：forward only·判据=llm-predict-prereg tailChannel·主判据 FAIL 不封锁本通道但封锁整体预测叙事）
  - `build_ticket` 增加 `elif spec['pool'] == 'llm_crs':` 分支：`by_top = sorted(qc 中 code 的 crs top1 概率降序)` 取前 n_legs 场；每场 `_leg_picks` 走 crs 分支同款（qc 已是右尾键降序，dual 取前 2）；池价冻结复用现有 crs 路径（`sXXsYY`→pick 转换已有）
  - `engine/shadow/paper.py` shadow-all 装载 L 族 qc：读 `engine/cache/llm_preds/{销售日}.json` → `{code: {"crs": [(pick, p) 右尾键内降序]}}`（装载函数对齐 V 族 v4b 快照模式——grep `load_v4b_snap` 调用点照抄）
  - SKILL.md 在影子层登记步骤前插入「LLM 预测步骤」段（文案含：预测先于回填纪律/三道过滤提醒/31 格协议/freeze 命令）
- [ ] **Step 4: 跑测试 + 回归**（`python3 -m pytest engine/tests/test_llm_pred.py engine/tests/test_llm_card.py engine/tests/test_llm_eval.py -v` 全绿；`python3 -c "import sys; sys.path.insert(0,'engine/shadow'); import shapes"` 无错）
- [ ] **Step 5: Commit** `feat(llm预测): 影子family L接入+SKILL流程挂接——右尾键降序选腿·forward第一天即积累`

---

## Self-Review 记录

- **Spec 覆盖**：§2 五层数据卡（Task 2/3）✓ 防泄漏三道过滤+终态禁改（Task 3）✓ §3 31 格协议+校准归一+hash 链（Task 4）✓ §4 双判据+续判规则+closeProxy 分层+prereg 指纹闸（Task 1/5）✓ §5 影子 family L+SKILL 挂接（Task 6）✓ §6 温度缩放（n≥100 后加）=**明示 out of scope**（样本到量另立小任务）✓
- **占位符扫描**：无 TBD；两处"以真实文件为准"的执行者对齐点（lineup_watch 字段名/存档键格式）附了明确对齐方法（读脚本落盘函数/读真实存档自测）——这是数据现实不是占位符
- **类型一致**：`build_card(..., now=)` 注入参数 Task 3/4 测试均用 ✓ `market_devig_on_keys/actual_key/tail_keys_of` 签名 Task 5 内自洽 ✓ freeze 的 CARDS_ROOT monkeypatch 点（llm_pred.CARDS_ROOT）与 Task 4 测试一致 ✓ `_load_day_rows` 测试注入点在 evaluate 内引用（monkeypatch le._load_day_rows）✓
- **已知风险**：①lineup_watch 快照字段名若与假设不符→Task 5 对齐点处理（代码已隔离在 close_pool/_load_day_rows 一处）②E1 descriptive 用 crs 键聚合（非 ttg 池）已在代码注释标注口径差异 ③paper.py L 族装载需动影子层核心——Task 6 Step 3 给了对齐模式（V 族）④subprocess insight 拉取在无网/慢网环境超时降级路径已写（intel 层"未获取"）
