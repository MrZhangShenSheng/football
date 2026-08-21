#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据就绪体检：预测前检查联赛画像 + 球队画像 + 别名覆盖，输出缺口清单。

Claude 在 Step 2.5 调用本脚本，拿缺口清单执行冷启动初始化：
- 缺 fd 联赛画像 → run.py update（若 fd 覆盖）或 Claude WebFetch ESPN 手工结构化
- 缺球队画像   → Claude 联赛分组搜索后按规范 JSON 写入 01-teams/
- 缺别名       → Claude 补 _aliases.json（规范 ID + zh + league）

用法：
  python data_check.py matches.json
matches.json 格式：[{"league": "日职目录名或fd名", "home": "中文队名", "away": "中文队名"}, ...]
输出：JSON 缺口清单（ready/missing 三类）
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path

from common import log, ROOT, TEAMS_DIR

LEAGUES_DIR = ROOT / "data" / "00-leagues"
TEAM_STALE_DAYS = 7


def load_aliases_zh() -> dict[str, tuple[str, str]]:
    """中文名 -> (规范ID, league目录)。"""
    raw = json.loads((TEAMS_DIR / "_aliases.json").read_text(encoding="utf-8"))
    out = {}
    for league, teams in raw.items():
        if league.startswith("_"):
            continue
        for team_id, srcs in teams.items():
            if srcs.get("zh"):
                out[srcs["zh"]] = (team_id, league)
    return out


def team_status(team_id: str, league: str) -> dict:
    p = TEAMS_DIR / league / f"{team_id}.json"
    if not p.exists():
        return {"status": "missing"}
    data = json.loads(p.read_text(encoding="utf-8"))
    lu = data.get("lastUpdated")
    if lu:
        age = (date.today() - datetime.strptime(lu, "%Y-%m-%d").date()).days
        if age > TEAM_STALE_DAYS:
            return {"status": "stale", "lastUpdated": lu, "ageDays": age}
    return {"status": "ready", "lastUpdated": lu}


def league_status(league: str) -> dict:
    p = LEAGUES_DIR / f"{league}.json"
    if not p.exists():
        return {"status": "missing"}
    data = json.loads(p.read_text(encoding="utf-8"))
    st = data.get("leagueStats") or {}
    fresh = data.get("freshness") or {}
    return {
        "status": "stale" if fresh.get("stale") else "ready",
        "roundsPlayed": data.get("roundsPlayed"),
        "standingsSource": fresh.get("standingsSource"),
        "fdCovered": st.get("computedFrom") == "fd",
    }


def main() -> None:
    if len(sys.argv) < 2:
        log("check", "用法: python data_check.py matches.json")
        return
    matches = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    aliases = load_aliases_zh()
    seen_leagues: dict[str, dict] = {}
    missing_alias: list[dict] = []
    teams_report: list[dict] = []

    for m in matches:
        lg = m["league"]
        if lg not in seen_leagues:
            seen_leagues[lg] = league_status(lg)
        for side in ("home", "away"):
            zh = m[side]
            if zh not in aliases:
                missing_alias.append({"team": zh, "league": lg, "match": f"{m['home']} vs {m['away']}"})
                continue
            team_id, team_league = aliases[zh]
            st = team_status(team_id, team_league)
            if st["status"] != "ready":
                teams_report.append({"team": zh, "teamId": team_id, "league": team_league, **st})

    report = {
        "checkedAt": date.today().isoformat(),
        "leagues": seen_leagues,
        "missingLeagues": [lg for lg, s in seen_leagues.items() if s["status"] == "missing"],
        "staleLeagues": [lg for lg, s in seen_leagues.items() if s["status"] == "stale"],
        "missingAlias": missing_alias,
        "notReadyTeams": teams_report,
        "readyCount": {"leagues": sum(1 for s in seen_leagues.values() if s["status"] == "ready"),
                       "teamsKnown": len(aliases)},
    }
    out = ROOT / "engine" / "cache" / "data_check_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    log("check", f"→ {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
