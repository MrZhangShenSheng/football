"""② xG-λ 信号源对比实验（三梯队一期 · docs/2026-09-27-probability-modernization-design.html）。

问题（E5）：fd CSV 的 HxG/AxG 每轮下载从未用于 λ 拟合——进球是噪声（运气分量），
xG 才是信号。本实验同划分对比两种 λ 源的 holdout 三向 logloss。

结论纪律：只出报告不切默认（换 λ 源=大哥拍板项，与修正系数消融纪律同构）。

用法：python xg_experiment.py
开发者 sszhang
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dc_fit import DEFAULT_XI, holdout_logloss, load_matches

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "data" / "04-summaries" / "xg-experiment-report.json"

LEAGUES = ["england-premier", "spain-laliga", "germany-bundesliga", "italy-serie-a", "france-ligue1"]


def main() -> None:
    rows = []
    for lg in LEAGUES:
        cache = sorted((ROOT / "engine" / "cache").glob(f"odds_{lg}_*.json"))
        seasons = [p.stem.removeprefix(f"odds_{lg}_") for p in cache][-2:]
        if not seasons:
            continue
        ms = load_matches(lg, seasons)
        if len(ms) < 50:
            rows.append({"league": lg, "n": len(ms), "skip": "样本<50"})
            continue
        n_xg = sum(1 for m in ms if m.get("hxg") is not None and m.get("axg") is not None)
        cov = n_xg / len(ms)
        # 覆盖<70%（2526老季xG缺失）：退化为同子集对比（有xG的场内 goals vs xG 公平赛）
        subset_mode = cov < 0.7
        pool = [m for m in ms if m.get("hxg") is not None and m.get("axg") is not None] if subset_mode else ms
        ll_goals = holdout_logloss(pool, DEFAULT_XI)
        ll_xg = holdout_logloss(pool, DEFAULT_XI, use_xg=True)
        row = {"league": lg, "seasons": seasons, "n": len(ms),
               "xgCoverage": round(cov, 2), "nCompared": len(pool),
               "subsetMode": subset_mode,
               "holdoutLogloss_goals": round(ll_goals, 4) if ll_goals else None,
               "holdoutLogloss_xg": round(ll_xg, 4) if ll_xg else None}
        if ll_goals and ll_xg:
            d = (ll_goals - ll_xg) / ll_goals * 100
            row["xgImprovementPct"] = round(d, 2)
            row["verdict"] = "xG更优" if d > 1 else ("进球更优" if d < -1 else "打平(<1%无差异)")
        rows.append(row)
        print(f"[xg-exp] {lg}: n={len(ms)} xG覆盖{cov:.0%} "
              f"进球LL={ll_goals and round(ll_goals,4)} xG-LL={ll_xg and round(ll_xg,4)} "
              f"{row.get('verdict', '')}")

    valid = [r for r in rows if r.get("xgImprovementPct") is not None]
    n_better = sum(1 for r in valid if r["xgImprovementPct"] > 1)
    conclusion = (
        f"{n_better}/{len(valid)} 联赛 xG 显著更优(>1%)——"
        + ("建议大哥拍板 dc_fit 默认换源（分联赛渐进）" if n_better >= 3
           else "证据不足维持进球源；xG 覆盖与样本随赛季推进增长，季度复跑")
    )
    REPORT.write_text(json.dumps({
        "at": "2026-09-27", "design": "docs/2026-09-27-probability-modernization-design.html §②",
        "xi": DEFAULT_XI, "split": 0.8, "rows": rows, "conclusion": conclusion,
        "note": "只出报告不切默认；2627 季 fd xG 覆盖 100%（50/50 实测英超），2526 季部分覆盖",
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[xg-exp] 结论: {conclusion}")
    print(f"[xg-exp] → {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
