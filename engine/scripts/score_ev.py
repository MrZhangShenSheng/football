"""比分 EV 审计（v5.1 起为 fallback 口径，主链=boldplay A-MIX DC 概率）：分联赛经验频率(Beta收缩) × 当轮体彩赔率 → 正EV清单。开发者 sszhang"""
import glob, json
from collections import Counter
from datetime import date
from band_calibration import DIVS, SEASONS, fetch_rows, devid, band_of, warn_source_gaps
from common import ROOT

PRIOR_STRENGTH = 50          # 收缩先验强度(场)

LEAGUE_MAP = {          # 体彩中文缩写 → fd 英文 id（2026-08-24 存档实测；08-28 补次级联赛）
    "英超": "england-premier", "西甲": "spain-laliga", "德甲": "germany-bundesliga",
    "意甲": "italy-serie-a", "法甲": "france-ligue1", "法乙": "france-ligue2",
    "荷甲": "netherlands-eredivisie", "葡超": "portugal-primeira",
    "沙职": "saudi", "瑞超": "sweden", "日职": "japan", "韩职": "korea",
    "英冠": "england-championship", "德乙": "germany-2-bundesliga",
    "荷乙": "netherlands-eerste", "西乙": "spain-laliga2", "意乙": "italy-serie-b",
    # 09-08 补本地库四联赛（体彩裸名实测自 02-results league 字段；无 fd 对应）
    "巴甲": "brazil", "丹超": "denmark", "挪超": "norway", "美职": "usa",
    "澳超": "australia",          # 2026-10-23 接入（ESPN aus.1·见 backfill.LEAGUE_ESPN）
    # 10-14 低分偏置修正：杯赛映射（此前返回 None → 全局池 2.738，欧冠实测 3.221 差 0.48）
    "欧冠": "uefa-champions", "欧联": "uefa-europa", "欧罗巴": "uefa-europa",
}                       # 无对应 → None → 调用方 resolve_pool_key 回退近似池

def map_league(name: str):
    """体彩中文联赛名 → fd id；无映射返回 None（调用方落全局池）。"""
    return LEAGUE_MAP.get(name)


# 库文件名 → 频率池键的别名（Ruling R1：不改库文件名，在此消解两处不一致）
POOL_ALIAS = {"spain-laliga2": "spain-liga2", "germany-2-bundesliga": "germany-bundesliga2"}

# 样本不足独立成池的赛事 → 地域近似池。多键则合并为加权池（非取首个——
# 2026-10-14 实测缺陷：原实现只取首个在表中者，("japan","korea") 的 korea 永不生效）。
POOL_FALLBACK = {
    "解放者杯": ("brazil",),
    "亚冠精英": ("japan", "korea", "saudi"),
    "亚冠乙": ("japan", "korea", "saudi"),
    "日联赛杯": ("japan",),
}


def resolve_pool(name: str, table: dict):
    """体彩联赛名 → (池键或合成键, 池 Counter)；无可用池返回 (None, None)。

    顺序：LEAGUE_MAP 直接命中（经 POOL_ALIAS 消解键名差异）→ POOL_FALLBACK 近似池
    （多键合并加权）→ (None, None)（调用方落全局池）。
    回退近似池而非全局平均：今日 013 解放者杯市场期望 2.22，巴西池 2.489 比全局 2.738 近。

    已知局限（2026-10-14）：亚冠实际进球水平(市场均 3.22)显著高于成员国联赛加权(2.678)，
    同欧冠 3.221 远高于各国联赛。加权池只是权宜，根治须建 afc.champions 独立库。
    """
    key = LEAGUE_MAP.get(name)
    if key:
        key = POOL_ALIAS.get(key, key)
        if key in table:
            return key, table[key]
    cands = [c for c in POOL_FALLBACK.get(name, ()) if c in table]
    if len(cands) == 1:
        return cands[0], table[cands[0]]
    if cands:
        agg = Counter()
        for c in cands:
            agg.update(table[c])
        return "+".join(cands), agg
    return None, None

def shrink(freq: float, n: int, prior: float, strength: int = PRIOR_STRENGTH) -> float:
    return (freq * n + prior * strength) / (n + strength)

def norm_score(hg, ag) -> str:
    return f"{int(hg)}:{int(ag)}"

# 本地赛果库准入（2026-10-14 低分偏置修正）：按样本量准入替代原「杯赛库不进频率池」。
# 原设计排除杯赛因「跨联赛队源混杂」，但欧冠 1043 场实测期望 3.221，而被迫套全局池
# 2.738 正是 −0.49 球主犯（见 docs/plans/2026-10-14-crs-low-score-bias-design.md）。
# 样本>=600 即独立成池；不足者由调用方回退地域近似池，不落全局平均。
# 2026-10-23 由 800 降至 600：league 库去重（8214 行重复：fd 八库整库双导 + 欧冠/欧联/
# 法乙/美职跨时区日双记）后，欧冠 706/欧联 653/苏超 734 三库跌破 800——它们此前达标
# 靠的正是重复行。按真实样本回退全局池会让欧冠吃 −0.461 球偏差（真实 3.197 vs
# 全局 2.736），正是 2026-10-14 低分偏置修正要消除的那个缺陷。600 门槛下仅这三库
# 新进、无其他库越线（下一个边界是世预赛 370，差距足够远）。
POOL_MIN_N = 600
LOCAL_POOL = ("japan", "korea", "sweden", "saudi", "brazil", "denmark", "norway", "usa",
              "australia")   # australia: 168 场 < POOL_MIN_N，实际走调用方地域近似回退
