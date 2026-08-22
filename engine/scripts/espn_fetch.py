#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ESPN 公开 API 直连采集：积分榜 + 赛果（替代 WebFetch，绕过浏览器 UA 403 与页面截断）。

关键经验（2026-08-21 实测）：
- site.api.espn.com 用浏览器 UA 会 403；requests/python 默认 UA 反而 200（勿加 UA 头）
- standings: /apis/v2/sports/soccer/{code}/standings?season=YYYY（YYYY 为赛季起始年）
- scoreboard: /apis/site/v2/sports/soccer/{code}/scoreboard?dates=YYYYMMDD（按日期查赛果）
- 覆盖 fd 不含的联赛：日职 jpn.1 / 瑞典 swe.1 / 挪威 nor.1 / 丹麦 den.1 / 沙特 ksa.1 等

三种用法：
  python espn_fetch.py standings <联赛代码> [--season 2025]   # 积分榜 → engine/cache/espn_standings_{code}.json
  python espn_fetch.py results <联赛代码> <YYYYMMDD>          # 单日赛果 → 打印 + 缓存
  python espn_fetch.py results <联赛代码> <起> <止>            # 日期区间赛果
  python espn_fetch.py history <联赛代码> <起始年> [结束年]    # ★历史赛季回填（limit=500 整季一次拉）
                                                            #   → data/02-results/league/{league}_matches.json
                                                            #   队名经 _aliases.json espn 字段映射到规范ID，映射不上即丢弃计数

联赛代码（ESPN → 本项目联赛名）：
  esp.1 西甲 gbr.1 英超 ger.1 德甲 ita.1 意甲 fra.1 法甲 ned.1 荷甲 por.1 葡超
  jpn.1 日职 swe.1 瑞超 nor.1 挪超 den.1 丹超 ksa.1 沙特 sco.1 苏超
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from common import load_aliases, log, ROOT

API_BASE = "https://site.api.espn.com"
STANDINGS_URL = API_BASE + "/apis/v2/sports/soccer/{}/standings"
SCOREBOARD_URL = API_BASE + "/apis/site/v2/sports/soccer/{}/scoreboard"
CACHE_DIR = ROOT / "engine" / "cache"
LEAGUE_RESULTS_DIR = ROOT / "data" / "02-results" / "league"

# ESPN 联赛代码 → 本项目 data/00-leagues 目录名（与现有文件对齐：日职/瑞超等用短名）
ESPN_LEAGUES = {
    "esp.1": "spain-laliga", "gbr.1": "england-premier", "ger.1": "germany-bundesliga",
    "ita.1": "italy-serie-a", "fra.1": "france-ligue1", "ned.1": "netherlands-eredivisie",
    "por.1": "portugal-primeira", "jpn.1": "japan", "jpn.2": "japan-j2",
    "swe.1": "sweden", "nor.1": "norway", "den.1": "denmark", "ksa.1": "saudi",
    "sco.1": "scotland", "kor.1": "korea-k-league", "ned.2": "netherlands",
}


