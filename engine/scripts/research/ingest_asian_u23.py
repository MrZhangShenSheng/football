# -*- coding: utf-8 -*-
"""一次性入库：亚运男足 + U23 亚锦赛体彩历史赛果 → league 库 asian-u23 + 别名组
（2026-09-30 主公拍板「解决别名映射失败」：补国家队/国字号特征源，令 v4b/闯关票能算亚运场）
数据源=engine/cache/hist_odds 体彩历史库（本届两赛事真实场次+赛果）。
马甲归一：亚运「XX亚」与 U23 亚锦赛「XX23」同为 U23 国字号班底 → 统一规范 ID。
开发者 sszhang
"""
import glob
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]   # engine/scripts/research → 仓库根
LEAGUE_FILE = ROOT / "data/02-results/league/asian-u23_matches.json"
ALIASES_FILE = ROOT / "data/01-teams/_aliases.json"

# 体彩名 → 规范 ID（亚运马甲 + U23 亚锦赛马甲；espn 名从简）
TEAMS = {
    "韩国亚": "korea-u23", "韩国23": "korea-u23",
    "中国亚": "china-u23", "中国23": "china-u23",
    "乌兹别亚": "uzbekistan-u23", "乌兹别23": "uzbekistan-u23",
    "日本亚": "japan-u23", "日本23": "japan-u23",
    "沙特亚": "saudi-u23", "沙特23": "saudi-u23",
    "伊朗亚": "iran-u23", "伊朗23": "iran-u23",
    "泰国亚": "thailand-u23", "泰国23": "thailand-u23",
    "越南亚": "vietnam-u23", "越南23": "vietnam-u23",
    "卡塔尔亚": "qatar-u23", "卡塔尔23": "qatar-u23",
    "阿联酋亚": "uae-u23", "阿联酋23": "uae-u23",
    "吉尔吉亚": "kyrgyzstan-u23", "吉尔吉23": "kyrgyzstan-u23",
    "朝鲜亚": "north-korea-u23",
    "中国港亚": "hong-kong-u23",
    "黎巴嫩23": "lebanon-u23",
    "约旦23": "jordan-u23",
    "伊拉克23": "iraq-u23",
    "叙利亚23": "syria-u23",
    "澳大利23": "australia-u23",
}
# 规范 ID → 体彩在售常用中文名（zh，供体彩清单映射；亚运马甲优先）
ZH = {
    "korea-u23": "韩国亚", "china-u23": "中国亚", "uzbekistan-u23": "乌兹别亚",
    "japan-u23": "日本亚", "saudi-u23": "沙特亚", "iran-u23": "伊朗亚",
    "thailand-u23": "泰国亚", "vietnam-u23": "越南亚", "qatar-u23": "卡塔尔亚",
    "uae-u23": "阿联酋亚", "kyrgyzstan-u23": "吉尔吉亚", "north-korea-u23": "朝鲜亚",
    "hong-kong-u23": "中国港亚", "lebanon-u23": "黎巴嫩亚", "jordan-u23": "约旦亚",
    "iraq-u23": "伊拉克亚", "syria-u23": "叙利亚亚", "australia-u23": "澳大利亚亚",
}

# 1) 从 hist_odds 捞两池（队名在 TEAMS 键内的有赛果场次，按 (date,home,away) 去重）
pool = {}
for p in sorted(glob.glob(str(ROOT / "engine/cache/hist_odds/*.json"))):
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        continue
    for m in d.get("matches", []):
        h, a, sc = str(m.get("home") or ""), str(m.get("away") or ""), m.get("score") or ""
        if ":" not in sc or h not in TEAMS or a not in TEAMS:
            continue
        try:
            hg, ag = (int(x) for x in sc.split(":")[:2])
        except ValueError:
            continue
        key = (str(m.get("date") or "")[:10], TEAMS[h], TEAMS[a])
        if key in pool:
            continue
        pool[key] = (hg, ag)

rows = [{"date": k[0], "home": k[1], "away": k[2], "hg": v[0], "ag": v[1]}
        for k, v in sorted(pool.items())]
assert len(rows) >= 40, f"捞池异常: {len(rows)} 场 (<40)"
LEAGUE_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"[入库] {LEAGUE_FILE.name}: {len(rows)} 场")

# 2) 别名表加 asian-u23 组（幂等：组已存在则覆盖本组，不动其他组）
al = json.loads(ALIASES_FILE.read_text(encoding="utf-8"))
group = {}
for zh_name, tid in TEAMS.items():
    group.setdefault(tid, {"zh": ZH[tid], "clubelo": None, "understat": None,
                           "espn": None, "altZh": []})["altZh"].append(zh_name)
al["asian-u23"] = group
ALIASES_FILE.write_text(json.dumps(al, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"[别名] asian-u23 组 {len(group)} 队 → {ALIASES_FILE.name}")

# 3) 自验：league_timeline 能读出 + 今日四队映射通
import sys
sys.path.insert(0, str(ROOT / "engine/scripts/research"))
import score_family_model as sfm
tl = sfm.league_timeline()
asian = [r for r in tl if r[1].endswith("-u23")]
from collections import defaultdict
cnt = defaultdict(int)
for _, h, a, _, _ in asian:
    cnt[h] += 1
    cnt[a] += 1
print(f"[自验] timeline 中 asian-u23 场次: {len(asian)}")
for t in ("korea-u23", "china-u23", "uzbekistan-u23", "japan-u23"):
    print(f"   {t}: {cnt[t]} 场 (MIN_HIST={sfm.MIN_HIST} {'✓' if cnt[t] >= sfm.MIN_HIST else '✗'})")
flat = sfm.load_aliases()
for zh_name in ("韩国亚", "中国亚", "乌兹别亚", "日本亚"):
    hit = [tid for tid, s in flat.items() if s.get("zh") == zh_name]
    print(f"   zh映射 {zh_name} → {hit}")
