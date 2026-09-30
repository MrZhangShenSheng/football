# -*- coding: utf-8 -*-
r"""亏损量化与归因（2026-09-30 大哥问「亏钱是指什么·能量化出来吗」）。

回答两件事：
  ① 折成钱：按固定注额把 ROI 换成"每 100 元亏多少""全样本共亏多少""每注期望亏多少"。
  ② 拆成两块：庄家抽水（谁押都躲不掉）+ 模型选错（模型相对瞎押的额外亏损）。
     总亏 = 抽水 + 选错。抽水是入场费，选错才是模型的罪。
     基准=同一批场次上"瞎押"（每场随机押一门，多次平均）与"全押主队/平/客"。
     若模型 ROI 低于瞎押 → 模型不只是没本事，是在主动挑差的（负 alpha）。

两个市场分开算，抽水天差地别：
  A. Pinnacle 收盘价（backtest_sweep 的价值下注口径·7216 场三向）
  B. 体彩比分池 CRS（clean_eval 口径·2551 场·实际下注的市场）
用法：python engine/scripts/research/loss_decompose.py
开发者 sszhang
"""
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest import walk_forward  # noqa: E402
from backtest_sweep import discover, market_index  # noqa: E402
from dc_fit import load_matches  # noqa: E402
from dc_predict import devig  # noqa: E402

OUT = ROOT / "data" / "04-summaries" / "2026-09-30-loss-decompose.json"
STAKE = 10.0          # 每注注额（元）——折算用，ROI 与注额无关
EDGE_GRID = (0.02, 0.05, 0.10)
N_RAND = 200          # 瞎押基准重复次数
SEED = 20260930
# 融合口径固定 a=0.40（不读 fusion.json）：2026-09-30 重校给 8 个联赛落 a=0 后，
# 那些联赛 p_fused≡p_mkt 不再产生下注信号，注数会随配置漂移、口径不可比。
# 此处要量化的是"若按原默认融合去下注会亏多少"，故锁定历史默认值。
FUSION_FIXED = (0.40, 1.0)
CLEAN_EVAL = ROOT / "data" / "04-summaries" / "2026-09-30-clean-eval.json"


def overround(o):
    """赔率的抽水率：1/o 之和 − 1（>0 即庄家毛利）。"""
    return sum(1.0 / x for x in o) - 1.0


def money(stake_units, ret_units):
    """注数/回款(以注为单位) → 金额三件套。"""
    stake = stake_units * STAKE
    ret = ret_units * STAKE
    roi = (ret - stake) / stake if stake else 0.0
    return {"bets": int(stake_units), "stake": round(stake, 0), "ret": round(ret, 0),
            "net": round(ret - stake, 0), "roi": round(roi * 100, 1),
            "per100": round(roi * 100, 1), "perBet": round(roi * STAKE, 2)}


def pinnacle_part(rng):
    recs, odds = [], {}
    for league, season in discover():
        mkt = market_index(league, season)
        matches = load_matches(league, [season]) if mkt else None
        if not matches:
            continue
        r = walk_forward(matches, mkt, *FUSION_FIXED)
        if r:
            recs += r
            odds.update(mkt)
    # 只留有收盘价的场次
    rows = [(r, odds[(r["date"], r["home"], r["away"])]) for r in recs
            if (r["date"], r["home"], r["away"]) in odds]
    n = len(rows)
    ovr = float(np.mean([overround(o) for _, o in rows]))
    print(f"\n{'=' * 78}\nA. Pinnacle 收盘价（三向·{n} 场·融合固定 a={FUSION_FIXED[0]}）"
          f"  平均抽水 {ovr:.2%}")
    print(f"{'=' * 78}")

    # 瞎押基准：每场随机押一门
    rand_roi = []
    for _ in range(N_RAND):
        s = r_ = 0.0
        for rec, o in rows:
            i = int(rng.integers(0, 3))
            s += 1
            if rec["outcome"] == i:
                r_ += o[i]
        rand_roi.append((r_ - s) / s)
    blind = float(np.mean(rand_roi))
    print(f"\n  瞎押基准（每场随机押一门·{N_RAND} 次平均）ROI {blind:+.1%}"
          f"  ≈ −抽水 {ovr:.1%}（数学上必然）")
    for tag, i in (("全押主队", 0), ("全押平局", 1), ("全押客队", 2)):
        s = len(rows)
        r_ = sum(o[i] for rec, o in rows if rec["outcome"] == i)
        print(f"  {tag}  ROI {(r_ - s) / s:+.1%}")

    print(f"\n  价值下注（模型概率 − 市场去水概率 > 阈值就押·每注 {STAKE:.0f} 元）")
    print(f"  {'口径':<10}{'阈值':>6}{'注数':>7}{'投入':>9}{'回款':>9}{'净亏':>9}"
          f"{'ROI':>8}{'每注亏':>8}{'选错':>8}")
    res = {}
    for key, tag in (("p_dc", "纯DC"), ("p_fused", "融合")):
        for e in EDGE_GRID:
            su = ru = 0.0
            for rec, o in rows:
                fair = devig(o)
                for i in range(3):
                    if rec[key][i] - fair[i] > e:
                        su += 1
                        if rec["outcome"] == i:
                            ru += o[i]
            if not su:
                continue
            mo = money(su, ru)
            sel = mo["roi"] / 100 - blind     # 相对瞎押的额外亏损=选错
            mo["selectionError"] = round(sel * 100, 1)
            res[f"{tag}>{e:.0%}"] = mo
            print(f"  {tag:<10}{e:>5.0%}{mo['bets']:>7}{mo['stake']:>9.0f}{mo['ret']:>9.0f}"
                  f"{mo['net']:>9.0f}{mo['roi']:>7.1f}%{mo['perBet']:>8.2f}{sel * 100:>7.1f}pp")
    return {"n": n, "overround": round(ovr, 4), "blindRoi": round(blind, 4),
            "valueBets": res, "stakePerBet": STAKE}


