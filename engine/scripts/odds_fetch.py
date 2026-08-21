#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 football-data.co.uk 下载联赛赔率 CSV（含 Pinnacle 收盘价 PPCH/PPCD/PPCA），存本地缓存。

这是概率锚的数据源（Pinnacle 收盘，毛利~2.5%）。仅覆盖 fd 收录联赛；
沙特/日职/北欧等不在覆盖内（此类场次预测时现场搜索或标锚缺失）。

用法：
  python odds_fetch.py SP1 F1 F2          # 当季（2627）
  python odds_fetch.py --season 2526 SP1  # 指定历史赛季（dc_fit 拟合数据）
"""
import csv
import io
import json
import sys
from datetime import date
from pathlib import Path

import requests

from common import log, ROOT

BASE = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
CACHE_DIR = ROOT / "engine" / "cache"
UA = {"User-Agent": "Mozilla/5.0 (football-kb personal project)"}

# 本项目常用联赛代码映射（football-data.co.uk）
LEAGUE_CODES = {
    "E0": "england-premier", "E1": "england-championship",
    "SP1": "spain-laliga", "SP2": "spain-liga2",
    "D1": "germany-bundesliga", "D2": "germany-bundesliga2",
    "I1": "italy-serie-a", "I2": "italy-serie-b",
    "F1": "france-ligue1", "F2": "france-ligue2",
    "N1": "netherlands-eredivisie",
    "B1": "belgium-first-a", "P1": "portugal-liga",
    "T1": "turkey-super-lig", "G1": "greece-super",
    "R1": "russia-premier",
}
DEFAULT_CODES = ["SP1", "F1", "F2"]


def fetch_league_csv(code: str, season: str) -> list[dict] | None:
    url = BASE.format(season=season, code=code)
    try:
        resp = requests.get(url, headers=UA, timeout=30)
        if resp.status_code != 200 or not resp.text.strip() or "<html" in resp.text[:200].lower():
            log("odds", f"{code} {season}: HTTP {resp.status_code}（CSV 可能未发布）")
            return None
        resp.encoding = "utf-8-sig"
        return list(csv.DictReader(io.StringIO(resp.text)))
    except requests.RequestException as e:
        log("odds", f"{code} {season}: 请求失败 {e}")
        return None


def main() -> None:
    season = "2627"
    args = sys.argv[1:]
    if args and args[0] == "--season":
        season = args[1]
        args = args[2:]
    codes = args or DEFAULT_CODES
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for code in codes:
        rows = fetch_league_csv(code, season)
        if rows is None:
            continue
        out_rows = []
        for r in rows:
            # 列名在不同赛季大小写不稳定，做一次归一
            def g(*keys):
                for k in keys:
                    for rk, rv in r.items():
                        if rk and rk.lower() == k.lower():
                            return rv
                return None
            pin_c_h = g("PPCH", "Psh")  # Pinnacle 收盘（PPC*=closing；老赛季为 Psh）
            pin_c_d = g("PPCD", "Psd")
            pin_c_a = g("PPCA", "Psa")
            if pin_c_h:
                out_rows.append({
                    "date": g("Date"), "home": g("HomeTeam"), "away": g("AwayTeam"),
                    "fthg": g("FTHG"), "ftag": g("FTAG"),
                    "pin_h": pin_c_h, "pin_d": pin_c_d, "pin_a": pin_c_a,
                    "pin_open_h": g("PPH"), "pin_open_d": g("PPD"), "pin_open_a": g("PPA"),
                    "b365c_h": g("B365CH"), "b365c_d": g("B365CD"), "b365c_a": g("B365CA"),
                    "hxg": g("HxG"), "axg": g("AxG"),
                })
        league_name = LEAGUE_CODES.get(code, code)
        out = CACHE_DIR / f"odds_{league_name}_{season}.json"
        payload = {"fetchedAt": date.today().isoformat(), "source": "football-data.co.uk", "season": season, "matches": out_rows}
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        log("odds", f"{code} {season} → {out.name}：{len(out_rows)} 场")


if __name__ == "__main__":
    main()
