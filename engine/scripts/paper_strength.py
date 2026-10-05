# engine/scripts/paper_strength.py
# -*- coding: utf-8 -*-
"""实力链步骤①②③：纸面实力三层合成 → 三步去水 → 实力对比。开发者 sszhang。
超参固定（预注册舱）：W_XG=0.4 W_DC=0.4 W_ELO=0.2 k=20 N=10 —— 不进调参空间。"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

W_XG, W_DC, W_ELO = 0.4, 0.4, 0.2
ELO_MID, ELO_SCALE = 1500.0, 100.0     # 自建Elo init=1500；z=(elo-1500)/100

def elo_z(st: dict) -> float:
    return (st["elo"] - ELO_MID) / ELO_SCALE if st.get("elo") is not None else 0.0

def _xg_z(st: dict, league_env_att: float = 1.35, league_env_def: float = 1.35) -> tuple[float, float]:
    """xG 相对联赛环境标准化（环境均值缺省1.35球/队·场——仅作z基线，②c 再做联赛精校）。"""
    if st.get("xg_att") is None:
        return 0.0, 0.0
    return (st["xg_att"] - league_env_att), (league_env_def - st["xg_def"])  # def: 越小越强→取反

def raw_strength(st: dict) -> tuple[float, float]:
    """三层合成 att/def 双分。att=攻力（正=强），def=防力（正=强·注意与DC def 反号约定）。"""
    xa, xd = _xg_z(st)
    ez = elo_z(st)
    att = W_XG * xa + W_DC * st["dc_att"] + W_ELO * ez
    df = W_XG * xd + W_DC * (-st["dc_def"]) + W_ELO * ez     # DC.def 负=强 → 翻成正号
    return att, df
