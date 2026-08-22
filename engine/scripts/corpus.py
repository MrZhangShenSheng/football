#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学习语料汇总：合并 data/02-results/ 全量 records → data/04-summaries/corpus.json。

闭环学习底座（设计文档 docs/2026-08-22-learning-loop-design.html ①）：
- calibrate.py（融合重校 n≥100）/ ablate.py（系数消融 n≥50/系数）/ 本地拟合就绪度（n≥30/联赛）
  的门槛判断都读本文件的 readiness 字段
- 按 (date, code) 去重，后写覆盖；杯赛/联赛口径保留原值

用法：
  python corpus.py            # 重建语料 + 打印就绪度一行摘要
"""
import json
from collections import Counter
from datetime import date
from pathlib import Path

from common import log, ROOT

RESULTS_DIR = ROOT / "data" / "02-results"
OUT = ROOT / "data" / "04-summaries" / "corpus.json"

# 门槛常量（与设计文档 §四 对齐；calibrate/ablate 复用 import）
CALIBRATE_MIN_N = 100
ABLATE_MIN_N = 50
FIT_MIN_N = 30


def normalize_record(r: dict, round_id: str) -> dict | None:
    """双 schema 归一：
    - v4.5 records[]：date/code/league/match/stars/grade(数字)/pick/odds/p_final/result/...
    - v4.6 matches[]：code/league/match/pick(带玩法前缀)/odds/star/grade(字母)/dc/fused/final/ev/inPlan...
    统一输出内部标准 record（后者赛果字段待回填流程补齐后也能进统计）。
    """
    code = r.get("code")
    if not code:
        return None
    if "p_final" in r or "result" in r or "date" in r:  # 老 schema
        out = dict(r)
        out.setdefault("date", round_id[:10])
    else:  # 新 schema（v4.6 matches[]）
        grade_map = {"A": 4, "B": 3, "C": 2, "D": 1}
        pick = str(r.get("pick") or "")
        out = {
            "date": round_id[:10], "code": code,
            "league": str(r.get("league") or "").split("(")[0],  # "荷甲(R3)" → "荷甲"
            "match": r.get("match"),
            "stars": r.get("star"), "grade": grade_map.get(r.get("grade")),
            "pick": pick.split(" ", 1)[1] if " " in pick else pick,  # "HAD 客胜" → "客胜"
            "play": pick.split(" ", 1)[0] if " " in pick else None,  # 玩法代码（HAD/TTG/HAFU/CRS）
            "odds": r.get("odds"),
            "p_final": r.get("fused") or r.get("dc"),
            "ev": r.get("ev"),
            "in_plan": r.get("inPlan"),
            "chain": r.get("chain"),
        }
    out["round"] = round_id
    return out


def build() -> dict:
    records = {}
    for p in sorted(RESULTS_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log("corpus", f"跳过坏文件 {p.name}")
            continue
        round_id = p.stem  # 轮次标识（来源文件名，如 2026-08-22 / 2026-08-22-r1）
        raw = data.get("records") or data.get("matches") or []
        for r in raw:
            nr = normalize_record(r, round_id)
            if not nr:
                continue
            key = (nr.get("date"), nr.get("code"))
            if not key[0]:
                continue
            records[key] = nr  # 后写覆盖（同场重扫以最新为准）

    rows = sorted(records.values(), key=lambda r: (r.get("date") or "", r.get("code") or ""))
    # 方案层（02-results 顶层 plans：老格式 dict{方案名: [编号]}；新格式 list[{plan, legs:[描述串]}]）
    plans = {}
    for p in sorted(RESULTS_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        raw_plans = data.get("plans")
        if isinstance(raw_plans, dict):
            for name, codes in raw_plans.items():
                plans[f"{p.stem}:{name}"] = codes
        elif isinstance(raw_plans, list):
            for pl in raw_plans:
                if isinstance(pl, dict) and pl.get("plan"):
                    plans[f"{p.stem}:{pl['plan']}"] = pl  # 保留完整 dict（legs 含玩法+赔率描述）
    # 就绪度统计
    by_league, by_star = Counter(), Counter()
    n_result = n_clv = n_pfinal = 0
    for r in rows:
        lg = r.get("league") or "?"
        by_league[lg] += 1
        if r.get("stars"):
            by_star[r["stars"]] += 1
        if r.get("result") and r.get("result") != "不可得":  # "不可得"=查询过但无数据源，非已回填
            n_result += 1
        if r.get("clv") is not None or r.get("clv_approx_dk") is not None:
            n_clv += 1
        if r.get("p_final"):
            n_pfinal += 1

    corpus = {
        "generatedAt": date.today().isoformat(),
        "n_total": len(rows),
        "n_rounds": len({r.get("round") for r in rows}),
        "readiness": {
            "n_result": n_result,
            "n_clv": n_clv,
            "n_pfinal": n_pfinal,
            "calibrateReady": n_result >= CALIBRATE_MIN_N,
            "calibrateGap": max(0, CALIBRATE_MIN_N - n_result),
            "ablateReady": n_result >= ABLATE_MIN_N,
            "by_league": dict(by_league.most_common()),
            "by_star": {str(k): v for k, v in sorted(by_star.items(), key=lambda kv: str(kv[0]))},
        },
        "records": rows,
        "plans": plans,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(corpus, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return corpus


def main() -> None:
    c = build()
    rd = c["readiness"]
    log("corpus", f"语料 {c['n_total']} 条（已回填 {rd['n_result']} · CLV {rd['n_clv']} · p_final {rd['n_pfinal']}）"
        f" → {OUT.relative_to(ROOT)}")
    cal = "✅可校准" if rd["calibrateReady"] else f"距融合重校门槛还差 {rd['calibrateGap']} 条回填"
    abl = "✅可消融" if rd["ablateReady"] else f"距系数消融门槛还差 {max(0, ABLATE_MIN_N - rd['n_result'])} 条"
    log("corpus", f"门槛：{cal}；{abl}")


if __name__ == "__main__":
    main()
