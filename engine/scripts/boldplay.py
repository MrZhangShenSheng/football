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
    """形状赔率带 + n>0（先验噪声永不入选）+ 正 EV（负期望永不入选）+ 每场最多 1 比分，按 ev 降序取 4。"""
    lo, hi = SHAPES[shape]["band"]
    picked, seen = [], set()
    for r in rows:
        mid = r["matchNumStr"]
        if (r.get("n", 0) <= 0 or mid in seen or not (lo <= r["odds"] <= hi)
                or r.get("ev") is None or r["ev"] <= 0):
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
    upset = [dict(l, play="crs", pick=l.get("pick", l["score"])) for l in upset]  # Task 6 settle() schema
    total_odds = 1.0
    for l in upset:
        total_odds *= l["odds"]
    mult = cap_multiplier(total_odds, SHAPES[shape]["multiplier"]) if upset else 1
    cost = min(SHAPES[shape]["cost"], 2 * mult) if upset else 0
    had_pool = [m for m in odds_day.get("matches", [])
                if m.get("had") and 1.55 <= min(m["had"].values())]
    def had_leg(m):
        h = m["had"]
        pick = min(h, key=h.get)
        return {"matchNumStr": m["matchNumStr"], "match": f'{m.get("home")}-{m.get("away")}',
                "play": "had", "pick": {"h": "主胜", "d": "平", "a": "客胜"}[pick], "odds": h[pick]}
    legs_pool = [had_leg(m) for m in had_pool]
    # base：两条 4 串注，共享 pool[2:4] 共 2 场（池≥6 满配；池≥4 单注降级；空池 0 注）
    if len(legs_pool) >= 6:
        base_notes = [legs_pool[0:4], legs_pool[2:6]]
    elif legs_pool:
        base_notes = [legs_pool[:4]]
    else:
        base_notes = []
    base_cost = 2 * len(base_notes)
    mid_legs = [legs_pool[:5]]
    MID_MULT = 3
    mid_cost = 2 * MID_MULT if mid_legs[0] else 0
    tiers = {
        "base": {"cost": base_cost, "legs": base_notes, "play": "had-4串1", "note": "×2注互补(共享≤2场)"},
        "mid": {"cost": mid_cost, "legs": mid_legs, "multiplier": MID_MULT, "play": "had-5串1×3倍", "note": "默认HAD"},
        "upset": {"cost": cost, "multiplier": mult, "legs": upset, "play": "crs-4串1",
                  "expOdds": round(total_odds, 1) if upset else 0,
                  "winIfHit": round(2 * total_odds * mult, 0) if upset else 0,
                  "note": f"{shape}形状 带宽{SHAPES[shape]['band']}"
                          + (" · 频率退路" if upset and upset[0].get("fallback") else "")},
    }
    if len(base_notes) < 2:                                 # 设计 2 非空注组
        tiers["base"]["degraded"] = True
    if len(mid_legs[0]) < 5:                                # 设计 5 串
        tiers["mid"]["degraded"] = True
    if len(upset) < 4:                                      # 设计 4 串
        tiers["upset"]["degraded"] = True
    return {
        "date": str(date.today()), "seq": seq, "shape": shape,
        "tiers": tiers,
        "totalCost": min(20, base_cost + mid_cost + cost),
        "densityNote": f"CRS 4串期望返还 ≈ 0.661^4 = {0.661**4:.1%}(体彩真实池水,非Pinnacle口径)",
        "postTaxNote": "单注奖金超1万部分税20%;4串单注限额50万已反算倍数",
    }

def _direction(score: str) -> str:
    h, a = (int(x) for x in score.split(":"))
    return "主胜" if h > a else ("平" if h == a else "客胜")

