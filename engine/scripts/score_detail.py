# -*- coding: utf-8 -*-
r"""比分预测明细打印器（控制台输出）——大哥要求：场次·预测比分·实际比分·是否命中。

数据源：
  比分预测 = 影子账本 CRS 系票（engine/shadow/paper_tickets.json·冻结选向）
  实际比分 = 体彩开奖缓存（engine/cache/sporttery_results_*.json）

用法:
  python engine/scripts/score_detail.py            # 最近3天（默认）
  python engine/scripts/score_detail.py 7          # 最近7天

开发者 sszhang
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "engine" / "cache"
SHADOW = ROOT / "engine" / "shadow" / "paper_tickets.json"


def load_res_files():
    out = []
    for f in sorted(CACHE.glob("sporttery_results_*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        out += d if isinstance(d, list) else d.get("matches", d.get("list", []))
    return out


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    tk = json.loads(SHADOW.read_text(encoding="utf-8"))
    picks_by_home = {}
    for t in tk:
        if "crs" not in t["spec_name"].lower() and "v4b" not in t["spec_name"].lower():
            continue
        for l in t["legs"]:
            home = l["match"].split(" vs ")[0]
            ps = l["pick"] if isinstance(l["pick"], list) else [l["pick"]]
            for p in ps:
                if str(p) not in picks_by_home.setdefault(home, []):
                    picks_by_home[home].append(str(p))

    ms = load_res_files()
    by_day = {}
    for m in ms:
        d = str(m.get("matchDate") or "")[:10]
        if d:
            by_day.setdefault(d, []).append(m)
    recent = sorted(by_day)[-days:]

    n_pred = n_hit = n_all = 0
    print(f"{'日期':10s} {'编号':7s} {'场次':26s} {'预测比分':16s} {'实际':6s} 命中")
    print("-" * 78)
    for d in recent:
        for m in sorted(by_day[d], key=lambda x: str(x.get("code", ""))):
            home = m.get("home", "?")
            sc = str(m.get("score", "?"))
            pred = picks_by_home.get(home)
            n_all += 1
            if pred:
                n_pred += 1
                hit = sc in pred
                n_hit += hit
                pstr, mark = "或".join(pred), "✓" if hit else "✗"
            else:
                pstr, mark = "—", "—"
            print(f"{d:10s} {str(m.get('code','')):7s} {home + ' vs ' + str(m.get('away','')):26s} "
                  f"{pstr:16s} {sc:6s} {mark}")
    print("-" * 78)
    print(f"共 {n_all} 场 · 有比分预测 {n_pred} 场 · 命中 {n_hit}"
          + (f" ({n_hit/n_pred:.0%})" if n_pred else ""))


if __name__ == "__main__":
    main()
