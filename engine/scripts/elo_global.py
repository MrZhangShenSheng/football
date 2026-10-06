# -*- coding: utf-8 -*-
"""v11 阶段2：Elo 全库自建递推器——26 库赛果（含国家队）全局 Elo，pre 值模式天然无泄漏。

产出 engine/cache/elo_history_{league}_GLOBAL.json（rows[].elo_*_pre·与现有 elo_history 同构，
team_state_on 的 Elo 砖直接可读）。eloratings.net World Football Elo 公式（与现有 2526 档同款）：
  We = 1/(1+10^(-(dr)/400))，dr=R_self−R_opp+HFA
  R' = R + K×G×(W−We)，K=25·G=进球差修正(平/一球差1·两球差1.5·三球+差(11+N)/8)
各库独立 init=1500（跨库对比靠 uefa-champions 等桥库场隐式校准·联赛内精确）。
幂等：重跑全量重建（纯确定性递推·无随机）。开发者 sszhang
用法：python engine/scripts/elo_global.py [--leagues lg1,lg2]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT

LEAGUE_DIR = ROOT / "data" / "02-results" / "league"
CACHE = ROOT / "engine" / "cache"

ELO_INIT = 1500.0
ELO_K = 25.0
ELO_HFA = 65.0        # 与现有 2526 档 meta.hfa 同值


def _goal_mult(hg: int, ag: int) -> float:
    d = abs(hg - ag)
    if d <= 1:
        return 1.0
    if d == 2:
        return 1.5
    return (11 + d) / 8.0


def build_league(lg: str, rows: list[dict]) -> dict:
    """单库递推：按日期序逐场更新·rows[].elo_*_pre 为该场赛前分（as-of 干净）。"""
    ratings: dict[str, float] = {}
    out = []
    for r in sorted(rows, key=lambda x: str(x.get("date", ""))):
        home, away = r.get("home"), r.get("away")
        hg, ag = r.get("hg"), r.get("ag")
        if not home or not away or not isinstance(hg, int) or not isinstance(ag, int):
            continue
        rh = ratings.get(home, ELO_INIT)
        ra = ratings.get(away, ELO_INIT)
        dr = rh - ra + ELO_HFA
        we = 1.0 / (1.0 + 10 ** (-dr / 400.0))
        w = 1.0 if hg > ag else (0.0 if hg < ag else 0.5)
        delta = ELO_K * _goal_mult(hg, ag) * (w - we)
        ratings[home], ratings[away] = rh + delta, ra - delta
        out.append({"date": str(r.get("date", ""))[:10], "home": home, "away": away,
                    "elo_home_pre": rh, "elo_away_pre": ra,
                    "result": "H" if hg > ag else ("A" if hg < ag else "D")})
    return {"league": lg, "hfa": ELO_HFA, "k": ELO_K, "init": ELO_INIT,
            "formula": "eloratings.net World Football Elo (global build v11)",
            "rows": out, "builtAt": "2026-10-06"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", default="")
    args = ap.parse_args()
    if args.leagues:
        leagues = [s.strip() for s in args.leagues.split(",") if s.strip()]
    else:
        leagues = sorted(p.name.replace("_matches.json", "") for p in LEAGUE_DIR.glob("*_matches.json"))
    for lg in leagues:
        p = LEAGUE_DIR / f"{lg}_matches.json"
        if not p.exists():
            print(f"[elo-global] 跳过 {lg}（无库）")
            continue
        raw = json.loads(p.read_text(encoding="utf-8"))
        rows = raw.get("matches", raw) if isinstance(raw, dict) else raw
        doc = build_league(lg, rows)
        out = CACHE / f"elo_history_{lg}_GLOBAL.json"
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[elo-global] {lg}: {len(doc['rows'])} 场递推 → {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
