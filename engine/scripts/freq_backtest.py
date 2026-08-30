"""freq vs amix 历史对照回测（freq-band 设计验收②）：score_odds 存档日 × 两法选腿 × 赛果判定。
--base-replay：保底档结构历史对照回放（boldplay-v2 验收·推演口径）。开发者 sszhang"""
import glob, json, math, sys
from datetime import date
from pathlib import Path
from backfill import expand_combos
from boldplay import build_ticket, is_process_snapshot, _load_results, _leg_hit, _tier_bets
from common import ROOT
from score_ev import build_freq_table
from freq_band import build_team_form

NEW_BASE_COST = 22.0   # 4串11 = 11注 × 2元


def replay_base_structure() -> None:
    """保底档历史结构对照回放（boldplay-v2 T7）：历史 legacy 票的 base 4 HAD 腿原样复用，
    旧 4串1×N注（全中才回款）vs 新 4串11（11注22元·中2关回1注2串1）逐轮对照。
    推演口径无税；翻身多池不可历史回放（spec §4.4——历史轮无 pools 数据），仅保底档。
    腿源：base 第一注组前 4 腿；base 降级<4 腿 → mid 前 4 腿兜底；仍<4 或赛果未回填 → 跳过并注明。
    开发者 sszhang"""
    rows, skipped = [], []
    tot = {"old_spend": 0.0, "old_payout": 0.0, "old_rounds": 0,
           "new_spend": 0.0, "new_payout": 0.0, "new_rounds": 0}
    for p in sorted((ROOT / "data" / "03-predictions").glob("*-boldplay*.json")):
        if is_process_snapshot(p):
            continue    # -rN 过程快照：同日主文件=真相（铁律7），防同轮双计（I2）
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            skipped.append(f"{p.name}(JSON损坏)"); continue
        if t.get("structure") == "new":
            skipped.append(f"{p.name}(已是两档结构·无可对照旧形状)"); continue
        tiers = t.get("tiers") or {}
        base, mid = tiers.get("base") or {}, tiers.get("mid") or {}
        notes = base.get("legs") or []
        if not (notes and isinstance(notes[0], list)):
            notes = [notes]
        legs4 = [dict(l) for l in (notes[0] if notes else [])][:4]
        if len(legs4) < 4:                                   # base 降级轮 → mid 5腿取前4
            legs4 = [dict(l) for l in ((mid.get("legs") or [[]])[0])][:4]
        if len(legs4) < 4:
            skipped.append(f"{p.name}(HAD腿{len(legs4)}<4·4串11不可组)"); continue
        results = _load_results(str(t.get("date", "")))
        hits = [_leg_hit(l, results.get(l["matchNumStr"]), "had") for l in legs4]
        if any(h is None for h in hits):
            miss = [l["matchNumStr"] for l, h in zip(legs4, hits) if h is None]
            skipped.append(f"{p.name}(赛果未回填:{','.join(miss)})"); continue
        old_payout = sum(2 * math.prod(l["odds"] for l in note) for note in notes
                         if note and all(_leg_hit(l, results.get(l["matchNumStr"]), "had") for l in note))
        new_payout, _ = _tier_bets({"legs": legs4,
                                    "bets": [{"legs": list(c), "multiplier": 1} for c in expand_combos(4)]},
                                   hits)
        old_spend = float(base.get("cost") or 0)
        tot["old_spend"] += old_spend; tot["old_payout"] += old_payout
        tot["new_spend"] += NEW_BASE_COST; tot["new_payout"] += new_payout
        if old_payout > 0: tot["old_rounds"] += 1
        if new_payout > 0: tot["new_rounds"] += 1
        rows.append({"file": p.stem, "hit": f"{sum(1 for h in hits if h)}/{len(hits)}",
                     "old_spend": old_spend, "old_payout": old_payout,
                     "new_payout": new_payout})
    print("[base-replay] 保底档历史对照（同4腿：旧4串1×N注全中才回款 vs 新4串11中2关回款）· 推演口径无税")
    print(f"{'轮次':<26s}{'腿中':>4s} {'旧投入':>6s} {'旧派彩':>8s} {'新投入':>6s} {'新派彩':>8s}")
    for r in rows:
        print(f"{r['file']:<26s}{r['hit']:>4s} {r['old_spend']:>6.0f}元 {r['old_payout']:>8.2f}元 "
              f"{NEW_BASE_COST:>6.0f}元 {r['new_payout']:>8.2f}元")
    print(f"合计 {len(rows)} 轮: 旧 投入{tot['old_spend']:.0f}元/派彩{tot['old_payout']:.2f}元/"
          f"回款{tot['old_rounds']}轮 · 新 投入{tot['new_spend']:.0f}元/派彩{tot['new_payout']:.2f}元/"
          f"回款{tot['new_rounds']}轮（新投入=11注全买口径，实际同预算对照见 trend 定夺期积累）")
    print("[base-replay] 翻身多池不可历史回放（spec §4.4——历史轮无 pools 数据），本表仅保底档")
    if skipped:
        print("[base-replay] 跳过: " + "；".join(skipped))


def main() -> None:
    if "--base-replay" in sys.argv[1:]:
        return replay_base_structure()
    table = build_freq_table()
    form = build_team_form()
    out = {"ranAt": str(date.today()), "source": "engine/cache/score_odds 存档日 × data/02-results 赛果",
           "days": [], "totals": {}}
    for method in ("freq", "amix"):
        out["totals"][method] = {"legs": 0, "hits": 0, "odds_sum": 0.0, "tickets": 0,
                                 "tickets_hit": 0, "days_covered": 0}
    for p in sorted(glob.glob("engine/cache/score_odds/*.json")):
        day = Path(p).stem
        odds = json.loads(Path(p).read_text(encoding="utf-8"))
        day_odds = {"matches": [m for d in odds.get("matchDays", []) for m in d.get("matches", [])]}
        if not day_odds["matches"]:
            continue
        results = _load_results(day)
        row = {"day": day, "results": len(results)}
        for method in ("freq", "amix"):
            ticket = build_ticket(day_odds, table, seq=1, method=method, form=form)
            legs = ticket["tiers"]["upset"]["legs"]
            hits = sum(1 for l in legs
                       if (results.get(l["matchNumStr"]) or {}).get("score") == l["pick"])
            covered = len(legs) >= 4
            t = out["totals"][method]
            t["legs"] += len(legs); t["hits"] += hits
            t["odds_sum"] += sum(l["odds"] for l in legs)
            if covered:
                t["tickets"] += 1; t["days_covered"] += 1
                if hits == len(legs) == 4:
                    t["tickets_hit"] += 1
            row[method] = {"legs": len(legs), "hits": hits,
                           "avgOdds": round(sum(l["odds"] for l in legs) / len(legs), 2) if legs else None,
                           "picks": [f'{l["matchNumStr"]}:{l["pick"]}@{l["odds"]}' for l in legs]}
        out["days"].append(row)
    for method, t in out["totals"].items():
        if t["legs"]:
            t["hit_rate"] = round(t["hits"] / t["legs"], 4)
            t["avg_odds"] = round(t["odds_sum"] / t["legs"], 2)
    Path("data/04-summaries/freq_backtest.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for method, t in out["totals"].items():
        print(f"[freq-backtest] {method}: 腿{t['legs']} 中{t['hits']}"
              f" 命中率{t.get('hit_rate', 0):.1%} 均赔{t.get('avg_odds', 0)}"
              f" 成票日{t['days_covered']} 全中票{t['tickets_hit']}")
    print("[freq-backtest] → data/04-summaries/freq_backtest.json")


if __name__ == "__main__":
    main()