def sporttery_part(rng):
    """体彩比分池：读 clean_eval 回收率折成钱；抽水与瞎押基准由 hist_odds 池现算。"""
    if not CLEAN_EVAL.exists():
        print("\n[跳过] 缺 clean_eval 产物（先跑 clean_eval.py）")
        return None
    d = json.loads(CLEAN_EVAL.read_text(encoding="utf-8"))
    n, single = d["nEval"], d["single"]

    import clean_eval as ce   # 复用同一装载口径拿 31 项池子算抽水/瞎押
    pools = [m["pool"] for m in ce.load_hist() if m["date"] >= ce.START_DATE]
    ovr = float(np.mean([overround(list(p.values())) for p in pools])) if pools else None
    # 瞎押基准：池内等概率随机挑一项押 1 注，重复 N_RAND 次取均值
    hist = [m for m in ce.load_hist() if m["date"] >= ce.START_DATE]
    rand = []
    for _ in range(N_RAND):
        s = r_ = 0.0
        for m in hist:
            keys = list(m["pool"])
            pick = keys[int(rng.integers(0, len(keys)))]
            s += 1
            if pick == m["actual"]:
                r_ += m["pool"][pick]
        rand.append((r_ - s) / s)
    blind = float(np.mean(rand))

    print(f"\n{'=' * 78}\nB. 体彩比分池 CRS（{n} 场·实际下注的市场·31 项比分）")
    print(f"{'=' * 78}")
    print(f"  池子平均抽水 {ovr:.1%} → 谁押都先扣掉这一块（vs Pinnacle 三向仅几个点）")
    print(f"  瞎押基准（池内随机挑一项）ROI {blind:+.1%}")
    print(f"\n  每场押第一选 1 注 × {STAKE:.0f} 元（{n} 场）：")
    print(f"  {'方案':<14}{'投入':>9}{'回款':>9}{'净亏':>9}{'回收率':>8}{'每注亏':>8}{'vs市场':>9}")
    mkt = single["市场最低赔率"]["recovery"]
    out = {}
    for name, v in single.items():
        rec = v["recovery"]
        stake, ret = n * STAKE, n * STAKE * rec
        out[name] = {"bets": n, "stake": round(stake), "ret": round(ret),
                     "net": round(ret - stake), "recovery": round(rec * 100, 1),
                     "perBet": round((rec - 1) * STAKE, 2),
                     "vsMarketPP": round((rec - mkt) * 100, 1)}
        tail = "基准" if "市场" in name else f"{(rec - mkt) * 100:+.1f}pp"
        print(f"  {name:<14}{stake:>9.0f}{ret:>9.0f}{ret - stake:>9.0f}"
              f"{rec * 100:>7.1f}%{(rec - 1) * STAKE:>8.2f}{tail:>9}")
    return {"n": n, "overround": round(ovr, 4), "blindRoi": round(blind, 4),
            "schemes": out, "stakePerBet": STAKE}


def main():
    rng = np.random.default_rng(SEED)
    a = pinnacle_part(rng)
    b = sporttery_part(rng)
    print(f"\n{'=' * 78}\n归因小结\n{'=' * 78}")
    if a:
        print(f"  A 三向：抽水 {a['overround']:.1%} → 瞎押 ROI {a['blindRoi']:+.1%}（入场费）。"
              f"模型价值下注比瞎押还差（见上表'选错'列为负）= 负 alpha，不只没本事。")
    if b:
        best = min((v["vsMarketPP"], k) for k, v in b["schemes"].items() if "市场" not in k)
        print(f"  B 比分：抽水 {b['overround']:.1%} 是大头 → 任何人押都 −{b['overround'] / (1 + b['overround']):.0%} 起步；"
              f"市场最低赔率回收 {b['schemes']['市场最低赔率']['recovery']:.1f}%，"
              f"自建模型最好的也比它差 {abs(best[0]):.1f}pp。")
        print("  结论：抽水占亏损大头、谁都躲不掉；模型选错是额外那一层，方向为负——"
              "即不下注才是这两个市场上的最优解。")
    OUT.write_text(json.dumps({"ranAt": date.today().isoformat(), "stakePerBet": STAKE,
                               "pinnacle": a, "sporttery": b}, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print(f"\n→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
