#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""自建 Elo 序贯引擎：从 fd 历史比分重建每场赛前 Elo。

绕开 clubelo api 被墙 + 映射全 null + 快照 look-ahead 三重障碍：
纯计算、零外部依赖、赛前 Elo = 更新前的值（序贯天然 walk-forward 严格）。

公式（eloratings.net World Football Elo 标准）：
  E_home = 1/(1+10^((elo_away - (elo_home+HFA))/400))   主队加主场优势
  赛后 Elo += K * G * (W - E)
  G = 进球差乘数（0-1球:1, 2球:1.5, 3球:1.75, ≥4球:(11+diff)/8）
  赛前 Elo = 该场更新前的值（用于预测，零 look-ahead）

用法：python elo_build.py [season]   默认 2526，全部 fd 联赛
"""
import json
import sys
from datetime import date

from common import log, ROOT
from dc_fit import load_matches

CACHE_DIR = ROOT / "engine" / "cache"
HFA = 65.0      # 主场优势（Elo 点）
K = 25.0        # 联赛 K 因子
INIT = 1500.0   # 赛季初统一初值
LEAGUES = [
    "england-premier", "spain-laliga", "germany-bundesliga", "italy-serie-a",
    "france-ligue1", "france-ligue2", "netherlands-eredivisie", "portugal-primeira",
]


def goal_mult(diff: int) -> float:
    if diff <= 1:
        return 1.0
    if diff == 2:
        return 1.5
    if diff == 3:
        return 1.75
    return (11.0 + diff) / 8.0


def build_season(league: str, season: str) -> list[dict]:
    matches = load_matches(league, [season])
    if not matches:
        return []
    elo = {}  # team -> rating，懒初始化为 INIT
    rows = []
    for m in matches:
        h, a = m["home"], m["away"]
        eh = elo.setdefault(h, INIT)
        ea = elo.setdefault(a, INIT)
        e_home = 1.0 / (1.0 + 10.0 ** ((ea - (eh + HFA)) / 400.0))
        w_home = 1.0 if m["hg"] > m["ag"] else (0.5 if m["hg"] == m["ag"] else 0.0)
        g = goal_mult(abs(m["hg"] - m["ag"]))
        # 赛前 Elo（更新前）——walk-forward 严格用此值
        rows.append({
            "date": m["date"].isoformat(), "home": h, "away": a,
            "elo_home_pre": round(eh, 1), "elo_away_pre": round(ea, 1),
            "elo_diff": round(eh - ea, 1),
            "result": "H" if w_home == 1.0 else ("D" if w_home == 0.5 else "A"),
        })
        delta = K * g * (w_home - e_home)
        elo[h] = eh + delta
        elo[a] = ea - delta
    return rows


def main() -> None:
    season = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "2526"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for lg in LEAGUES:
        rows = build_season(lg, season)
        if not rows:
            log("elo", f"{lg} {season}: 无数据，跳过")
            continue
        dest = CACHE_DIR / f"elo_history_{lg}_{season}.json"
        payload = {
            "league": lg, "season": season, "hfa": HFA, "k": K, "init": INIT,
            "formula": "eloratings.net World Football Elo", "matches": len(rows),
            "rows": rows, "builtAt": date.today().isoformat(),
        }
        dest.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        first, last = rows[0], rows[-1]
        log("elo", f"{lg} {season}: {len(rows)}场 → {dest.name} "
                  f"首场diff={first['elo_diff']} 末场diff={last['elo_diff']}")
    log("elo", f"✅ Elo 历史重建完成（{season}）")


if __name__ == "__main__":
    main()
