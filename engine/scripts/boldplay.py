"""Bold Play 阶梯出票卡生成器：三档组装/限额反算/月封顶。开发者 sszhang
密度口径（体彩真实池水，skill v4.9 实测）：HAD 0.871^串 / CRS 0.661^串；4串单注限额50万。"""
import glob, json
from datetime import date
from band_calibration import devid
from score_ev import build_freq_table, ev_scan

SHAPES = {"guilin": {"band": (10.0, 17.0), "multiplier": 4, "cost": 8},
          "meizhou": {"band": (18.0, 28.0), "multiplier": 5, "cost": 10}}
SINGLE_LIMIT = 500_000.0        # 4-5 串单注奖金限额（官方规则）
MONTHLY_CAP = 240.0
ROUND_COST = 20.0

def band_ok(had: dict) -> str:
    """体彩 had 自去水方向带：max>=0.60 偏好，否则中性。"""
    p_max = max(devid(had["h"], had["d"], had["a"]))
    return "偏好" if p_max >= 0.60 else "中性"

def cap_multiplier(total_odds: float, budget_mult: int, limit: float = SINGLE_LIMIT) -> int:
    """倍数限额反算：单注奖金 = 2*total_odds*倍数 ≤ limit；上限 50。"""
    m = int(limit // (2 * total_odds))
    return max(1, min(budget_mult, m, 50))

def monthly_spend(records: list, month: str) -> float:
    return sum(r.get("cost", r.get("totalCost", 0)) for r in records
               if str(r.get("date", "")).startswith(month))

def budget_gate(spend: float, cap: float = MONTHLY_CAP, round_cost: float = ROUND_COST) -> bool:
    return spend + round_cost <= cap

def pick_upset_legs(rows: list, shape: str) -> list:
    """形状赔率带 + n>0（先验噪声永不入选）+ 每场最多 1 比分，按 ev 降序取 4。"""
    lo, hi = SHAPES[shape]["band"]
    picked, seen = [], set()
    for r in rows:
        mid = r["matchNumStr"]
        if r.get("n", 0) <= 0 or mid in seen or not (lo <= r["odds"] <= hi):
            continue
        seen.add(mid); picked.append(r)
        if len(picked) == 4:
            break
    return picked

def _fallback_upset(odds_day: dict) -> list:
    """经验频率退路：每场取 1-1/1-0/2-1 中体彩赔率最高者（标注 fallback）。"""
    picked = []
    for m in odds_day.get("matches", []):
        crs = m.get("crs") or {}
        best = max((s for s in ("1:1", "1:0", "2:1") if s in crs), key=lambda s: crs[s], default=None)
        if best:
            picked.append({"matchNumStr": m["matchNumStr"], "match": f'{m.get("home")}-{m.get("away")}',
                           "score": best, "odds": crs[best], "n": -1, "ev": None, "fallback": True})
        if len(picked) == 4:
            break
    return picked

def build_ticket(odds_day: dict, freq_table: dict, seq: int) -> dict:
    shape = "guilin" if seq % 2 == 1 else "meizhou"
    rows = ev_scan(odds_day, freq_table)
    upset = pick_upset_legs(rows, shape) or _fallback_upset(odds_day)
    total_odds = 1.0
    for l in upset:
        total_odds *= l["odds"]
    mult = cap_multiplier(total_odds, SHAPES[shape]["multiplier"]) if upset else 1
    cost = min(SHAPES[shape]["cost"], 2 * mult) if upset else 0
    had_pool = [m for m in odds_day.get("matches", [])
                if m.get("had") and 1.55 <= min(m["had"].values())]
    def had_legs(n_matches, n_notes):
        legs = []
        for m in had_pool[:n_matches]:
            h = m["had"]
            pick = min(h, key=h.get)
            legs.append({"matchNumStr": m["matchNumStr"], "match": f'{m.get("home")}-{m.get("away")}',
                         "play": "had", "pick": {"h": "主胜", "d": "平", "a": "客胜"}[pick], "odds": h[pick]})
        if n_notes == 1 or len(legs) < 2:
            return [legs]
        half = (len(legs) + 1) // 2
        return [legs[:half], legs[half:]]
    return {
        "date": str(date.today()), "seq": seq, "shape": shape,
        "tiers": {
            "base": {"cost": 4, "legs": had_legs(4, 2), "play": "had-4串1", "note": "2注互补"},
            "mid": {"cost": 6, "legs": had_legs(5, 1), "play": "had-5串1", "note": "默认HAD"},
            "upset": {"cost": cost, "multiplier": mult, "legs": upset, "play": "crs-4串1",
                      "expOdds": round(total_odds, 1) if upset else 0,
                      "winIfHit": round(2 * total_odds * mult, 0) if upset else 0,
                      "note": f"{shape}形状 带宽{SHAPES[shape]['band']}"
                              + (" · 频率退路" if upset and upset[0].get("fallback") else "")},
        },
        "totalCost": 4 + 6 + cost,
        "densityNote": f"CRS 4串期望返还 ≈ 0.661^4 = {0.661**4:.1%}(体彩真实池水,非Pinnacle口径)",
        "postTaxNote": "单注奖金超1万部分税20%;4串单注限额50万已反算倍数",
    }

def main() -> None:
    latest = sorted(glob.glob("engine/cache/score_odds/*.json"))[-1]
    odds = json.load(open(latest, encoding="utf-8"))
    table = build_freq_table()
    hist = [json.load(open(p, encoding="utf-8")) for p in glob.glob("data/03-predictions/*-boldplay.json")]
    seq = len(hist) + 1
    spend = monthly_spend(hist, str(date.today())[:7])
    if not budget_gate(spend):
        print(f"[boldplay] 月封顶触及: 本月已花 {spend:.0f}/{MONTHLY_CAP:.0f} 元, 本轮停")
        return
    out = build_ticket(odds["matchDays"][-1], table, seq)
    out["ranAt"] = str(date.today())
    path = f"data/03-predictions/{date.today()}-boldplay.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    u = out["tiers"]["upset"]
    print(f"[boldplay] seq={out['seq']} {out['shape']} | 翻身档 {u['cost']}元 ×{u['multiplier']}倍 "
          f"合赔{u['expOdds']} 中即≈{u['winIfHit']:.0f}元 | 总投入 {out['totalCost']}元 → {path}")

if __name__ == "__main__":
    main()
