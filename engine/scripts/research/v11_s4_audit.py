# -*- coding: utf-8 -*-
"""S4 审计补跑：验证段全网格——排除'拟合段选出参数碰巧在验证段正'的选择偏差嫌疑。
如果验证段也有'had_hot 越高越好'的系统性趋势 → 信号真实；如果只有选出那组正 → 碰巧。
开发者 sszhang"""
import sys, json
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import strength_loaders as sl
from v11_s4_recalib import run_v3w, LEAGUES, FIT_WINDOW, VAL_WINDOW

ctx = sl.build_ctx(LEAGUES)
z2i = sl.zh_to_id()
memo = {}

print("══ 验证段全网格（当前段 2025-10~2026-09）══")
print(f"{'boom':>6s} {'had_hot':>8s} {'验证段ROI':>10s} {'交易日':>6s}")
for boom in (0.05, 0.08, 0.10, 0.12, 0.15):
    for had in (1.2, 1.3, 1.4, 1.5):
        r = run_v3w(VAL_WINDOW, ctx, z2i, memo, boom_thr=boom, had_hot=had)
        print(f"{boom:6.2f} {had:8.1f} {r['roi']*100:+9.1f}% {r['days']:6d}")
