"""⑧ λ 不确定性传播（bootstrap 轻量版 · 三梯队三期 · docs/2026-09-27 概率-modernization-design.html）。

问题：所有概率点估计——韩职 636 场与荷乙 0 场的 λ 输出置信度相同（数学上荒谬）。
手工 ×0.97 单锚补丁应为模型内生：小样本 → λ 区间宽 → 预测区间宽 → 星级自动下调。

方案（bootstrap 参数后验）：对联赛赛果做 B 次重采样 → 每次跑 dc_fit.fit() →
对决 (λ_h, λ_a) 的经验分布 → 95% CI → 三向概率 CI 宽度（区间宽=自动降星依据）。

验证口径：大样本（韩职全量 636）vs 小样本（韩职截断 60 场模拟冷启动）的 CI 宽度
对比——小样本应显著更宽（机制正确性）。B=40 平衡计算量。

用法：python lambda_uncertainty.py [league] [home] [away] [n_small]
开发者 sszhang
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dc_fit import fit
from dc_predict import score_matrix

ROOT = Path(__file__).resolve().parents[2]
B = 40


def three_way(lh: float, la: float, rho: float) -> list[float]:
    import numpy as np
    m = np.asarray(score_matrix(lh, la, rho), dtype=float)
    return [float((m > 0).sum() if False else sum(m[i, j] for i in range(7) for j in range(7) if (i > j if k == 0 else (i == j if k == 1 else i < j)))) for k in range(3)]


def bootstrap_ci(matches: list[dict], home: str, away: str, b: int = B, seed: int = 7) -> dict:
    """B 次重采样拟合 → 对决 λ/三向 95% CI。"""
    rng = random.Random(seed)
    lh_s, la_s, p3_s = [], [], []
    for _ in range(b):
        sample = [matches[rng.randrange(len(matches))] for _ in range(len(matches))]
        try:
            teams, attack, defense, adv, rho, _ = fit(sample, 0.005)
        except Exception:
            continue
        if home not in teams or away not in teams:
            continue
        i = teams.index(home)
        j = teams.index(away)
        lh = 2.718 ** (attack[i] + defense[j] + adv)
        la = 2.718 ** (attack[j] + defense[i])
        p3 = three_way(lh, la, rho)
        lh_s.append(lh)
        la_s.append(la)
        p3_s.append(p3)
    def ci(vals):
        if len(vals) < 10:
            return None
        s = sorted(vals)
        return {"p25": round(s[int(len(s) * 0.025)], 3), "p975": round(s[int(len(s) * 0.975)], 3),
                "width": round(s[int(len(s) * 0.975)] - s[int(len(s) * 0.025)], 3),
                "mean": round(sum(s) / len(s), 3)}
    p3_ci = []
    for k in range(3):
        p3_ci.append(ci([p[k] for p in p3_s]))
    return {"nBoot": len(lh_s), "lambdaHome": ci(lh_s), "lambdaAway": ci(la_s), "threeWay": p3_ci}


def main() -> None:
    league = sys.argv[1] if len(sys.argv) > 1 else "korea"
    home = sys.argv[2] if len(sys.argv) > 2 else "gangwon"
    away = sys.argv[3] if len(sys.argv) > 3 else "incheon-united"
    n_small = int(sys.argv[4]) if len(sys.argv) > 4 else 60
    src = json.loads((ROOT / "data" / "02-results" / "league" / f"{league}_matches.json").read_text(encoding="utf-8"))
    from datetime import date as _date
    ms = []
    for m in src["matches"]:
        m2 = dict(m)
        if isinstance(m2.get("date"), str):  # 本地库 ISO 字符串 → date（fit 需要做差）
            y, mo, dd = (int(x) for x in m2["date"].split("-"))
            m2["date"] = _date(y, mo, dd)
        ms.append(m2)
    full = bootstrap_ci(ms, home, away)
    small = bootstrap_ci(ms[-n_small:], home, away)
    w_full = [t["width"] if t else None for t in full["threeWay"]]
    w_small = [t["width"] if t else None for t in small["threeWay"]]
    verdict = "机制正确✅ 小样本三向CI显著更宽" if all(
        w2 and w1 and w2 > w1 * 1.3 for w1, w2 in zip(w_full, w_small)) else "宽度差异不足·检查"
    out = {"league": league, "pair": f"{home} vs {away}", "B": B,
           "full": {"nMatches": len(ms), "lambdaHome": full["lambdaHome"], "threeWayWidth": w_full},
           f"small{n_small}": {"lambdaHome": small["lambdaHome"], "threeWayWidth": w_small},
           "verdict": verdict}
    print(f"[λ-ci] {league} {home} vs {away} (B={B})")
    print(f"  全量{len(ms)}场: λh 95%CI={full['lambdaHome']} 三向宽={w_full}")
    print(f"  截断{n_small}场: λh 95%CI={small['lambdaHome']} 三向宽={w_small}")
    print(f"  裁决: {verdict}")
    Path(ROOT / "data" / "04-summaries" / "lambda-uncertainty-report.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("[λ-ci] → data/04-summaries/lambda-uncertainty-report.json")


if __name__ == "__main__":
    main()
