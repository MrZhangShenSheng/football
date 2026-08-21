#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""从 understat.com 抓取球队 xG/xGA（仅覆盖六大联赛），更新球队画像。

页面源码内嵌 teamsData = JSON.parse('...')，十六进制转义（backslash-xXX）需解码。
_aliases.json 的 understat 字段 = understat 页面 teamsData 的 title 名。

用法：
  python xg_fetch.py                    # 全部已映射球队
  python xg_fetch.py marseille          # 指定球队
"""
import json
import re
import sys
from datetime import date

import requests

from common import load_aliases, load_team, save_team, log

UA = {"User-Agent": "Mozilla/5.0 (football-kb personal project)"}
# 当前 2026-27 赛季 understat 路径段
SEASON = "2026"
LEAGUE_URL = "https://understat.com/league/{}/{}"


def decode_escaped(s: str) -> str:
    return s.encode("utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")


def fetch_league(league_slug: str) -> dict | None:
    """抓整个联赛页的 teamsData，返回 title -> 字段 dict。"""
    url = LEAGUE_URL.format(league_slug, SEASON)
    try:
        html = requests.get(url, headers=UA, timeout=20).text
    except requests.RequestException as e:
        log("xg", f"请求失败 {url}: {e}")
        return None
    m = re.search(r"teamsData\s*=\s*JSON.parse\('([^']+)'\)", html)
    if not m:
        log("xg", f"未找到 teamsData（赛季 {SEASON} 可能未开数据）")
        return None
    raw = json.loads(decode_escaped(m.group(1)))
    return {v.get("title"): {"id": k, **v.get("history", [{}])[-1]} for k, v in raw.items()}


def main() -> None:
    aliases = load_aliases()
    targets = [t for t in (sys.argv[1:] or aliases) if aliases.get(t, {}).get("understat")]
    if not targets:
        log("xg", "无 understat 映射球队，全部跳过")
        return
    # 按联赛页分组请求（一次页面覆盖多队）
    by_slug: dict[str, list[str]] = {}
    for t in targets:
        by_slug.setdefault(aliases[t]["understat"], []).append(t)
    # 同一联赛的球队共用一个联赛页：先用任一队名无法推联赛页 slug，直接抓六大联赛有映射的
    league_pages = {}
    for slug in ("laliga", "ligue_1"):
        data = fetch_league(slug)
        if data:
            league_pages[slug] = data
    ok = miss = 0
    for team_id in targets:
        info = aliases[team_id]
        title = info["understat"]
        row = next((v for page in league_pages.values() for k, v in page.items() if k == title), None)
        if not row:
            miss += 1
            log("xg", f"{team_id} ← '{title}' 在已抓联赛页中未找到")
            continue
        data = load_team(team_id, info["league"])
        xg_for = row.get("xG")
        xg_a = row.get("xGA")
        data["xg"] = {
            "for": xg_for,
            "against": xg_a,
            "matches": row.get("matches"),
            "ppda": row.get("ppda"),
            "deep": row.get("deep"),
            "source": "understat",
            "fetchedAt": date.today().isoformat(),
        }
        save_team(team_id, info["league"], data, zh=info.get("zh"))
        ok += 1
        log("xg", f"{team_id}: xG {xg_for} / xGA {xg_a}（{row.get('matches')}场）")
    log("xg", f"完成：成功 {ok} · 未找到 {miss}")


if __name__ == "__main__":
    main()