def get_json(url: str, params: dict) -> dict | None:
    """ESPN API GET。注意：不带 UA 头（浏览器 UA 会被 403）。"""
    for attempt in (1, 2):
        try:
            resp = requests.get(url, params=params, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt == 2:
                log("espn", f"请求失败 {url}: {e}")
                return None


def parse_standings(data: dict) -> list[dict]:
    """ESPN standings JSON → 本项目积分榜格式（与 league_profile.py 输出对齐）。"""
    entries = []
    for child in data.get("children", []):
        for entry in child.get("standings", {}).get("entries", []):
            st = {s["name"]: s.get("value", s.get("displayValue")) for s in entry.get("stats", [])}
            entries.append({
                "pos": int(st.get("rank") or len(entries) + 1),
                "team": entry["team"]["displayName"],
                "played": int(st.get("gamesPlayed") or 0),
                "won": int(st.get("wins") or 0),
                "drawn": int(st.get("ties") or 0),
                "lost": int(st.get("losses") or 0),
                "gf": int(st.get("pointsFor") or 0),
                "ga": int(st.get("pointsAgainst") or 0),
                "gd": int(st.get("pointDifferential") or 0),
                "pts": int(st.get("points") or 0),
            })
    return entries


def cmd_standings(code: str, season: str | None) -> None:
    data = get_json(STANDINGS_URL.format(code), {"season": season} if season else {})
    if not data or not data.get("children"):
        log("espn", f"{code} standings 为空（联赛代码或赛季参数有误？）")
        return
    standings = parse_standings(data)
    payload = {
        "fetchedAt": date.today().isoformat(),
        "source": "espn",
        "leagueCode": code,
        "season": season,
        "standings": standings,
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / f"espn_standings_{code.replace('.', '_')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    # 同时更新 data/00-leagues（若本项目已有该联赛画像则覆盖 standings 段）
    league = ESPN_LEAGUES.get(code)
    if league:
        lg_path = ROOT / "data" / "00-leagues" / f"{league}.json"
        if lg_path.exists():
            profile = json.loads(lg_path.read_text(encoding="utf-8"))
            profile["standings"] = standings
            profile["roundsPlayed"] = max((s["played"] for s in standings), default=0)
            profile.setdefault("freshness", {})
            profile["freshness"]["standingsSource"] = "espn"
            profile["freshness"]["stale"] = False
            lg_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            log("espn", f"{code} 积分榜已合并 → {lg_path.name}")
    log("espn", f"{code} {len(standings)} 队 → {out.name}（榜首 {standings[0]['team']} {standings[0]['pts']}分）"
        if standings else f"{code} 无数据")


def parse_results(data: dict) -> list[dict]:
    """ESPN scoreboard events → 简洁赛果列表。"""
    out = []
    for ev in data.get("events", []):
        comp = ev.get("competitions", [{}])[0]
        status = comp.get("status", {}).get("type", {})
        row = {"date": ev.get("date"), "status": status.get("name"), "home": None, "away": None}
        for c in comp.get("competitors", []):
            side = "home" if c.get("homeAway") == "home" else "away"
            row[side] = {"team": c["team"]["displayName"], "score": c.get("score")}
        out.append(row)
    return out


def cmd_results(code: str, d1: str, d2: str | None) -> None:
    d2 = d2 or d1
    start = datetime.strptime(d1, "%Y%m%d").date()
    end = datetime.strptime(d2, "%Y%m%d").date()
    all_rows = []
    cur = start
    while cur <= end:
        data = get_json(SCOREBOARD_URL.format(code), {"dates": cur.strftime("%Y%m%d")})
        if data:
            rows = parse_results(data)
            all_rows.extend(rows)
            for r in rows:
                if r["status"] == "STATUS_FULL_TIME":
                    log("espn", f"{cur} {r['home']['team']} {r['home']['score']}-{r['away']['score']} {r['away']['team']}")
        cur += timedelta(days=1)
    payload = {"fetchedAt": date.today().isoformat(), "source": "espn", "leagueCode": code,
               "dateFrom": start.isoformat(), "dateTo": end.isoformat(), "results": all_rows}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / f"espn_results_{code.replace('.', '_')}_{start}_{end}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log("espn", f"{code} {start}~{end} 共 {len(all_rows)} 场 → {out.name}")


def espn_name_map() -> dict[str, str]:
    """别名表 espn 字段 → 规范ID（映射不上的 ESPN 队名将被丢弃）。"""
    out = {}
    for team_id, srcs in load_aliases().items():
        espn = srcs.get("espn")
        if espn:
            out[espn] = team_id
    return out


def cmd_history(code: str, y1: str, y2: str | None) -> None:
    """历史赛季回填：scoreboard dates={y1}0201-{y2}1231 limit=500 → 本地 matches 格式（供 dc_fit --source local）。

    只入库 STATUS_FULL_TIME 且双方分数齐全的场次；队名映射不上即丢弃并计数（宁缺毋滥）。
    """
    y2 = y2 or y1
    start, end = f"{y1}0201", f"{y2}1231"
    league = ESPN_LEAGUES.get(code)
    if not league:
        log("espn", f"{code} 不在 ESPN_LEAGUES 映射内")
        return
    data = get_json(SCOREBOARD_URL.format(code), {"dates": f"{start}-{end}", "limit": 500})
    if not data or not data.get("events"):
        log("espn", f"{code} {y1}~{y2} 无数据（ESPN 不覆盖该联赛该时段？）")
        return
    name_map = espn_name_map()
    rows, dropped = [], []
    for ev in data.get("events", []):
        comp = ev.get("competitions", [{}])[0]
        if comp.get("status", {}).get("type", {}).get("name") != "STATUS_FULL_TIME":
            continue
        try:
            d = datetime.strptime(ev.get("date", "")[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        home = away = None
        hg = ag = None
        for c in comp.get("competitors", []):
            name = c["team"]["displayName"]
            score = c.get("score")
            if score is None:
                home = away = None
                break
            if c.get("homeAway") == "home":
                if name not in name_map:
                    dropped.append(name)
                home, hg = name_map.get(name), int(float(score))
            else:
                if name not in name_map:
                    dropped.append(name)
                away, ag = name_map.get(name), int(float(score))
        if home and away and hg is not None and ag is not None:
            rows.append({"date": d.isoformat(), "home": home, "away": away, "hg": hg, "ag": ag})
    # 合并入库（追加去重：同 date+home+away）
    LEAGUE_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = LEAGUE_RESULTS_DIR / f"{league}_matches.json"
    existing = {}
    if out_path.exists():
        for m in json.loads(out_path.read_text(encoding="utf-8")).get("matches", []):
            existing[(m["date"], m["home"], m["away"])] = m
    for r in rows:
        existing[(r["date"], r["home"], r["away"])] = r
    merged = sorted(existing.values(), key=lambda m: m["date"])
    payload = {
        "league": league, "source": "espn-history", "seasons": [y1, y2],
        "fetchedAt": date.today().isoformat(),
        "droppedUnmapped": len(dropped), "matches": merged,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log("espn", f"{code} {y1}~{y2}: 入库 {len(rows)} 场（未映射丢弃 {len(dropped)}）→ {out_path.relative_to(ROOT)}")
    if dropped:
        from collections import Counter
        top = Counter(dropped).most_common(5)
        log("espn", "丢弃样本（补 _aliases.json espn 字段可恢复）: " + ", ".join(f"{k}×{v}" for k, v in top))


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "standings" and len(args) >= 2:
        code = args[1]
        season = args[3] if len(args) >= 4 and args[2] == "--season" else None
        cmd_standings(code, season)
    elif cmd == "results" and len(args) >= 3:
        cmd_results(args[1], args[2], args[3] if len(args) >= 4 else None)
    elif cmd == "history" and len(args) >= 3:
        cmd_history(args[1], args[2], args[3] if len(args) >= 4 else None)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
