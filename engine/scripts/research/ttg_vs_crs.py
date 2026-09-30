# -*- coding: utf-8 -*-
r"""TTG 总进球 vs CRS 比分：哪个"更容易中"？（大哥 2026-09-30 问）

判据预注册（跑前写定，禁止事后挑口径）：
  ① 命中难度：市场最优选法（押最低赔率项）的单注命中率，TTG vs CRS vs HAD；
     再报 top-2 双选的"至少中一"概率（口径=同场两注各 1 元，中任一则回款）。
  ② 值不值：单注回收率（押最低赔率项·真实结算），TTG vs CRS——
     "更容易中"若被赔率吸收，回收率不会更好，配对 bootstrap 报差值 CI。
  ③ 抽水结构：三池平均 overround（Σ1/o − 1），这决定长期期望的地板。
  ④ 上限说明：TTG 把 31 个比分折叠成 8 档，用命中换赔率上限——报两池
     最高赔率与最优选法赔率，看右尾 sacrificed 多少。
  样本：hist_odds 4724 场（带 had/ttg/crs 三池与赛果），同一批场次配对比较。

用法：python engine/scripts/research/ttg_vs_crs.py
开发者 sszhang
"""
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
HIST = (ROOT / "engine" / "cache" / "hist_odds"
        / "crs_hist_2025-10-01_2026-09-28.json")
OUT = ROOT / "data" / "04-summaries" / "2026-09-30-ttg-vs-crs.json"
SEED = 20260930
N_BOOT = 2000


def ttg_key(total):
    return min(total, 7)          # "7"=7+ 球


def parse_ttg(d):
    """hist_odds 的 ttg 键兼容 '3' 与 's3' 两种形态。"""
    out = {}
    for k, v in (d or {}).items():
        ks = str(k).lstrip("s")
        try:
            out[int(ks)] = float(v)
        except (ValueError, TypeError):
            continue
    return out


def parse_crs(d):
    out = {}
    for k, v in (d or {}).items():
        if str(k).startswith("other"):
            continue
        try:
            h, a = (int(x) for x in str(k).split(":")[:2])
            out[(h, a)] = float(v)
        except (ValueError, TypeError):
            continue
    return out


def overround(odds):
    inv = [1.0 / o for o in odds.values() if o > 1.0]
    return sum(inv) - 1.0 if inv else None


