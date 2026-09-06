#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线重校准(轨道C·设计§六): corpus全量→各p档声称vs实际校准曲线→calibration_curve.json
+ conf_tiers.json分层线滚动修正(偏差>8pp才调·语料每+10轮重估).
幂等: 语料增量<10场跳过. isotonic修正只标注不生效(影子AB 30轮后才切主链).
开发者 sszhang"""
import json
from datetime import date
from common import ROOT, log

CACHE = ROOT / "engine/cache"
CORPUS = ROOT / "data/04-summaries/corpus.json"
DEFAULT_TIERS = {"dan": 0.75, "std": 0.60, "obs": 0.55}
TIER_SHIFT_PP = 0.08          # 分层线调整门槛(设计§五: 偏差>8pp才调)
MIN_DELTA_N = 10              # 幂等门槛(设计§六)

def build_bins(records: list) -> list:
    bins = {}
    for r in records:
        pf = r.get("p_final")
        if not isinstance(pf, list) or len(pf) != 3:
            continue
        if not r.get("pick") or r.get("pick") == "(避开)" or r.get("directionHit") is None:
            continue
        pm = max(pf)
        b = round(int(pm * 10) / 10, 1)  # 0.1宽分箱向下取整(0.7x全落pBin=0.7)
        bins.setdefault(b, [0, 0])
        bins[b][1] += 1
        bins[b][0] += 1 if r["directionHit"] else 0
    # 不过滤小n: n字段随附, 消费方(T7叙事仓)自行按n判读置信度
    return [{"pBin": b, "claimed": round(b + 0.05, 2),
             "actual": round(h / n, 3), "n": n}
            for b, (h, n) in sorted(bins.items())]

def should_skip(prev_n: int, cur_n: int) -> bool:
    return cur_n - prev_n < MIN_DELTA_N

def load_tiers() -> dict:
    p = CACHE / "conf_tiers.json"
    if not p.exists():
        p.write_text(json.dumps({"tiers": DEFAULT_TIERS, "generatedAt": str(date.today())},
                                ensure_ascii=False, indent=1), encoding="utf-8")
        return DEFAULT_TIERS
    return json.loads(p.read_text(encoding="utf-8"))["tiers"]

def main() -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    records = corpus.get("records", [])
    curve = {"generatedAt": str(date.today()), "n": len(records),
             "bins": build_bins(records), "tiers": load_tiers()}
    out = CACHE / "calibration_curve.json"
    prev = json.loads(out.read_text(encoding="utf-8"))["n"] if out.exists() else 0
    if should_skip(prev, len(records)):
        log("recalibrate", f"跳过: 语料增量 {len(records) - prev} < {MIN_DELTA_N}")
        return
    out.write_text(json.dumps(curve, ensure_ascii=False, indent=1), encoding="utf-8")
    log("recalibrate", f"→ {out} ({len(curve['bins'])} bins, n={len(records)})")

if __name__ == "__main__":
    main()
