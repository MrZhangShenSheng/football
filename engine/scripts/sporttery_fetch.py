#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""直连体彩官方API拉完整赛程，绕过 WebFetch 截断。

输出 engine/cache/sporttery_matches.json：所有在售场次（含日期/编号/联赛/对阵/开赛/赔率）。
v4.5：五池全采——had 胜平负 / hhad 让球 / crs 比分(31选项) / ttg 总进球(8档) / hafu 半全场(9组合)，
     并从 poolList 提取每池 single 单关资格（实测：CRS/TTG/HAFU 全场次单关，HHAD 从不单关，HAD 部分场次单关）。
     头部带完整浏览器 UA + Referer，567 抖动自动重试。
"""
import json
import sys
import time
from datetime import date
from pathlib import Path

import requests

from common import log, ROOT

URL = "https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry?channel=c"
OUT = ROOT / "engine" / "cache" / "sporttery_matches.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.sporttery.cn/",
}
RETRIES = 3

CRS_KEYS = [f"s0{i}s0{j}" for i in range(6) for j in range(6)] + ["s1sh", "s1sd", "s1sa"]
HAFU_KEYS = ["hh", "hd", "ha", "dh", "dd", "da", "ah", "ad", "aa"]


def fetch() -> dict:
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(URL, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_err = e
            if attempt < RETRIES:
                time.sleep(2 * attempt)
    raise last_err


def pool_singles(m: dict) -> dict:
    """poolList → {poolCode: single(0/1)}，池在售才有键。"""
    out = {}
    for p in m.get("poolList") or []:
        out[p.get("poolCode")] = p.get("single")
    return out


def pick(src: dict, keys: list[str]) -> dict:
    return {k: src.get(k) for k in keys if src.get(k)}


def main() -> None:
    try:
        data = fetch()
    except Exception as e:
        log("sporttery", f"请求失败({RETRIES}次重试后): {e}")
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
            crs = m.get("crs") or {}
            ttg = m.get("ttg") or {}
            hafu = m.get("hafu") or {}
            out_matches.append({
                "code": num,
                "league": m.get("leagueAbbName") or m.get("leagueName"),
                "home": home_name,
                "away": away_name,
                "matchTime": m.get("matchTime"),
                "matchDate": m.get("matchDate"),
                "had": {"h": had.get("h"), "d": had.get("d"), "a": had.get("a")} if had else None,
                "hhad": {"goalLine": hhad.get("goalLine"), "h": hhad.get("h"), "d": hhad.get("d"), "a": hhad.get("a")} if hhad else None,
                "crs": pick(crs, CRS_KEYS) if crs else None,
                "ttg": pick(ttg, [f"s{i}" for i in range(8)]) if ttg else None,
                "hafu": pick(hafu, HAFU_KEYS) if hafu else None,
                "poolSingle": pool_singles(m),
            })
    payload = {"fetchedAt": date.today().isoformat(), "source": "sporttery", "count": len(out_matches), "matches": out_matches}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log("sporttery", f"{len(out_matches)} 场 → {OUT.relative_to(ROOT)}")
    # 打印编号连续性检查 + 五池在售/单关统计
    codes = [m["code"] for m in out_matches if m.get("code")]
    log("sporttery", "编号: " + ", ".join(codes))
    stat = {}
    for m in out_matches:
        for pool, single in m.get("poolSingle", {}).items():
            s = stat.setdefault(pool, [0, 0])
            s[0] += 1
            if str(single) == "1":
                s[1] += 1
    summary = ", ".join(f"{p} 售{s[0]}/单关{s[1]}" for p, s in sorted(stat.items()))
    log("sporttery", f"玩法池: {summary}")


if __name__ == "__main__":
    main()