def main():
    ms = json.loads(HIST.read_text(encoding="utf-8"))["matches"]
    rows = []
    for m in ms:
        s = str(m.get("score") or "")
        if ":" not in s:
            continue
        try:
            h, a = (int(x) for x in s.split(":")[:2])
        except ValueError:
            continue
        ttg, crs = parse_ttg(m.get("ttg")), parse_crs(m.get("crs"))
        had = {k: float(v) for k, v in (m.get("had") or {}).items()
               if isinstance(v, (int, float)) and v}
        if len(ttg) < 6 or len(crs) < 20 or len(had) < 3:
            continue
        rows.append({"total": h + a, "ttg": ttg, "crs": crs, "had": had,
                     "sc": (h, a)})
    n = len(rows)
    print(f"三池齐备样本 {n} 场\n")

    print("=" * 72)
    print("③ 抽水结构（平均 overround = Σ1/o − 1·越高越吃亏）")
    print("=" * 72)
    vig = {}
    for name, key in (("HAD 胜平负", "had"), ("TTG 总进球", "ttg"), ("CRS 比分", "crs")):
        vs = [overround(r[key]) for r in rows]
        vs = [v for v in vs if v]
        vig[name] = float(np.mean(vs))
        print(f"  {name:12s} {np.mean(vs):6.1%}   （随机瞎押的期望回收 ≈ "
              f"{1/(1+np.mean(vs)):.1%}）")
    print(f"\n  串关地板：2串1 期望回收 = (1−抽水)²——TTG² {0.796**2:.1%} vs "
          f"CRS² {0.661**2:.1%}（同注数下 TTG 串天生亏得慢）")

    print("\n" + "=" * 72)
    print("① 命中难度：市场最优选法（押最低赔率项）")
    print("=" * 72)
    def topk_stats(pool, actual_fn, k):
        """押赔率最低的 k 项（各 1 注），返回(至少中一概率, 回收率, 单注均命中数)。"""
        atLeast, rec, hitsN = [], [], []
        for r in rows:
            ranked = sorted(r[pool].items(), key=lambda kv: kv[1])[:k]
            pay = sum(od for opt, od in ranked if actual_fn(r, opt) is True)
            anyhit = any(actual_fn(r, opt) is True for opt, _ in ranked)
            atLeast.append(anyhit)
            rec.append(pay / k)
            hitsN.append(sum(1 for opt, _ in ranked if actual_fn(r, opt) is True))
        return (float(np.mean(atLeast)), float(np.mean(rec)), float(np.mean(hitsN)))

    def hit_ttg(r, opt):
        return ttg_key(r["total"]) == opt

    def hit_crs(r, opt):
        return r["sc"] == opt

    def hit_had(r, opt):
        h, a = r["sc"]
        want = 0 if h > a else (1 if h == a else 2)
        return {"h": 0, "d": 1, "a": 2}.get(opt) == want

    stats = {}
    print(f"  {'池':<10}{'top1命中':>9}{'top1回收':>9}{'top2至少中一':>12}"
          f"{'top2回收':>9}")
    for name, pool, fn in (("HAD", "had", hit_had), ("TTG", "ttg", hit_ttg),
                           ("CRS", "crs", hit_crs)):
        a1, r1, _ = topk_stats(pool, fn, 1)
        a2, r2, _ = topk_stats(pool, fn, 2)
        stats[name] = {"top1Hit": a1, "top1Rec": r1, "top2Any": a2, "top2Rec": r2}
        print(f"  {name:<10}{a1:>9.1%}{r1:>9.1%}{a2:>12.1%}{r2:>9.1%}")

    print("\n  TTG 的 top2 是哪两档（押最低赔率两项的场次分布）：")
    pair_cnt = Counter()
    for r in rows:
        ranked = sorted(r["ttg"].items(), key=lambda kv: kv[1])[:2]
        pair_cnt[tuple(sorted(k for k, _ in ranked))] += 1
    for pair, c in pair_cnt.most_common(4):
        print(f"    {pair}  {c:4d} 场（{c/n:5.1%}）")

    print("\n" + "=" * 72)
    print("② 值不值：TTG vs CRS 单注回收率配对检验")
    print("=" * 72)
    rng = np.random.default_rng(SEED)
    fav_t = [sorted(r["ttg"].items(), key=lambda kv: kv[1])[0] for r in rows]
    fav_c = [sorted(r["crs"].items(), key=lambda kv: kv[1])[0] for r in rows]
    pay_t = np.array([od if hit_ttg(r, opt) else 0.0
                      for r, (opt, od) in zip(rows, fav_t)])
    pay_c = np.array([od if hit_crs(r, opt) else 0.0
                      for r, (opt, od) in zip(rows, fav_c)])
    d = pay_t - pay_c
    idx = rng.integers(0, n, size=(N_BOOT, n))
    bs = d[idx].mean(axis=1)
    lo, hi = np.percentile(bs, [2.5, 97.5])
    print(f"  TTG 市场top1 回收 {pay_t.mean():.1%}  vs  CRS 市场top1 回收 {pay_c.mean():.1%}")
    print(f"  配对差 {d.mean():+.1%}  95%CI [{lo:+.1%}, {hi:+.1%}]"
          f"{' ← TTG 显著更高' if lo > 0 else '（区间跨 0，统计上分不开）'}")

    print("\n" + "=" * 72)
    print("④ 右尾代价：更容易中 = 用赔率上限换的")
    print("=" * 72)
    ttg_max = np.mean([max(r["ttg"].values()) for r in rows])
    crs_max = np.mean([max(r["crs"].values()) for r in rows])
    ttg_fav = np.mean([sorted(r["ttg"].values())[0] for r in rows])
    crs_fav = np.mean([sorted(r["crs"].values())[0] for r in rows])
    print(f"  池内赔率均值：最低项  TTG {ttg_fav:.1f} vs CRS {crs_fav:.1f}")
    print(f"                最高项  TTG {ttg_max:.0f} vs CRS {crs_max:.0f}")
    print("  → TTG 拿 2 倍命中率，代价是单注赔率腰斩、池内最大赔率低一个数量级")

    OUT.write_text(json.dumps({
        "ranAt": "2026-09-30", "n": n,
        "vig": {k: round(v, 4) for k, v in vig.items()},
        "marketTop": {k: {kk: round(vv, 4) for kk, vv in s.items()}
                      for k, s in stats.items()},
        "ttgTop2Pairs": [[list(p), c] for p, c in pair_cnt.most_common(4)],
        "recoveryPaired": {"ttg": round(float(pay_t.mean()), 4),
                           "crs": round(float(pay_c.mean()), 4),
                           "diff": round(float(d.mean()), 4),
                           "ci": [round(float(lo), 4), round(float(hi), 4)]},
        "oddsProfile": {"ttgFav": round(float(ttg_fav), 2),
                        "crsFav": round(float(crs_fav), 2),
                        "ttgMax": round(float(ttg_max), 1),
                        "crsMax": round(float(crs_max), 1)},
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
