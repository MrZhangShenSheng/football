"""形状回测 v2：加真实出票排除逻辑的阶梯对比 ★ 2026-09-11 大哥追加

在 v1（孪生票 top4 基线）之上叠真实链路里 fd 数据可复现的排除逻辑：
- R1 胶着排除：B365 收盘去水三向极差 <10pp 的场次不入串（SKILL Step1.5/Step5）
- R2 开季限 1：每队赛季出场序 ≤3 判 R1-3 轮，每轮 top4 中开季腿 ≤1（选场纪律）
- R3 超低赔通道：p≥0.55 或 (收盘 ≤1.25 且 p≥0.50)（v5.6 彩票档合格腿口径）

不可复现（fd 无数据，诚实跳过）：伤停差值/保级平局保护/战意/踩线基面反向/DC 熔断。

用法：python engine/scripts/research/shape_backtest_v2.py [--boots 300]
输出：data/04-summaries/shape-backtest-v2-report.json + 控制台阶梯对比
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "scripts" / "research"))
sys.path.insert(0, str(ROOT / "engine" / "shadow"))

from shape_backtest import (  # noqa: E402
    SHAPES, boot_ci, build_ticket, load_matches, run_shape, summarize,
)

OUT = ROOT / "data" / "04-summaries" / "shape-backtest-v2-report.json"
EARLY_ROUNDS = 3          # 开季 R1-3 判定（每队出场序）
EARLY_MAX_PER_ROUND = 1   # 选场纪律：开季腿每轮 ≤1
TIE_GAP = 0.10            # 胶着：三向去水极差 <10pp
SUPER_LOW_ODDS = 1.25     # 超低赔通道
SUPER_LOW_P = 0.50


def p_gap(m: dict) -> float:
    """三向去水极差（max-min）。"""
    inv = [1 / o for o in m["b365"]]
    s = sum(inv)
    q = [x / s for x in inv]
    return max(q) - min(q)


def early_map(matches: list[dict]) -> dict:
    """(season, league, team) 出场序 ≤3 的 (date, home, away) → True。"""
    seen: dict[tuple, int] = defaultdict(int)
    early = set()
    for m in sorted(matches, key=lambda x: (x["season"], x["date"])):
        for team in (m["home"], m["away"]):
            key = (m["season"], m["league"], team)
            seen[key] += 1
            if seen[key] <= EARLY_ROUNDS:
                early.add((m["season"], m["date"], m["home"], m["away"]))
    return early


def eligible(m: dict, rules: set[str]) -> bool:
    """规则包过滤：p 门槛（含超低赔通道）+ 胶着排除（开季配额在 pick_top4 层做）。"""
    p_ok = m["p_main"] >= 0.55
    if "R3" in rules and m["b365"][m["pick"]] <= SUPER_LOW_ODDS and m["p_main"] >= SUPER_LOW_P:
        p_ok = True
    if not p_ok:
        return False
    if "R1" in rules and m["_gap"] < TIE_GAP:
        return False
    return True


def pick_top4(pool: list[dict], rules: set[str]) -> list[dict] | None:
    """top4 选腿 + 开季配额。"""
    pool = sorted(pool, key=lambda m: -m["p_main"])
    if "R2" not in rules:
        return pool[:4] if len(pool) >= 4 else None
    chosen, early_cnt = [], 0
    for m in pool:
        if m["_early"]:
            if early_cnt >= EARLY_MAX_PER_ROUND:
                continue
            early_cnt += 1
        chosen.append(m)
        if len(chosen) == 4:
            return chosen
    return None if len(chosen) < 4 else chosen


def main() -> None:
    boots = 300
    if "--boots" in sys.argv:
        boots = int(sys.argv[sys.argv.index("--boots") + 1])

    matches = load_matches(False)
    gaps = {}
    early = early_map(matches)
    for m in matches:
        m["_gap"] = p_gap(m)
        m["_early"] = (m["season"], m["date"], m["home"], m["away"]) in early

    by_day: dict[tuple, list[dict]] = defaultdict(list)
    for m in matches:
        by_day[(m["season"], m["date"])].append(m)

    LADDERS = [
        ("基线_p55top4", set()),
        ("R1_加胶着排除", {"R1"}),
        ("R2_加开季限1", {"R2"}),
        ("R1R2_叠加", {"R1", "R2"}),
        ("R1R2R3_全加超低赔通道", {"R1", "R2", "R3"}),
    ]

    report = {"generatedAt": datetime.now().isoformat(timespec="minutes"),
              "rules": {"胶着极差": "<10pp 排除", "开季": f"每队前{EARLY_ROUNDS}场·每轮≤{EARLY_MAX_PER_ROUND}腿",
                        "超低赔通道": f"≤{SUPER_LOW_ODDS} 且 p≥{SUPER_LOW_P}",
                        "不可复现": "伤停/保级平局/战意/踩线基面/DC熔断（fd 无数据）"},
              "ladders": {}}

    for tag, rules in LADDERS:
        rounds = []
        for (season, date), pool in sorted(by_day.items()):
            elig = [m for m in pool if eligible(m, rules)]
            if len(elig) < 4:
                continue
            chosen = pick_top4(elig, rules)
            if not chosen:
                continue
            rounds.append({"season": season, "date": date, "legs": chosen,
                           "runs": [run_shape(chosen, n, s) for n, s in SHAPES]})
        lad = {"rounds": len(rounds), "byShape": {}}
        for i, (name, _) in enumerate(SHAPES):
            runs = [r["runs"][i] for r in rounds]
            lad["byShape"][name] = summarize(runs)
        # 与基线的配对差（同轮三形状回收率差——按日对齐轮次交集）
        report["ladders"][tag] = lad
        print(f"[v2] {tag}: {len(rounds)} 轮")
        for name, s in lad["byShape"].items():
            print(f"  {name:<8} 回收率 {s['ret']*100:>6.1f}% 净 {s['net']:>+9.2f} "
                  f"全中 {s['hit_all']}/{s['rounds']} 连灭 {s['max_streak0']} 回撤 {s['max_dd']}")

    # 阶梯间配对差（R1R2R3 全加 vs 基线，按 (season,date) 对齐）
    def collect(rules):
        out = {}
        for (season, date), pool in sorted(by_day.items()):
            elig = [m for m in pool if eligible(m, rules)]
            if len(elig) < 4:
                continue
            chosen = pick_top4(elig, rules)
            if not chosen:
                continue
            out[(season, date)] = [run_shape(chosen, n, s) for n, s in SHAPES]
        return out

    base = collect(set())
    full = collect({"R1", "R2", "R3"})
    common = sorted(set(base) & set(full))
    report["pair_vs_baseline"] = {"commonRounds": len(common), "diff": {}}
    for i, (name, _) in enumerate(SHAPES):
        diffs = [full[d][i]["ret"] - base[d][i]["ret"] for d in common]
        lo, hi = boot_ci(diffs, boots)
        report["pair_vs_baseline"]["diff"][name] = {
            "mean": round(sum(diffs) / len(diffs), 4), "ci95": [lo, hi]}
        print(f"[v2] 全加 vs 基线 {name}: {sum(diffs)/len(diffs)*100:+.2f}pp "
              f"CI95 [{lo*100:+.2f}, {hi*100:+.2f}]（{len(common)} 共同轮）")

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[v2] → {OUT}")


if __name__ == "__main__":
    main()
