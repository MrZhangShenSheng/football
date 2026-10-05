# -*- coding: utf-8 -*-
"""hist_odds 赛事批量入库器（2026-10-05 立项·ingest_uefa_nations 泛化版）。

用法：
  python ingest_hist_league.py --dry                # 全赛事扫描：已知别名覆盖率+未知队清单（不入库）
  python ingest_hist_league.py --ingest 欧冠,欧罗巴  # 指定赛事入库（league库+别名组·幂等增量）
  python ingest_hist_league.py --ingest all         # 全部缺口赛事入库
队名解析优先级：现有 _aliases 全表反查（俱乐部大多已有）→ EXTRA_ZH_TO_ID 补充表 → 未知队报清单。
开发者 sszhang
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[3]
HIST_DIR = ROOT / "engine" / "cache" / "hist_odds"
LEAGUE_DIR = ROOT / "data" / "02-results" / "league"
ALIASES_PATH = ROOT / "data" / "01-teams" / "_aliases.json"
from common import load_aliases   # noqa: E402

# 赛事中文名 → league 库键（None=不入库·仅展示）
LEAGUE_MAP = {
    "欧冠": "uefa-champions", "欧罗巴": "uefa-europa", "世预赛": "world-cup-qual",
    "亚冠精英": "afc-champions-elite", "欧协联": "uefa-conference", "世界杯": "world-cup",
    "欧预赛": "euro-qual", "俱世界杯": "club-world-cup", "国际赛": "international-friendly",
    "澳超": "australia-a-league", "日乙": "japan-j2", "法乙": "france-ligue2",
    "荷乙": "netherlands-eerste", "芬超": "finland-veikkaus", "英甲": "england-league-one",
    "英联赛杯": "england-efl-cup", "日联赛杯": "japan-league-cup", "英足总杯": "england-fa-cup",
}
# 已入库联赛中文名（跳过）
DONE_ZH = {"英超": "england-premier", "西甲": "spain-laliga", "意甲": "italy-serie-a",
           "德甲": "germany-bundesliga", "法甲": "france-ligue1", "英冠": "england-championship",
           "德乙": "germany-bundesliga2", "葡超": "portugal-primeira", "日职": "japan",
           "美职": "usa", "瑞超": "sweden", "荷甲": "netherlands-eredivisie",
           "挪超": "norway", "韩职": "korea", "沙职": "saudi", "欧国联": "uefa-nations",
           "解放者杯": "libertadores"}

# 国家队补充映射（世预赛/世界杯/欧预赛等 uefa-nations 44 队之外的国家队）
NATION_EXTRA = {
    "巴西": "brazil-nat", "阿根廷": "argentina-nat", "日本": "japan-nat", "韩国": "korea-nat",
    "中国": "china-nat", "澳洲": "australia-nat", "澳大利亚": "australia-nat", "伊朗": "iran-nat",
    "乌拉圭": "uruguay-nat", "哥伦比亚": "colombia-nat", "厄瓜多尔": "ecuador-nat",
    "智利": "chile-nat", "秘鲁": "peru-nat", "巴拉圭": "paraguay-nat", "委内瑞拉": "venezuela-nat",
    "玻利维亚": "bolivia-nat", "墨西哥": "mexico-nat", "美国": "usa-nat", "加拿大": "canada-nat",
    "哥斯达": "costa-rica-nat", "巴拿马": "panama-nat", "洪都拉斯": "honduras-nat",
    "牙买加": "jamaica-nat", "特立尼达和多巴哥": "trinidad-tobago-nat", "库拉索": "curacao-nat",
    "海地": "haiti-nat", "苏里南": "suriname-nat", "尼加拉瓜": "nicaragua-nat",
    "危地马拉": "guatemala-nat", "萨尔瓦多": "el-salvador-nat",
    "埃及": "egypt-nat", "尼日利亚": "nigeria-nat", "南非": "south-africa-nat",
    "加纳": "ghana-nat", "塞内加尔": "senegal-nat", "喀麦隆": "cameroon-nat",
    "摩洛哥": "morocco-nat", "阿尔及利亚": "algeria-nat", "突尼斯": "tunisia-nat",
    "科特迪瓦": "cote-divoire-nat", "刚果": "congo-nat", "赞比亚": "zambia-nat",
    "乌干达": "uganda-nat", "肯尼亚": "kenya-nat",
    "乌兹别克": "uzbekistan-nat", "约旦": "jordan-nat", "沙特": "saudi-nat",
    "卡塔尔": "qatar-nat", "阿曼": "oman-nat", "伊拉克": "iraq-nat", "巴林": "bahrain-nat",
    "科威特": "kuwait-nat", "黎巴嫩": "lebanon-nat", "叙利亚": "syria-nat",
    "也门": "yemen-nat", "巴勒斯坦": "palestine-nat", "泰国": "thailand-nat",
    "越南": "vietnam-nat", "马来西亚": "malaysia-nat", "印尼": "indonesia-nat",
    "印度": "india-nat", "新加坡": "singapore-nat", "菲律宾": "philippines-nat",
    "中国香港": "hong-kong-nat", "中国台北": "chinese-taipei-nat", "蒙古": "mongolia-nat",
    "尼泊尔": "nepal-nat", "缅甸": "myanmar-nat", "柬埔寨": "cambodia-nat",
    "新西兰": "new-zealand-nat", "斐济": "fiji-nat", "塔希提": "tahiti-nat",
    "新喀里多": "new-caledonia-nat", "巴布亚新几内亚": "papua-new-guinea-nat",
    "列支敦": "liechtenstein-nat", "刚果金": "dr-congo-nat", "吉尔吉斯": "kyrgyzstan-nat",
    "土库曼": "turkmenistan-nat", "圣马力诺": "san-marino-nat", "塔吉克": "tajikistan-nat",
    "孟加拉": "bangladesh-nat", "安道尔": "andorra-nat", "巴基斯坦": "pakistan-nat",
    "摩尔多瓦": "moldova-nat", "直布罗陀": "gibraltar-nat", "阿联酋": "uae-nat",
    "佛得角": "cabo-verde-nat", "阿尔及利": "algeria-nat", "乌克兰": "ukraine-nat",
    "以色列": "israel-nat", "白俄罗斯": "belarus-nat",
}
# 非国家队赛事的补充俱乐部映射（欧冠/欧罗巴/次级联赛的小联赛冠军等·按 dry-run 清单补）
CLUB_EXTRA: dict[str, str] = {}

CLUB_EXTRA.update({
    "流浪者": "rangers", "塞萨洛": "paok", "帕纳辛纳": "panathinaikos", "布星": "slovan-bratislava",
    "年轻人": "young-boys", "卢多戈雷": "ludogorets", "亨克": "genk", "奥林匹亚": "olimpija-ljubljana",
    "圣吉联合": "union-saint-gilloise", "格风暴": "sturm-graz", "卡拉巴赫": "qarabag",
    "布赖顿": "brighton", "里耶卡": "rijeka", "贝西克塔": "besiktas", "里加足校": "rfs-riga",
    "基迪纳摩": "dynamo-kyiv", "塞尔维特": "servette", "谢里夫": "sheriff-tiraspol",
    "新圣徒": "the-new-saints", "布斯巴达": "sparta-praha", "比亚韦": "jagiellonia",
    "帕福斯": "pafos", "巴塞尔": "basel", "哈茨": "hearts", "色格拉": "cercle-brugge",
    "拉纳卡": "aek-larnaca", "克卢日": "cfr-cluj", "斯海杜克": "hajduk-split",
    "克拉克斯": "klaksvik", "阿伯丁": "aberdeen", "普博泰夫": "botev-plovdiv",
    "伏伊伏丁": "vojvodina", "安特卫普": "antwerp", "沙姆洛克": "shamrock-rovers",
    "贝游击": "partizan", "谢尔本": "shelbourne", "林菲尔德": "linfield",
    "苏捷斯卡": "sutjeska", "杰尔": "gyor", "比森": "beerschot", "波兹南": "lech-poznan",
    "基马诺克": "kilmarnock", "维快速": "rapid-wien", "特斯巴达": "spartak-trnava",
    "保克什": "paks", "希伯尼安": "hibernian", "沃尔夫斯": "wolfsberger-ac",
    "兹林尼": "zrinjski", "日利纳": "zilina", "索陆军": "cska-sofia",
    "德里城": "derry-city", "圣加仑": "st-gallen", "克拉约瓦": "u-craiova",
    "顿矿工": "shakhtar-donetsk", "埃凤凰": "pyunik-yerevan", "明迪纳摩": "dynamo-minsk",
    "雷克维京": "vikingur-reykjavik", "迪弗当日": "differdange", "拉恩": "larne",
    "卢加诺": "lugano",
"马里博尔": "maribor", "齐拉": "zira", "巴战士": "zeljeznicar",
    "红色小鬼": "lincoln-red-imps", "克里特": "ofi-crete", "亚拉腊": "ararat-armenia",
    "奥林": "olimpija-ljubljana", "托林斯": "tammeka-tartu",
})
CLUB_EXTRA.update({
    "亚眠": "amiens", "卡昂": "caen", "巴斯蒂亚": "bastia", "奎维利": "quevilly",
    "孔卡诺": "concarneau", "瓦朗谢纳": "valenciennes",
})


def _split_score(score) -> tuple[int, int] | None:
    try:
        h, a = str(score).split(":")
        return int(h), int(a)
    except (ValueError, AttributeError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--ingest", default="")
    args = ap.parse_args()

    rows = []
    for f in sorted(HIST_DIR.glob("crs_hist_*.json")):
        rows += json.loads(f.read_text(encoding="utf-8"))["matches"]
    by_lg: dict[str, list] = {}
    for m in rows:
        lg = str(m.get("league", ""))
        if lg:
            by_lg.setdefault(lg, []).append(m)

    zh2id = {}
    for tid, srcs in load_aliases().items():
        for zh in [srcs.get("zh"), *(srcs.get("variants") or [])]:
            if zh:
                zh2id.setdefault(zh, tid)
    targets = [lg for lg in by_lg if lg in LEAGUE_MAP]
    if args.dry:
        print(f"== dry-run：{len(targets)} 个缺口赛事 · 队名已知别名覆盖率 ==")
        unknown_all = {}
        for lg in sorted(targets, key=lambda x: -len(by_lg[x])):
            n, known, unknown = len(by_lg[lg]), set(), {}
            for m in by_lg[lg]:
                for side in ("home", "away"):
                    zh = m[side]
                    if zh in zh2id or zh in NATION_EXTRA or zh in CLUB_EXTRA:
                        known.add(zh)
                    else:
                        unknown[zh] = unknown.get(zh, 0) + 1
            print(f"  {lg:6s} {n:4d}场 已知 {len(known):3d}/{len(known)+len(unknown):3d} 队"
                  + (f" · 未知TOP: {sorted(unknown.items(), key=lambda kv: -kv[1])[:8]}" if unknown else " ✓全解"))
            unknown_all[lg] = unknown
        Path("_dry_unknown.json").write_text(json.dumps(unknown_all, ensure_ascii=False, indent=1))
        print("未知清单全量 → _dry_unknown.json")
        return

    if args.ingest != "all":
        want = {s.strip() for s in args.ingest.split(",") if s.strip()}
        targets = [lg for lg in targets if lg in want]
    aliases = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    meta = aliases.pop("_meta", None)
    for lg in targets:
        key = LEAGUE_MAP[lg]
        out_path = LEAGUE_DIR / f"{key}_matches.json"
        out_rows, seen = [], set()
        if out_path.exists():
            out_rows = json.loads(out_path.read_text(encoding="utf-8")).get("matches", [])
            seen = {(r["date"], r["home"], r["away"]) for r in out_rows}
        unknown, alias_new = set(), {}
        ingested = 0
        for m in by_lg[lg]:
            hg_ag = _split_score(m.get("score"))
            if hg_ag is None:
                continue
            pair = []
            for side in ("home", "away"):
                zh = m[side]
                tid = zh2id.get(zh) or NATION_EXTRA.get(zh) or CLUB_EXTRA.get(zh)
                if not tid:
                    unknown.add(zh)
                    tid = zh                       # 未映射：原中文名落库（保持可读·可后补）
                pair.append(tid)
            if not unknown:
                alias_new.setdefault(pair[0], zh if pair[0] not in zh2id else None)
            k = (str(m["date"])[:10], pair[0], pair[1])
            if k in seen:
                continue
            seen.add(k)
            out_rows.append({"date": k[0], "home": pair[0], "away": pair[1],
                             "hg": hg_ag[0], "ag": hg_ag[1]})
            ingested += 1
        out_rows.sort(key=lambda r: r["date"])
        out_path.write_text(json.dumps(
            {"league": key, "source": f"sporttery hist_odds ({lg})", "matches": out_rows},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[ingest] {lg} → {key}: {len(out_rows)} 场（本次新增 {ingested}）"
              + (f" ⚠未映射队 {sorted(unknown)}（原中文名落库·别名后补再重跑归一）" if unknown else " ✓全映射"))


if __name__ == "__main__":
    main()
