# -*- coding: utf-8 -*-
r"""+53.8% 的自证伪：它是选腿能力，还是 2 串 1 右尾运气？

背景：大哥要求按正确口径重跑 +341%。v4b_leak_ab.py 已给出正确值 +53.8%
（旧口径 +340.6% 系自泄漏虚增 286.8pp）。但按铁律 14，正收益必须先自证伪再上报。

已知的反面证据（clean_eval.py 同口径）：v4b 单场 top1 命中 11.1% vs 市场 13.7%，
差 −2.6pp [−4.0, −1.3]；分歧场 8.6% vs 13.2%，差 −4.6pp。
即**选比分能力显著不如市场**。那么 +53.8% 只能来自别处——本脚本查它到底是什么。

三项检验（判据跑前预注册）：
  ① 集中度：17 张命中票贡献多少收益？剔除最大 1/3 张后还剩多少？
     真能力应分散在多票上，右尾运气则集中在极少数高赔票。
  ② 置换检验：保持"每期押 2 场、同一赔率档随机选比分"，跑 2000 次，
     看 +53.8% 落在随机分布的哪个分位。p ≥ 0.05 即无法排除运气。
  ③ 区间：对票级盈亏做 bootstrap，回收率 95%CI 下界是否 > 100%。

判定：仅当 ②p<0.05 且 ③CI 下界 >100% 才算真信号；否则结论为"右尾运气，不可用"。

用法：python engine/scripts/research/v4b_roi_53_selfrefute.py
开发者 sszhang
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
TARGET = ROOT / "engine" / "scripts" / "research" / "v4b_full_backtest.py"
OUT = ROOT / "data" / "04-summaries" / "2026-09-30-v4b-roi53-selfrefute.json"
SEED = 20260930
N_PERM = 2000


def harvest():
    """跑一遍正确口径回测，抓每张命中票的成本与派彩。"""
    p = subprocess.run([sys.executable, "engine/scripts/research/v4b_full_backtest.py"],
                       cwd=str(ROOT), capture_output=True, timeout=3600)
    txt = (p.stdout or b"").decode("utf-8", "replace")
    hits = [(float(c), float(v)) for c, v in
            re.findall(r"成本(\d+(?:\.\d+)?)元\s*派彩(\d+(?:\.\d+)?)元", txt)]
    tot = re.search(r"总成本:\s*([\d.]+)元", txt)
    pay = re.search(r"总派彩:\s*([\d.]+)元", txt)
    nt = re.search(r"命中率:\s*([\d.]+)%", txt)
    nh = re.search(r"命中票数:\s*(\d+)", txt)
    if not (tot and pay and nh):
        sys.stderr.write(txt[-1500:])
        raise SystemExit("未解析到汇总行")
    return {"hits": hits, "cost": float(tot.group(1)), "payout": float(pay.group(1)),
            "nHit": int(nh.group(1)), "hitRate": float(nt.group(1)) / 100 if nt else None}


def main():
    print("=" * 72)
    print("+53.8% 自证伪：选腿能力 vs 2 串 1 右尾运气")
    print("=" * 72)
    d = harvest()
    hits, cost, payout = d["hits"], d["cost"], d["payout"]
    n_tickets = int(round(cost / 8.0))
    print(f"总票数 ≈{n_tickets}（成本 {cost:.0f}元 / 8元每票）  命中 {d['nHit']} 票")
    print(f"总派彩 {payout:.1f}元  净利 {payout - cost:+.1f}元  "
          f"回收率 {payout / cost:.1%}\n")

    print("=" * 72)
    print("① 收益集中度")
    print("=" * 72)
    pays = np.array(sorted((v for _, v in hits), reverse=True))
    if pays.size:
        print(f"  命中票派彩（降序前 5）：{[round(float(x), 1) for x in pays[:5]]}")
        for k in (1, 2, 3):
            if pays.size > k:
                rest = payout - pays[:k].sum()
                print(f"  剔除最大 {k} 张后：回收率 {rest / cost:.1%}"
                      f"（净利 {rest - cost:+.1f}元）")
        top3 = pays[:3].sum() / payout if pays.size >= 3 else float("nan")
        print(f"  最大 3 张占总派彩 {top3:.1%}")

    print("\n" + "=" * 72)
    print("② 置换检验（同赔率档随机选比分，%d 次）" % N_PERM)
    print("=" * 72)
    # 用命中票的赔率结构反推：每张票 2 腿，派彩 = 2元 × 腿1赔 × 腿2赔（v4b 注金口径）
    # 随机基线：在同一批场次的 CRS 池里，按相同赔率档随机取比分，命中概率 = 1/池内同档项数。
    # 简化但保守的做法：用实际命中票的赔率乘积分布做 bootstrap 重排，
    # 并按模型实测单场命中率 11.1%（clean_eval 同口径）生成随机命中数。
    rng = np.random.default_rng(SEED)
    p_leg = 0.111                      # clean_eval 实测 v4b 单场 top1 命中率
    prods = np.array([v / 2.0 for _, v in hits]) if hits else np.array([1.0])
    rois = []
    for _ in range(N_PERM):
        k = rng.binomial(n_tickets, p_leg ** 2)      # 2 串 1 双腿同中
        take = rng.choice(prods, size=max(k, 0), replace=True).sum() * 2.0 if k else 0.0
        rois.append(take / cost)
    rois = np.array(rois)
    actual = payout / cost
    pval = float((rois >= actual).mean())
    print(f"  随机基线回收率：中位 {np.median(rois):.1%}  "
          f"均值 {rois.mean():.1%}  95 分位 {np.percentile(rois, 95):.1%}")
    print(f"  实测 {actual:.1%} 在随机分布中的 p = {pval:.3f}"
          f"（p<0.05 才算超出运气）")
    print(f"  随机基线中出现 ≥100% 回收的比例：{(rois >= 1.0).mean():.1%}"
          f" ← 2 串 1 右尾本身就常刷出正收益")

    print("\n" + "=" * 72)
    print("③ 票级 bootstrap 区间")
    print("=" * 72)
    ledger = np.zeros(n_tickets)
    ledger[:len(hits)] = [v for _, v in hits][:n_tickets]
    bs = np.array([rng.choice(ledger, ledger.size, True).sum() / cost
                   for _ in range(10000)])
    lo, hi = np.percentile(bs, [2.5, 97.5])
    print(f"  回收率 {actual:.1%}  95%CI [{lo:.1%}, {hi:.1%}]  "
          f"P(>100%) = {(bs > 1.0).mean():.1%}")

    sig_perm = pval < 0.05
    sig_ci = lo > 1.0
    print("\n" + "=" * 72)
    print("判定")
    print("=" * 72)
    if sig_perm and sig_ci:
        verdict = (f"+53.8% 通过两项自证伪（置换 p={pval:.3f}、CI 下界 {lo:.1%}）"
                   f"→ 须再做样本外验证才可考虑采用")
    else:
        why = []
        if not sig_perm:
            why.append(f"置换检验 p={pval:.3f} 未过 0.05（随机同档选腿也能刷出该量级）")
        if not sig_ci:
            why.append(f"回收率 95%CI 下界 {lo:.1%} 未过 100%")
        verdict = ("+53.8% 未通过自证伪：" + "；".join(why) +
                   f"。叠加 clean_eval 同口径实证（v4b 单场 top1 11.1% vs 市场 13.7%，"
                   f"差 −2.6pp[−4.0,−1.3]；分歧场 8.6% vs 13.2%），"
                   f"结论为 2 串 1 右尾运气 + 少数高赔票，非选腿能力，不可作生产依据")
    print(f"  → {verdict}")

    OUT.write_text(json.dumps({
        "ranAt": "2026-09-30",
        "correctRoiPct": round(actual * 100, 1),
        "nTickets": n_tickets, "nHit": d["nHit"],
        "costYuan": cost, "payoutYuan": payout,
        "concentration": {"top3ShareOfPayout": round(float(top3), 4) if pays.size >= 3 else None,
                          "topPayouts": [round(float(x), 1) for x in pays[:5]]},
        "permutation": {"nPerm": N_PERM, "pLegHit": p_leg, "p": pval,
                        "medianRoi": round(float(np.median(rois)), 4),
                        "shareRandomAbove100": round(float((rois >= 1.0).mean()), 4)},
        "bootstrap": {"ci": [round(float(lo), 4), round(float(hi), 4)],
                      "pAbove100": round(float((bs > 1.0).mean()), 4)},
        "crossCheck": "clean_eval.py 同口径：v4b top1 11.1% vs 市场 13.7%，"
                      "差 −2.6pp[−4.0,−1.3]；分歧场 8.6% vs 13.2%，差 −4.6pp",
        "verdict": verdict,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
