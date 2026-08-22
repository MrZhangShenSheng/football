#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""赛果自动回填：02-results 未回填记录 → ESPN 按日拉赛果 → 别名匹配 → 写回。

闭环 P2-A（docs/2026-08-22-learning-loop-design.html）：
- 数据源：espn_fetch results（主流联赛全覆盖；韩职/jpn.2 ESPN 无结果 → 标"赛果不可得"）
- 匹配：中文队名经 _aliases.json zh → espn 反查 → 比对日期+主客方向 → 写回
- 写回规则（铁律 7）：只改 result/directionHit/scoreHit 字段，不动预测锁定字段
- 输出：本轮回填 N/M + 不可得 K 场清单

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
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/{}/scoreboard"

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


def parse_match_str(match: str) -> tuple[str, str]:
    """'鹿岛鹿角 vs 福冈黄蜂' → (主中文名, 客中文名)。"""
    parts = re.split(r"\s+vs\s+| VS | 对 ", str(match or ""))
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", ""


def outcome_of(hg: int, ag: int) -> int:
    return 0 if hg > ag else (1 if hg == ag else 2)


def pick_outcome_idx(rec: dict) -> int | None:
    """推荐选项 → 三向索引（主胜0/平1/客胜2；比分 pick 由 directionHit 判定回填时单独处理）。"""
    pick = str(rec.get("pick") or "")
    # v4.6 格式可能带玩法前缀
    pick = pick.split(" ", 1)[1] if pick.split(" ", 1)[0] in ("HAD", "HHAD") and " " in pick else pick
    if pick in ("主胜", "胜"):
        return 0
    if pick in ("平", "平局"):
        return 1
    if pick in ("客胜", "负"):
        return 2
    return None  # 比分/总进球/半全场 pick → 只回填 result，option 命中由规则补


def option_hit(rec: dict, hg: int, ag: int) -> bool | None:
    """选项命中判定（支持方向/比分/总进球/半全场 pick 文本）。"""
    pick = str(rec.get("pick") or "")
    oi = pick_outcome_idx(rec)
    if oi is not None:
        return outcome_of(hg, ag) == oi
    m = re.match(r"^(\d)-(\d)$", pick)
    if m:  # 比分
        return int(m.group(1)) == hg and int(m.group(2)) == ag
    m = re.match(r"^TTG\s*(\d)\+?$", pick, re.I)
    if m:  # 总进球 N 球 / N+球
        total = hg + ag
        n = int(m.group(1))
        return total >= n if pick.endswith("+") else total == n
    if pick in ("dd", "hh", "ha", "hd", "aa", "ad", "ah", "da", "dh"):  # 半全场
        half = outcome_of((1 if pick[0] == "h" else (0 if pick[0] == "d" else 0)) * 0 or 0, 0)  # 半场比分未知
        return None  # 半场数据 ESPN scoreboard 默认不带 → 不判 option，只回填 result
    return None


def backfill(day_limit: str | None = None) -> dict:
    zh_map = zh_to_espn_map()
    # 收集未回填记录（按 联赛+日期 分组，批量拉 ESPN）
    targets = []  # (path, data, rec)
    for p in sorted(RESULTS_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        recs = data.get("records") or data.get("matches") or []
        changed = False
        for rec in recs:
            if rec.get("result") or not rec.get("pick"):
                continue
            d = rec.get("date") or (rec.get("code") and None) or data.get("date")
            if not d or d > TODAY:
                continue  # 未开赛
            if day_limit and d > day_limit:
                continue
            lg = str(rec.get("league") or "").split("(")[0].strip()
            if lg in ESPN_UNAVAILABLE:
                rec.setdefault("result", None)
                if rec.get("result") is None:
                    rec["result"] = "不可得"
                    rec["directionHit"] = None
                    rec["backfillNote"] = f"{lg} ESPN 无赛果接口"
                    changed = True
                continue
            code = LEAGUE_ESPN.get(lg)
            if not code:
                continue
            targets.append((p, data, rec, d, code, lg))
        if changed:
            p.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # 按日期+联赛拉赛果（去重请求）。北京日期 vs ESPN 日期差 ±1 天 → 扫 d-1/d/d+1 三天窗口
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

    n_fill = n_miss = 0
    for p, data, rec, d, code, lg in targets:
        rows = rows_for(code, d)
        zh_home, zh_away = parse_match_str(rec.get("match"))
        hit_row = None
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
        else:
            n_miss += 1
            rec.setdefault("backfillNote", "ESPN 近 3 日赛果可能未更新（缓存延迟），可稍后重跑")
            data["_dirty"] = True
    # 统一写回
    written = set()
    for p, data, *_ in targets:
        if id(data) not in written and data.pop("_dirty", False):
            p.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            written.add(id(data))
    return {"filled": n_fill, "missed": n_miss}


def main() -> None:
    day_limit = sys.argv[1] if len(sys.argv) > 1 else None
    res = backfill(day_limit)
    log("backfill", f"回填 {res['filled']} 场 · ESPN 无匹配 {res['missed']} 场（队名映射缺口→补 _aliases espn 字段可恢复）")
    log("backfill", "回填后跑 run.py corpus 刷新语料+趋势报告")


if __name__ == "__main__":
    main()
