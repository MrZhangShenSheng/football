# -*- coding: utf-8 -*-
r"""批量 walk-forward 回测汇总（2026-09-30）：全联赛×全赛季跑 backtest.walk_forward，
按铁律主指标 RPS 逐场配对检验「融合(DC+市场) vs 纯市场」与「纯DC vs 纯市场」。

为何要这支：单联赛单赛季可评 ~200 场，RPS 差 0.001 量级根本读不出信噪；且 backtest.py
只打印均值不做显著性。本脚本合并全样本做配对 bootstrap（同 clean_eval 口径），
差值 95% 区间整体落在 0 下方（RPS 越小越好）才算融合真的赢市场。

判据（预注册）：
  ① 主判据 RPS：mean(融合 − 市场) 的 95% 区间上限 < 0 才算赢（有序三向概率评分）。
  ② 辅助 log-loss 同口径。
  ③ 财务：按 Pinnacle 收盘价「价值下注」——对概率高于市场隐含(去水后)阈值的一边押平注，
     统计回收率；收盘价成交=零 CLV 假设，跑不赢 1.0 即无利可图。
  ④ 分联赛明细只作参考，n<150 的联赛×赛季不单独下结论。
时间线：复用 backtest.walk_forward（每段只用该段之前的比赛拟合），不含自泄漏。
用法：python engine/scripts/research/backtest_sweep.py
开发者 sszhang
"""
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "scripts"))

from backtest import evaluate, logloss, rps, walk_forward  # noqa: E402
from common import load_fusion_ab, ROOT as _R  # noqa: E402
from dc_fit import load_matches  # noqa: E402
from dc_predict import devig  # noqa: E402

CACHE = ROOT / "engine" / "cache"
OUT = ROOT / "data" / "04-summaries" / "2026-09-30-backtest-sweep.json"
N_BOOT = 2000
SEED = 20260930
MIN_N_LEAGUE = 150          # 分联赛下结论门槛
EDGE_GRID = (0.02, 0.05, 0.10)   # 价值下注阈值：模型概率 − 市场去水概率
UNIT = 1.0


def market_index(league, season):
    """收盘价索引 {(date,home,away): (h,d,a)}，同 backtest.main 口径。"""
    p = CACHE / f"odds_{league}_{season}.json"
    if not p.exists():
        return None
    raw = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    for m in raw.get("matches", []):
        try:
            d = datetime.strptime(m["date"], "%d/%m/%Y").date()
        except (ValueError, TypeError):
            continue
        if m.get("pin_h") and m.get("pin_d") and m.get("pin_a") and m.get("fthg") is not None:
            out[(d.isoformat(), m["home"], m["away"])] = (float(m["pin_h"]), float(m["pin_d"]), float(m["pin_a"]))
    return out


DUP_ALIAS = {"portugal-primeira": "portugal-liga"}   # 同一葡超两份缓存（2026-09-30 实测 153 场全同），去重防双计


def discover():
    """→ [(league, season)]，按联赛名排序；重名缓存去重（同联赛两份文件会双计样本）。"""
    pairs, seen = [], set()
    for p in sorted(CACHE.glob("odds_*.json")):
        stem = p.stem[len("odds_"):]
        league, _, season = stem.rpartition("_")
        if not (league and season.isdigit()):
            continue
        key = (DUP_ALIAS.get(league, league), season)
        if key in seen:
            print(f"  [去重] 跳过 {league} {season}（与 {key[0]} 同一联赛）")
            continue
        seen.add(key)
        pairs.append((league, season))
    return pairs


def value_bets(recs, key, edge, odds_by_match):
    """价值下注回收率：模型概率 − 市场去水概率 > edge 的一边押 1 注（收盘价成交）。"""
    stake = ret = 0.0
    n = 0
    for r in recs:
        o = odds_by_match.get((r["date"], r["home"], r["away"]))
        if not o:
            continue
        p_mkt_fair = devig(o)
        for i in range(3):
            if r[key][i] - p_mkt_fair[i] > edge:
                stake += UNIT
                n += 1
                if r["outcome"] == i:
                    ret += UNIT * o[i]
    return {"bets": n, "stake": round(stake, 1), "ret": round(ret, 1),
            "roi": round((ret - stake) / stake * 100, 1) if stake else None}


def paired_ci(x, y, rng):
    d = np.asarray(x) - np.asarray(y)
    idx = rng.integers(0, len(d), size=(N_BOOT, len(d)))
    lo, hi = np.percentile(d[idx].mean(axis=1), [2.5, 97.5])
    return float(d.mean()), float(lo), float(hi)


