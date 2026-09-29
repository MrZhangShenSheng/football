# -*- coding: utf-8 -*-
r"""v4 模型票型回测（2026-09-29）—— 用真正的 v4 族模型选腿。

复用 score_family_parlay_v2.py 的 v4 模型逻辑：
1. 36维特征 → softmax → 6族概率
2. 族概率 × 族内条件频率 → 31比分概率
3. 用模型 top-k 比分选腿（而不是市场 top-k）

测试票型：4串1、4串5、4串11、3串4、2串1

开发者 sszhang
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter, defaultdict
from itertools import combinations, product
from pathlib import Path

random.seed(42)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import score_family_model as sfm

ROOT = sfm.ROOT
UNIT = 2.0


def load_hist_full():
    """加载 hist_odds 完整数据（含 had/crs/score）"""
    out = []
    for p in sorted((ROOT / "engine/cache/hist_odds").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        for m in d.get("matches", []):
            sc = str(m.get("score") or "")
            crs = m.get("crs") or {}
            had_raw = m.get("had") or {}
            ttg_raw = m.get("ttg") or {}
            if ":" not in sc or len(crs) < 20:
                continue
            try:
                h, a = (int(x) for x in sc.split(":")[:2])
            except ValueError:
                continue
            had = {k: float(v) for k, v in had_raw.items() if k in ("h", "d", "a") and v}
            ttg = {k: float(v) for k, v in ttg_raw.items() if v}
            if len(had) < 3:
                continue
            odds = {}
            for kk, v in crs.items():
                if str(kk).startswith("other") or not v:
                    continue
                try:
                    hh, aa = (int(x) for x in str(kk).split(":")[:2])
                    odds[(hh, aa)] = float(v)
                except (ValueError, TypeError):
                    continue
            if len(odds) < 20:
                continue
            out.append({
                "date": str(m.get("date") or "")[:10],
                "code": m.get("code"),
                "league": m.get("league"),
                "home_zh": m.get("home"),
                "away_zh": m.get("away"),
                "actual": (h, a),
                "odds": odds,
                "had": had,
                "ttg": ttg,
            })
    out.sort(key=lambda x: x["date"])
    return out


def build_model_packs():
    """构建 v4 模型预测包（复用 score_family_parlay_v2 逻辑）"""
    zh = {}
    for tid, srcs in sfm.load_aliases().items():
        if srcs.get("zh"):
            zh[srcs["zh"]] = tid

    tl = sfm.league_timeline()
    hist = load_hist_full()

    blind = []
    for m in hist:
        if m["date"] < sfm.CUT:
            continue
        hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
        if hid and aid:
            blind.append({**m, "hid": hid, "aid": aid})

    # 合并时间轴
    merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
    merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
               for i, m in enumerate(blind)]
    merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

    # 滚动统计
    stats = defaultdict(sfm.TeamStats)
    X_tr, y_tr, X_bl, meta = [], [], [], []
    rec_map = defaultdict(list)
    tot_g = tot_n = 0

    for r in merged:
        kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
        lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
        fv_h = stats[h].vector(0, lg_gf)
        fv_a = stats[a].vector(1, lg_gf)

        if kind == "L" and date < sfm.CUT and fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST:
            # 用 v2 基线特征（26维）—— 和原始 score_family_parlay_v2 一致
            row = sfm.feature_row((fv_h, fv_a))
            X_tr.append(row)
            y_tr.append(sfm.CLASSES.index(sfm.family_of(hg, ag)))
        elif kind == "B":
            meta.append(blind[r[6]])
            row = sfm.feature_row((fv_h, fv_a))
            X_bl.append(row)

        # 更新统计
        stats[h].add(hg, ag, True)
        stats[a].add(ag, hg, False)
        rec_map[h].append((a, hg, ag))
        rec_map[a].append((h, ag, hg))
        tot_g += hg + ag
        tot_n += 1

    # 训练 v4 模型
    print(f"训练 v4 模型：{len(X_tr)} 训练样本，{len(X_bl)} 盲测样本")
    model = sfm.train_softmax(X_tr, y_tr, len(sfm.CLASSES), balanced=True)
    P = sfm.predict_proba(model, X_bl)

    # 族内条件分布
    fam_dist = defaultdict(Counter)
    for r in merged:
        if r[0] == "L" and r[1] < sfm.CUT:
            fam_dist[sfm.family_of(r[4], r[5])][(r[4], r[5])] += 1

    # 构建预测包
    packs = []
    for i, m in enumerate(meta):
        # v4 模型比分概率分布
        model_probs = {}
        for ci, cname in enumerate(sfm.CLASSES):
            tot = sum(fam_dist.get(cname, {}).values()) or 1
            for s, c in fam_dist.get(cname, {}).items():
                model_probs[s] = model_probs.get(s, 0.0) + P[i][ci] * (c / tot)

        # 模型 top-k
        mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])

        # 市场 top-k
        mkt_sorted = sorted(m["odds"].items(), key=lambda kv: kv[1])

        # HAD 方向（模型族概率）
        ph = P[i][0] + P[i][1]  # home_clean + home_multi
        pd = P[i][2]            # draw
        pa = P[i][3] + P[i][4]  # away_clean + away_multi
        dir_probs = {"h": ph, "d": pd, "a": pa}
        dir_key = max(dir_probs, key=dir_probs.get)

        # gap（模型置信度）
        gap = mdl_sorted[0][1] - mdl_sorted[1][1] if len(mdl_sorted) >= 2 else 0

        packs.append({
            "date": m["date"],
            "actual": m["actual"],
            "odds": m["odds"],
            "had": m["had"],
            "ttg": m.get("ttg", {}),
            "model_probs": model_probs,
            "model_sorted": mdl_sorted,
            "mkt_sorted": mkt_sorted,
            "dir_key": dir_key,
            "dir_probs": dir_probs,
            "gap": gap,
        })

    return packs


def simulate_parlay(packs_sample, pick_method, crs_k, had_k, shape="4串1"):
    """
    模拟串关票。

    pick_method: "model" | "market"
    crs_k: CRS 选几个
    had_k: HAD 选几个
    shape: 串关结构
    """
    n_legs = len(packs_sample)

    # 骨架组合
    if shape == "4串1":
        combos = [tuple(range(n_legs))]
    elif shape == "4串5":
        combos = list(combinations(range(n_legs), 3)) + [tuple(range(n_legs))]
    elif shape == "4串11":
        combos = [c for k in (2, 3, 4) for c in combinations(range(n_legs), k)]
    elif shape == "3串4":
        combos = list(combinations(range(n_legs), 2)) + [tuple(range(n_legs))]
    elif shape == "3串1":
        combos = [tuple(range(n_legs))]
    elif shape == "2串1":
        combos = [tuple(range(n_legs))]
    else:
        combos = [tuple(range(n_legs))]

    # 每腿选项
    leg_opts = []
    for p in packs_sample:
        opts = []
        # CRS 选项
        if crs_k > 0:
            if pick_method == "model":
                for score, prob in p["model_sorted"][:crs_k]:
                    odds = p["odds"].get(score, 999)
                    opts.append(("crs", score, odds))
            else:  # market
                for score, odds in p["mkt_sorted"][:crs_k]:
                    opts.append(("crs", score, odds))
        # HAD 选项
        if had_k > 0:
            if pick_method == "model":
                # 按模型方向概率排序
                dir_sorted = sorted(p["dir_probs"].items(), key=lambda kv: -kv[1])
                for d, prob in dir_sorted[:had_k]:
                    odds = p["had"].get(d, 999)
                    opts.append(("had", d, odds))
            else:  # market
                had_sorted = sorted(p["had"].items(), key=lambda kv: kv[1])
                for d, odds in had_sorted[:had_k]:
                    opts.append(("had", d, odds))
        leg_opts.append(opts)

    if any(not opts for opts in leg_opts):
        return None

    # 展开所有注
    all_bets_per_combo = list(product(*leg_opts))
    n_bets = len(all_bets_per_combo) * len(combos)
    cost = n_bets * UNIT
    payout = 0.0
    any_hit = False
    max_mult = 0

    for combo in combos:
        combo_leg_opts = [leg_opts[i] for i in combo]
        for bet in product(*combo_leg_opts):
            all_hit_flag = True
            combo_odds = 1.0
            for idx, (pool, pick, odds) in enumerate(bet):
                leg_idx = combo[idx]
                actual = packs_sample[leg_idx]["actual"]  # (h, a) 元组
                if pool == "crs":
                    # pick 是元组 (h, a)，actual 也是元组
                    hit = (pick == actual)
                elif pool == "had":
                    actual_dir = "h" if actual[0] > actual[1] else ("a" if actual[0] < actual[1] else "d")
                    hit = (pick == actual_dir)
                else:
                    hit = False

                if not hit:
                    all_hit_flag = False
                    break
                combo_odds *= odds

            if all_hit_flag:
                payout += UNIT * combo_odds
                any_hit = True
                max_mult = max(max_mult, combo_odds)

    return {
        "cost": cost,
        "payout": payout,
        "hit": any_hit,
        "max_mult": max_mult,
        "n_bets": n_bets,
    }


def run_backtest(packs, n_sim=5000):
    """运行回测"""
    results = []

    # 按 gap 排序（用于 gap 选场）
    packs_by_gap = sorted(packs, key=lambda p: -p["gap"])

    # 测试配置
    test_cases = [
        # (名称, 腿数, shape, crs_k, had_k, pick_method, select_method)
        # === 4串1 系列 ===
        ("4串1 CRS单选 market 随机", 4, "4串1", 1, 0, "market", "random"),
        ("4串1 CRS单选 model 随机", 4, "4串1", 1, 0, "model", "random"),
        ("4串1 CRS单选 model gap", 4, "4串1", 1, 0, "model", "gap"),

        ("4串1 CRS双选 market 随机", 4, "4串1", 2, 0, "market", "random"),
        ("4串1 CRS双选 model 随机", 4, "4串1", 2, 0, "model", "random"),
        ("4串1 CRS双选 model gap", 4, "4串1", 2, 0, "model", "gap"),

        ("4串1 CRS双+HAD单 market 随机", 4, "4串1", 2, 1, "market", "random"),
        ("4串1 CRS双+HAD单 model 随机", 4, "4串1", 2, 1, "model", "random"),
        ("4串1 CRS双+HAD单 model gap", 4, "4串1", 2, 1, "model", "gap"),

        ("4串1 CRS双+HAD双 model gap", 4, "4串1", 2, 2, "model", "gap"),

        # === 4串11 容错 ===
        ("4串11 CRS双选 model gap", 4, "4串11", 2, 0, "model", "gap"),
        ("4串11 CRS双+HAD单 model gap", 4, "4串11", 2, 1, "model", "gap"),

        # === 3串系列 ===
        ("3串1 CRS双选 model gap", 3, "3串1", 2, 0, "model", "gap"),
        ("3串4 CRS双选 model gap", 3, "3串4", 2, 0, "model", "gap"),

        # === 2串1 对照 ===
        ("2串1 CRS双选 market 随机", 2, "2串1", 2, 0, "market", "random"),
        ("2串1 CRS双选 model 随机", 2, "2串1", 2, 0, "model", "random"),
        ("2串1 CRS双选 model gap", 2, "2串1", 2, 0, "model", "gap"),
        ("2串1 CRS双+HAD双 model gap", 2, "2串1", 2, 2, "model", "gap"),
    ]

    for name, n_legs, shape, crs_k, had_k, pick_method, select_method in test_cases:
        total_cost = 0
        total_payout = 0
        total_hits = 0
        max_mult_seen = 0
        hit_mults = []

        # 选场池
        if select_method == "gap":
            pool = packs_by_gap[:len(packs)//5]  # gap top 20%
        else:
            pool = packs

        if len(pool) < n_legs:
            continue

        for _ in range(n_sim):
            sample = random.sample(pool, n_legs)
            result = simulate_parlay(sample, pick_method, crs_k, had_k, shape)

            if result is None:
                continue

            total_cost += result["cost"]
            total_payout += result["payout"]
            if result["hit"]:
                total_hits += 1
                ticket_mult = result["payout"] / result["cost"] if result["cost"] > 0 else 0
                hit_mults.append(ticket_mult)
            max_mult_seen = max(max_mult_seen, result["max_mult"])

        if total_cost == 0:
            continue

        hit_rate = total_hits / n_sim
        recovery = total_payout / total_cost
        profit = recovery - 1
        avg_hit_mult = sum(hit_mults) / len(hit_mults) if hit_mults else 0

        results.append({
            "name": name,
            "hit_rate": hit_rate,
            "profit": profit,
            "recovery": recovery,
            "avg_hit_mult": avg_hit_mult,
            "max_mult": max_mult_seen,
            "n_hits": total_hits,
        })

    return results


def run_daily_backtest(packs):
    """按天滚动回测（复用下午闯关票的逻辑）"""
    # 按天分组
    by_day = defaultdict(list)
    for p in packs:
        by_day[p["date"]].append(p)

    results = []

    # 测试配置：(名称, shape, crs_k, had_k, pick_method, n_select)
    configs = [
        # 2串1 系列
        ("2串1 CRS双选 model gap", "2串1", 2, 0, "model", 2),
        ("2串1 CRS双选 market gap", "2串1", 2, 0, "market", 2),
        ("2串1 CRS双+HAD双 model gap", "2串1", 2, 2, "model", 2),
        ("2串1 CRS双+HAD单 model gap", "2串1", 2, 1, "model", 2),

        # 3串1 系列
        ("3串1 CRS双选 model gap", "3串1", 2, 0, "model", 3),
        ("3串1 CRS双+HAD单 model gap", "3串1", 2, 1, "model", 3),

        # 4串1 系列（柳州方案）
        ("4串1 CRS双选 model gap", "4串1", 2, 0, "model", 4),
        ("4串1 CRS双选 market gap", "4串1", 2, 0, "market", 4),
        ("4串1 CRS双+HAD单 model gap", "4串1", 2, 1, "model", 4),
        ("4串1 CRS双+HAD双 model gap", "4串1", 2, 2, "model", 4),
        ("4串1 CRS三选 model gap", "4串1", 3, 0, "model", 4),
    ]

    for name, shape, crs_k, had_k, pick_method, n_select in configs:
        total_cost = 0.0
        total_payout = 0.0
        total_hits = 0
        total_tickets = 0
        max_mult_seen = 0

        n_legs = int(shape[0])

        for day in sorted(by_day):
            day_packs = by_day[day]
            if len(day_packs) < n_select:
                continue

            # 每天选 gap top n_select 场
            day_sorted = sorted(day_packs, key=lambda p: -p["gap"])
            selected = day_sorted[:n_select]

            if len(selected) < n_legs:
                continue

            # 模拟这一票
            result = simulate_parlay(selected[:n_legs], pick_method, crs_k, had_k, shape)
            if result is None:
                continue

            total_cost += result["cost"]
            total_payout += result["payout"]
            total_tickets += 1
            if result["hit"]:
                total_hits += 1
            max_mult_seen = max(max_mult_seen, result["max_mult"])

        if total_cost == 0 or total_tickets == 0:
            continue

        hit_rate = total_hits / total_tickets
        recovery = total_payout / total_cost
        profit = recovery - 1

        results.append({
            "name": name,
            "n_tickets": total_tickets,
            "hit_rate": hit_rate,
            "profit": profit,
            "recovery": recovery,
            "max_mult": max_mult_seen,
        })

    return results


def main():
    print("=" * 90)
    print("v4 模型票型回测（按天滚动 —— 复用下午闯关票逻辑）")
    print("=" * 90)

    print("\n构建 v4 模型预测包...")
    packs = build_model_packs()

    # 按天分组统计
    by_day = defaultdict(list)
    for p in packs:
        by_day[p["date"]].append(p)
    print(f"共 {len(packs)} 场 / {len(by_day)} 天\n")

    # 按天滚动回测
    results = run_daily_backtest(packs)

    print("=" * 90)
    print("按天滚动回测结果（按盈利率排序）")
    print("=" * 90)
    print(f"{'组合':<40} {'票数':>6} {'命中率':>10} {'盈利率':>10} {'回收率':>10} {'最大倍数':>12}")
    print("-" * 90)

    for r in sorted(results, key=lambda x: -x["profit"]):
        mark = " ✅" if r["profit"] > 0 else ""
        print(f"{r['name']:<40} {r['n_tickets']:>6} {r['hit_rate']*100:>9.2f}% "
              f"{r['profit']*100:>9.1f}%{mark} {r['recovery']*100:>9.1f}% {r['max_mult']:>11.1f}x")

    print("-" * 90)

    # 找正盈利
    positive = [r for r in results if r["profit"] > 0]
    if positive:
        print(f"\n✅ 找到 {len(positive)} 个正盈利组合！")
        for r in sorted(positive, key=lambda x: -x["profit"]):
            print(f"   {r['name']}: 盈利率 {r['profit']*100:+.1f}%，{r['n_tickets']}票")
    else:
        print("\n❌ 未找到正盈利组合")

    # 对比 model vs market
    print("\n" + "=" * 90)
    print("model vs market 对比")
    print("=" * 90)
    for shape in ["2串1", "4串1"]:
        model_r = [r for r in results if shape in r["name"] and "model" in r["name"] and "CRS双选" in r["name"]]
        market_r = [r for r in results if shape in r["name"] and "market" in r["name"] and "CRS双选" in r["name"]]
        if model_r and market_r:
            print(f"  {shape} CRS双选: model {model_r[0]['profit']*100:+.1f}% vs market {market_r[0]['profit']*100:+.1f}%")


if __name__ == "__main__":
    main()
