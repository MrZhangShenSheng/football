# engine/scripts/pick_logic.py
# -*- coding: utf-8 -*-
"""实力链步骤⑥：选场（右尾框架主目标·五信号可消融·V6/V7疫苗内置）。
⚠ 权重 weights 由调用方传：门1前用等权记录，门1过后门2开始前按预注册冻结。开发者 sszhang"""
from __future__ import annotations
import math
import random

def _entropy(cells: dict) -> float:
    p = [v for v in cells.values() if v > 0]
    z = sum(p) or 1.0
    return -sum((v / z) * math.log(v / z) for v in p)

def _signals(c: dict) -> dict:
    return {"s1_entropy": -_entropy(c["matrix"]),          # 集中度=负熵（越高越集中）
            "s2_calib": 1.0 if c.get("calibBandOk") else 0.0,
            "s3_league": 1.0 if c.get("leagueCalibOk") else 0.0,
            "s4_info": 1.0 if c.get("infoEvent") else 0.0,
            "s5_disagreement": c.get("disagreement", 0.0)}  # 对照信号·预期证伪

def pick(candidates: list[dict], top_n: int, weights: dict[str, float]) -> list[dict]:
    """排序选场：S3 是门（不达标直接出局），其余信号加权和。每 pick 带 reasons。"""
    scored = []
    for c in candidates:
        if not c.get("leagueCalibOk", False):
            continue                                        # S3 门
        sig = _signals(c)
        score = sum(weights.get(k, 0.0) * v for k, v in sig.items())
        scored.append({**c, "score": round(score, 6),
                       "reasons": {k: round(v, 4) for k, v in sig.items() if weights.get(k, 0) > 0}})
    scored.sort(key=lambda x: -x["score"])
    return scored[:top_n]

def random_baseline_dist(candidates: list[dict], top_n: int, n_perm: int = 1000,
                         seed: int = 7) -> dict:
    """V6：随机选 top_n 场 n_perm 次的命中分布（mean/p95）——选场逻辑必须显著超它。"""
    rng = random.Random(seed)
    metric = "p_top"
    vals = []
    for _ in range(n_perm):
        chosen = rng.sample(candidates, min(top_n, len(candidates)))
        vals.append(sum(c[metric] for c in chosen) / len(chosen))
    vals.sort()
    return {"mean": sum(vals) / len(vals), "p95": vals[int(0.95 * len(vals)) - 1],
            "perm_n": n_perm, "metric": metric}

def full_vs_picked(picked: dict, full: dict) -> dict:
    """V7：选出N场命中 vs 当日全体命中 强制同报（diff+粗CI）。"""
    import math
    diff = picked["hitPicked"] - full["hitFull"]
    se = math.sqrt(max(picked["hitPicked"] * (1 - picked["hitPicked"]), 1e-9) / max(picked["n"], 1))
    return {"hitPicked": picked["hitPicked"], "hitFull": full["hitFull"],
            "diff": round(diff, 4), "ci": [round(diff - 1.96 * se, 4), round(diff + 1.96 * se, 4)]}
