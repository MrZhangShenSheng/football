# engine/scripts/attribution_dims.py
"""比分归因维度计算器（纯函数·同输入同输出）。设计 §二。开发者 sszhang"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from market_lambda import had_devig, solve_lamh

def lambda_from_had(odds: list[float], lam_sum: float) -> tuple[float, float] | None:
    """L1 基线：HAD 三向去水→p_h→solve_lamh 反解 (λh, λa)。开发者 sszhang"""
    if lam_sum is None or lam_sum <= 0.5:
        return None
    p_h, _, _ = had_devig(*odds)
    if not 0.05 < p_h < 0.95:
        return None
    lam_h = solve_lamh(lam_sum, p_h)
    return round(lam_h, 3), round(lam_sum - lam_h, 3)


# attribution_dims.py 追加
ELO_SCALE = 400.0  # Elo 差 400≈净胜 1 球（经验先验·待频次表校准·设计§二A1）

def dim_elo(elo_h, elo_a) -> dict | None:
    """A1 实力差→净胜偏移（对半分给 h/a）。缺 Elo 返回 None 进 missing。开发者 sszhang"""
    if elo_h is None or elo_a is None:
        return None
    net = (elo_h - elo_a) / ELO_SCALE
    return {"dim": "A1.elo", "h": round(net / 2, 3), "a": round(-net / 2, 3),
            "evidence": f"elo_diff={round(elo_h - elo_a)}"}
