#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""直连体彩官方API拉完整赛程，绕过 WebFetch 截断。

输出 engine/cache/sporttery_matches.json：所有在售场次（含日期/编号/联赛/对阵/开赛/赔率）。
v4.5：五池全采——had 胜平负 / hhad 让球 / crs 比分(31选项) / ttg 总进球(8档) / hafu 半全场(9组合)，
     并从 poolList 提取每池 single 单关资格（实测：CRS/TTG/HAFU 全场次单关，HHAD 从不单关，HAD 部分场次单关）。
     头部带完整浏览器 UA + Referer，567 抖动自动重试。

赛果子命令（2026-08-23 实测接入，zqlszl/zqsgkj 两口径）：
  python sporttery_fetch.py league-results <key> [年份]   # 联赛历史赛果 → data/02-results/league/{key}_matches.json
                                                          # 90天分段（超限报20008），队名经 _aliases zh 字段映射
  python sporttery_fetch.py results <YYYY-MM-DD> [止日期] # 体彩开奖口径赛果 → engine/cache/sporttery_results_*.json
                                                          # 含场次编号(周六028)+比分+体彩赔率，供回填流程对票
"""
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

from common import load_aliases, log, ROOT

URL = "https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry?channel=c"
OUT = ROOT / "engine" / "cache" / "sporttery_matches.json"
CACHE_DIR = ROOT / "engine" / "cache"
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
    """poolList → {poolCode: single(0/1)}，池在售才有键。
    2026-08-23 实测：体彩 API 将 single 字段废弃（恒 0），单关资格迁移至 bettingSingle
    （分布与 08-22 实测一致：HAD 13/36 可单关、HHAD 全 0、CRS/TTG/HAFU 全场单关）。"""
    out = {}
    for p in m.get("poolList") or []:
        out[p.get("poolCode")] = p.get("bettingSingle", p.get("single"))
    return out


def pick(src: dict, keys: list[str]) -> dict:
    return {k: src.get(k) for k in keys if src.get(k)}


# ---------- 赛果子命令（zqlszl / zqsgkj 两口径，2026-08-23 探测接入）----------

# 联赛目录键 → 体彩 uniformLeagueId（getLeagueListV1 实测；韩职=ESPN 无数据的洞）
SPORTTERY_LEAGUES = {
    "korea": 86,
    "japan": 2279,
    "saudi": 1767,
    "sweden": 1085,
    "norway": 1779,
    "finland": 1073,
    "usa": 40,
}
LEAGUE_LIST_URL = URL.replace("getMatchCalculatorV1", "league/getLeagueListV1")
LEAGUE_RESULT_URL = URL.replace("getMatchCalculatorV1", "league/getMatchResultV1")
DRAW_RESULT_URL = URL.replace("getMatchCalculatorV1", "getUniformMatchResultV1")
LEAGUE_RESULTS_DIR = ROOT / "data" / "02-results" / "league"
SEGMENT_DAYS = 90  # 实测上限：91 天报 errorCode 20008


def get_json(url: str, params: dict | None = None) -> dict:
    """带重试的 GET（体彩 API 偶发超时/截断抖动）。失败抛最后一次异常。"""
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=60)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_err = e
            if attempt < RETRIES:
                time.sleep(2 * attempt)
    raise last_err


def zh_name_map(league_key: str) -> dict[str, str]:
    """别名表 zh 字段（体彩官方中文名）→ 规范ID（映射不上的体彩队名将被丢弃）。"""
    out = {}
    for team_id, srcs in load_aliases().items():
        zh = srcs.get("zh")
        if zh:
            out[zh] = team_id
    return out


def resolve_season(league_id: int, year: str | None) -> tuple[str, str]:
    """联赛列表 → 指定年（缺省最新）的 (seasonId, seasonName)。韩职单年制'2026'，欧洲跨年制'2026/2027'。"""
    data = get_json(LEAGUE_LIST_URL)
    seasons = []
    for grp in ("hot", "normal", "other"):
        for lg in (data.get("value", {}).get(grp) or []):
            if lg.get("uniformLeagueId") == league_id and lg.get("seasonList"):
                seasons = lg["seasonList"]
                break
        if seasons:
            break
    if not seasons:

        def _walk(node):
            if isinstance(node, dict):
                if node.get("uniformLeagueId") == league_id and node.get("seasonList"):
                    return node["seasonList"]
                for v in node.values():
                    r = _walk(v)
                    if r:
                        return r
            elif isinstance(node, list):
                for x in node:
                    r = _walk(x)
                    if r:
                        return r
            return None
        seasons = _walk(data.get("value", {})) or []
    if not seasons:
        raise SystemExit(f"联赛 uniformLeagueId={league_id} 不在体彩联赛列表")
    if year:
        for s in seasons:
            if s["seasonName"].startswith(year):
                return str(s["seasonId"]), s["seasonName"]
        raise SystemExit(f"联赛 {league_id} 无 {year} 赛季（可用: {', '.join(s['seasonName'] for s in seasons)}）")
    s = seasons[0]
    return str(s["seasonId"]), s["seasonName"]


def cmd_league_results(league_key: str, year: str | None) -> None:
    """zqlszl 口径：联赛赛季赛果（90天分段）→ data/02-results/league/{key}_matches.json（espn history 同格式）。"""
    league_id = SPORTTERY_LEAGUES.get(league_key)
    if league_id is None:
        log("sporttery", f"未知联赛 {league_key}（可用: {', '.join(SPORTTERY_LEAGUES)}）")
        return
    season_id, season_name = resolve_season(league_id, year)
    d = get_json(LEAGUE_RESULT_URL, {"seasonId": season_id, "uniformLeagueId": league_id})
    v = d.get("value", {})
    start = date.fromisoformat(v.get("seasonStartDate") or f"{year or date.today().year}-01-01")
    end = date.fromisoformat(v.get("seasonEndDate") or date.today().isoformat())
    if end > date.today():
        end = date.today()
    name_map = zh_name_map(league_key)
    rows, dropped = [], []
    cur = start
    while cur <= end:
        seg_end = min(cur + timedelta(days=SEGMENT_DAYS - 1), end)
        seg = get_json(LEAGUE_RESULT_URL, {"seasonId": season_id, "uniformLeagueId": league_id,
                                           "startDate": cur.isoformat(), "endDate": seg_end.isoformat()})
        for blk in seg.get("value", {}).get("matchList", []):
            for m in blk.get("subMatchList", []):
                if m.get("wbsjMatchSc") != "Played" or not m.get("sectionsNo999"):
                    continue
                hg, ag = m["sectionsNo999"].split(":")
                home, away = name_map.get(m["homeAbbCnName"]), name_map.get(m["awayAbbCnName"])
                if not home:
                    dropped.append(m["homeAbbCnName"])
                if not away:
                    dropped.append(m["awayAbbCnName"])
                if home and away:
                    rows.append({"date": m["matchDate"], "home": home, "away": away,
                                 "hg": int(hg), "ag": int(ag)})
        cur = seg_end + timedelta(days=1)
        time.sleep(0.3)
    # 合并入库（追加去重：同 date+home+away）
    LEAGUE_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = LEAGUE_RESULTS_DIR / f"{league_key}_matches.json"
    existing = {}
    if out_path.exists():
        for m in json.loads(out_path.read_text(encoding="utf-8")).get("matches", []):
            existing[(m["date"], m["home"], m["away"])] = m
    for r in rows:
        existing[(r["date"], r["home"], r["away"])] = r
    merged = sorted(existing.values(), key=lambda m: m["date"])
    payload = {"league": league_key, "source": "sporttery-history", "seasons": [season_name],
               "fetchedAt": date.today().isoformat(), "droppedUnmapped": len(dropped), "matches": merged}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log("sporttery", f"{league_key} {season_name}: 入库 {len(rows)} 场（未映射丢弃 {len(dropped)}，库存累计 {len(merged)}）→ {out_path.relative_to(ROOT)}")
    if dropped:
        from collections import Counter
        top = Counter(dropped).most_common(8)
        log("sporttery", "丢弃样本（补 _aliases.json zh 字段可恢复）: " + ", ".join(f"{k}×{n}" for k, n in top))


def cmd_results(d1: str, d2: str | None) -> None:
    """zqsgkj 开奖口径：按体彩开奖日拉赛果（含场次编号+比分+体彩赔率）→ engine/cache/sporttery_results_*.json。"""
    start = date.fromisoformat(d1)
    end = date.fromisoformat(d2) if d2 else start
    if (end - start).days > 29:
        log("sporttery", "区间超 30 天（体彩限制），截断为前 30 天")
        end = start + timedelta(days=29)
    out_matches = []
    cur = start
    while cur <= end:
        seg_end = min(cur + timedelta(days=29), end)
        d = get_json(DRAW_RESULT_URL, {"matchBeginDate": cur.isoformat(), "matchEndDate": seg_end.isoformat(),
                                       "leagueId": "", "pageSize": 30, "pageNo": 1, "isFix": 0,
                                       "matchPage": 2, "pcOrWap": 1})  # matchPage=2 实测直接返回区间全量
        for m in d.get("value", {}).get("matchResult") or []:
            if not m.get("matchNumStr"):
                continue
            out_matches.append({
                "code": m["matchNumStr"],
                "matchId": m.get("matchId"),
                "league": m.get("leagueNameAbbr"),
                "home": m.get("homeTeam"),
                "away": m.get("awayTeam"),
                "matchDate": m.get("matchDate"),
                "score": m.get("sectionsNo999") or None,
                "halfScore": m.get("sectionsNo1") or None,
                "had": {"h": m.get("h"), "d": m.get("d"), "a": m.get("a")} if m.get("h") else None,
                "goalLine": m.get("goalLine"),
                "status": "Played" if m.get("sectionsNo999") else "Fixture",
            })
        cur = seg_end + timedelta(days=1)
        time.sleep(0.3)
    out = CACHE_DIR / f"sporttery_results_{start.isoformat()}_{end.isoformat()}.json"
    payload = {"fetchedAt": date.today().isoformat(), "source": "sporttery", "count": len(out_matches), "matches": out_matches}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    played = sum(1 for m in out_matches if m["status"] == "Played")
    log("sporttery", f"{start}~{end}: {len(out_matches)} 场（已完赛 {played}）→ {out.name}")
    for m in out_matches[:5]:
        log("sporttery", f"  {m['code']} {m['league']} {m['home']} {m['score'] or '未开'} {m['away']}")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "league-results":
        cmd_league_results(args[1], args[2] if len(args) > 2 else None)
        return
    if args and args[0] == "results":
        if len(args) < 2:
            log("sporttery", "用法: python sporttery_fetch.py results <YYYY-MM-DD> [止日期]")
            return
        cmd_results(args[1], args[2] if len(args) > 2 else None)
        return
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
