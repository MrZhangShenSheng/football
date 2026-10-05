# engine/scripts/lambda_bridge.py
# -*- coding: utf-8 -*-
"""实力链步骤④：实力对比→λ（对数线性·与DC同构）+ 伤停乘子 + β矩估计拟合。开发者 sszhang。
λ_h = T/2 · exp(d_att)，λ_a = T/2 · exp(d_def)；伤停乘子 exp(-β·Δ主力)。"""
from __future__ import annotations
import math

MAIN_APPS = 5     # apps>=5 视为主力（缺阵才伤实力）

def main_player_count(inj_list: list[dict] | None) -> int:
    if not inj_list:
        return 0
    return sum(1 for p in inj_list if (p.get("apps") or 0) >= MAIN_APPS)

def injury_multiplier(delta_main: int, beta: float) -> float:
    """Δ主力 = 主缺 − 客缺；负=客更缺→主λ上调。"""
    return math.exp(-beta * delta_main)

def fit_beta(samples: list[dict]) -> float:
    """矩估计（OLS斜率）：log_ratio ~ delta_main，β=-斜率。一次性拟合·非网格挑优。"""
    xs = [s["delta_main"] for s in samples]
    ys = [s["log_ratio"] for s in samples]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom if denom else 0.0
    return max(0.0, -slope)          # β 物理约束 ≥0（缺阵只会削弱不会增强）

def lambdas(cmp: dict, inj_h: int | None, inj_a: int | None, beta: float) -> dict:
    """λ桥：compare 输出 → λ主/λ客 + 三行账（base/HFA已并入d_att故账内单列体现·injury独立）。"""
    lam_h = cmp["T"] / 2 * math.exp(cmp["d_att"])
    lam_a = cmp["T"] / 2 * math.exp(cmp["d_def"])
    if inj_h is None or inj_a is None:
        contrib = {"base": round(lam_h + lam_a, 4), "hfa": "in-d_att", "injury": "neutral(no-data)"}
    else:
        delta_main = inj_h - inj_a
        m = injury_multiplier(delta_main, beta)
        lam_h, lam_a = lam_h * m, lam_a * (1 / m if m else 1.0)
        contrib = {"base": round(cmp["T"] / 2, 4), "hfa": "in-d_att/d_def",
                   "injury": {"deltaMain": delta_main, "multiplier": round(m, 4)}}
    return {"lam_h": lam_h, "lam_a": lam_a, "contrib": contrib}
