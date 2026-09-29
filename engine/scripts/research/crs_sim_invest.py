# -*- coding: utf-8 -*-
"""CRS 投入产出模拟：真实赔率存档 × 无泄漏预测 × 真实赛果，逐轮结算。

动机（2026-09-29 大哥指令"模拟跑一下计算投入与产出"）：上一轮胜率表用假设均价，
本脚本换成 score_odds 存档的真实赔率 + 真实赛果跑成账。

口径纪律（对齐设计文档 R4 盲测）：
- 预测只用历史比分频率（时点滚动，不含当场赛果）
- 赔率取存档当日真实 CRS 报价（含体彩抽水，不去水美化）
- 未完赛/无报价场次跳过不计
- 各结构同一批选腿 → 配对对照消除选腿运气

开发者 sszhang
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from band_calibration import DIVS, SEASONS, fetch_rows

ROOT = Path(__file__).resolve().parents[3]
UNIT = 2.0


def global_score_rank():
    """全库历史比分频率排序 = 本模拟的预测基线（纯模板，12.1% 水平）。"""
    hist = Counter()
    for div in DIVS:
        for season in SEASONS:
            for r in fetch_rows(season, div):
                try:
                    hist[(int(r["FTHG"]), int(r["FTAG"]))] += 1
                except (TypeError, ValueError, KeyError):
                    continue
    return [s for s, _ in hist.most_common()]


def _parse(sc):
    """赛果解析：02-results 用 '2-1'、sporttery 缓存用 '2:1'。"""
    s = str(sc).strip()
    for sep in (":", "-"):
        if sep in s:
            try:
                h, a = (int(x) for x in s.split(sep)[:2])
                return (h, a)
            except ValueError:
                continue
    return None


def _around(day: str) -> list[str]:
    """销售日 ±1 天（体彩晚场挂次日凌晨口径）。"""
    from datetime import date, timedelta
    try:
        y, m, d = (int(x) for x in day.split("-"))
        base = date(y, m, d)
    except ValueError:
        return [day]
    return [(base + timedelta(days=k)).isoformat() for k in (0, 1, -1)]


def load_results():
    res = {}
    for fp in glob.glob(str(ROOT / "data/02-results/*.json")):
        try:
            d = json.loads(Path(fp).read_text(encoding="utf-8"))
        except Exception:
            continue
        day = Path(fp).stem
        ms = d if isinstance(d, list) else (d.get("matches") or d.get("results") or [])
        for m in ms:
            if not isinstance(m, dict):
                continue
            code = m.get("matchNumStr") or m.get("code") or m.get("no")
            sc = m.get("result") or m.get("score") or m.get("fullScore")
            if not code or not sc:
                continue
            pp = _parse(sc)
            if pp:
                # 编号每周复用（"周一001"），必须带销售日键防串场
                res[f"{day}|{code}"] = pp
    return res


def collect_hist(min_items=20):
    """hist_odds 采集库（sporttery_hist_odds.py 产物）：自带赛果，无需外部赛果表。

    这是主样本源——score_odds 存档只有 29 天，hist_odds 可回溯年级别。
    """
    legs_by_day = defaultdict(list)
    for p in sorted(glob.glob(str(ROOT / "engine/cache/hist_odds/*.json"))):
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            continue
        for m in d.get("matches", []):
            act = _parse(m.get("score") or "")
            crs_raw = m.get("crs") or {}
            if not act:
                continue
            odds = {}
            for kk, v in crs_raw.items():
                if str(kk).startswith("other"):
                    continue
                try:
                    hh, aa = (int(x) for x in str(kk).split(":")[:2])
                    if v:
                        odds[(hh, aa)] = float(v)
                except (ValueError, TypeError):
                    continue
            if len(odds) < min_items:
                continue
            day = str(m.get("date") or "")[:10]
            legs_by_day[day].append(
                {"code": str(m.get("code") or ""), "odds": odds, "actual": act,
                 "conc": _concentration(odds), "league": m.get("league"),
                 "match": f'{m.get("home")} vs {m.get("away")}'})
    return legs_by_day


def collect_legs(results):
    """逐销售日收集可买腿：有真实 CRS 报价 + 有赛果。"""
    legs_by_day = defaultdict(list)
    for p in sorted(glob.glob(str(ROOT / "engine/cache/score_odds/*.json"))):
        day = Path(p).stem
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            continue
        mds = d.get("matchDays") or ([d] if d.get("matches") else [])
        for md in mds:
            for m in md.get("matches", []):
                code = str(m.get("matchNumStr") or "")
                crs = m.get("crs") or {}
                # 销售日 vs 归档日可能差一天（晚场挂次日凌晨），±1 天内找赛果
                act = None
                for dd in _around(day):
                    act = results.get(f"{dd}|{code}")
                    if act:
                        break
                if not code or not act or len(crs) < 20:
                    continue
                odds = {}
                for kk, v in crs.items():
                    try:
                        hh, aa = (int(x) for x in str(kk).split(":")[:2])
                        odds[(hh, aa)] = float(v)
                    except (ValueError, TypeError):
                        continue
                if len(odds) >= 20:
                    legs_by_day[day].append(
                        {"code": code, "odds": odds, "actual": act,
                         "conc": _concentration(odds),
                         "match": f'{m.get("home")} vs {m.get("away")}'})
    return legs_by_day


def _concentration(odds: dict, top: int = 3) -> float:
    """分布集中度 = 去水后市场隐含概率前 top 项之和。

    赔率倒数归一化（power 去水的简化：比例去水），衡量"这场比分好不好猜"。
    集中度高 = 少数比分吃掉大部分概率 = 可预测；低 = 分布平摊 = 不可预测。
    """
    inv = sorted((1.0 / o for o in odds.values() if o > 0), reverse=True)
    s = sum(inv)
    return sum(inv[:top]) / s if s else 0.0


def picks_for(L, rank, k, mode):
    """该场选哪 k 个比分。

    mode=market：按该场赔率升序（= 市场隐含概率最高的 k 个）——逐场自适应
    mode=template：按全库频率排序取前 k 个——所有场次同一组（基线对照）
    """
    if mode == "market":
        return [s for s, _ in sorted(L["odds"].items(), key=lambda kv: kv[1])[:k]]
    return [s for s in rank if s in L["odds"]][:k]


def sim(legs_by_day, rank, n, k, max_combos, min_conc=0.0, mode="market"):
    """一个 (n串,k选) 结构跑全历史：返回账目。min_conc=集中度闸门。"""
    bets = k ** n
    cost_per = bets * UNIT
    tot_cost = tot_pay = 0.0
    rounds = wins = 0
    best = 0.0
    leg_hit = leg_all = 0
    curve = []
    for day in sorted(legs_by_day):
        legs = legs_by_day[day]
        # 每条腿的选中比分 = 预测 top-k 中该场有报价的前 k 个
        buyable = []
        for L in legs:
            if L["conc"] < min_conc:
                continue   # 集中度闸门：分布太散的场次不进组合
            picks = picks_for(L, rank, k, mode)
            if len(picks) < k:
                continue
            buyable.append({**L, "picks": picks})
        if len(buyable) < n:
            continue
        for L in buyable:
            leg_all += 1
            leg_hit += int(L["actual"] in L["picks"])
        # 组合：按最小赔率降序取（同成本下博更大），限 max_combos 个
        buyable.sort(key=lambda L: -min(L["odds"][s] for s in L["picks"]))
        combos = list(combinations(buyable[:max(n, min(6, len(buyable)))], n))[:max_combos]
        for cb in combos:
            rounds += 1
            tot_cost += cost_per
            pay = 0.0
            if all(L["actual"] in L["picks"] for L in cb):
                o = 1.0
                for L in cb:
                    o *= L["odds"][L["actual"]]
                pay = o * UNIT
            tot_pay += pay
            if pay > 0:
                wins += 1
                best = max(best, pay)
            curve.append(pay - cost_per)
    return {"bets": bets, "cost_per": cost_per, "rounds": rounds,
            "cost": tot_cost, "pay": tot_pay, "wins": wins, "best": best,
            "leg_hit": leg_hit, "leg_all": leg_all, "curve": curve}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--legs", default="2,3,4")
    ap.add_argument("--ks", default="1,2,3")
    ap.add_argument("--budget", type=float, default=30.0)
    ap.add_argument("--max-combos", type=int, default=3)
    ap.add_argument("--source", choices=["hist", "archive"], default="hist",
                    help="hist=体彩历史赔率采集库（主）· archive=score_odds 存档")
    ap.add_argument("--mode", choices=["market", "template"], default="market",
                    help="market=逐场按赔率选热门比分 · template=全库频率固定组")
    a = ap.parse_args()

    print("=" * 80)
    print("CRS 投入产出模拟 · 真实赔率 × 时点滚动预测 × 真实赛果")
    print("=" * 80)
    rank = global_score_rank()
    if a.source == "hist":
        legs_by_day = collect_hist()
        src = "hist_odds 采集库（体彩历史赔率，自带赛果）"
    else:
        legs_by_day = collect_legs(load_results())
        src = "score_odds 存档 × 02-results 赛果"
    tot_legs = sum(len(v) for v in legs_by_day.values())
    print(f"  样本源：{src}")
    print(f"  预测基线（全库比分频率 top8）: "
          + ", ".join(f"{h}:{aa}" for h, aa in rank[:8]))
    print(f"  可模拟 {len(legs_by_day)} 个比赛日 / {tot_legs} 条腿"
          f"（真实赔率+赛果齐备）· 选腿口径={a.mode}\n")

    ns = [int(x) for x in a.legs.split(",")]
    ks = [int(x) for x in a.ks.split(",")]

    print("=" * 80)
    print(f"投入产出总账（预算 {a.budget:.0f} 元/注组 · 每日最多 {a.max_combos} 组合）")
    print("=" * 80)
    print(f"  {'结构':9} {'注数':>4} {'单组':>6} {'组数':>5} {'总投入':>8} {'总回款':>9} "
          f"{'回收率':>7} {'中奖组':>6} {'最大单组':>9}")
    rows = []
    for n in ns:
        for k in ks:
            if k ** n * UNIT > a.budget:
                continue
            r = sim(legs_by_day, rank, n, k, a.max_combos, mode=a.mode)
            if not r["rounds"]:
                continue
            rr = r["pay"] / r["cost"] * 100 if r["cost"] else 0
            rows.append((n, k, r, rr))
            print(f"  {n}串{k}选{'':3} {r['bets']:>4} {r['cost_per']:>5.0f}元 "
                  f"{r['rounds']:>5} {r['cost']:>7.0f}元 {r['pay']:>8.0f}元 "
                  f"{rr:>6.1f}% {r['wins']:>6} {r['best']:>8.0f}元")

    if rows:
        lh = rows[0][2]
        print(f"\n  单场命中率实测：", end="")
        for n, k, r, rr in rows:
            if n == ns[0]:
                print(f" k={k}→{r['leg_hit']/r['leg_all']*100:.1f}%", end="")
        print(f"   (样本 {rows[0][2]['leg_all']} 腿)")

    print("\n" + "=" * 80)
    print("资金曲线：如果每轮都买，钱包怎么走")
    print("=" * 80)
    for n, k, r, rr in rows:
        c = r["curve"]
        bal, peak, trough = 0.0, 0.0, 0.0
        for x in c:
            bal += x
            peak = max(peak, bal)
            trough = min(trough, bal)
        print(f"  {n}串{k}选：净{bal:>+8.0f}元 · 峰值{peak:>+7.0f}元 · "
              f"谷底{trough:>+8.0f}元 · 连灭最长 "
              f"{max((len(list(g)) for kk, g in __import__('itertools').groupby(c, key=lambda x: x < 0) if kk), default=0)} 组")

    print("\n" + "=" * 80)
    print("反推：准确率提到多少，这些结构才不亏")
    print("=" * 80)
    for n, k, r, rr in rows:
        if not r["wins"]:
            print(f"  {n}串{k}选：本样本 0 中奖 → 无法从实测反推，需更长样本")
            continue
        # 实测命中时的平均回款
        avg_pay = r["pay"] / r["wins"]
        c_now = r["leg_hit"] / r["leg_all"]
        c_req = (r["cost_per"] / avg_pay) ** (1.0 / n)
        print(f"  {n}串{k}选：当前单场 {c_now*100:.1f}% → 需 {c_req*100:.1f}% "
              f"(缺 {(c_req-c_now)*100:+.1f}pp) · 命中均回款 {avg_pay:.0f}元")

    # ---- 集中度分档：先看单场命中率是否真随集中度上升 ----
    print("\n" + "=" * 80)
    print("集中度分档 · 单场命中率（闸门可行性的前置检验）")
    print("=" * 80)
    allegs = [L for v in legs_by_day.values() for L in v]
    bands = [(0.0, .26, "<26% 散"), (.26, .30, "26-30%"),
             (.30, .34, "30-34%"), (.34, 1.0, "≥34% 集中")]
    print(f"  {'档':12} {'场次':>5} {'1选':>7} {'2选':>7} {'3选':>7} {'4选':>7}")
    for lo, hi, name in bands:
        sub = [L for L in allegs if lo <= L["conc"] < hi]
        if not sub:
            continue
        cells = []
        for k in (1, 2, 3, 4):
            h = sum(1 for L in sub if L["actual"] in picks_for(L, rank, k, a.mode))
            cells.append(f"{h/len(sub)*100:6.1f}%")
        print(f"  {name:12} {len(sub):>5} " + " ".join(cells))
    print(f"  {'全样本':12} {len(allegs):>5} " + " ".join(
        f"{sum(1 for L in allegs if L['actual'] in picks_for(L, rank, k, a.mode))/len(allegs)*100:6.1f}%"
        for k in (1, 2, 3, 4)))

    # ---- 带闸门的结构对照 ----
    print("\n" + "=" * 80)
    print("集中度闸门 × 结构 · 投入产出对照（同批数据配对）")
    print("=" * 80)
    print(f"  {'结构':9} {'闸门':>8} {'组数':>5} {'总投入':>8} {'总回款':>9} "
          f"{'回收率':>7} {'中奖':>5} {'单场命中':>8} {'净额':>9}")
    for n in ns:
        for k in ks:
            if k ** n * UNIT > a.budget:
                continue
            for mc in (0.0, 0.26, 0.30, 0.34):
                r = sim(legs_by_day, rank, n, k, a.max_combos, min_conc=mc, mode=a.mode)
                if not r["rounds"] or not r["leg_all"]:
                    continue
                rr = r["pay"] / r["cost"] * 100 if r["cost"] else 0
                lab = "无" if mc == 0 else f"≥{mc*100:.0f}%"
                print(f"  {n}串{k}选{'':3} {lab:>8} {r['rounds']:>5} "
                      f"{r['cost']:>7.0f}元 {r['pay']:>8.0f}元 {rr:>6.1f}% "
                      f"{r['wins']:>5} {r['leg_hit']/r['leg_all']*100:>7.1f}% "
                      f"{r['pay']-r['cost']:>+8.0f}元")
            print()


if __name__ == "__main__":
    main()
