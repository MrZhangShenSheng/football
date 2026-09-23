#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""欧指锚对拍报告：okooo 99家平均欧指（比例法去水）vs fd Pinnacle 收盘去水，
附带体彩票价去水同口径对照（现状出票链市场腿=体彩即时价去水的质量基线）。

设计：docs/2026-09-23-euro-anchor-design.html 第二步门槛：
  argmax 一致率 ≥85% 且 MAE < 0.03 → 直接接入；不达标 → 仿射校正再测。

join key 全部 = 销售日 + 体彩编号（周三001）。
样本 = corpus 有 pinClose 场次（fd 覆盖联赛，双源同场）。
落盘：data/04-summaries/euro-verify.json（幂等覆盖）。
用法：python euro_verify.py
开发者 sszhang
"""
import json
from pathlib import Path

from common import log, ROOT
from dc_predict import devig

CORPUS = ROOT / "data" / "04-summaries" / "corpus.json"
EURO_DIR = ROOT / "engine" / "cache" / "euro_odds"
TC_DIR = ROOT / "engine" / "cache" / "score_odds"
OUT = ROOT / "data" / "04-summaries" / "euro-verify.json"


def load_euro() -> dict:
    """{(date, orderCn): 去水三向}。"""
    out = {}
    for f in sorted(EURO_DIR.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for m in d.get("matches", []):
            ea = m.get("euroAvg")
            if ea:
                out[(d["date"], m["orderCn"])] = devig([ea["home"], ea["draw"], ea["away"]])
    return out


def load_tc() -> dict:
    """{(businessDate, matchNumStr): 体彩 HAD 去水三向}（score_odds dump-odds 存档）。"""
    out = {}
    for f in sorted(TC_DIR.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for day in d.get("matchDays", []):
            bd = day.get("businessDate")
            for m in day.get("matches", []):
                had = m.get("had")
                if had and all(had.get(k) for k in ("h", "d", "a")):
                    out[(bd, m["matchNumStr"])] = devig([float(had["h"]), float(had["d"]), float(had["a"])])
    return out


def metric(samples: list[tuple[list, list, dict]]) -> dict:
    """samples = [(pin 三向, 对照源去水三向, 对照源赔率 dict)] → 一致率/MAE/分层。"""
    if not samples:
        return {"n": 0}
    agree = mae_sum = 0
    bands = {"hot": [0, 0, 0.0], "mid": [0, 0, 0.0], "cold": [0, 0, 0.0]}
    for pin, q, ea in samples:
        pa, qa = max(range(3), key=lambda i: pin[i]), max(range(3), key=lambda i: q[i])
        hit = int(pa == qa)
        mae = sum(abs(pin[i] - q[i]) for i in range(3)) / 3
        agree += hit
        mae_sum += mae
        band = "hot" if ea["home"] <= 1.5 else ("cold" if ea["home"] >= 3.0 else "mid")
        bands[band][0] += hit
        bands[band][1] += 1
        bands[band][2] += mae
    n = len(samples)
    return {
        "n": n,
        "argmaxAgree": round(agree / n, 4),
        "mae": round(mae_sum / n, 5),
        "byBand": {k: {"n": v[1], "agree": round(v[0] / v[1], 4) if v[1] else None,
                       "mae": round(v[2] / v[1], 5) if v[1] else None}
                   for k, v in bands.items() if v[1]},
    }


def main() -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    euro, tc = load_euro(), load_tc()

    euro_samples, tc_samples, by_league = [], [], {}
    for r in corpus.get("records", []):
        pin = r.get("pinClose")
        if not pin or len(pin) != 3:
            continue
        key = (r["date"], r["code"])
        if key in euro:
            ea = None
            for m in json.loads((EURO_DIR / f"{r['date']}.json").read_text(encoding="utf-8"))["matches"]:
                if m["orderCn"] == r["code"] and m.get("euroAvg"):
                    ea = m["euroAvg"]
                    break
            if ea:
                euro_samples.append((pin, euro[key], ea))
                by_league.setdefault(r.get("league"), [0, 0])
                by_league[r.get("league")][0] += 1
                by_league[r.get("league")][1] += int(
                    max(range(3), key=lambda i: pin[i]) == max(range(3), key=lambda i: euro[key][i]))
        if key in tc:
            tc_samples.append((pin, tc[key], {"home": 1 / tc[key][0]}))   # 带宽键用去水概率倒数近似

    res_euro = metric(euro_samples)
    res_tc = metric(tc_samples)
    report = {
        "generatedAt": corpus.get("generatedAt"),
        "gate": {"argmaxAgree": 0.85, "mae": 0.03},
        "euroVsPin": res_euro,
        "sportteryVsPin": res_tc,
        "byLeagueEuro": {k: {"n": v[0], "agree": v[1]} for k, v in sorted(by_league.items())},
        "verdict": ("PASS-direct" if res_euro.get("n", 0) >= 50
                    and res_euro.get("argmaxAgree", 0) >= 0.85 and res_euro.get("mae", 1) < 0.03
                    else "FAIL-or-insufficient"),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log("euro", f"对拍样本 {res_euro['n']} 场：okooo锚 vs Pinnacle 一致率 "
        f"{res_euro.get('argmaxAgree', 0):.1%} / MAE {res_euro.get('mae', 0):.4f}")
    log("euro", f"对照组 体彩票价 vs Pinnacle：n={res_tc.get('n', 0)} 一致率 "
        f"{res_tc.get('argmaxAgree', 0):.1%} / MAE {res_tc.get('mae', 0):.4f}")
    log("euro", f"门槛判定：{report['verdict']}（≥85% 且 <0.03 直接接入）→ {OUT.name}")


if __name__ == "__main__":
    main()
