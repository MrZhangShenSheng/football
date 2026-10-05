# -*- coding: utf-8 -*-
"""欧国联历史赛果入库（2026-10-05 立项·ingest_asian_u23 同款先例复刻）。

背景：实力链生产跑 2026-10-05 欧国联 7 场全部 zh_to_id 映射失败（国家队不在别名表）——
hist_odds 里有 149 场欧国联历史（2024-09~2026-09·44 队·全带比分），本脚本把它入库：
  1. data/02-results/league/uefa-nations_matches.json（{league,source,matches:[{date,home,away,hg,ag}]}·kebab规范ID）
  2. data/01-teams/_aliases.json 顶层加 uefa-nations 组（44 队 {zh, clubelo:null, understat:null, espn, variants:[]}）
幂等：重跑按 (date,home,away) 去重只增新场；别名组整组覆写（映射表是唯一真源）。
未知队名（映射表外）报错退出非静默。可重跑增量。

开发者 sszhang
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[3]
HIST_DIR = ROOT / "engine" / "cache" / "hist_odds"
LEAGUE_OUT = ROOT / "data" / "02-results" / "league" / "uefa-nations_matches.json"
ALIASES_PATH = ROOT / "data" / "01-teams" / "_aliases.json"
LEAGUE_KEY = "uefa-nations"
SOURCE_TAG = "欧国联"

# 44 队中文 → kebab 规范ID（体彩中文名以 hist_odds 实际出现为准：斯洛文尼/阿尔巴尼/哈萨克 为体裁缩写）
ZH_TO_ID = {
    "丹麦": "denmark", "亚美尼亚": "armenia", "保加利亚": "bulgaria", "克罗地亚": "croatia",
    "冰岛": "iceland", "匈牙利": "hungary", "北爱尔兰": "northern-ireland", "北马其顿": "north-macedonia",
    "卢森堡": "luxembourg", "哈萨克": "kazakhstan", "土耳其": "turkiye", "塞尔维亚": "serbia",
    "塞浦路斯": "cyprus", "奥地利": "austria", "威尔士": "wales", "希腊": "greece",
    "德国": "germany", "意大利": "italy", "拉脱维亚": "latvia", "挪威": "norway",
    "捷克": "czechia", "斯洛伐克": "slovakia", "斯洛文尼": "slovenia", "格鲁吉亚": "georgia",
    "比利时": "belgium", "法国": "france", "法罗群岛": "faroe-islands", "波兰": "poland",
    "波黑": "bosnia-and-herzegovina", "爱尔兰": "ireland", "爱沙尼亚": "estonia", "瑞典": "sweden",
    "瑞士": "switzerland", "科索沃": "kosovo", "罗马尼亚": "romania", "芬兰": "finland",
    "苏格兰": "scotland", "英格兰": "england", "荷兰": "netherlands", "葡萄牙": "portugal",
    "西班牙": "spain", "阿塞拜疆": "azerbaijan", "阿尔巴尼": "albania", "黑山": "montenegro",
}
# kebab ID → ESPN 显示名（国家队 ESPN 口径；无 ESPN 的记 null，首发轨将如实记 no_alias）
ID_TO_ESPN = {
    "denmark": "Denmark", "armenia": "Armenia", "bulgaria": "Bulgaria", "croatia": "Croatia",
    "iceland": "Iceland", "hungary": "Hungary", "northern-ireland": "Northern Ireland",
    "north-macedonia": "North Macedonia", "luxembourg": "Luxembourg", "kazakhstan": "Kazakhstan",
    "turkiye": "Turkey", "serbia": "Serbia", "cyprus": "Cyprus", "austria": "Austria",
    "wales": "Wales", "greece": "Greece", "germany": "Germany", "italy": "Italy",
    "latvia": "Latvia", "norway": "Norway", "czechia": "Czech Republic", "slovakia": "Slovakia",
    "slovenia": "Slovenia", "georgia": "Georgia", "belgium": "Belgium", "france": "France",
    "faroe-islands": "Faroe Islands", "poland": "Poland", "bosnia-and-herzegovina": "Bosnia & Herzegovina",
    "ireland": "Ireland", "estonia": "Estonia", "sweden": "Sweden", "switzerland": "Switzerland",
    "kosovo": "Kosovo", "romania": "Romania", "finland": "Finland", "scotland": "Scotland",
    "england": "England", "netherlands": "Netherlands", "portugal": "Portugal", "spain": "Spain",
    "azerbaijan": "Azerbaijan", "albania": "Albania", "montenegro": "Montenegro",
}


def _split_score(score: str) -> tuple[int, int] | None:
    try:
        h, a = str(score).split(":")
        return int(h), int(a)
    except (ValueError, AttributeError):
        return None


def main() -> None:
    rows = []
    for f in sorted(HIST_DIR.glob("crs_hist_*.json")):
        rows += json.loads(f.read_text(encoding="utf-8"))["matches"]
    nations = [m for m in rows if SOURCE_TAG in str(m.get("league", ""))]

    out_rows, skipped_noscore, seen = [], 0, set()
    if LEAGUE_OUT.exists():                       # 幂等：已有场并入再去重
        prev = json.loads(LEAGUE_OUT.read_text(encoding="utf-8"))
        out_rows = prev.get("matches", [])
        seen = {(r["date"], r["home"], r["away"]) for r in out_rows}
    unknown = set()
    for m in nations:
        hg_ag = _split_score(m.get("score"))
        if hg_ag is None:
            skipped_noscore += 1
            continue
        home, away = ZH_TO_ID.get(m["home"]), ZH_TO_ID.get(m["away"])
        if not home or not away:
            unknown |= {m["home"], m["away"]} - set(ZH_TO_ID)
            continue
        key = (str(m["date"])[:10], home, away)
        if key in seen:
            continue
        seen.add(key)
        out_rows.append({"date": key[0], "home": home, "away": away,
                         "hg": hg_ag[0], "ag": hg_ag[1]})
    if unknown:
        sys.exit(f"[ingest] 未知队名（补 ZH_TO_ID 再跑）: {sorted(unknown)}")
    out_rows.sort(key=lambda r: r["date"])
    LEAGUE_OUT.write_text(json.dumps(
        {"league": LEAGUE_KEY, "source": f"sporttery hist_odds ({SOURCE_TAG})",
         "matches": out_rows}, ensure_ascii=False, indent=1), encoding="utf-8")

    aliases = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    meta = aliases.pop("_meta", None)
    aliases[LEAGUE_KEY] = {tid: {"zh": zh, "clubelo": None, "understat": None,
                                 "espn": ID_TO_ESPN.get(tid), "variants": []}
                           for zh, tid in sorted(ZH_TO_ID.items(), key=lambda kv: kv[1])}
    if meta:
        aliases["_meta"] = meta
    ALIASES_PATH.write_text(json.dumps(aliases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[ingest] {LEAGUE_OUT.name}: {len(out_rows)} 场（跳过无比分 {skipped_noscore}）·"
          f"别名组 {LEAGUE_KEY}: {len(ZH_TO_ID)} 队")


if __name__ == "__main__":
    main()
