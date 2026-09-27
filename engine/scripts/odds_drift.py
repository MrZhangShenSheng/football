"""赔率异动资金方向解读器（2026-09-27 大哥纠错后立规）。

背景：09-27 12:53 终审把"平局赔率 2.75→2.88 上升"误读为"市场向平局收敛"——
升赔=隐含概率下降=资金离开，方向完全读反，三处连环错（降价用词/收敛判断/
CLV 符号）。立规：**资金方向解读只准由本脚本输出，对话层禁止手算手读**。

语义铁表（唯一权威，文字只能复述不能另解）：
  赔率下降  = 隐含概率上升 = 该选项受血（资金涌入/庄家压价控险）
  赔率上升  = 隐含概率下降 = 该选项失血（资金离开/庄家升赔吸引买家）
  CLV = 出票赔率/对比赔率 - 1（出票赔率低于对比赔率 = 负 = 买贵了）

用法：
  python odds_drift.py "2.28,2.75,3.06" "2.08,2.88,3.32" --labels 主,平,客
  python odds_drift.py --snapshot engine/cache/xxx.json engine/cache/yyy.json  # 两组三向
输出：去水概率 delta 表 + 每选项受血/失血判定 + 一句话资金方向。
开发者 sszhang
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def devig(triple: list[float]) -> list[float]:
    """三向去水归一。"""
    inv = [1.0 / x for x in triple]
    s = sum(inv)
    return [x / s for x in inv]


def drift_report(old: list[float], new: list[float], labels: list[str] | None = None) -> dict:
    labels = labels or ["H", "D", "A"]
    po, pn = devig(old), devig(new)
    rows = []
    for lab, o, n, a, b in zip(labels, old, new, po, pn):
        delta_pp = (b - a) * 100
        if n < o - 1e-9:
            flow = "受血(资金涌入·市场认可)"
        elif n > o + 1e-9:
            flow = "失血(资金离开·市场转冷)"
        else:
            flow = "稳"
        clv_if_bought_at_old = o / n - 1  # 按旧价出票 vs 新价: 负=旧价更差
        rows.append({
            "option": lab, "oddsOld": o, "oddsNew": n,
            "pOld": round(a, 3), "pNew": round(b, 3),
            "deltaPp": round(delta_pp, 1), "flow": flow,
            "clvOldVsNew": round(clv_if_bought_at_old, 3),
        })
    gain = max(rows, key=lambda r: r["deltaPp"])
    lose = min(rows, key=lambda r: r["deltaPp"])
    verdict = (f"资金净流向: {gain['option']} 受血 {gain['deltaPp']:+.1f}pp"
               f" | {lose['option']} 失血 {lose['deltaPp']:+.1f}pp")
    return {"rows": rows, "verdict": verdict}


def _parse_triple(s: str) -> list[float]:
    return [float(x) for x in s.split(",")]


def main() -> None:
    args = sys.argv[1:]
    if len(args) >= 2 and not args[0].startswith("-"):
        old, new = _parse_triple(args[0]), _parse_triple(args[1])
        labels = None
        if "--labels" in args:
            labels = args[args.index("--labels") + 1].split(",")
        rep = drift_report(old, new, labels)
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return
    print(__doc__)


if __name__ == "__main__":
    main()
