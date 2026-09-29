# -*- coding: utf-8 -*-
"""对比两个脚本的预测结果"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path("engine/scripts/research")))

# 原始脚本
from score_family_parlay_v2 import build as build_orig
packs_orig = build_orig()

# 我的脚本
from ticket_v4_full_backtest import build_model_packs
packs_new = build_model_packs()

print("=== 对比同一场比赛 ===")
for p1 in packs_orig[:5]:
    for p2 in packs_new:
        if p1["date"] == p2["date"] and p1["actual"] == p2["actual"]:
            print(f"日期: {p1['date']}, 实际: {p1['actual']}")
            print(f"  原始 crs1: {p1['crs1']}, crs2: {p1['crs2']}")
            print(f"  新版 top1: {p2['model_sorted'][0][0]}, top2: {p2['model_sorted'][1][0]}")
            print(f"  原始 gap: {p1['gap']:.4f}")
            print(f"  新版 gap: {p2['gap']:.4f}")
            print()
            break
