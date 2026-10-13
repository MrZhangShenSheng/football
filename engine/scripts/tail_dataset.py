# engine/scripts/tail_dataset.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tail_dataset.py — 右尾肥尾场次数据集构建器（spec docs/2026-10-13-tail-model-design.html）。

防泄漏三道闸：as_of=开赛前一日；league/hst 时间线 lag=2 天（elo 赛前值 lag=0）；
特征市场价只用 crsOpen——ttg/had 封盘只作判据对手盘，禁入特征。
判据预注册 engine/cache/tail-model-prereg.json（跑前冻结·铁律14②）。
开发者 sszhang"""
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import market_lambda as mkl

TG_BIN_MAX = 7                 # tgBin = min(h+a, 7)，7 档=7+球
MIN_POOL_ITEMS = 20            # 与 backtest_crs_batch 同源
LEAK_LAG_DAYS = 2              # league/hst 时间线滞后（铁律14①语义）
DECISION_DAY_OFFSET = -1       # 决策时点=开赛前一日
FEATURE_VERSION = "tail-v1"    # 特征清单版本（meta.json 冻结用）


def label_skeleton(score: str) -> dict:
    """比分 → 骨架三轴标签。cleanSheet：0=客零封 1=主零封 2=无（0:0 归无·口径冻结 prereg）。
    开发者 sszhang"""
    h, a = (int(x) for x in score.split(":"))
    tg = h + a
    if h > a:
        direction, cs = 0, (1 if a == 0 else 2)
    elif h == a:
        direction, cs = 1, 2
    else:
        direction, cs = 2, (0 if h == 0 else 2)
    return {"tg": tg, "tgBin": min(tg, TG_BIN_MAX), "direction": direction, "cleanSheet": cs}


def market_anchor_features(row: dict) -> dict:
    """crsOpen 全套派生 11 维（唯一市场特征源·开盘口径=决策时点合法）。

    三向聚合含 other 归侧；tg 矩/肥尾质量只计精确键（other 不摊）；熵=全分布含 other 原子（口径冻结）。"""
    pool = row.get("crsOpen") or {}
    if len(pool) < MIN_POOL_ITEMS:
        return {}
    p = mkl.crs_power_devig(pool)
    if not p:
        return {}
    exact, p_home, p_draw, p_away, p_other = [], 0.0, 0.0, 0.0, 0.0
    ent = 0.0
    for k, v in p.items():
        if k in ("s1sh", "s1sd", "s1sa"):
            p_other += v
            if k == "s1sh":
                p_home += v
            elif k == "s1sd":
                p_draw += v
            else:
                p_away += v
        elif k.startswith("s") and len(k) == 6 and k[3] == "s":
            h, a = int(k[1:3]), int(k[4:6])
            exact.append((h, a, v))
            if h > a:
                p_home += v
            elif h == a:
                p_draw += v
            else:
                p_away += v
        if v > 0:
            ent -= v * math.log(v)
    if not exact:
        return {}
    e_tg = sum((h + a) * v for h, a, v in exact)
    p_ge4 = sum(v for h, a, v in exact if h + a >= 4)
    p_ge5 = sum(v for h, a, v in exact if h + a >= 5)
    p_diff3 = sum(v for h, a, v in exact if abs(h - a) >= 3)
    p_away_big = sum(v for h, a, v in exact if h == 0 and a >= 3)
    return {
        "mkt_e_tg_open": e_tg,
        "mkt_p_tg_ge4_open": p_ge4,
        "mkt_p_tg_ge5_open": p_ge5,
        "mkt_p_diff_ge3_open": p_diff3,
        "mkt_p_away_cs_big_open": p_away_big,
        "mkt_p_home_open": p_home,
        "mkt_p_draw_open": p_draw,
        "mkt_p_away_open": p_away,
        "mkt_p_max_side_open": max(p_home, p_draw, p_away),
        "mkt_entropy_open": ent,
        "mkt_other_mass_open": p_other,
    }
