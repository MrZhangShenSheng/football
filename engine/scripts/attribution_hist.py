# engine/scripts/attribution_hist.py
"""比分归因·历史卷适配器：fd CSV 行 → MatchRecord（三键匹配队名）。设计§四。开发者 sszhang"""
import csv
import json
import sys
from datetime import date
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
ROOT = SCRIPTS_DIR.parents[1]
FD_DIR_PATH = ROOT / "engine" / "cache" / "shape_backtest"
LEAGUE_DIR = ROOT / "data" / "02-results" / "league"

# fd code → (league 库目录, 中文联赛名)。目录名以 data/02-results/league/ 实际为准
FD_LEAGUES = {
    "E0": ("england-premier", "英超"), "E1": ("england-championship", "英冠"),
    "SP1": ("spain-laliga", "西甲"), "SP2": ("spain-liga2", "西乙"),
    "D1": ("germany-bundesliga", "德甲"), "D2": ("germany-bundesliga2", "德乙"),
    "I1": ("italy-serie-a", "意甲"), "I2": ("italy-serie-b", "意乙"),
    "F1": ("france-ligue1", "法甲"), "F2": ("france-ligue2", "法乙"),
    "N1": ("netherlands-eredivisie", "荷甲"), "B1": ("belgium-first-a", "比甲"),
    "P1": ("portugal-primeira", "葡超"), "T1": ("turkey-super-lig", "土超"),
    "G1": ("greece-super", "希超"), "R1": ("russia-premier", "俄超"),
}

_league_cache: dict[str, list[dict]] = {}

def _league_rows(league_dir: str) -> list[dict]:
    """league 库行（date/home/away/hg/ag）带缓存。开发者 sszhang"""
    if league_dir not in _league_cache:
        path = LEAGUE_DIR / f"{league_dir}_matches.json"
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        _league_cache[league_dir] = rows if isinstance(rows, list) else rows.get("matches", [])
    return _league_cache[league_dir]

def fd_date(d: str) -> str:
    """fd dd/mm/YYYY → ISO。开发者 sszhang"""
    dd, mm, yy = d.split("/")
    return f"{yy}-{mm.zfill(2)}-{dd.zfill(2)}"

def _match_teams(iso: str, hg: int, ag: int, league_dir: str) -> tuple[str | None, str | None]:
    """三键匹配（日期±1+比分）→ 规范 ID (home, away)。多候选取日期最近。开发者 sszhang"""
    d0 = date.fromisoformat(iso)
    best = None
    for r in _league_rows(league_dir):
        if r["hg"] != hg or r["ag"] != ag:
            continue
        gap = abs((date.fromisoformat(r["date"][:10]) - d0).days)
        if gap <= 1 and (best is None or gap < best[0]):
            best = (gap, r["home"], r["away"])
    return (best[1], best[2]) if best else (None, None)

def _f(v: str) -> float | None:
    v = (v or "").strip()
    return float(v) if v else None

def hist_match_record(row: dict, league_dir: str, league_zh: str) -> dict:
    """fd CSV 行 → MatchRecord 历史口径。odds=收盘 PSCH 优先缺则 B365 开盘（evidence 标）。开发者 sszhang"""
    iso = fd_date(row["Date"])
    hg, ag = int(row["FTHG"]), int(row["FTAG"])
    th, ta = _match_teams(iso, hg, ag, league_dir)
    odds = None
    if all(_f(row.get(f"PSC{k}")) for k in "HDA"):
        odds = [_f(row[f"PSC{k}"]) for k in "HDA"]
    elif all(_f(row.get(f"B365{k}")) for k in "HDA"):
        odds = [_f(row[f"B365{k}"]) for k in "HDA"]
    return {
        "date": iso, "kickoff": iso, "code": f"hist:{iso}", "league": league_zh,
        "match": f"{row['HomeTeam']} vs {row['AwayTeam']}",
        "team_h": th, "team_a": ta, "league_dir": league_dir,
        "odds": odds, "pin_close": None, "elo_h": None, "elo_a": None,
        "inj_n_h": 0, "inj_n_a": 0, "rest_days_h": None, "rest_days_a": None,
        "result": {"hg": hg, "ag": ag,
                   "ht_hg": _f(row.get("HTHG")), "ht_ag": _f(row.get("HTAG"))},
        "red_h": int(_f(row.get("HR")) or 0), "red_a": int(_f(row.get("AR")) or 0),
        "hst": int(_f(row.get("HST")) or 0), "ast": int(_f(row.get("AST")) or 0),
        "xg_h": _f(row.get("HxG")), "xg_a": _f(row.get("AxG")),
    }

from score_attribution import build_bill

def run_batch(season: str, codes: list[str]) -> None:
    """60 CSV（或指定子集）→ hist/{code}_{season}-attribution.json。开发者 sszhang"""
    out_dir = ROOT / "data" / "04-summaries" / "score-attribution" / "hist"
    out_dir.mkdir(parents=True, exist_ok=True)
    for code in codes:
        league_dir, league_zh = FD_LEAGUES[code]
        path = FD_DIR_PATH / f"{code}_{season}.csv"
        if not path.exists():
            print(f"[跳过] {code}_{season} 无文件"); continue
        rows = list(csv.DictReader(open(path, encoding="latin-1")))
        bills, unmatched, no_odds = [], 0, 0
        for row in rows:
            if not (row.get("FTHG") or "").strip():
                continue
            rec = hist_match_record(row, league_dir, league_zh)
            if rec["team_h"] is None:
                unmatched += 1
            if rec["odds"] is None:
                no_odds += 1
            bills.append(build_bill(rec))
        out = out_dir / f"{code}_{season}-attribution.json"
        out.write_text(json.dumps(bills, ensure_ascii=False), encoding="utf-8")
        ok = len([b for b in bills if "baseline" in b])
        print(f"{code}_{season}: {len(rows)}行 出账{ok} 队名未匹配{unmatched} odds缺{no_odds}")

def main() -> None:
    """batch-hist CLI。开发者 sszhang"""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["batch-hist"])
    ap.add_argument("--season", default="all")
    ap.add_argument("--code", default="")
    args = ap.parse_args()
    seasons = ["2324", "2425", "2526", "2627"] if args.season == "all" else [args.season]
    codes = args.code.split(",") if args.code else list(FD_LEAGUES)
    for s in seasons:
        run_batch(s, codes)

if __name__ == "__main__":
    main()
