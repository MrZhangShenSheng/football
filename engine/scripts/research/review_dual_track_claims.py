#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复审验证：双轨分化设计两条核心论据的口径检查（2026-09-01 会话）。

1. 准入门槛 65% 的数据支撑：倾向项（隐含最高项）落在 65~70% / 65~75% 段的真实命中
   （设计声称"该档实测腿级命中 70%+"，出处不明，需直接验算）
2. 62.4% 总进球带"甜点区"的基线对照：朴素基线（全样本最常见 3 档总进球的命中占比）
   —— 若朴素基线已接近，DC 引擎的边际贡献存疑
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
from dc_predict import devig  # noqa: E402

CACHE = ROOT / "engine" / "cache"

FD_LEAGUES = [
    "england-premier", "england-championship", "spain-laliga", "germany-bundesliga",
    "germany-bundesliga2", "italy-serie-a", "italy-serie-b", "france-ligue1",
    "france-ligue2", "netherlands-eredivisie", "portugal-primeira",
    "belgium-first-a", "turkey-super-lig", "greece-super",
]


def main():
    rows = []
    totals = []
    for lg in FD_LEAGUES:
        for season in ("2526", "2627"):
            p = CACHE / f"odds_{lg}_{season}.json"
            if not p.exists():
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
            for m in data.get("matches", []):
                if m.get("fthg") is None or not m.get("pin_h"):
                    continue
                try:
                    odds = (float(m["pin_h"]), float(m["pin_d"]), float(m["pin_a"]))
                    hg, ag = int(m["fthg"]), int(m["ftag"])
                except (ValueError, TypeError):
                    continue
                probs = devig(odds)
                outcome = 0 if hg > ag else (1 if hg == ag else 2)
                rows.append({"lg": lg, "probs": probs, "outcome": outcome})
                totals.append(hg + ag)

    n = len(rows)
    print(f"样本 {n} 场（与设计 A 部分同口径 2526+2627）\n")

    # 1. 倾向项分档命中（场级：取隐含最高项为倾向）
    print("=" * 78)
    print("1. 倾向项（隐含最高项）分档真实命中 —— 验证准入 65% 的支撑")
    print("=" * 78)
    bands = [(0.60, 0.65), (0.65, 0.70), (0.65, 0.75), (0.70, 0.80), (0.80, 1.01)]
    for lo, hi in bands:
        seg = [r for r in rows if lo <= max(r["probs"]) < hi]
        if not seg:
            continue
        hit = sum(max(range(3), key=lambda i: r["probs"][i]) == r["outcome"] for r in seg)
        # 95% Wilson 区间粗看稳定性
        ph = hit / len(seg)
        se = (ph * (1 - ph) / len(seg)) ** 0.5
        print(f"  隐含 {lo:.0%}~{hi:.0%}: {len(seg):>5} 场 | 命中 {hit:>4} = {ph:.1%}"
              f" (±1.96SE ±{1.96 * se:.1%})")

    # 2. 总进球带朴素基线
    print()
    print("=" * 78)
    print("2. 总进球带朴素基线 —— 全样本最常见 3 档总进球命中占比（对照 62.4%）")
    print("=" * 78)
    cnt = Counter(totals)
    total_n = len(totals)
    print("  总进球分布：")
    for g in sorted(cnt):
        print(f"    {g} 球: {cnt[g]:>4} 场 = {cnt[g] / total_n:.1%}")
    top3 = [g for g, _ in cnt.most_common(3)]
    naive = sum(cnt[g] for g in top3) / total_n
    print(f"  最常见 3 档 = {sorted(top3)}，朴素基线命中 = {naive:.1%}")
    print(f"  设计声称 DC 前 3 档 62.4%（含泄漏 in-sample）→ 对朴素基线超额 = {0.624 - naive:+.1%}")


if __name__ == "__main__":
    main()
