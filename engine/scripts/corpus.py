#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学习语料汇总：合并 data/02-results/ 全量 records → data/04-summaries/corpus.json。

闭环学习底座（设计文档 docs/2026-08-22-learning-loop-design.html ①）：
- calibrate.py（融合重校 n≥100）/ ablate.py（系数消融 n≥50/系数）/ 本地拟合就绪度（n≥30/联赛）
  的门槛判断都读本文件的 readiness 字段
- 按 (date, code) 去重，后写覆盖；杯赛/联赛口径保留原值
- 跨日同场合并（2026-09-02 发现：同场卖两天进两个日期文件，(date,code) 键拦不住，
  366 条含 130 条重复 → 校准/消融被热门场重复加权）：同 (league, home, away, play) 且
  pick 相同 = 同一判断跨日复用 → 预测锁定字段取最早轮、回填字段取非空合并；
  pick 不同 = 跨日改选的独立判断 → 保留并计数

用法：
  python corpus.py            # 重建语料 + 打印就绪度一行摘要
"""
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

from common import log, ROOT

RESULTS_DIR = ROOT / "data" / "02-results"
OUT = ROOT / "data" / "04-summaries" / "corpus.json"

# 轮次后缀（-r1/-r2/-v2）：字典序会把 无后缀 排最后（"." > "-"）且 r10<r2，
# 后写覆盖顺序就反了（首轮覆盖终审修订轮）→ 按数值轮次排序
_ROUND_SUFFIX = re.compile(r"^(.+)-(?:r|v)(\d+)$")

# 回填字段（合并时取非空；预测锁定字段取最早轮不动）
BACKFILL_FIELDS = ("result", "directionHit", "scoreHit", "clv",
                   "clv_approx_dk", "clv_note", "pinClose", "pinSource")
# 独立市场锚字段：calibrate/trend_report 用 p_pinnacle 替代 p_final 做市场基线
# （修复市场基线污染：p_final 含模型贡献时不能兼作市场侧）
INDEPENDENT_MARKET_FIELD = "p_pinnacle"
_RESULT_SCORE = re.compile(r"^\d+-\d+$")


def _split_match(match) -> tuple[str, str] | None:
    if not match or " vs " not in str(match):
        return None
    h, a = str(match).split(" vs ", 1)
    return re.sub(r"\[.*?\]", "", h).strip(), re.sub(r"\[.*?\]", "", a).strip()


def _merge_backfill(base: dict, other: dict) -> None:
    for f in BACKFILL_FIELDS:
        bv, ov = base.get(f), other.get(f)
        if f == "result":  # 真实比分 > "不可得" > None
            def rank(v):
                if v and _RESULT_SCORE.match(str(v)):
                    return 2
                return 1 if v else 0
            if rank(ov) > rank(bv):
                base[f] = ov
        elif bv is None and ov is not None:
            base[f] = ov
    # 独立市场锚随 pinClose 同步（pinClose 可能被合并更新）
    pin = base.get("pinClose")
    if pin and len(pin) == 3:
        base[INDEPENDENT_MARKET_FIELD] = pin


def merge_crossday(rows: list[dict]) -> tuple[list[dict], int, int]:
    """跨日同场合并：rows 须已按 date 升序（最早轮=基底）。

    返回 (合并后 rows, 合并掉条数, pick 不同保留条数)。
    """
    groups: dict[tuple, list[dict]] = {}
    passthrough: list[dict] = []
    for r in rows:
        hm = _split_match(r.get("match"))
        if not hm:
            passthrough.append(r)   # 无对阵串的老记录不参与合并
            continue
        groups.setdefault((r.get("league"), hm[0], hm[1], r.get("play")), []).append(r)
    out: list[dict] = []
    merged = diff_pick = 0
    for grp in groups.values():
        if len(grp) == 1:
            out.append(grp[0])
            continue
        base, others = grp[0], grp[1:]
        for o in others:
            if str(o.get("pick")) == str(base.get("pick")):
                _merge_backfill(base, o)
                merged += 1
            else:  # 跨日改选 = 独立判断，保留
                out.append(o)
                diff_pick += 1
        out.append(base)
    out.extend(passthrough)
    return out, merged, diff_pick


def round_sort_key(p: Path) -> tuple[str, int]:
    # 主文件（无 -rN 后缀 = 终审+回填结算版）排最后：后写覆盖使其胜出一切 r 快照
    m = _ROUND_SUFFIX.match(p.stem)
    return (m.group(1), int(m.group(2))) if m else (p.stem, 999)

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
    new_schema = "fused" in r or "final" in r or "inPlan" in r  # v4.6 matches[]（result:null 预置不算老特征）
    old_schema = "p_final" in r or ("result" in r and r.get("result")) or "date" in r
    if not new_schema and old_schema:  # 老 schema 原样
        out = dict(r)
        out.setdefault("date", round_id[:10])
        # 独立市场锚：老 schema 若含 pinClose（去水三向），透传为 p_pinnacle
        if out.get("pinClose") and len(out["pinClose"]) == 3:
            out[INDEPENDENT_MARKET_FIELD] = out["pinClose"]
    else:  # 新 schema（v4.6 matches[]）归一
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
            "pools": r.get("pools"),      # 三池卡透传不展开（spec §4.5#6；实时结算判定递延台账）
            # 回填字段透传（新 schema 预置 result:null，回填后此处同步）
            "result": r.get("result"),
            "directionHit": r.get("directionHit"),
            "scoreHit": r.get("scoreHit"),
            "clv": r.get("clv"),
            "clv_approx_dk": r.get("clv_approx_dk"),
            "clv_note": r.get("clv_note"),
        }
        # 独立市场锚：pinClose（Pinnacle 去水三向）→ p_pinnacle
        pin = out.get("pinClose")
        if pin and len(pin) == 3:
            out[INDEPENDENT_MARKET_FIELD] = pin
    out["round"] = round_id
    return out


def build() -> dict:
    records = {}
    for p in sorted(RESULTS_DIR.glob("*.json"), key=round_sort_key):
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
            key = (nr.get("date"), nr.get("code"), nr.get("play"))
            if not key[0]:
                continue
            records[key] = nr  # 后写覆盖（同场同玩法重扫以最新为准；同场不同玩法各留一条——v4.6 同场次多票合规结构）

    rows = sorted(records.values(), key=lambda r: (r.get("date") or "", r.get("code") or ""))
    rows, merged_n, diff_pick_n = merge_crossday(rows)
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
            "n_dedup_merged": merged_n,
            "n_dedup_diff_pick_kept": diff_pick_n,
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
    log("corpus", f"语料 {c['n_total']} 条（已回填 {rd['n_result']} · CLV {rd['n_clv']} · p_final {rd['n_pfinal']} · "
        f"跨日同场合并 {rd['n_dedup_merged']} 条/改选保留 {rd['n_dedup_diff_pick_kept']}） → {OUT.relative_to(ROOT)}")
    cal = "✅可校准" if rd["calibrateReady"] else f"距融合重校门槛还差 {rd['calibrateGap']} 条回填"
    abl = "✅可消融" if rd["ablateReady"] else f"距系数消融门槛还差 {max(0, ABLATE_MIN_N - rd['n_result'])} 条"
    log("corpus", f"门槛：{cal}；{abl}")


if __name__ == "__main__":
    main()
