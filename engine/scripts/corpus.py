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
        round_id = p.stem  # 轮次标识（来源文件名，如 2026-08-22 / 2026-08-21-v2）
        for r in data.get("records", []):
            key = (r.get("date"), r.get("code"))
            if not key[0] or not key[1]:
                continue
            records[key] = {**r, "round": round_id}  # 后写覆盖（同场重扫以最新为准）

    rows = sorted(records.values(), key=lambda r: (r.get("date") or "", r.get("code") or ""))
    # 方案层（02-results 顶层 plans：方案名 → 场次编号列表）
    plans = {}
    for p in sorted(RESULTS_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(data.get("plans"), dict):
            for name, codes in data["plans"].items():
                plans[f"{p.stem}:{name}"] = codes
    # 就绪度统计
    by_league, by_star = Counter(), Counter()
    n_result = n_clv = n_pfinal = 0
    for r in rows:
        lg = r.get("league") or "?"
        by_league[lg] += 1
        if r.get("stars"):
            by_star[r["stars"]] += 1
        if r.get("result"):
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
            "by_star": {str(k): v for k, v in sorted(by_star.items())},
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