def settle(ticket: dict, results: dict) -> dict:
    """逐 leg 判定。results: matchNumStr → 实际比分 'h:a'；had pick 由比分方向推导。开发者 sszhang"""
    leg_hits = {}
    for tier, blob in ticket.get("tiers", {}).items():
        raw = blob.get("legs") or []
        groups = raw if (raw and isinstance(raw[0], list)) else [raw]
        tier_groups = []
        for legs in groups:
            hits = []
            for leg in legs:
                sc = results.get(leg["matchNumStr"])
                if sc is None:
                    hits.append(None); continue
                ok = (leg["pick"] == sc) if leg["play"] == "crs" else (leg["pick"] == _direction(sc))
                hits.append(ok)
            tier_groups.append(hits)
        leg_hits[tier] = tier_groups
    u = ticket.get("tiers", {}).get("upset", {})
    upset_hit = bool(u) and all(h is True for g in leg_hits.get("upset", []) for h in g)
    payout = 0.0
    if upset_hit:
        payout = 2 * u.get("multiplier", 1)
        for leg in u["legs"]:
            payout *= leg["odds"]
    return {"legHits": leg_hits, "upsetHit": upset_hit, "payout": payout,
            "densityRecovered": round(payout / ticket.get("totalCost", 1), 4)}

def _settle_cli() -> None:
    """settle 子命令：最新出票 + 同日期赛果 → 逐 leg 判定写回。开发者 sszhang"""
    tickets = sorted(glob.glob("data/03-predictions/*-boldplay.json"))
    if not tickets:
        print("[boldplay] 无出票记录"); return
    path = tickets[-1]
    ticket = json.load(open(path, encoding="utf-8"))
    res_path = f"data/02-results/{ticket['date']}.json"
    try:
        day = json.load(open(res_path, encoding="utf-8"))
    except FileNotFoundError:
        print(f"[boldplay] 赛果未回填: {res_path}"); return
    # 实测口径（2026-08-24 查 data/02-results/*.json）：编号字段是 code（如"周一001"），
    # 比分字段是 result、横杠分隔（如"2-5"），且未完赛场缺 result → 转 "h:a" 冒号口径
    results = {}
    for m in day.get("matches", []):
        r = str(m.get("result", "")).strip()
        if r and ":" in r:
            results[m.get("code")] = r
        elif r and "-" in r:
            results[m.get("code")] = r.replace("-", ":")
    if not results:
        print(f"[boldplay] 赛果未回填: {res_path}"); return
    res = settle(ticket, results)
    ticket["settle"] = res
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ticket, f, ensure_ascii=False, indent=1)
    hits = res["legHits"].get("upset", [[]])[0] if res["legHits"].get("upset") else []
    print(f"[boldplay-settle] upset {sum(1 for h in hits if h is True)}/{len(hits)} 命中"
          f" · 中奖 {'是' if res['upsetHit'] else '否'} · 派彩(税前) {res['payout']:.0f} → 写回 {path}")

def main() -> None:
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "settle":
        _settle_cli(); return
    latest = sorted(glob.glob("engine/cache/score_odds/*.json"))[-1]
    odds = json.load(open(latest, encoding="utf-8"))
    table = build_freq_table()
    hist = [json.load(open(p, encoding="utf-8")) for p in glob.glob("data/03-predictions/*-boldplay.json")]
    seq = len(hist) + 1
    spend = monthly_spend(hist, str(date.today())[:7])
    if not budget_gate(spend):
        print(f"[boldplay] 月封顶触及: 本月已花 {spend:.0f}/{MONTHLY_CAP:.0f} 元, 本轮停")
        return
    day = max(odds["matchDays"], key=lambda d: len(d.get("matches", [])))  # 选场次最多的天次
    if len(day.get("matches", [])) < 4:
        print(f"[boldplay] 降级: 当轮仅 {len(day.get('matches', []))} 场 (<4)")
    out = build_ticket(day, table, seq)
    out["ranAt"] = str(date.today())
    path = f"data/03-predictions/{date.today()}-boldplay.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    u = out["tiers"]["upset"]
    print(f"[boldplay] seq={out['seq']} {out['shape']} | 翻身档 {u['cost']}元 ×{u['multiplier']}倍 "
          f"合赔{u['expOdds']} 中即≈{u['winIfHit']:.0f}元 | 总投入 {out['totalCost']}元 → {path}")

if __name__ == "__main__":
    main()
