#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""Dixon-Coles 预测：读拟合参数输出 7x7 比分概率矩阵 + 三向概率，可选与市场概率 logit 融合。

用法：
  python dc_predict.py spain-laliga "Rayo Vallecano" "Alaves"
  python dc_predict.py spain-laliga Rayo Alaves --market 2.05,3.4,3.9   # Pinnacle 三向
输出：stdout JSON（比分矩阵、三向概率、TOP 比分、融合概率、总进球分布 ttg、半全场近似 hafu ★v4.5）
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

from common import log, ROOT

CACHE_DIR = ROOT / "engine" / "cache"
FUSION = CACHE_DIR / "fusion.json"


def dc_tau(x: int, y: int, lh: float, la: float, rho: float) -> float:
    """Dixon-Coles 1997 原始定义：rho<0 时上调 0-0/1-1、下调 1-0/0-1。"""
    if x == 0 and y == 0:
        return 1.0 - lh * la * rho
    if x == 0 and y == 1:
        return 1.0 + lh * rho
    if x == 1 and y == 0:
        return 1.0 + la * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def score_matrix(lh: float, la: float, rho: float) -> np.ndarray:
    p = np.zeros((7, 7))
    for x in range(7):
        for y in range(7):
            pm = math.exp(-lh) * lh ** x / math.factorial(x) * math.exp(-la) * la ** y / math.factorial(y)
            p[x, y] = max(pm * dc_tau(x, y, lh, la, rho), 1e-12)
    p /= p.sum()
    return p


def devig(odds: list[float]) -> list[float]:
    inv = [1.0 / o for o in odds]
    s = sum(inv)
    return [i / s for i in inv]


def logit(p: float) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


def sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


HALF_LAMBDA_SHARE = 0.45  # 半场进球占全场期望比例（足球统计常识值 0.44~0.46）
SCORE_RANGE = range(6)    # 半场/下半场单边枚举 0~5 球（概率截断忽略）


def ttg_dist(p: np.ndarray) -> list[float]:
    """7x7 比分矩阵 → 竞彩 ttg 8 档分布（0~6 各一档，≥7 合并末档）。"""
    dist = [0.0] * 8
    for i in range(7):
        for j in range(7):
            dist[min(i + j, 7)] += float(p[i, j])
    return dist


def hafu_approx(lh: float, la: float) -> dict[str, float]:
    """半全场 9 组合近似：半场 λ=全场×0.45、下半场 ×0.55，两段独立泊松精确枚举聚合。

    P(HT=x, 2nd=u) 独立 → FT=(x+u, y+v)；HT/FT 各自三向符号组合成 9 键（hh..aa）。
    """
    lh1, la1 = lh * HALF_LAMBDA_SHARE, la * HALF_LAMBDA_SHARE
    lh2, la2 = lh - lh1, la - la1

    def pois(k: int, lam: float) -> float:
        return math.exp(-lam) * lam ** k / math.factorial(k)

    out = {a + b: 0.0 for a in "hda" for b in "hda"}
    for x in SCORE_RANGE:
        for y in SCORE_RANGE:
            p1 = pois(x, lh1) * pois(y, la1)
            if p1 < 1e-12:
                continue
            ht = "h" if x > y else ("d" if x == y else "a")
            for u in SCORE_RANGE:
                for v in SCORE_RANGE:
                    p2 = pois(u, lh2) * pois(v, la2)
                    fx, fy = x + u, y + v
                    ft = "h" if fx > fy else ("d" if fx == fy else "a")
                    out[ht + ft] += p1 * p2
    total = sum(out.values()) or 1.0
    return {k: v / total for k, v in out.items()}


def fuse(p_dc: list[float], p_mkt: list[float], a: float, b: float) -> list[float]:
    """对数意见池融合：p ∝ p_dc^a' · p_mkt^b'（a',b' 归一化，保证一致不变性）。"""
    s = a + b
    a, b = a / s, b / s
    z = [a * math.log(max(p, 1e-12)) + b * math.log(max(m, 1e-12)) for p, m in zip(p_dc, p_mkt)]
    m = max(z)
    e = [math.exp(v - m) for v in z]
    t = sum(e)
    return [v / t for v in e]


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    market = None
    if "--market" in sys.argv:
        i = sys.argv.index("--market")
        market = [float(x) for x in sys.argv[i + 1].split(",")]
    if len(args) < 3:
        log("dc_predict", '用法: python dc_predict.py <league> "<home>" "<away>" [--market h,d,a]')
        return
    league, home, away = args[0], args[1], args[2]

    dc_path = CACHE_DIR / f"{league}_dc.json"
    if not dc_path.exists():
        log("dc_predict", f"无 {dc_path.name}（先跑 dc_fit.py）")
        return
    dc = json.loads(dc_path.read_text(encoding="utf-8"))
    # 队名宽松匹配：fd 命名大小写/缩写
    def find(name):
        for t in dc["teams"]:
            if t.lower() == name.lower() or t.lower().startswith(name.lower()[:6]):
                return t
        return None
    h_key, a_key = find(home), find(away)
    if not h_key or not a_key:
        log("dc_predict", f"球队未在拟合参数中：{home}={'找到' if h_key else h_key}, {away}={a_key}")
        log("dc_predict", "可用: " + ", ".join(sorted(dc["teams"])[:12]) + " ...")
        return

    th, ta = dc["teams"][h_key], dc["teams"][a_key]
    lh = math.exp(th["attack"] + ta["defense"] + dc["homeAdv"])
    la = math.exp(ta["attack"] + th["defense"])
    p = score_matrix(lh, la, dc["rho"])

    p_home = float(sum(p[i, j] for i in range(7) for j in range(7) if i > j))
    p_draw = float(np.trace(p))
    p_away = 1.0 - p_home - p_draw
    three = [p_home, p_draw, p_away]

    result = {
        "league": league, "home": h_key, "away": a_key,
        "lambdaHome": round(lh, 3), "lambdaAway": round(la, 3),
        "p_dc": [round(v, 4) for v in three],
        "top_scores": [],
        "ttg": [round(v, 4) for v in ttg_dist(p)],
        "hafu": {k: round(v, 4) for k, v in sorted(hafu_approx(lh, la).items())},
        "market": None, "p_fused": None,
        "fusion": {"a": None, "b": None, "source": "engine/cache/fusion.json"},
    }
    flat = [(f"{i}-{j}", float(p[i, j])) for i in range(7) for j in range(7)]
    for score, prob in sorted(flat, key=lambda kv: -kv[1])[:5]:
        result["top_scores"].append({"score": score, "prob": round(prob, 4)})

    if market:
        p_mkt = devig(market)
        fus = {"a": 0.4, "b": 1.0}
        if FUSION.exists():
            fus = json.loads(FUSION.read_text(encoding="utf-8"))
        p_f = fuse(three, p_mkt, fus["a"], fus["b"])
        result["market"] = [round(v, 4) for v in p_mkt]
        result["p_fused"] = [round(v, 4) for v in p_f]
        result["fusion"] = {"a": fus["a"], "b": fus["b"]}
        diffs = [round(f - m, 4) for f, m in zip(p_f, p_mkt)]
        result["fusion_diff_pp"] = [round(d * 100, 1) for d in diffs]

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
