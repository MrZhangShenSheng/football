# -*- coding: utf-8 -*-
r"""自有 CLV 基线对照：−14.3% 是选单差，还是体彩全盘的系统性漂移？（铁律 14 第 3 条）

背景：票据 103 条腿的 clv_self 均值 −14.3%、仅 22.3% 为正，看着像"选单能力差"。
但 2026-09-30 已有前例：corpus 里 Pinnacle 口径 CLV 均值 −12.7%、95.7% 为负，
那其实是体彩与 Pinnacle 的抽水差（13pp），不是选单差——同一个坑不能踩两次。

本脚本做对照：把 05-trends 多时点快照里**所有选项**（不只我们选中的腿）的
首见价→末见价漂移算出来，得到"体彩全盘基线漂移"。
  · 若全盘基线也在 −14% 附近 → clv_self 衡量的是体彩临盘普涨/普跌的结构性成分，
    不是选单能力，该指标当前口径无法评价选单 → 必须换口径
  · 若全盘基线≈0 而我们选中的腿显著更负 → 真的是选单买贵了/买早了，是真问题

判据预注册（跑前写定，禁止事后挑）：
  ① 全盘基线漂移中位数与我们选中腿中位数之差，bootstrap 95%CI 是否含 0
  ② 分池报告（had/hhad/crs 各自基线），禁止只报对结论有利的池
  ③ 同时报样本量；任一池 <30 个选项-场次对，标"样本不足"不下结论

首跑结论（2026-09-30）：前置体检即未通过——05-trends 后续快照是**增量**（无变动则
matches 为空），29 天 155 个时点仅 27 个带数据，无任何一天有 2 个非空时点，
故漂移基线在现有数据上算不出来，clv_self 的 −14.3% 归因悬置（既不能说选单差，
也不能说结构性漂移）。首版脚本曾把空 matches 当"无数据"跳过，于是每个选项只见过
一次价、漂移恒为 0，误报"选中腿显著更负、买贵了"——该结论已作废，教训见文件头体检段。

用法：python engine/scripts/research/clv_self_baseline.py
开发者 sszhang
"""
import glob
import json
import statistics
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
TRENDS = ROOT / "data" / "05-trends"
TICKETS = ROOT / "data" / "06-tickets" / "tickets.json"
OUT = ROOT / "data" / "04-summaries" / "2026-09-30-clv-self-baseline.json"
SEED = 20260930
POOLS = ("had", "hhad", "crs")
MIN_N = 30


def load_day(path):
    """一天内 (池, matchId, 选项) → [首见价, 末见价]。"""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    seen = {}
    for snap in d.get("snapshots", []):
        for m in snap.get("matches", []):
            mid = m.get("matchId")
            for pool in POOLS:
                book = m.get(pool) or {}
                for opt, price in book.items():
                    if opt == "goalLine" or not isinstance(price, (int, float)):
                        continue
                    if price <= 1.0:
                        continue
                    key = (pool, mid, opt)
                    if key in seen:
                        seen[key][1] = float(price)
                    else:
                        seen[key] = [float(price), float(price)]
    return seen


def boot_median_diff(a, b, seed=SEED, n=20000):
    """中位数差的 bootstrap 区间。"""
    rng = np.random.default_rng(seed)
    a, b = np.asarray(a), np.asarray(b)
    d = np.array([np.median(rng.choice(a, a.size, replace=True))
                  - np.median(rng.choice(b, b.size, replace=True)) for _ in range(n)])
    return np.percentile(d, [2.5, 97.5]), d


