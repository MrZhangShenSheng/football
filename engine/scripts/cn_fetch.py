#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""国内兜底源：titan007（球探体育）联赛数据 JS 直取（ESPN 不可达时的降级链路）。

数据端点（2026-08-21 实测，免登录免 cookie）：
  https://zq.titan007.com/jsData/matchResult/{赛季}/s{联赛ID}.js
  内容 = var arrLeague/arrTeam/totalScore/homeScore/guestScore/... JS 数组
  编码 utf-8（少数文件 gbk，解码失败自动回退）
  反爬：必须带浏览器 UA + Referer，否则 442

totalScore 字段语义（对照 25/26 英超榜逐队验证）：
  [0]=?, [1]=排名, [2]=队ID, [3]=?, [4]=场次, [5]=胜, [6]=平, [7]=负,
  [8]=进, [9]=失, [10]=净胜, [11..13]=胜/平/负率%, [14]=场均进, [15]=场均失,
  [16]=积分
  （阿森纳 26胜7平5负 71:27 积85 —— 26*3+7=85 ✓；沙特 s292 布局一致 ✓）

用法：
  python cn_fetch.py standings <联赛ID> [赛季]   # 积分榜（赛季默认 2026-2027）
  python cn_fetch.py teams <联赛ID> [赛季]       # 队伍中英文对照表（别名映射补录用）
  python cn_fetch.py list                        # 列出常用联赛 ID

常用联赛 ID（titan007 SclassID，leftData.js 提取）：
  36 英超 31 西甲 8 德甲 11 法甲 16 荷甲 23 葡超 29 苏超
  25 日职联 284 日职乙 26 瑞典超 122 瑞典甲 22 挪超 7 丹麦超 13 芬超
  292 沙特联 27 瑞士超
