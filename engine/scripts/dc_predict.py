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


def hafu_approx(lh: float, la: float, s: float = HALF_LAMBDA_SHARE, rho_half: float = 0.0) -> dict[str, float]:
    """半全场 9 组合：半场 λ=全场×s、下半场守恒；rho_half 仅修半场段（dc_tau），
    下半场为构造量不修（低分偏差由 FT 三向重标定兜底，设计 §6）。

    P(HT=x, 2nd=u) 独立 → FT=(x+u, y+v)；HT/FT 各自三向符号组合成 9 键（hh..aa）。
    默认参数 = 旧版行为（s=0.45、rho_half=0 → tau 恒 1，零破坏验收）。
    """
    lh1, la1 = lh * s, la * s
    lh2, la2 = lh - lh1, la - la1

    def pois(k: int, lam: float) -> float:
        return math.exp(-lam) * lam ** k / math.factorial(k)

    out = {a + b: 0.0 for a in "hda" for b in "hda"}
    for x in SCORE_RANGE:
        for y in SCORE_RANGE:
            p1 = pois(x, lh1) * pois(y, la1) * max(dc_tau(x, y, lh1, la1, rho_half), 1e-12)
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


TEMPERATURE = CACHE_DIR / "temperature.json"


def load_half_params(league: str | None = None) -> tuple[float, float]:
    """(s, rho_half)：联赛级命中用联赛；miss（日韩瑞沙）→全局；无文件→(0.45, 0) 现状行为。"""
    if not (CACHE_DIR / "half_share.json").exists():
        return HALF_LAMBDA_SHARE, 0.0
    data = json.loads((CACHE_DIR / "half_share.json").read_text(encoding="utf-8"))
    ent = (data.get("leagues") or {}).get(league) if league else None
    if not ent:
        ent = data.get("global") or {}
    return float(ent.get("s", HALF_LAMBDA_SHARE)), float(ent.get("rho_half", 0.0))


def load_temperature() -> dict:
    """{pool: T}；缺文件/未启用池 → 1.0（现状行为）。"""
    defaults = {"crs": 1.0, "ttg": 1.0, "hafu": 1.0}
    if not TEMPERATURE.exists():
        return defaults
    data = json.loads(TEMPERATURE.read_text(encoding="utf-8"))
    loaded = {k: (float(v.get("T", 1.0)) if v.get("enabled") else 1.0)
              for k, v in (data.get("pools") or {}).items()}
    defaults.update(loaded)
    return defaults


def temper(probs: list[float], t: float) -> list[float]:
    """池级温度：p_T ∝ p^(1/T)（Guo 2017 概率空间变体）；t=1 恒等。"""
    if abs(t - 1.0) < 1e-12:
        return list(probs)
    z = [max(p, 1e-12) ** (1.0 / t) for p in probs]
    s = sum(z)
    return [v / s for v in z]


def reweight_matrix(p: np.ndarray, target: list[float]) -> np.ndarray:
    """三域（主胜 i>j / 平局 i=j / 客胜 i<j）一步精确重加权对齐 target 三向。

    IPF structure conservation 特例：域内交叉乘积比不变（保 ρ 低分形状/TTG 形状）。
    target 须归一（|Σ-1|<1e-3，护栏在 CLI 层）。
    """
    out = p.copy()
    for d, cond in enumerate((lambda i, j: i > j, lambda i, j: i == j, lambda i, j: i < j)):
        mask = np.array([[cond(i, j) for j in range(7)] for i in range(7)])
        cur = float(p[mask].sum())
        if cur > 1e-12:
            out[mask] = p[mask] * (target[d] / cur)
    return out / out.sum()


def reweight_hafu(hafu: dict[str, float], target: list[float]) -> dict[str, float]:
    """HAFU 9 键条件分解：保持 P(HT|FT)，第二字母（FT）三列对齐 target。
    契约：某 FT 列整列缺失（cur≤1e-12）时该列静默置 0、概率摊给其余列。
    """
    out = dict(hafu)
    for d, ft in enumerate("hda"):
        keys = [k for k in out if k[1] == ft]
        cur = sum(out[k] for k in keys)
        if cur > 1e-12:
            for k in keys:
                out[k] = out[k] * (target[d] / cur)
    t = sum(out.values())
    return {k: v / t for k, v in out.items()}


def fuse(p_dc: list[float], p_mkt: list[float], a: float, b: float,
         elo_diff: float | None = None, c: float = 0.0) -> list[float]:
    """对数意见池融合：p ∝ p_dc^a' · p_mkt^b'（a',b' 归一化，保证一致不变性）。

    可选 Elo 修正（elo_diff/c 非 None/0）：在 log 空间给主胜加偏置、客胜减等量偏置、
    平局不修——主队实力强(elo_diff>0)抬主胜压客胜。softmax 归一保证三向和=1。
    """
    s = a + b
    a, b = a / s, b / s
    z = [a * math.log(max(p, 1e-12)) + b * math.log(max(m, 1e-12)) for p, m in zip(p_dc, p_mkt)]
    if elo_diff is not None and c != 0.0:
        adj = c * (elo_diff / 100.0)
        z[0] += adj
        z[2] -= adj
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
    s_lg, rho_h = load_half_params(league)

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
        "hafu": {k: round(v, 4) for k, v in sorted(hafu_approx(lh, la, s_lg, rho_h).items())},
        "halfParams": {"league": league, "s": s_lg, "rhoHalf": rho_h},
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

    # --adjust 三域重标定（设计 §5）：target 三向 → 矩阵/HAFU 重加权 + 池级温度
    adjust = None
    if "--adjust" in sys.argv:
        i = sys.argv.index("--adjust")
        adjust = [float(x) for x in sys.argv[i + 1].split(",")]
        if len(adjust) != 3:
            log("dc_predict", f"--adjust 须三项 h,d,a，收到 {len(adjust)} 项，拒绝")
            return
        if abs(sum(adjust) - 1.0) > 1e-3:
            log("dc_predict", f"--adjust 三向和={sum(adjust):.4f} ≠1，拒绝（防 skill 流程手算失误）")
            return
        adjust = [max(v, 0.01) for v in adjust]
        if min(adjust) == 0.01:
            log("dc_predict", "warn: target 有项 <0.01 被 clamp（修正系数叠乘压塌某域）")
    tpool = load_temperature()
    result["temperature"] = tpool
    if adjust:
        pm = reweight_matrix(p, adjust)
        # crs 池温度（设计 §5：输出链全部用温度后概率；与 boldplay.py:157 消费端一致）
        pm_t = np.array(temper([float(pm[i, j]) for i in range(7) for j in range(7)], tpool["crs"])).reshape(7, 7)
        hf = reweight_hafu(hafu_approx(lh, la, s_lg, rho_h), adjust)
        hft = temper(list(hf.values()), tpool["hafu"])
        result["adjusted"] = {
            "source": "fused+factor-adjusted" if market else "raw+manual-adjusted",
            "target": adjust,
            "p_three": [round(v, 4) for v in adjust],
            "top_scores": [{"score": f"{i}-{j}", "prob": round(float(pm_t[i, j]), 4)}
                           for i, j in sorted(((i, j) for i in range(7) for j in range(7)),
                                               key=lambda t: -pm_t[t])[:5]],
            "ttg": [round(v, 4) for v in temper(ttg_dist(pm), tpool["ttg"])],
            "hafu": {k: round(v, 4) for k, v in sorted(zip(hf.keys(), hft))},
        }

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