def main():
    files = sorted(glob.glob(str(TRENDS / "*-odds.json")))
    print(f"体彩快照文件 {len(files)} 天（{Path(files[0]).stem} ~ {Path(files[-1]).stem}）\n")

    # 前置体检：后续快照是增量（无变动则 matches 为空），一天内必须 ≥2 个非空时点
    # 才可能算出漂移。2026-09-30 首跑发现 29 天 155 个时点仅 27 个带 matches、
    # 且无任何一天有 2 个非空时点 → 漂移基线在现有数据上根本算不出来。
    usable = 0
    for f in files:
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        if sum(1 for s in d.get("snapshots", []) if s.get("matches")) >= 2:
            usable += 1
    print(f"可算漂移的天数（≥2 个非空快照时点）：{usable} / {len(files)}")
    if usable == 0:
        print("\n❌ 前置体检未通过：没有任何一天有两个以上非空快照时点。")
        print("   05-trends 的后续快照只记变动场次（无变动则 matches 为空），")
        print("   现有数据算不出任何选项的价格漂移 → 全盘基线无法建立，")
        print("   因此 clv_self 的 −14.3% 既不能归因为选单差、也不能归因为结构性漂移。")
        print("   连带影响：engine/cache/drift_features.json 同样只有 3 场真数据、漂移全零，")
        print("   设计文档'特征积累两月后 n≥500 进融合'实际攒了 0 场。")
        OUT.write_text(json.dumps({
            "ranAt": "2026-09-30", "nDays": len(files), "usableDays": 0,
            "verdict": "前置体检未通过：05-trends 后续快照为增量（无变动即空 matches），"
                       "无任何一天有 2 个非空时点 → 漂移基线无法建立，clv_self 归因悬置",
            "sideFinding": "drift_features.json 仅 3 场真数据、漂移全零，drift 特征线实际未积累",
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"\n→ {OUT.relative_to(ROOT)}")
        return

    per_pool = {p: [] for p in POOLS}
    multi = 0
    for f in files:
        seen = load_day(f)
        for (pool, _mid, _opt), (first, last) in seen.items():
            if first != last:
                multi += 1
            per_pool[pool].append(last / first - 1.0)

    print("=" * 64)
    print("① 体彩全盘基线漂移（首见价 → 末见价，所有选项）")
    print("=" * 64)
    base = {}
    for p in POOLS:
        v = per_pool[p]
        if len(v) < MIN_N:
            print(f"  {p:5s}: n={len(v)} ⚠ 样本不足，不下结论")
            base[p] = {"n": len(v), "insufficient": True}
            continue
        arr = np.array(v)
        print(f"  {p:5s}: n={len(arr):6d} 中位 {np.median(arr):+.2%} "
              f"均值 {arr.mean():+.2%} 正占比 {(arr > 0).mean():.1%}")
        base[p] = {"n": int(arr.size), "median": round(float(np.median(arr)), 4),
                   "mean": round(float(arr.mean()), 4),
                   "posShare": round(float((arr > 0).mean()), 4)}
    allv = np.array([x for p in POOLS for x in per_pool[p]])
    print(f"  合计 : n={allv.size} 中位 {np.median(allv):+.2%} "
          f"均值 {allv.mean():+.2%} 正占比 {(allv > 0).mean():.1%}")
    print(f"  （其中首末价不同的选项-场次对 {multi} 个，"
          f"占 {multi/max(1,allv.size):.1%}——价格未动的对漂移恒为 0）")

    print("\n" + "=" * 64)
    print("② 我们选中腿的 clv_self")
    print("=" * 64)
    t = json.loads(TICKETS.read_text(encoding="utf-8"))
    ts = t if isinstance(t, list) else t.get("tickets", [])
    picked = [l["clv_self"] for x in ts for l in (x.get("legs") or [])
              if l.get("clv_self") is not None]
    print(f"  n={len(picked)} 中位 {statistics.median(picked):+.2%} "
          f"均值 {sum(picked)/len(picked):+.2%} "
          f"正占比 {sum(1 for v in picked if v > 0)/len(picked):.1%}")

    print("\n" + "=" * 64)
    print("③ 判据：选中腿 vs 全盘基线（中位数差 bootstrap）")
    print("=" * 64)
    (lo, hi), _ = boot_median_diff(picked, allv)
    diff = statistics.median(picked) - float(np.median(allv))
    print(f"  中位数差 {diff:+.2%}  95%CI [{lo:+.2%}, {hi:+.2%}]")
    contains0 = lo <= 0.0 <= hi
    print(f"  区间是否含 0: {contains0}")

    if contains0:
        verdict = ("选中腿漂移与体彩全盘基线无显著差异 → clv_self 当前口径反映的是"
                   "体彩临盘系统性漂移，不是选单能力，不可用它评价选腿好坏")
    elif diff < 0:
        verdict = ("选中腿显著比全盘基线更负 → 真的买贵/买早了，是可改进的真问题"
                   "（方向：延后出票至临近停售）")
    else:
        verdict = "选中腿显著优于全盘基线 → 出票时点占优（beat the close）"
    print(f"\n  → {verdict}")
    print("\n  注：末见价≠真实收盘价（停售后无价），口径近似；"
          "\n  且 05-trends 每日快照数少（今日仅 4 个时点），首末价相同的选项漂移恒 0，会把分布拉向 0。")

    OUT.write_text(json.dumps({
        "ranAt": "2026-09-30", "nDays": len(files),
        "baselineByPool": base,
        "baselineAll": {"n": int(allv.size), "median": round(float(np.median(allv)), 4),
                        "mean": round(float(allv.mean()), 4),
                        "posShare": round(float((allv > 0).mean()), 4),
                        "nPriceMoved": multi},
        "picked": {"n": len(picked), "median": round(statistics.median(picked), 4),
                   "mean": round(sum(picked)/len(picked), 4)},
        "medianDiff": round(diff, 4), "ci": [round(float(lo), 4), round(float(hi), 4)],
        "ciContainsZero": bool(contains0), "verdict": verdict,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
