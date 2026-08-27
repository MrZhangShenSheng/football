"""freq vs amix 历史对照回测（freq-band 设计验收②）：score_odds 存档日 × 两法选腿 × 赛果判定。开发者 sszhang"""
import glob, json
from datetime import date
from pathlib import Path
from boldplay import build_ticket, _load_results
from score_ev import build_freq_table
from freq_band import build_team_form


def main() -> None:
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
