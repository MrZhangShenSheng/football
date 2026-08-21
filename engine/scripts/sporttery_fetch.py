#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""直连体彩官方API拉完整赛程，绕过 WebFetch 截断。

输出 engine/cache/sporttery_matches.json：所有在售场次（含日期/编号/联赛/对阵/开赛/赔率）。
"""
import json
import sys
from datetime import date
from pathlib import Path

import requests

from common import log, ROOT

URL = "https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry?channel=c"
OUT = ROOT / "engine" / "cache" / "sporttery_matches.json"
UA = {"User-Agent": "Mozilla/5.0"}


def main() -> None:
    try:
        resp = requests.get(URL, headers=UA, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log("sporttery", f"请求失败: {e}")
        sys.exit(1)

    value = data.get("value", {})
    match_info_list = value.get("matchInfoList") or value.get("matchList") or []
    out_matches = []
    for day_block in match_info_list:
        sub = day_block.get("subMatchList", day_block.get("matchList", [])) if isinstance(day_block, dict) else []
        # 某些版本 matchInfoList 直接是比赛列表
        matches = sub if sub else match_info_list
        for m in (matches if isinstance(matches, list) else [matches]):
            num = m.get("matchNumStr") or m.get("matchNum")
            teams = m.get("homeTeamAbbName") or m.get("homeTeamName", {})
            away = m.get("awayTeamAbbName") or m.get("awayTeamName", {})
            home_name = teams.get("name") if isinstance(teams, dict) else teams
            away_name = away.get("name") if isinstance(away, dict) else away
            had = m.get("had") or {}
            hhad = m.get("hhad") or {}
            out_matches.append({
                "code": num,
                "league": m.get("leagueAbbName") or m.get("leagueName"),
                "home": home_name,
                "away": away_name,
                "matchTime": m.get("matchTime"),
                "matchDate": m.get("matchDate"),
                "had": {"h": had.get("h"), "d": had.get("d"), "a": had.get("a")} if had else None,
                "hhad": {"goalLine": hhad.get("goalLine"), "h": hhad.get("h"), "d": hhad.get("d"), "a": hhad.get("a")} if hhad else None,
            })
    payload = {"fetchedAt": date.today().isoformat(), "source": "sporttery", "count": len(out_matches), "matches": out_matches}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log("sporttery", f"{len(out_matches)} 场 → {OUT.relative_to(ROOT)}")
    # 打印编号连续性检查
    codes = [m["code"] for m in out_matches if m.get("code")]
    log("sporttery", "编号: " + ", ".join(codes))


if __name__ == "__main__":
    main()