FD_BACKED = frozenset(DIVS.values())   # fd 覆盖联赛键集（镜像库仅 fd 断粮时导入防双算）


def _load_rows(path: str) -> list:
    """库文件赛果行（准入判定与导入共用一次解析）。"""
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data.get("matches", []) if isinstance(data, dict) else (data or [])


def build_freq_table() -> dict:
    """fd 多季 + 本地 league 库 → {league: Counter({'__n': 总场, '1:0': 场次, ...})}。

    本地库导入规则（2026-09-08 espn-history 镜像设计）：
    - fd 不覆盖联赛（LOCAL_POOL 8 个）→ 无条件导入；
    - fd 覆盖联赛（英西德意法荷葡）→ 仅该联赛 fd 行为空（503 断粮）时导入镜像，
      fd 正常时零行为变化、镜像静默退位，永不双算。"""
    table = {}
    for season in SEASONS:
        for div, league in DIVS.items():
            blob = table.setdefault(league, Counter())
            for r in fetch_rows(season, div):
                try:
                    blob[norm_score(r["FTHG"], r["FTAG"])] += 1; blob["__n"] += 1
                except (KeyError, ValueError, TypeError): continue
    # ROOT 绝对定位（2026-09-03 修复：相对路径在 cwd=engine/scripts 的 run.py 调用链下
    # 静默丢掉日韩瑞沙 ~2300 场——boldplay.py:21 2026-09-02 同类修复的漏网之鱼）
    for path in glob.glob(str(ROOT / "data/02-results/league/*_matches.json")):
        key = path.replace("\\", "/").split("/")[-1].replace("_matches.json", "")  # Windows glob 返回反斜杠，先归一再取文件名（2026-08-25 本地1948场静默丢失修复）
        if key in FD_BACKED:
            if (table.get(key) or Counter()).get("__n", 0):
                continue                               # fd 活着 → 跳过镜像防双算
        rows = _load_rows(path)
        if key not in FD_BACKED and key not in LOCAL_POOL and len(rows) < POOL_MIN_N:
            continue                                   # 样本不足 → 不成池（调用方回退近似池）
        blob = table.setdefault(key, Counter())
        for m in rows:
            try:
                blob[norm_score(m["hg"], m["ag"])] += 1; blob["__n"] += 1
            except (KeyError, ValueError, TypeError): continue
    warn_source_gaps("score-ev 频率模板")   # 2026-09-04 审计 P0：fd 网络断粮护栏
    return table

def freq_for(table: dict, league: str, score: str) -> tuple:
    """返回 (收缩后q, n)。联赛缺失或无该比分 → 向全局池收缩（修正版：.get 兼容 dict）。"""
    def cnt(name):
        blob = table.get(name) or {}
        return blob.get("__n", 0), blob.get(score, 0)
    n, c = cnt(league)
    gn = gc = 0
    for name in table:
        a, b = cnt(name)
        gn += a; gc += b
    prior = (gc / gn) if gn else 0.0
    raw = (c / n) if n else prior
    return shrink(raw, n, prior), n

def ev_scan(odds_day: dict, freq_table: dict) -> list:
    rows = []
    for m in odds_day.get("matches", []):
        had = m.get("had") or {}
        try:
            probs = devid(had["h"], had["d"], had["a"])
        except (KeyError, TypeError, ZeroDivisionError):
            continue
        direction_band = band_of(max(probs))
        for score, o in (m.get("crs") or {}).items():
            if score in ("胜其他", "平其他", "负其他"): continue
            q, n = freq_for(freq_table, map_league(m.get("league", "")) or m.get("league", ""), score)
            rows.append({"matchNumStr": m["matchNumStr"], "match": f'{m.get("home")}-{m.get("away")}',
                         "league": m.get("league"), "leagueId": map_league(m.get("league", "")),
                         "score": score, "odds": o,
                         "q": round(q, 4), "n": n, "ev": round(q * o - 1, 4),
                         "directionBand": direction_band})
    return sorted(rows, key=lambda r: r["ev"], reverse=True)

def main() -> None:
    table = build_freq_table()
    # ROOT 绝对定位（2026-09-04 审计 P1：相对 glob 在 cwd=engine/scripts 下静默空→IndexError）
    latest = sorted(glob.glob(str(ROOT / "engine/cache/score_odds/*.json")))[-1]
    odds = json.load(open(latest, encoding="utf-8"))
    rows = []
    for day in odds.get("matchDays", []):
        rows += ev_scan(day, table)
    positive = [r for r in rows if r["ev"] > 0]
    backed = [r for r in positive if r.get("n", 0) > 0]
    out = {"ranAt": str(date.today()), "oddsArchive": latest,
           "source": "fd多季+league库频率(Beta收缩50) × sporttery当轮赔率",
           "n_scanned": len(rows), "n_positive": len(positive),
           "n_positive_league_backed": len(backed),
           "n_positive_prior_only": len(positive) - len(backed),
           "positive_top": positive[:40], "all_top30": rows[:30]}
    with open(ROOT / "data/04-summaries/score_ev.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[score-ev] 扫描 {len(rows)} 项, 正EV {len(positive)} 项 → data/04-summaries/score_ev.json")

if __name__ == "__main__":
    main()
