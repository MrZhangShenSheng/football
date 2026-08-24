"""概率带分联赛校准：fd 多季×8联赛，五带回报率 bootstrap CI。开发者 sszhang"""
import csv, io, random
from collections import defaultdict
from datetime import date
import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
FD_URL = "https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
DIVS = {"E0": "england-premier", "SP1": "spain-laliga", "D1": "germany-bundesliga",
        "I1": "italy-serie-a", "F1": "france-ligue1", "N1": "netherlands-eredivisie",
        "P1": "portugal-primeira", "F2": "france-ligue2"}
SEASONS = ["2223", "2324", "2425", "2526"]

def devid(oh, od_, oa):
    s = 1/oh + 1/od_ + 1/oa
    return (1/oh)/s, (1/od_)/s, (1/oa)/s

def band_of(p: float) -> str:
    if p < 0.15: return "<0.15"
    if p < 0.30: return "0.15-0.30"
    if p < 0.45: return "0.30-0.45"
    if p < 0.60: return "0.45-0.60"
    return ">=0.60"

def bootstrap_ci(rets: list, n: int = 1000, seed: int = 42) -> tuple:
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(rets, k=len(rets))) / len(rets) for _ in range(n))
    return means[int(0.025*n)], means[int(0.975*n)]

def judge(band: str, ci_lo: float, baseline: float) -> str:
    if band == "<0.15": return "排除带"
    return "可买带" if ci_lo > baseline else "不差带"

def fetch_rows(season: str, div: str) -> list:
    try:
        txt = requests.get(FD_URL.format(season=season, div=div), headers=UA, timeout=20).text
        return list(csv.DictReader(io.StringIO(txt)))
    except Exception:
        return []

def main() -> None:
    import json
    cells = defaultdict(list)               # (league, band) -> [回报率...]
    for season in SEASONS:
        for div, league in DIVS.items():
            for r in fetch_rows(season, div):
                try:
                    oh, od_, oa = float(r["PSCH"]), float(r["PSCD"]), float(r["PSCA"])
                    ftr = r.get("FTR")
                    if not all([oh, od_, oa]) or ftr not in ("H", "D", "A"): continue
                    for p, o, side in zip(devid(oh, od_, oa), (oh, od_, oa), ("H", "D", "A")):
                        cells[(league, band_of(p))].append(o if ftr == side else 0.0)
                except (KeyError, ValueError): continue
    overall = [v for k in cells for v in cells[k]]
    baseline = sum(overall) / len(overall)          # 全带平均回报率(≈抽水)
    out = {"ranAt": str(date.today()), "source": "fd CSV 2223-2526 ×8联赛 Pinnacle收盘",
           "baseline": round(baseline, 4), "by_league": {}}
    for lg in sorted({k[0] for k in cells}):
        blob = {}
        for band in ["<0.15", "0.15-0.30", "0.30-0.45", "0.45-0.60", ">=0.60"]:
            rets = cells.get((lg, band), [])
            if len(rets) < 50: continue             # 小样本不校准
            mean = sum(rets)/len(rets); lo, hi = bootstrap_ci(rets)
            blob[band] = {"n": len(rets), "ret": round(mean, 4),
                          "ci95": [round(lo, 4), round(hi, 4)], "verdict": judge(band, lo, baseline)}
        out["by_league"][lg] = blob
    with open("data/04-summaries/band_calibration.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[band-calibration] baseline={baseline:.4f} → data/04-summaries/band_calibration.json")

if __name__ == "__main__":
    main()
