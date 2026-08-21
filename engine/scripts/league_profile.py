#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""联赛画像：从 fd 缓存计算积分榜 + 联赛统计，输出 data/00-leagues/{league}.json。

双源分工：
- 联赛统计（场均进球/主胜率/冷门率/TOP比分）：fd 本地计算（统计不怕延迟）
- 积分榜：fd 推导为兜底；ESPN 实时采集由 Claude 预测时 WebFetch 合并
  （standings 被外部更新后 freshness.standingsSource 标注，roundsPlayed 不一致时 stale=true）

用法：
  python league_profile.py spain-laliga 2526      # 指定联赛+赛季
  python league_profile.py --all                  # 全部有缓存的联赛
"""
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from common import log, ROOT

CACHE_DIR = ROOT / "engine" / "cache"
OUT_DIR = ROOT / "data" / "00-leagues"
# 降级区规模（多数联赛最后3名；德甲2+附加赛等特殊规则后续按需配置）
RELEGATION_SLOTS = {"germany-bundesliga": 2}
EUROPE_SLOTS = 4
UPSET_ODDS_THRESHOLD = 2.5  # 收盘赔率高于此的球队获胜 = 冷门


def load_season_matches(league: str, seasons: list[str]) -> list[dict]:
    matches = []
    for season in seasons:
        p = CACHE_DIR / f"odds_{league}_{season}.json"
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        for m in data.get("matches", []):
            if m.get("fthg") is None:
                continue
            matches.append(m)
    return matches


def build(league: str, seasons: list[str]) -> dict:
    matches = load_season_matches(league, seasons)
    if not matches:
        return {}
    season_label = f"{seasons[0][:2]}{seasons[0][2:]}-{int(seasons[0][:2]) + 1}{(int(seasons[0][:2]) + 1) % 100:02d}"
    latest_season = seasons[-1]

    # —— 积分榜推导（fd 兜底版）——
    stats = defaultdict(lambda: {"played": 0, "won": 0, "drawn": 0, "lost": 0,
                                 "gf": 0, "ga": 0, "pts": 0,
                                 "home": {"w": 0, "d": 0, "l": 0}, "away": {"w": 0, "d": 0, "l": 0},
                                 "form": []})
    total_goals = 0
    home_wins = draws = away_wins = upsets = rated = 0
    score_counter = defaultdict(int)
    for m in matches:
        hg, ag = int(m["fthg"]), int(m["ftag"])
        h, a = m["home"], m["away"]
        total_goals += hg + ag
        score_counter[f"{min(hg, 10)}-{min(ag, 10)}"] += 1
        res = 0 if hg > ag else (1 if hg == ag else 2)
        if res == 0:
            home_wins += 1
        elif res == 1:
            draws += 1
        else:
            away_wins += 1
        # 冷门：高赔方获胜
        try:
            ph = float(m.get("pin_h") or 0)
            pa = float(m.get("pin_a") or 0)
            if ph > 0 and pa > 0:
                rated += 1
                if (res == 0 and ph > UPSET_ODDS_THRESHOLD) or (res == 2 and pa > UPSET_ODDS_THRESHOLD):
                    upsets += 1
        except ValueError:
            pass
        for team, gf, ga, side in ((h, hg, ag, "home"), (a, ag, hg, "away")):
            s = stats[team]
            s["played"] += 1
            s["gf"] += gf
            s["ga"] += ga
            if (gf > ga and side == "home") or (gf > ga and side == "away"):
                s["won"] += 1
                s["pts"] += 3
                s[side]["w"] += 1
                s["form"].append("W")
            elif gf == ga:
                s["drawn"] += 1
                s["pts"] += 1
                s[side]["d"] += 1
                s["form"].append("D")
            else:
                s["lost"] += 1
                s[side]["l"] += 1
                s["form"].append("L")
    n = len(matches)
    standings = []
    for i, (team, s) in enumerate(sorted(stats.items(), key=lambda kv: (-kv[1]["pts"], -(kv[1]["gf"] - kv[1]["ga"]))), 1):
        standings.append({
            "pos": i, "team": team,
            "played": s["played"], "won": s["won"], "drawn": s["drawn"], "lost": s["lost"],
            "gf": s["gf"], "ga": s["ga"], "gd": s["gf"] - s["ga"], "pts": s["pts"],
            "form": "".join(s["form"][-5:]),
            "homeRecord": s["home"], "awayRecord": s["away"],
        })
    top_scores = sorted(score_counter.items(), key=lambda kv: -kv[1])[:3]
    rel_slots = RELEGATION_SLOTS.get(league, 3)

    # —— DC 参数引用（若有）——
    dc_params = {}
    dc_path = CACHE_DIR / f"{league}_dc.json"
    if dc_path.exists():
        dc = json.loads(dc_path.read_text(encoding="utf-8"))
        dc_params = {"homeAdv": dc.get("homeAdv"), "rho": dc.get("rho"), "xi": dc.get("xi")}

    return {
        "league": league,
        "season": f"20{latest_season[:2]}-{int(latest_season[:2]) + 1}",
        "generatedAt": date.today().isoformat(),
        "roundsPlayed": max((s["played"] for s in standings), default=0),
        "standings": standings,
        "leagueStats": {
            "avgGoals": round(total_goals / n, 3),
            "homeWinRate": round(home_wins / n, 3),
            "drawRate": round(draws / n, 3),
            "upsetRate": round(upsets / rated, 3) if rated else None,
            "topScores": [{"score": s, "count": c} for s, c in top_scores],
            "computedFrom": "fd", "matchesUsed": n, "seasonsMerged": seasons,
        },
        "context": {
            "titleRace": ({"leader": standings[0]["team"], "gap": standings[0]["pts"] - standings[1]["pts"]}
                          if len(standings) >= 2 else None),
            "relegationZone": [s["pos"] for s in standings[-rel_slots:]],
            "europeZone": list(range(1, EUROPE_SLOTS + 1)),
        },
        "dcParams": dc_params,
        "freshness": {"standingsSource": "fd-derived", "stale": False,
                      "note": "Claude 预测时 WebFetch ESPN 实时积分榜覆盖 standings 并改 standingsSource=espn"},
    }


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if "--all" in sys.argv or not args:
        by_league: dict[str, list[str]] = defaultdict(list)
        for p in CACHE_DIR.glob("odds_*_*.json"):
            parts = p.stem.split("_")  # odds_{league}_{season}
            if len(parts) == 3:
                by_league[parts[1]].append(parts[2])
        targets = [(lg, sorted(ss)) for lg, ss in sorted(by_league.items())]
    else:
        league = args[0]
        seasons = args[1].split(",") if len(args) > 1 else ["2526", "2627"]
        targets = [(league, [s for s in seasons if (CACHE_DIR / f"odds_{league}_{s}.json").exists()])]
    for league, seasons in targets:
        profile = build(league, seasons)
        if not profile:
            log("profile", f"{league}: 无场次数据，跳过")
            continue
        out = OUT_DIR / f"{league}.json"
        out.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        st = profile["leagueStats"]
        log("profile", f"{league} → {out.name}: {st['matchesUsed']}场 均进{st['avgGoals']} "
                      f"主胜率{st['homeWinRate']} 平局率{st['drawRate']} 冷门率{st['upsetRate']} "
                      f"TOP比分{[t['score'] for t in st['topScores']]}")


if __name__ == "__main__":
    main()
