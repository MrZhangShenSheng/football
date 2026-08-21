#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 clubelo.com 免费 API 抓取球队 Elo 评级，更新球队画像。

API: https://api.clubelo.com/{ClubName}  返回 CSV（Club,Country,Level,Elo,From,To）
球队名需 URL 编码（空格/重音字符）；仅处理 _aliases.json 中 clubelo 字段非空的球队。

用法：
  python elo_fetch.py                    # 全部已映射球队
  python elo_fetch.py benfica lech-poznan # 指定球队
"""
import csv
import io
import sys
from urllib.parse import quote

import requests

from common import load_aliases, load_team, save_team, log

API = "https://api.clubelo.com/{}"
UA = {"User-Agent": "Mozilla/5.0 (football-kb personal project)"}


def fetch_clubelo(name: str) -> dict | None:
    """抓单支球队 Elo CSV，返回最新一行 dict；超时重试 1 次。"""
    url = API.format(quote(name))
    for attempt in (1, 2):
        try:
            resp = requests.get(url, headers=UA, timeout=20)
            resp.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(resp.text)))
            return rows[-1] if rows else None
        except requests.RequestException as e:
            if attempt == 2:
                log("elo", f"请求失败 {name}: {e}")
                return None


def main() -> None:
    aliases = load_aliases()
    targets = sys.argv[1:] or list(aliases)
    ok = skip = fail = 0
    for team_id in targets:
        info = aliases.get(team_id)
        if not info:
            log("elo", f"未知名 {team_id}（不在 _aliases.json）")
            fail += 1
            continue
        clubelo_name = info.get("clubelo")
        if not clubelo_name:
            skip += 1
            continue
        row = fetch_clubelo(clubelo_name)
        if not row:
            fail += 1
            continue
        data = load_team(team_id, info["league"])
        data["elo"] = {
            "rating": int(row["Elo"]),
            "rank": None,
            "country": row.get("Country"),
            "level": row.get("Level"),
            "from": row.get("From"),
            "source": "clubelo.com",
        }
        save_team(team_id, info["league"], data, zh=info.get("zh"))
        ok += 1
        log("elo", f"{team_id} ← {clubelo_name}: {row['Elo']}")
    log("elo", f"完成：成功 {ok} · 跳过(无映射) {skip} · 失败 {fail}")


if __name__ == "__main__":
    main()
