#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""赛果自动回填：02-results 未回填记录 → 双链路对票 → 写回。

闭环 P2-A（docs/2026-08-22-learning-loop-design.html）· P0 修复 2026-08-23：
- 链路 1（主）：体彩 zqsgkj 开奖口径按场次编号精确对票（含半场比分，半全场命中可判定）
- 链路 2（兜底）：espn_fetch results 按日拉赛果，中文队名经 _aliases zh → espn 匹配
- 写回规则（铁律 7）：只改 result/directionHit/scoreHit/backfillNote 字段，不动预测锁定字段
- 输出：本轮回填 N/M（体彩对票 K）+ 不可得清单

用法：
  python backfill.py               # 扫全部 02-results 未回填（比赛日已过）
  python backfill.py 2026-08-22    # 只处理指定轮
"""
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from common import load_aliases, log, ROOT

RESULTS_DIR = ROOT / "data" / "02-results"
SALES_CACHE = ROOT / "engine" / "cache" / "sporttery_matches.json"  # 在售缓存（预测 Step 1 必存）
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/{}/scoreboard"
UNAVAILABLE_MARK = "不可得"  # 双链路均无赛果时的 result 标记（重跑时会重试）
PLAY_PREFIXES = ("HAD", "HHAD", "TTG", "HAFU", "CRS")  # v4.6 pick 玩法前缀
HAFU_LETTER = {"h": 0, "d": 1, "a": 2}  # 半全场字母 → 三向索引（主胜0/平1/客胜2）

# 中文联赛名 → ESPN 代码（corpus league 字段口径）
LEAGUE_ESPN = {
    "英超": "eng.1", "西甲": "esp.1", "德甲": "ger.1", "意甲": "ita.1", "法甲": "fra.1",
    "荷甲": "ned.1", "葡超": "por.1", "日职": "jpn.1", "沙特": "ksa.1", "沙职": "ksa.1", "沙特联": "ksa.1",
    "瑞超": "swe.1", "挪超": "nor.1", "丹超": "den.1", "苏超": "sco.1",
}
# ESPN 无赛果接口的联赛（实测 400/空）
ESPN_UNAVAILABLE = {"韩职", "日乙", "芬超", "荷乙", "法乙", "德乙", "英冠", "美职", "巴甲",
                    "德超杯", "德国杯", "欧罗巴", "欧冠", "解放者杯", "世界杯", "友谊赛"}
TODAY = date.today().isoformat()


def zh_to_espn_map() -> dict[str, str]:
    """中文名 → ESPN 名（经 _aliases zh 字段反查）。"""
    out = {}
    for tid, srcs in load_aliases().items():
        zh = srcs.get("zh")
        espn = srcs.get("espn")
        if zh and espn:
            out[zh] = espn
    return out


def fetch_espn_results(code: str, d: str) -> list[dict]:
    """ESPN 单日赛果 → [{home_espn, away_espn, hg, ag}]。dates 必须 YYYYMMDD 无连字符（带连字符返回空）。"""
    try:
        r = requests.get(SCOREBOARD.format(code), params={"dates": d.replace("-", "")}, timeout=20)
        r.raise_for_status()
    except requests.RequestException:
        return []
    out = []
    for ev in r.json().get("events", []):
        comp = ev.get("competitions", [{}])[0]
        if comp.get("status", {}).get("type", {}).get("name") != "STATUS_FULL_TIME":
            continue
        row = {}
        ok = True
        for c in comp.get("competitors", []):
            sc = c.get("score")
            if sc is None:
                ok = False
                break
            side = "home" if c.get("homeAway") == "home" else "away"
            row[side] = c["team"]["displayName"]
            row["hg" if side == "home" else "ag"] = int(float(sc))
        if ok and row.get("home"):
            out.append(row)
    return out


def match_team(zh_name: str, zh_map: dict[str, str], espn_home: str, espn_away: str) -> str | None:
    """中文队名匹配 ESPN 主/客。支持 'X vs Y' 记录里取主客中文名精确对 espn 名。"""
    espn = zh_map.get(zh_name)
    if espn is None:
        return None
    if espn == espn_home:
        return "home"
    if espn == espn_away:
        return "away"
    return None


def fetch_sporttery_day(d: str) -> dict[str, dict]:
    """体彩 zqsgkj 开奖口径单日赛果 → {场次编号: {score/halfScore/status}}（回填对票主链路）。"""
    from sporttery_fetch import DRAW_RESULT_URL, get_json
    try:
        data = get_json(DRAW_RESULT_URL, {"matchBeginDate": d, "matchEndDate": d, "leagueId": "",
                                          "pageSize": 30, "pageNo": 1, "isFix": 0, "matchPage": 2, "pcOrWap": 1})
    except Exception:
        return {}
    out = {}
    for m in data.get("value", {}).get("matchResult") or []:
        code = m.get("matchNumStr")
        if code:
            out[code] = {"score": m.get("sectionsNo999"), "halfScore": m.get("sectionsNo1"),
                         "matchDate": m.get("matchDate"),
                         "status": "Played" if m.get("sectionsNo999") else "Fixture"}
    return out


def match_sporttery(code: str | None, d: str, sp_cache: dict) -> dict | None:
    """编号对票：扫 d-2~d+3 体彩票池，返回票池条目（含 Fixture，由调用方判定）。

    zqsgkj 的 matchDate=完赛自然日（实测 8-21 桶混周四/周五编号）：预测日 d 的轮次
    覆盖当晚~第三天凌晨完赛（周一场最晚 d+3），凌晨跨日场最晚落 d+2~d+3 桶。
    编号一周内唯一，6 天窗口无歧义。
    """
    if not code:
        return None
    base = datetime.strptime(d, "%Y-%m-%d").date()
    for delta in (-2, -1, 0, 1, 2, 3):
        dd = (base + timedelta(days=delta)).isoformat()
        if dd not in sp_cache:
            sp_cache[dd] = fetch_sporttery_day(dd)
        m = sp_cache[dd].get(code)
        if m:
            return m
    return None


def load_kickoffs() -> dict[str, str]:
    """在售缓存 → {场次编号: 'YYYY-MM-DD HH:MM:SS'}。zqsgkj 只返回已完赛场次，
    未开赛判定须查在售缓存的开赛时间（否则今晚场会被误标'不可得'）。"""
    try:
        data = json.loads(SALES_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for m in data.get("matches") or []:
        if m.get("code"):
            out[m["code"]] = f"{m.get('matchDate', '')} {m.get('matchTime', '')}".strip()
    return out


def strip_play(pick: str) -> str:
    """剥 v4.6 玩法前缀 + 括号注释 + 状态尾词：'HAFU dd(平/平)(方案外)' → 'dd'。

    pick 常带人工注释（"(终审剔除)"/"（用户实票@1.70存续）"/"...维持"），不清掉会导致
    directionHit 判 None（2026-08-23 周六028 5-0 主胜漏判事故）。
    """
    parts = pick.split(" ", 1)
    body = parts[1].strip() if parts[0] in PLAY_PREFIXES and len(parts) == 2 else pick
    body = re.sub(r"[（(][^（）()]*[)）]", " ", body)
    return body.replace("维持", "").replace("存续", "").strip()


def parse_score(s: str | None) -> tuple[int, int] | None:
    """比分文本 → (主, 客)。兼容体彩 '2:1' 与本地 '2-1' 两种分隔。"""
    m = re.match(r"^(\d+)\s*[:-]\s*(\d+)$", str(s or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_match_str(match: str) -> tuple[str, str]:
    """'鹿岛鹿角 vs 福冈黄蜂' → (主中文名, 客中文名)。"""
    parts = re.split(r"\s+vs\s+| VS | 对 ", str(match or ""))
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", ""


def outcome_of(hg: int, ag: int) -> int:
    return 0 if hg > ag else (1 if hg == ag else 2)


def pick_outcome_idx(rec: dict) -> int | None:
    """推荐选项 → 三向索引（主胜0/平1/客胜2；其余玩法由 option_hit 判定）。"""
    pick = strip_play(str(rec.get("pick") or ""))
    if pick in ("主胜", "胜"):
        return 0
    if pick in ("平", "平局"):
        return 1
    if pick in ("客胜", "负"):
        return 2
    return None  # 比分/总进球/半全场 pick → option_hit 判定


def option_hit(rec: dict, hg: int, ag: int, hhg: int | None = None, hag: int | None = None) -> bool | None:
    """选项命中判定（方向/比分/总进球/半全场 pick 文本，带玩法前缀均可）。

    hhg/hag 为半场比分（体彩口径 halfScore 可判半全场；ESPN 不带 → 不判）。
    """
    pick = strip_play(str(rec.get("pick") or ""))
    oi = pick_outcome_idx(rec)
    if oi is not None:
        return outcome_of(hg, ag) == oi
    m = re.match(r"^(\d+)\s*-\s*(\d+)$", pick)
    if m:  # 比分
        return int(m.group(1)) == hg and int(m.group(2)) == ag
    m = re.match(r"^(\d)\+?$", pick)
    if m:  # 总进球 N 球 / N+球
        total = hg + ag
        n = int(m.group(1))
        return total >= n if pick.endswith("+") else total == n
    if len(pick) == 2 and pick[0] in HAFU_LETTER and pick[1] in HAFU_LETTER:  # 半全场
        if hhg is None or hag is None:
            return None  # 无半场数据不判
        return outcome_of(hhg, hag) == HAFU_LETTER[pick[0]] and outcome_of(hg, ag) == HAFU_LETTER[pick[1]]
    return None


def backfill(day_limit: str | None = None) -> dict:
    zh_map = zh_to_espn_map()
    # 收集未回填记录（result 为空或'不可得'均重试——'不可得'曾因 ESPN 单链路断粮，体彩可救回）
    targets = []  # (path, data, rec, date, espn_code, league)
    n_fix = 0
    for p in sorted(RESULTS_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        recs = data.get("records") or data.get("matches") or []
        for rec in recs:
            if not rec.get("pick"):
                continue
            if rec.get("result") not in (None, UNAVAILABLE_MARK):
                # 已回填但方向未判（旧版 strip_play 判不出带注释 pick）→ 纯本地补算，不重查网络
                sc = parse_score(rec.get("result"))
                oi = pick_outcome_idx(rec)
                if sc and oi is not None and rec.get("directionHit") is None:
                    rec["directionHit"] = outcome_of(*sc) == oi
                    data["_dirty"] = True
                    n_fix += 1
                continue
            d = rec.get("date") or data.get("date")
            if not d or d > TODAY:
                continue  # 未开赛
            if day_limit and d > day_limit:
                continue
            lg = str(rec.get("league") or "").split("(")[0].strip()
            targets.append((p, data, rec, d, LEAGUE_ESPN.get(lg), lg))

    # ESPN 按日期+联赛拉赛果（去重请求）。北京日期 vs ESPN 日期差 ±1 天 → 扫 d-1/d/d+1 三天窗口
    cache: dict[tuple[str, str], list[dict]] = {}

    def rows_for(code: str, d: str) -> list[dict]:
        out = []
        base = datetime.strptime(d, "%Y-%m-%d").date()
        for delta in (-1, 0, 1):
            dd = (base + timedelta(days=delta)).isoformat()
            key = (code, dd)
            if key not in cache:
                cache[key] = fetch_espn_results(code, dd)
            out.extend(cache[key])
        return out

    n_fill = n_miss = n_sp = 0
    sp_cache: dict[str, dict[str, dict]] = {}
    kickoffs = load_kickoffs()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for p, data, rec, d, code, lg in targets:
        ko = kickoffs.get(rec.get("code"))
        if ko and ko >= now:
            if rec.get("result") == UNAVAILABLE_MARK:  # 清旧误标（未开赛曾被标'不可得'）
                rec["result"] = None
                rec["directionHit"] = None
                rec.pop("backfillNote", None)
                data["_dirty"] = True
            continue  # 在售缓存确认未开赛（zqsgkj 只返回已完赛，查了也白查），完赛后重跑自动回填
        # 链路 1（主）：体彩编号对票——精确匹配，不受 ESPN 停摆/队名映射缺口影响，含半场比分
        sp = match_sporttery(rec.get("code"), d, sp_cache)
        sp_score = parse_score(sp.get("score")) if sp else None
        if sp_score:
            hg, ag = sp_score
            hh, ha = parse_score(sp.get("halfScore")) or (None, None)
            rec["result"] = f"{hg}-{ag}"
            oi = pick_outcome_idx(rec)
            rec["directionHit"] = (outcome_of(hg, ag) == oi) if oi is not None else None
            oh = option_hit(rec, hg, ag, hh, ha)
            rec["scoreHit"] = oh if oh is not None else rec.get("scoreHit")
            rec.pop("backfillNote", None)  # 救回成功，清'不可得/缓存延迟'旧标注
            n_fill += 1
            n_sp += 1
            data["_dirty"] = True
            continue
        if sp and sp["status"] == "Fixture":
            continue  # 体彩确认未开赛——本轮跳过（不标'不可得/缓存延迟'），完赛后重跑自动回填
        # 链路 2（兜底）：ESPN 队名匹配（体彩编号对票失败时仍可能覆盖）
        hit_row = None
        if code:
            rows = rows_for(code, d)
            zh_home, zh_away = parse_match_str(rec.get("match"))
            for row in rows:
                h_side = match_team(zh_home, zh_map, row["home"], row["away"])
                a_side = match_team(zh_away, zh_map, row["home"], row["away"])
                if h_side == "home" and a_side == "away":
                    hit_row = row
                    break
        if hit_row:
            hg, ag = hit_row["hg"], hit_row["ag"]
            rec["result"] = f"{hg}-{ag}"
            oi = pick_outcome_idx(rec)
            rec["directionHit"] = (outcome_of(hg, ag) == oi) if oi is not None else None
            oh = option_hit(rec, hg, ag)
            rec["scoreHit"] = oh if oh is not None else rec.get("scoreHit")
            n_fill += 1
            data["_dirty"] = True
            continue
        n_miss += 1
        if lg in ESPN_UNAVAILABLE or not code:
            rec["result"] = UNAVAILABLE_MARK
            rec["directionHit"] = None
            rec["backfillNote"] = f"{lg} 体彩/ESPN 均无赛果"
        else:
            rec.setdefault("backfillNote", "ESPN 近 3 日赛果可能未更新（缓存延迟），可稍后重跑")
        data["_dirty"] = True
    # 统一写回
    written = set()
    for p, data, *_ in targets:
        if id(data) not in written and data.pop("_dirty", False):
            p.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            written.add(id(data))
    return {"filled": n_fill, "sporttery": n_sp, "missed": n_miss, "fixed": n_fix}


def main() -> None:
    day_limit = sys.argv[1] if len(sys.argv) > 1 else None
    res = backfill(day_limit)
    log("backfill", f"回填 {res['filled']} 场（体彩对票 {res['sporttery']}）· 无匹配 {res['missed']} 场"
                   f"（体彩/ESPN 均无 → 标不可得；ESPN 缓存延迟 → 稍后重跑）"
                   f" · 补算方向 {res['fixed']} 条（带注释 pick 旧漏判）")
    log("backfill", "回填后跑 run.py corpus 刷新语料+趋势报告")


if __name__ == "__main__":
    main()
