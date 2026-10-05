# -*- coding: utf-8 -*-
"""实力链步骤⑤：Poisson×DC修正 比分矩阵（体彩CRS 39键）+ TTG/HAD 出口。开发者 sszhang。

键口径= sporttery_fetch.CRS_KEYS 逐键对齐：36具体格 's{i:02d}s{j:02d}'（i,j∈0..5·零填充
6字符）+ 三其他 's1sh'/'s1sd'/'s1sa'（胜/平/负其他）。计划文"31格"=投注页展示选项数，
API/池键实测 39（2026-10-05 Task9 现场裁定对齐 CRS_KEYS；brief 原 's{h}s{a}' 单字符键
配双字符切片为系统性解析 bug，随零填充键格式一并修正——h=int(k[1:3]), a=int(k[4:6])）。
"""
from __future__ import annotations
import math

MAX_GOALS = 6   # 预注册舱 hyperparamsFixed.maxGoals（具体格 0..5）
CRS_OTHER_HAD = {"s1sh": "h", "s1sd": "d", "s1sa": "a"}   # 体彩三其他键 → HAD 三向
TTG_BUCKETS = 9  # s0..s8（s8=8+开桶·三其他全部归此）


def _pois(lam: float, k: int) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _tau(h: int, a: int, lam_h: float, lam_a: float, rho: float) -> float:
    """Dixon-Coles (1997) 低比分修正：仅 (0,0)(0,1)(1,0)(1,1) 非平凡。"""
    if h == 0 and a == 0:
        return 1 - lam_h * lam_a * rho
    if h == 0 and a == 1:
        return 1 + lam_h * rho
    if h == 1 and a == 0:
        return 1 + lam_a * rho
    if h == 1 and a == 1:
        return 1 - rho
    return 1.0


def dc_matrix(lam_h: float, lam_a: float, rho: float, max_goals: int = MAX_GOALS) -> dict:
    """39键：'s{i:02d}s{j:02d}'（i,j∈0..5）+ 's1sh'/'s1sd'/'s1sa'（>5球桶）。ρ 已含在 tau。"""
    m = {}
    for h in range(max_goals):
        for a in range(max_goals):
            m[f"s{h:02d}s{a:02d}"] = _pois(lam_h, h) * _pois(lam_a, a) * _tau(h, a, lam_h, lam_a, rho)
    specific = sum(m.values())
    overflow = max(0.0, 1.0 - specific)
    # 溢出概率按主胜/平/客胜的尾部分配（>5球三向·用λ比粗分）
    tail_h = _tail(lam_h, lam_a, max_goals, "h")
    tail_d = _tail(lam_h, lam_a, max_goals, "d")
    tail_a = _tail(lam_h, lam_a, max_goals, "a")
    tot = tail_h + tail_d + tail_a or 1.0
    m["s1sh"], m["s1sd"], m["s1sa"] = overflow * tail_h / tot, overflow * tail_d / tot, overflow * tail_a / tot
    return m


def _tail(lam_h: float, lam_a: float, g: int, side: str) -> float:
    """>g 球溢出区三向质量（并集 {h≥g}∪{a≥g}·扫至 g+12·评分平局极罕见≈e-6级）。"""
    tot_h = tot_d = tot_a = 0.0
    for h in range(0, g + 12):
        for a in range(0, g + 12):
            if h < g and a < g:
                continue
            p = _pois(lam_h, h) * _pois(lam_a, a)
            if h > a:
                tot_h += p
            elif h == a:
                tot_d += p
            else:
                tot_a += p
    return {"h": tot_h, "d": tot_d, "a": tot_a}[side]


def ttg_from(matrix: dict) -> dict:
    """总进球 9 档 s0..s8（行和·具体格 tg=min(h+a,8)·三其他=高进球区归 s8 开桶）。"""
    out = {f"s{i}": 0.0 for i in range(TTG_BUCKETS)}
    for k, v in matrix.items():
        if k[1:3].isdigit() and k[4:6].isdigit():          # 具体 s{i:02d}s{j:02d}
            tg = int(k[1:3]) + int(k[4:6])
            out[f"s{min(tg, TTG_BUCKETS - 1)}"] += v
        else:                                               # 胜/平/负其他 → 8+ 开桶
            out[f"s{TTG_BUCKETS - 1}"] += v
    return out


def had_from(matrix: dict) -> dict:
    """胜平负三向（三区和·三其他并入对应向）。"""
    out = {"h": 0.0, "d": 0.0, "a": 0.0}
    for k, v in matrix.items():
        if k in CRS_OTHER_HAD:                              # s1sh/s1sd/s1sa
            out[CRS_OTHER_HAD[k]] += v
        else:
            h, a = int(k[1:3]), int(k[4:6])
            out["h" if h > a else "d" if h == a else "a"] += v
    return out
