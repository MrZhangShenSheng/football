# -*- coding: utf-8 -*-
"""A2 数据地基修复（2026-10-05）：①122 队 fd 缩写名映射注入 _aliases（fd 字段）
②ingest_hist_league 的 CLUB_EXTRA/NATION_EXTRA 中文映射补写别名组（修欧冠五库 zh_to_id 断链）
③法乙入库。幂等可重跑。开发者 sszhang
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
ROOT = Path(__file__).resolve().parents[3]
ALIASES_PATH = ROOT / "data" / "01-teams" / "_aliases.json"

# fd CSV 名 → 规范ID（122 队·2026-10-05 全量人工映射·zh 为常见体彩名用于 zh_to_id·null=hist未出现）
FD_MAP = {
    # 苏格兰
    "Aberdeen": ("aberdeen", "阿伯丁"), "Rangers": ("rangers", "流浪者"), "Dundee": ("dundee", "邓迪"),
    "Dundee United": ("dundee-united", "邓迪联"), "Falkirk": ("falkirk", "法尔科克"),
    "Hearts": ("hearts", "哈茨"), "Hibernian": ("hibernian", "希伯尼安"), "Kilmarnock": ("kilmarnock", "基马诺克"),
    "Motherwell": ("motherwell", "马瑟韦尔"), "St Johnstone": ("st-johnstone", "圣约翰斯顿"),
    "St Mirren": ("st-mirren", "圣米伦"),
    # 西班牙
    "Ath Bilbao": ("athletic-bilbao", "毕尔巴"), "Ath Madrid": ("atletico-madrid", None),
    "Vallecano": ("rayo-vallecano", None), "Espanol": ("espanyol", "西班牙人"),
    "La Coruna": ("deportivo-la-coruna", "拉科鲁"), "Santander": ("racing-santander", "桑坦德"),
    "Oviedo": ("real-oviedo", "奥维耶多"), "Valladolid": ("real-valladolid", "瓦拉多"),
    "Burgos": ("burgos", None), "Castellon": ("castellon", None), "Eibar": ("eibar", "埃瓦尔"),
    "Tenerife": ("tenerife", "特内里费"), "Albacete": ("albacete", None), "Cordoba": ("cordoba-fc", None),
    "Eldense": ("eldense", None), "Sabadell": ("sabadell", None), "Ceuta": ("ad-ceuta", None),
    "Sp Gijon": ("sporting-gijon", "希洪"), "Celta B": ("celta-vigo-b", None),
    "Sociedad B": ("real-sociedad-b", None),
    # 德国
    "Ein Frankfurt": ("eintracht-frankfurt", "法兰克"), "M'gladbach": ("borussia-monchengladbach", None),
    "FC Koln": ("fc-koln", "科隆"), "Darmstadt": ("sv-darmstadt98", "达姆斯塔"),
    "Nurnberg": ("1-fc-nurnberg", "纽伦堡"), "Hertha": ("hertha-berlin", "柏林赫塔"),
    "Hannover": ("hannover-96", "汉诺威"), "Cottbus": ("energie-cottbus", None),
    "Osnabruck": ("vfl-osnabruck", None), "Greuther Furth": ("greuther-furth", None),
    # 法国
    "Paris SG": ("paris-saint-germain", None), "Rennes": ("stade-rennais", "雷恩"),
    "St Etienne": ("saint-etienne", "圣埃蒂安"), "Guingamp": ("guingamp", "甘冈"),
    "Nancy": ("nancy", "南锡"), "Laval": ("laval", "拉瓦勒"), "Sochaux": ("sochaux", "索肖"),
    "Pau FC": ("pau-fc", "波城FC"), "Rodez": ("rodez", "罗德兹"),
    # 意大利
    "Milan": ("ac-milan", None), "Verona": ("hellas-verona", "维罗纳"),
    "Benevento": ("benevento", "贝内文托"), "Catanzaro": ("catanzaro", None),
    "Mantova": ("mantova", None), "Modena": ("modena", "摩德纳"), "Padova": ("padova", None),
    "Sudtirol": ("sudtirol", None), "Vicenza": ("vicenza", None), "Arezzo": ("arezzo", None),
    "Ascoli": ("ascoli", None), "Avellino": ("avellino", None), "Carrarese": ("carrarese", None),
    "Juve Stabia": ("juve-stabia", None), "Virtus Entella": ("virtus-entella", None),
    # 比利时
    "Genk": ("genk", "亨克"), "Gent": ("gent", "根特"), "Antwerp": ("antwerp", "安特卫普"),
    "Charleroi": ("charleroi", "沙勒罗瓦"), "Kortrijk": ("kortrijk", None),
    "Mechelen": ("mechelen", "梅赫伦"), "Standard": ("standard-liege", "标准列日"),
    "St Truiden": ("st-truiden", None), "Westerlo": ("westerlo", None),
    "Lommel SK": ("lommel", None), "Beveren": ("beveren", None),
    "Oud-Heverlee Leuven": ("oh-leuven", None), "RAAL La Louviere": ("raal-la-louviere", None),
    "Waregem": ("zulte-waregem", None),
    # 土耳其
    "Besiktas": ("besiktas", "贝西克塔"), "Kasimpasa": ("kasimpasa", None),
    "Goztep": ("goztepe", "戈泽佩"), "Konyaspor": ("konyaspor", "科尼亚"),
    "Corum": ("corum-fk", None), "Eyupspor": ("eyupspor", None),
    "Alanyaspor": ("alanyaspor", "阿拉尼亚"), "Amedspor": ("amedspor", None),
    "Buyuksehyr": ("buyuksehir-belediye", None), "Erzurumspor": ("erzurumspor", None),
    "Gaziantep": ("gaziantep-fk", "加济安泰"), "Genclerbirligi": ("genclerbirligi", None),
    "Kocaelispor": ("kocaelispor", None), "Rizespor": ("rizespor", "里泽"),
    "Samsunspor": ("samsunspor", "萨姆松"),
    # 希腊
    "AEK": ("aek-athens", "AEK雅典"), "Aris": ("aris-thessaloniki", "阿里斯"),
    "PAOK": ("paok", "塞萨洛"), "Panathinaikos": ("panathinaikos", "帕纳辛纳"),
    "Olympiakos": ("olympiacos", "奥林匹亚"), "OFI Crete": ("ofi-crete", "克里特"),
    "Asteras Tripolis": ("asteras-tripolis", None), "Atromitos": ("atromitos", None),
    "Iraklis": ("iraklis", None), "Kalamata": ("kalamata", None),
    "Kifisia": ("kifisia", None), "Levadeiakos": ("levadeiakos", None),
    "Panetolikos": ("panetolikos", None), "Volos NFC": ("volos", None),
    # 英格兰次级/其他
    "Nott'm Forest": ("nottingham-forest", None), "Hull": ("hull-city", "赫尔城"),
    "QPR": ("qpr", "QPR"), "Cardiff": ("cardiff-city", "加的夫城"),
    "Red Star": ("red-star-belgrade", "红星"), "Andorra": ("fc-andorra", None),
    # 荷兰/葡萄牙
    "For Sittard": ("fortuna-sittard", None), "Nijmegen": ("nec-nijmegen", None),
    "Zwolle": ("pec-zwolle", None), "Sp Braga": ("braga", "布拉加"),
    "Sp Lisbon": ("sporting-cp", "里斯本"), "Academico Viseu": ("academico-viseu", None),
}


def main() -> None:
    aliases = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    meta = aliases.pop("_meta", None)
    flat = {}
    for grp, teams in aliases.items():
        if grp == "_meta":
            continue
        for tid, srcs in teams.items():
            flat[tid] = srcs

    n_fd_added, n_new = 0, 0
    new_entries = {}
    for fd_name, (tid, zh) in FD_MAP.items():
        if tid in flat:
            if not flat[tid].get("fd"):
                flat[tid]["fd"] = fd_name
                n_fd_added += 1
        else:
            new_entries[tid] = {"zh": zh, "clubelo": None, "understat": None,
                                "espn": None, "fd": fd_name, "variants": []}
            n_new += 1
    if new_entries:
        aliases.setdefault("euro-clubs", {}).update(new_entries)
    # 回写 fd 到各原组
    for grp, teams in aliases.items():
        for tid in list(teams):
            if tid in flat and flat[tid].get("fd"):
                teams[tid]["fd"] = flat[tid]["fd"]

    # ② ingest 的 CLUB_EXTRA/NATION_EXTRA 中文映射补写别名组（修欧冠五库 zh_to_id 断链）
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ing", ROOT / "engine" / "scripts" / "research" / "ingest_hist_league.py")
    ing = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ing)
    for zh, tid in ing.CLUB_EXTRA.items():
        if not any(tid in teams for teams in (aliases.get(g, {}) for g in aliases) if isinstance(teams, dict)):
            aliases.setdefault("euro-clubs", {}).setdefault(
                tid, {"zh": zh, "clubelo": None, "understat": None, "espn": None, "fd": None, "variants": []})
        else:
            for teams in aliases.values():
                if isinstance(teams, dict) and tid in teams and not teams[tid].get("zh"):
                    teams[tid]["zh"] = zh
    for zh, tid in ing.NATION_EXTRA.items():
        if not any(tid in teams for teams in (aliases.get(g, {}) for g in aliases) if isinstance(teams, dict)):
            aliases.setdefault("nations-extra", {}).setdefault(
                tid, {"zh": zh, "clubelo": None, "understat": None, "espn": None, "fd": None, "variants": []})

    if meta:
        aliases["_meta"] = meta
    ALIASES_PATH.write_text(json.dumps(aliases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[A2] fd 字段注入 {n_fd_added} 队 · 新建条目 {n_new} · euro-clubs {len(aliases.get('euro-clubs', {}))} · "
          f"nations-extra {len(aliases.get('nations-extra', {}))}")


if __name__ == "__main__":
    main()