"""
import ast
import json
import re
import sys
from datetime import date
from pathlib import Path

import requests

from common import log, ROOT

BASE = "https://zq.titan007.com/jsData/matchResult/{season}/s{sid}.js"
LEAGUE_PAGE = "https://zq.titan007.com/cn/League/{sid}.html"
DEFAULT_SEASON = "2026-2027"
CACHE_DIR = ROOT / "engine" / "cache"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Referer": "https://zq.titan007.com/",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# titan007 SclassID → 本项目 data/00-leagues 目录名
CN_LEAGUES = {
    "36": "england-premier", "31": "spain-laliga", "8": "germany-bundesliga",
    "11": "france-ligue1", "16": "netherlands-eredivisie", "23": "portugal-primeira",
    "29": "scotland", "25": "japan", "284": "japan-j2", "26": "sweden",
    "122": "sweden-superettan", "22": "norway", "7": "denmark", "13": "finland",
    "292": "saudi", "27": "switzerland",
}


def fetch_league_js(sid: str, season: str) -> dict | None:
    """拉取并解析联赛 JS，返回 {league, teams, totalScore}。

    数据文件名 = s{ID}.js 或 s{ID}_{阶段码}.js（单年制联赛如瑞超/挪超带阶段码）。
    先试裸文件名，404 则从联赛页 HTML 提取带阶段码的真实路径。
    """
    candidates = [BASE.format(season=season, sid=sid)]
    real = _resolve_js_path(sid)
    if real:
        candidates.insert(0, real)
    for url in candidates:
        for attempt in (1, 2):
            try:
                resp = requests.get(url, headers=HEADERS, timeout=20)
                if resp.status_code != 200 or b"arrLeague" not in resp.content:
                    break  # 换下一个候选路径
                try:
                    text = resp.content.decode("utf-8")
                except UnicodeDecodeError:
                    text = resp.content.decode("gbk", errors="ignore")
                lg = re.search(r"var arrLeague = \[(\d+),'([^']*)','([^']*)','([^']*)'", text)
                tm = re.search(r"var arrTeam = (\[\[.+?\]\]);", text, re.S)
                ts = re.search(r"var totalScore = (\[\[.+?\]\]);", text, re.S)
                return {
                    "meta": {"id": lg.group(1), "zh": lg.group(2), "en": lg.group(4)} if lg else {},
                    "teams": ast.literal_eval(tm.group(1)) if tm else [],
                    "totalScore": ast.literal_eval(ts.group(1)) if ts else [],
                }
            except (requests.RequestException, ast.SyntaxError):
                continue
    log("cn", f"{sid} {season}: 数据未发布或不可达（候选: {len(candidates)} 个路径）")
    return None


def _resolve_js_path(sid: str) -> str | None:
    """从联赛页 HTML 提取 jsData/matchResult 真实文件名（含阶段码）。"""
    try:
        resp = requests.get(LEAGUE_PAGE.format(sid=sid), headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return None
        html = resp.content.decode("utf-8", errors="ignore")
        m = re.search(r'src="(/jsData/matchResult/[^"?]+)', html)
        return "https://zq.titan007.com" + m.group(1) if m else None
    except requests.RequestException:
        return None


def parse_standings(data: dict) -> list[dict]:
    team_map = {t[0]: {"zh": t[1], "en": t[3] if len(t) > 3 else ""} for t in data["teams"]}
    out = []
    for row in data["totalScore"]:
        tid = row[2]
        info = team_map.get(tid, {"zh": str(tid), "en": ""})
        out.append({
            "pos": row[1], "team": info["en"] or info["zh"], "teamZh": info["zh"],
            "played": row[4], "won": row[5], "drawn": row[6], "lost": row[7],
            "gf": row[8], "ga": row[9], "gd": row[10], "pts": row[16],
        })
    return out


def cmd_standings(sid: str, season: str) -> None:
    data = fetch_league_js(sid, season)
    if not data or not data["totalScore"]:
        return
    standings = parse_standings(data)
    # 防污染：新赛季 titan007 可能返回全 0 榜（球队已列但未开赛）——不覆盖已有画像
    if not any(s["pts"] or s["played"] for s in standings):
        log("cn", f"s{sid} {season} 全 0 榜（赛季未开/数据未灌），跳过合并")
        return
    payload = {"fetchedAt": date.today().isoformat(), "source": "titan007",
               "league": data["meta"], "season": season, "standings": standings}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / f"cn_standings_s{sid}_{season}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    # 合并进 data/00-leagues（ESPN 不可达时的兜底覆盖）
    league = CN_LEAGUES.get(sid)
    if league:
        lg_path = ROOT / "data" / "00-leagues" / f"{league}.json"
        if lg_path.exists():
            profile = json.loads(lg_path.read_text(encoding="utf-8"))
            profile["standings"] = standings
            profile["roundsPlayed"] = max((s["played"] for s in standings), default=0)
            profile.setdefault("freshness", {})
            profile["freshness"]["standingsSource"] = "titan007"
            profile["freshness"]["stale"] = False
            lg_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            log("cn", f"s{sid} 积分榜已合并 → {lg_path.name}")
    top = standings[0] if standings else None
    log("cn", f"s{sid}({data['meta'].get('zh')}) {len(standings)} 队 → {out.name}"
        + (f"（榜首 {top['teamZh']} {top['pts']}分）" if top else ""))


def cmd_teams(sid: str, season: str) -> None:
    data = fetch_league_js(sid, season)
    if not data:
        return
    rows = [{"id": t[0], "zh": t[1], "en": t[3] if len(t) > 3 else ""} for t in data["teams"]]
    for r in rows:
        print(f"{r['id']:>6}  {r['zh']:<12} {r['en']}")
    log("cn", f"s{sid} 共 {len(rows)} 队（中英文对照，可直接补 _aliases.json）")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "standings" and len(args) >= 2:
        cmd_standings(args[1], args[2] if len(args) >= 3 else DEFAULT_SEASON)
    elif cmd == "teams" and len(args) >= 2:
        cmd_teams(args[1], args[2] if len(args) >= 3 else DEFAULT_SEASON)
    elif cmd == "list":
        for sid, name in CN_LEAGUES.items():
            print(f"{sid:>5}  {name}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
