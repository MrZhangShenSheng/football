"""比分 EV 审计：分联赛经验频率(Beta收缩) × 当轮体彩赔率 → 正EV清单。开发者 sszhang"""
import glob, json
from collections import Counter
from datetime import date
from band_calibration import DIVS, SEASONS, fetch_rows, devid, band_of

PRIOR_STRENGTH = 50          # 收缩先验强度(场)

def shrink(freq: float, n: int, prior: float, strength: int = PRIOR_STRENGTH) -> float:
    return (freq * n + prior * strength) / (n + strength)

def norm_score(hg, ag) -> str:
    return f"{int(hg)}:{int(ag)}"

def build_freq_table() -> dict:
    """fd 8联赛多季 + league库(日韩瑞沙) → {league: Counter({'__n': 总场, '1:0': 场次, ...})}"""
    table = {}
    for season in SEASONS:
        for div, league in DIVS.items():
            blob = table.setdefault(league, Counter())
            for r in fetch_rows(season, div):
                try:
                    blob[norm_score(r["FTHG"], r["FTAG"])] += 1; blob["__n"] += 1
                except (KeyError, ValueError, TypeError): continue
    for path in glob.glob("data/02-results/league/*_matches.json"):
        key = path.split("/")[-1].replace("_matches.json", "")
        if key not in ("japan", "korea", "sweden", "saudi"): continue
        blob = table.setdefault(key, Counter())
        for m in json.load(open(path, encoding="utf-8")) or []:
            try:
                blob[norm_score(m["hg"], m["ag"])] += 1; blob["__n"] += 1
            except (KeyError, ValueError, TypeError): continue
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
            q, n = freq_for(freq_table, m.get("league", ""), score)
            rows.append({"matchNumStr": m["matchNumStr"], "match": f'{m.get("home")}-{m.get("away")}',
                         "league": m.get("league"), "score": score, "odds": o,
                         "q": round(q, 4), "n": n, "ev": round(q * o - 1, 4),
                         "directionBand": direction_band})
    return sorted(rows, key=lambda r: r["ev"], reverse=True)

def main() -> None:
    table = build_freq_table()
    latest = sorted(glob.glob("engine/cache/score_odds/*.json"))[-1]
    odds = json.load(open(latest, encoding="utf-8"))
    rows = []
    for day in odds.get("matchDays", []):
        rows += ev_scan(day, table)
    positive = [r for r in rows if r["ev"] > 0]
    out = {"ranAt": str(date.today()), "oddsArchive": latest,
           "source": "fd多季+league库频率(Beta收缩50) × sporttery当轮赔率",
           "n_scanned": len(rows), "n_positive": len(positive),
           "positive_top": positive[:40], "all_top30": rows[:30]}
    with open("data/04-summaries/score_ev.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[score-ev] 扫描 {len(rows)} 项, 正EV {len(positive)} 项 → data/04-summaries/score_ev.json")

if __name__ == "__main__":
    main()
