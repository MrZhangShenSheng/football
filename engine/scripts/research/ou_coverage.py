#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fd OU 2.5 列覆盖率实测（goal-engine P0 / T1，2026-09-01 会话）。

总进球引擎（TTG）的市场锚拟用 fd 的 OU 2.5 收盘价（Pinnacle 优先、B365 降级），
本脚本对 LEAGUE_CODES 全部联赛 × {2526, 2627} 两季逐行实测 OU 2.5 四列
（P>2.5 / P<2.5 / B365>2.5 / B365<2.5）的非空覆盖率，给出数据源分层结论：

  pin  = fetched 季 Pinnacle OU 覆盖 min ≥ 0.9（直用 Pinnacle）
  b365 = Pinnacle 不足但 fetched 季 B365 覆盖 min ≥ 0.9（降级 B365）
  c    = fetched 季两者皆不足（C 口径=纯统计无锚）
  unknown = 两季均 missing（抓取失败，无法判定）

口径注记：cov 为 either-side 口径——任一侧（>2.5 或 <2.5）非空即算命中，
故所得覆盖率为"可用覆盖率上界"；实际做锚去水需两侧价齐备，消费方（T2）须按
两侧齐备自行复核。抓取失败标 status=missing（单次失败重试恰好 1 次，总尝试
≤2 次守网络铁律 4），missing 季不参与 verdict 聚合。

产出：engine/cache/ou_coverage.json
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
from odds_fetch import LEAGUE_CODES, fetch_league_csv  # noqa: E402

CACHE = ROOT / "engine" / "cache"
SEASONS = ("2526", "2627")
TIER_THRESHOLD = 0.9
STATUS_FETCHED = "fetched"
STATUS_MISSING = "missing"
VERDICT_UNKNOWN = "unknown"


def cov(rows, *keys):
    """行级覆盖率（either-side：任一 key 列名大小写不敏感命中且值 .strip() 非空即算，口径照抄 odds_fetch g()）。"""
    hit = 0
    for r in rows:
        for k in keys:
            if any(rk and rk.lower() == k.lower() and (rv or "").strip() for rk, rv in r.items() if rk):
                hit += 1
                break
    return round(hit / len(rows), 4) if rows else 0.0


def fetch_with_retry(code, season):
    """抓取一个联赛×赛季；失败重试恰好 1 次（总尝试 ≤2），仍无数据返回 None。"""
    rows = fetch_league_csv(code, season)
    if rows is None:
        rows = fetch_league_csv(code, season)
    return rows or None  # 空 list 同样视为无数据


def main():
    leagues = {}
    for code, name in LEAGUE_CODES.items():
        leagues[name] = {}
        for season in SEASONS:
            rows = fetch_with_retry(code, season)
            if rows:
                rec = {
                    "n": len(rows),
                    "pin": cov(rows, "P>2.5", "P<2.5"),
                    "b365": cov(rows, "B365>2.5", "B365<2.5"),
                    "status": STATUS_FETCHED,
                }
            else:
                rec = {"n": 0, "pin": None, "b365": None, "status": STATUS_MISSING}
            leagues[name][season] = rec
            print(f"  {code} {name} {season}: status={rec['status']} n={rec['n']} pin={rec['pin']} b365={rec['b365']}")

    verdict = {}
    for name, recs in leagues.items():
        fetched = [r for r in recs.values() if r["status"] == STATUS_FETCHED]
        if not fetched:
            verdict[name] = VERDICT_UNKNOWN
            continue
        pin_min = min(r["pin"] for r in fetched)
        b365_min = min(r["b365"] for r in fetched)
        verdict[name] = "pin" if pin_min >= TIER_THRESHOLD else ("b365" if b365_min >= TIER_THRESHOLD else "c")

    out = CACHE / "ou_coverage.json"
    payload = {
        "fetchedAt": datetime.now().isoformat(timespec="seconds"),
        "threshold": TIER_THRESHOLD,
        "seasons": list(SEASONS),
        "leagues": leagues,
        "verdict": verdict,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print("\n分层结论（仅 fetched 季参与 min 聚合；两季全 missing → unknown）：")
    for tier in ("pin", "b365", "c", VERDICT_UNKNOWN):
        names = [n for n, v in verdict.items() if v == tier]
        print(f"  {tier:>7}: {', '.join(names) if names else '（无）'}")
    print(f"\n→ {out}")


if __name__ == "__main__":
    main()
