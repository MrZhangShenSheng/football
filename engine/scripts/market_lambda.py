# engine/scripts/market_lambda.py
# -*- coding: utf-8 -*-
"""市场反解引擎（v35-P4·D3）——体彩盘口直接反解概率/λ·零模型零画像依赖。

证据（surviving_chain.json 凭证·2026-10-11）:
  crs_power_devig  <- U2+铁律13: 市场top1 13.5%/top3 33.5% = 现存最好的比分分布
  market_lambda_solver <- U2: 矩法反解λ双null显著胜V3W(Δ+0.0994/+0.2488)·ρ=0(U4)
开发者 sszhang
"""
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score_matrix as sm          # 同目录·dc_matrix/had_from 口径同链

MIN_CRS_CELLS = 20                 # 盘口<20项→降级（crs_fusion marketFused 门槛沿袭）
RHO_ZERO = 0.0                     # U4: ρ 修正判死·独立泊松
TTG_KEYS = tuple(str(i) for i in range(8))   # 体彩总进球 0..7（"7"=7+ 档）
TTG_PLUS_VAL = 7.0                 # "7+"期望替身·保守下界（p7 通常<1%）
CRS_OTHER_MAP = {"other_h": "s1sh", "other_d": "s1sd", "other_a": "s1sa"}


def crs_key(k: str) -> str:
    """市场盘键→39格矩阵键：'1:1'→'s01s01'·other 三键→s1sx·非法返回 ''。"""
    k = str(k)
    if k in CRS_OTHER_MAP:
        return CRS_OTHER_MAP[k]
    if ":" in k:
        h, a = k.split(":", 1)
        if h.isdigit() and a.isdigit() and int(h) <= 5 and int(a) <= 5:
            return f"s{int(h):02d}s{int(a):02d}"
    return ""


def _power_devig_probs(inv: np.ndarray) -> np.ndarray:
    """k 二分解 Σ(1/o)^k=1（U3 修复版目标·2026-10-11 翻案实现）。"""
    lo, hi = 0.5, 5.0
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if (inv ** mid).sum() > 1 else (lo, mid)
    p = inv ** ((lo + hi) / 2)
    return p / p.sum()


def crs_power_devig(crs: dict) -> dict:
    """体彩 CRS 31 格 power 去水→sXXsYY 键分布。盘口不足返回 {}（由上层降级）。"""
    cells = {}
    for k, v in (crs or {}).items():
        try:
            o = float(v)
        except (TypeError, ValueError):
            continue
        ck = crs_key(k)
        if ck and o > 1.0:
            cells[ck] = o
    if len(cells) < MIN_CRS_CELLS:
        return {}
    p = _power_devig_probs(np.array([1.0 / o for o in cells.values()]))
    return {k: float(x) for k, x in zip(cells.keys(), p)}


def had_devig(o_h, o_d, o_a) -> list:
    inv = np.array([1 / float(o_h), 1 / float(o_d), 1 / float(o_a)])
    return [float(x) for x in inv / inv.sum()]


def lam_sum_from_ttg(ttg: dict) -> float:
    """E[总进球]：TTG power 去水后期望（确定性无优化·预注册造法）。"""
    ks = sorted((str(k) for k in ttg if str(k) in TTG_KEYS and float(ttg[k]) > 1.0),
                key=float)
    p = _power_devig_probs(np.array([1.0 / float(ttg[k]) for k in ks]))
    return float(sum((TTG_PLUS_VAL if k == "7" else float(k)) * pi for k, pi in zip(ks, p)))


def ttg_boom(ttg: dict) -> float:
    """市场口径 boom：TTG 6+7 档去水概率合计（替代 V3W other 桶 boom 语义）。"""
    ks = sorted((str(k) for k in ttg if str(k) in TTG_KEYS and float(ttg[k]) > 1.0),
                key=float)
    p = _power_devig_probs(np.array([1.0 / float(ttg[k]) for k in ks]))
    return float(sum(pi for k, pi in zip(ks, p) if k in ("6", "7")))


def solve_lamh(lam_sum: float, p_h: float) -> float:
    """40 次二分解 P(H>A)(λh, λsum−λh)=p_h（had_from 聚合·ρ=0·λh∈(0.05, λsum−0.05)）。"""
    lo, hi = 0.05, max(0.06, lam_sum - 0.05)
    for _ in range(40):
        mid = (lo + hi) / 2
        p = sm.had_from(sm.dc_matrix(mid, lam_sum - mid, RHO_ZERO))["h"]
        lo, hi = (mid, hi) if p < p_h else (lo, mid)
    return (lo + hi) / 2
