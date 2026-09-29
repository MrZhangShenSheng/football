# -*- coding: utf-8 -*-
"""检查数据范围"""
import json
from pathlib import Path
from collections import Counter

hist_dir = Path("engine/cache/hist_odds")
dates = []
total_matches = 0
for p in sorted(hist_dir.glob("*.json")):
    d = json.loads(p.read_text(encoding="utf-8"))
    for m in d.get("matches", []):
        dt = str(m.get("date") or "")[:10]
        if dt:
            dates.append(dt)
            total_matches += 1

dates_unique = sorted(set(dates))
print(f"hist_odds 数据范围: {dates_unique[0]} ~ {dates_unique[-1]}")
print(f"总天数: {len(dates_unique)}")
print(f"总场次: {total_matches}")

# 按月统计
months = Counter(d[:7] for d in dates)
print(f"\n按月分布:")
for m in sorted(months):
    print(f"  {m}: {months[m]} 场")