def main():
    rng = np.random.default_rng(SEED)
    all_recs, per_key, odds_all = [], {}, {}
    for league, season in discover():
        mkt = market_index(league, season)
        if not mkt:
            continue
        matches = load_matches(league, [season])
        if not matches:
            continue
        a, b = load_fusion_ab(league)
        recs = walk_forward(matches, mkt, a, b)
        if not recs:
            continue
        for r in recs:
            r["league"], r["season"] = league, season
        per_key[(league, season)] = {"n": len(recs), "a": a, "b": b,
                                    "metrics": evaluate(recs)}
        all_recs += recs
        odds_all.update(mkt)
        print(f"  {league:<26} {season}  可评 {len(recs):>4} 场  a={a} b={b}")

    n = len(all_recs)
    print(f"\n批量 walk-forward：{len(per_key)} 个联赛×赛季，合计可评 {n} 场\n")
    if n < 200:
        print("样本不足，不下结论")
        return

    r_mkt = [rps(r["p_mkt"], r["outcome"]) for r in all_recs]
    r_dc = [rps(r["p_dc"], r["outcome"]) for r in all_recs]
    r_fu = [rps(r["p_fused"], r["outcome"]) for r in all_recs]
    l_mkt = [logloss(r["p_mkt"], r["outcome"]) for r in all_recs]
    l_dc = [logloss(r["p_dc"], r["outcome"]) for r in all_recs]
    l_fu = [logloss(r["p_fused"], r["outcome"]) for r in all_recs]
    acc = lambda k: float(np.mean([max(range(3), key=lambda i: r[k][i]) == r["outcome"] for r in all_recs]))  # noqa: E731

    print("① 全样本主指标（RPS/log-loss 越小越好）")
    print(f"{'口径':<10}{'RPS':>9}{'log-loss':>11}{'方向命中':>10}")
    for tag, rr, ll, k in (("纯市场", r_mkt, l_mkt, "p_mkt"), ("纯DC", r_dc, l_dc, "p_dc"),
                           ("融合", r_fu, l_fu, "p_fused")):
        print(f"{tag:<10}{np.mean(rr):>9.4f}{np.mean(ll):>11.4f}{acc(k):>9.1%}")

    print("\n② 配对检验（差值=方案−纯市场，负=更好，[95%区间]）")
    for tag, rr, ll in (("纯DC", r_dc, l_dc), ("融合", r_fu, l_fu)):
        m, lo, hi = paired_ci(rr, r_mkt, rng)
        m2, lo2, hi2 = paired_ci(ll, l_mkt, rng)
        verdict = "赢市场✅" if hi < 0 else ("输市场" if lo > 0 else "分不开")
        print(f"  {tag:<6} ΔRPS {m:+.4f} [{lo:+.4f}, {hi:+.4f}] {verdict}"
              f"   Δlog-loss {m2:+.4f} [{lo2:+.4f}, {hi2:+.4f}]")

    print("\n③ 价值下注（收盘价成交·平注）")
    print(f"{'阈值':<8}{'DC下注':>9}{'DC ROI':>9}{'融合下注':>10}{'融合ROI':>9}")
    fin = {}
    for e in EDGE_GRID:
        vd, vf = value_bets(all_recs, "p_dc", e, odds_all), value_bets(all_recs, "p_fused", e, odds_all)
        fin[e] = {"dc": vd, "fused": vf}
        print(f"  >{e:<6.0%}{vd['bets']:>8}{(vd['roi'] if vd['roi'] is not None else 0):>8.1f}%"
              f"{vf['bets']:>10}{(vf['roi'] if vf['roi'] is not None else 0):>8.1f}%")

    print(f"\n④ 分联赛明细（n≥{MIN_N_LEAGUE} 才单独参考）")
    rows = []
    for (lg, se), v in sorted(per_key.items(), key=lambda kv: kv[1]["metrics"]["fused"]["rps"]):
        mk, fu = v["metrics"]["market_only"], v["metrics"]["fused"]
        flag = "" if v["n"] >= MIN_N_LEAGUE else "  (n小仅记录)"
        rows.append((lg, se, v["n"], mk["rps"], fu["rps"]))
        print(f"  {lg:<26}{se}  n={v['n']:>4}  市场RPS {mk['rps']:.4f}  融合RPS {fu['rps']:.4f}"
              f"  Δ{fu['rps'] - mk['rps']:+.4f}{flag}")

    OUT.write_text(json.dumps({
        "ranAt": "2026-09-30", "n": n, "leagues": len(per_key),
        "note": "批量 walk-forward·RPS 主指标·配对 bootstrap vs 纯市场·价值下注按 Pinnacle 收盘价",
        "overall": {
            "market": {"rps": round(float(np.mean(r_mkt)), 4), "logloss": round(float(np.mean(l_mkt)), 4), "acc": round(acc("p_mkt"), 3)},
            "dc": {"rps": round(float(np.mean(r_dc)), 4), "logloss": round(float(np.mean(l_dc)), 4), "acc": round(acc("p_dc"), 3)},
            "fused": {"rps": round(float(np.mean(r_fu)), 4), "logloss": round(float(np.mean(l_fu)), 4), "acc": round(acc("p_fused"), 3)},
        },
        "paired_vs_market": {
            "dc_rps": paired_ci(r_dc, r_mkt, rng), "fused_rps": paired_ci(r_fu, r_mkt, rng),
            "dc_logloss": paired_ci(l_dc, l_mkt, rng), "fused_logloss": paired_ci(l_fu, l_mkt, rng),
        },
        "value_bets": {f">{e:.0%}": v for e, v in fin.items()},
        "perLeague": [{"league": lg, "season": se, "n": nn, "rpsMarket": mr, "rpsFused": fr}
                      for lg, se, nn, mr, fr in rows],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
