# engine/scripts/attribution_dims.py
"""比分归因维度计算器（纯函数·同输入同输出）。设计 §二。开发者 sszhang"""
import sys
from datetime import datetime
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


# attribution_dims.py 追加（Task 4：L0 赛制档案+dim_stage）
import json

# brief 原稿 parents[1] 指向 engine/data/00-leagues（不存在）必落空，按仓库实际结构
# 改 parents[2]=repo 根（与 score_attribution.py 的 ROOT=SCRIPTS_DIR.parents[1] 同层级约定）
FORMATS_PATH = Path(__file__).resolve().parents[2] / "data" / "00-leagues" / "_formats.json"
FIFA_VIRUS_DAYS = 5  # 窗口结束后 N 天内的联赛场算 FIFA 病毒（先验·待频次表校准）

def _formats() -> dict:
    return json.loads(FORMATS_PATH.read_text(encoding="utf-8"))

def _in_fifa_window_tail(date_str: str) -> bool:
    for win in _formats().get("_fifa_windows_2026", []):
        end = win.split("/")[1]
        d0 = datetime.fromisoformat(date_str).date()
        d1 = datetime.fromisoformat(end).date()
        if 0 < (d0 - d1).days <= FIFA_VIRUS_DAYS:
            return True
    return False

def dim_stage(rec: dict) -> dict:
    """L0 赛制情境→形状系数（期1只判 league_regular/fifa_virus；淘汰赛档期4接 C1 细分）。开发者 sszhang"""
    fmt = _formats().get(rec.get("league"))
    if fmt is None:
        return {"dim": "L0.stage", "tail_scale": 1.0, "evidence": "no_format"}
    ev = "league_regular" if fmt.get("type", "league").startswith("league") else fmt["type"]
    if _in_fifa_window_tail(rec["date"]):
        ev += "+fifa_virus"
    return {"dim": "L0.stage", "tail_scale": 1.0, "evidence": ev}
