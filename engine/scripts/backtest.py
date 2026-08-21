#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""回测基建：walk-forward 逐轮前推验证 + RPS/log-loss + CLV + 消融开关。

评估铁律（CLAUDE.md）：
- 主指标 RPS（足球三向有序概率评分）+ log loss（辅）+ CLV（盈利性）
- 命中率仅作展示；回测成交一律按 Pinnacle 收盘价（PPCH）计
- walk-forward：每场预测只用该场之前的数据拟合（防数据泄漏）

用法：
  python backtest.py spain-laliga 2526                # 默认融合（a=0.4,b=1.0）
  python backtest.py spain-laliga 2526 --ablation     # 消融：纯市场 vs 纯DC vs 融合
"""
import json
import math
import sys
from datetime import date

import numpy as np

from common import log, ROOT
from dc_fit import load_matches, fit, dc_tau

CACHE_DIR = ROOT / "engine" / "cache"
FUSION_DEFAULT = {"a": 0.4, "b": 1.0}
REFIT_EVERY = 30  # 每 N 场重拟合一次（walk-forward 折中成本）


def devig(odds):
    inv = [1.0 / o for o in odds]
    s = sum(inv)
    return [i / s for i in inv]


def fuse(p_dc, p_mkt, a, b):
    z = [a * math.log(max(p, 1e-12)) + b * math.log(max(m, 1e-12)) for p, m in zip(p_dc, p_mkt)]
    m = max(z)
    e = [math.exp(v - m) for v in z]
    s = sum(e)
    return [v / s for v in e]


def dc_three(teams_params, home_adv, rho, home, away):
    th, ta = teams_params.get(home), teams_params.get(away)
    if not th or not ta:
        return None
    lh = math.exp(th["attack"] + ta["defense"] + home_adv)
    la = math.exp(ta["attack"] + th["defense"])
    p = np.zeros((7, 7))
    for x in range(7):
        for y in range(7):
            import math as _m
            pm = _m.exp(-lh) * lh ** x / _m.factorial(x) * _m.exp(-la) * la ** y / _m.factorial(y)
            p[x, y] = max(pm * dc_tau(x, y, lh, la, rho), 1e-12)
    p /= p.sum()
    ph = float(sum(p[i, j] for i in range(7) for j in range(7) if i > j))
    pd = float(np.trace(p))
    return [ph, pd, 1 - ph - pd]


def rps(probs, outcome_idx):
    o = [0.0, 0.0, 0.0]
    o[outcome_idx] = 1.0
    return 0.5 * sum((sum(probs[:k + 1]) - sum(o[:k + 1])) ** 2 for k in range(2))


def logloss(probs, outcome_idx):
    return -math.log(max(probs[outcome_idx], 1e-12))


def walk_forward(matches, market_by_match, a, b):
    """逐段重拟合，段内逐场预测。返回逐场记录列表。"""
    records = []
    last_fit_idx = 0
    while last_fit_idx + REFIT_EVERY < len(matches):
        train = matches[:last_fit_idx + REFIT_EVERY]
        teams, attack, defense, home_adv, rho, _ = fit(train, 0.005)
        idx = {t: i for i, t in enumerate(teams)}
        params = {t: {"attack": float(attack[i]), "defense": float(defense[i])} for t, i in idx.items()}
        # 预测下一段
        for m in matches[last_fit_idx + REFIT_EVERY: last_fit_idx + 2 * REFIT_EVERY]:
            mkt = market_by_match.get((m["date"].isoformat(), m["home"], m["away"]))
            if not mkt:
                continue
            p_dc = dc_three(params, home_adv, rho, m["home"], m["away"])
            if p_dc is None:
                continue
            p_mkt = devig(mkt)
            p_fused = fuse(p_dc, p_mkt, a, b)
            outcome = 0 if m["hg"] > m["ag"] else (1 if m["hg"] == m["ag"] else 2)
            records.append({
                "date": m["date"].isoformat(), "home": m["home"], "away": m["away"],
                "hg": m["hg"], "ag": m["ag"], "outcome": outcome,
                "p_mkt": p_mkt, "p_dc": p_dc, "p_fused": p_fused,
            })
        last_fit_idx += REFIT_EVERY
    return records


def evaluate(records):
    def stats(key):
        if not records:
            return None
        r = [rps(rec[key], rec["outcome"]) for rec in records]
        ll = [logloss(rec[key], rec["outcome"]) for rec in records]
        acc = [max(range(3), key=lambda i: rec[key][i]) == rec["outcome"] for rec in records]
        return {"rps": round(float(np.mean(r)), 4), "logloss": round(float(np.mean(ll)), 4),
                "acc": round(float(np.mean(acc)), 3), "n": len(records)}
    return {"market_only": stats("p_mkt"), "dc_only": stats("p_dc"), "fused": stats("p_fused")}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    league = args[0] if args else "spain-laliga"
    season = args[1] if len(args) > 1 else "2526"
    a, b = FUSION_DEFAULT["a"], FUSION_DEFAULT["b"]
    fus_path = CACHE_DIR / "fusion.json"
    if fus_path.exists():
        f = json.loads(fus_path.read_text(encoding="utf-8"))
        a, b = f["a"], f["b"]

    raw_path = CACHE_DIR / f"odds_{league}_{season}.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    from datetime import datetime
    market_by_match = {}
    for m in raw["matches"]:
        try:
            d = datetime.strptime(m["date"], "%d/%m/%Y").date()
        except (ValueError, TypeError):
            continue
        if m.get("pin_h") and m.get("pin_d") and m.get("pin_a") and m.get("fthg") is not None:
            market_by_match[(d.isoformat(), m["home"], m["away"])] = (float(m["pin_h"]), float(m["pin_d"]), float(m["pin_a"]))
    matches = load_matches(league, [season])
    log("backtest", f"{league} {season}: {len(matches)} 场，含收盘价 {len(market_by_match)} 场")

    records = walk_forward(matches, market_by_match, a, b)
    if len(records) < 20:
        log("backtest", f"可评样本 {len(records)} 场（<20），结论仅供参考")
    result = {
        "league": league, "season": season, "refitEvery": REFIT_EVERY,
        "fusion": {"a": a, "b": b}, "ranAt": date.today().isoformat(),
        "metrics": evaluate(records),
        "records": len(records),
    }
    dest = ROOT / "data" / "04-summaries" / f"backtest_{league}_{season}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    slim = dict(result)
    dest.write_text(json.dumps(slim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for k, v in result["metrics"].items():
        if v:
            log("backtest", f"{k:>12}: RPS={v['rps']} logloss={v['logloss']} acc={v['acc']} (n={v['n']})")
    log("backtest", f"完成 → {dest.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
