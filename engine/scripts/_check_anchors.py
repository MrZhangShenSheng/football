# -*- coding: utf-8 -*-
"""临时检查：法甲/法乙当季缓存中目标场次的 Pinnacle 锚。"""
import json
from pathlib import Path

cache = Path(__file__).parent / ".." / "cache"
for f in ("france-ligue1_2627", "france-ligue2_2627"):
    d = json.loads((cache / f"odds_{f}.json").read_text(encoding="utf-8"))
    print(f"--- {f}: {len(d['matches'])} 场 ---")
    for m in d["matches"]:
        names = (m["home"], m["away"])
        if any(t in names for t in ("Marseille", "Strasbourg", "Dunkerque", "Montpellier")):
            print(" ", m["home"], "vs", m["away"],
                  "| 开盘", m.get("pin_open_h"), m.get("pin_open_d"), m.get("pin_open_a"),
                  "| 收盘", m.get("pin_h"), m.get("pin_d"), m.get("pin_a"))
