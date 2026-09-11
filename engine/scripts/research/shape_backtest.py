"""形状回测：孪生票配对设计（4串1 vs 4串11 vs 全2关-4）★ 2026-09-11 大哥拍板

设计（docs 对话 2026-09-11，拍板=双口径选腿 + 三完整季+2627）：
- 数据源：fd CSV 16 联赛 × 4 季（2324/2425/2526/2627），本地缓存 engine/cache/shape_backtest/
- 赔率主口径：B365 收盘（B365CH/CD/CA）；Pinnacle 收盘（PPCH/PSCH 双列名 fallback）做敏感性子样本
- 选腿：市场去水主概率降序（=影子层 strong 池同口径）；口径A=无门槛 top4 / 口径B=p≥0.55 过滤后 top4
- 成轮：自然比赛日跨联赛混轮（对齐体彩销售日口径），合格腿 ≥4 才成轮，每轮孪生三票
- 结算：复用 engine/shadow/shapes.py 的 _skeleton/settle（BUDGET=30 等成本：4串1×15倍30元 / 4串11×1倍22元 / 全2关-4×2倍24元）
- 统计：轮级配对差 bootstrap CI + 随机选腿基线（同轮重抽，分离选腿α与形状β）

偏差声明（报告必带）：B365 抽水 ~5% < 体彩 12.9% → 绝对回收率偏乐观（每关约 +8pp 量级），
三形状同源对比相对排序不受影响；收盘价成交无滑点；fd 不含日职/沙特/北欧（影子实盘有）。

用法：python engine/scripts/research/shape_backtest.py [--refresh] [--boots 300]
输出：data/04-summaries/shape-backtest-report.json + 控制台摘要
"""
from __future__ import annotations

import csv
import io
import json
import random
import sys
from collections import defaultdict
from datetime import datetime
from itertools import combinations
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "shadow"))
from shapes import BUDGET, BET_UNIT, _skeleton, settle  # noqa: E402

BASE = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
UA = {"User-Agent": "Mozilla/5.0 (football-kb personal project)"}
CACHE = ROOT / "engine" / "cache" / "shape_backtest"
OUT = ROOT / "data" / "04-summaries" / "shape-backtest-report.json"

LEAGUES = {
    "E0": "英超", "E1": "英冠", "SP1": "西甲", "SP2": "西乙",
    "D1": "德甲", "D2": "德乙", "I1": "意甲", "I2": "意乙",
    "F1": "法甲", "F2": "法乙", "N1": "荷甲", "B1": "比甲",
    "P1": "葡超", "T1": "土超", "G1": "希超", "R1": "俄超",
}
SEASONS = ["2324", "2425", "2526", "2627"]

# 三形状 spec（对齐 shapes.PLANS 的 strong 池 single 选项）
SHAPES = [("4串1", "4串1"), ("4串11", "4串11"), ("全2关-4", "全2关")]
# 结算口径对齐 paper.py:351（outcome=int 0/1/2，与 bets.pick 同型方可 _pick_price 比较）
def outcome_int(hg: int, ag: int) -> int:
    return 0 if hg > ag else (1 if hg == ag else 2)


def fetch_csv(code: str, season: str, refresh: bool) -> str | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{code}_{season}.csv"
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8-sig")
    try:
        resp = requests.get(BASE.format(season=season, code=code), headers=UA, timeout=30)
    except requests.RequestException as e:
        print(f"  [fetch] {code} {season}: {e}")
        return None
    if resp.status_code != 200 or "<html" in resp.text[:200].lower():
        return None
    path.write_text(resp.text, encoding="utf-8")
    return resp.text


def g(row: dict, *keys):
    for k in keys:
        for rk, rv in row.items():
            if rk and rk.lower() == k.lower():
                return rv
    return None


def parse_date(raw: str):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def odds3(row, prefix: str):
    """收盘三向（prefix='B365C' 或 Pinnacle fallback 列名组）；缺任一则 None。"""
    if prefix == "B365C":
        cols = ("B365CH", "B365CD", "B365CA")
    else:
        cols = (("PPCH", "PSCH", "Psh"), ("PPCD", "PSCD", "Psd"), ("PPCA", "PSCA", "Psa"))
    vals = []
    for i in range(3):
        keys = cols[i] if isinstance(cols[0], tuple) else (cols[i],)
        raw = g(row, *keys)
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return None
        if v <= 1.01:
            return None
        vals.append(v)
    return vals


def load_matches(refresh: bool) -> list[dict]:
    """fd 四季 → 场次清单（主口径 B365 + Pinnacle 敏感性标记）。"""
    matches = []
    for season in SEASONS:
        for code in LEAGUES:
            text = fetch_csv(code, season, refresh)
            if not text:
                continue
            n0 = len(matches)
            for row in csv.DictReader(io.StringIO(text)):
                date = parse_date(g(row, "Date") or "")
                try:
                    hg, ag = int(g(row, "FTHG")), int(g(row, "FTAG"))
                except (TypeError, ValueError):
                    continue
                b365 = odds3(row, "B365C")
                if not date or b365 is None:
                    continue
                pin = odds3(row, "PIN")
                inv = [1 / o for o in b365]
                s = sum(inv)
                k = inv.index(max(inv))
                outcome = outcome_int(hg, ag)
                matches.append({
                    "season": season, "league": code, "date": date,
                    "home": g(row, "HomeTeam"), "away": g(row, "AwayTeam"),
                    "score": (hg, ag), "outcome": outcome,
                    "b365": b365, "pin": pin,
                    "pick": k, "p_main": inv[k] / s,
                    "margin": s - 1.0,
                })
            if len(matches) > n0:
                print(f"  [load] {season} {code} {LEAGUES[code]}: {len(matches)-n0} 场")
    return matches


def make_legs(chosen: list[dict]) -> list[dict]:
    """选中的 4 腿 → shapes.settle 的 legs 口径（outcome + B365 冻结价）。"""
    return [{"code": m["home"], "outcome": m["outcome"], "score": m["score"]} for m in chosen]


def build_ticket(chosen: list[dict], shape: str) -> dict:
    """三形状票构造（had single 选项，对齐 shapes.build_ticket 的 strong 池分支）。"""
    tlegs = [{"src": i, "kind": "had", "picks": [m["pick"]],
              "odds": [m["b365"][j] if j == m["pick"] else None for j in range(3)]}
             for i, m in enumerate(chosen)]
    sk = _skeleton(shape, len(chosen))
    # 显式循环构造（勿用 [{...} for ...]——内层花括号会被解析为 dict comprehension，
    # "legs" 键反复覆盖致每注只剩一腿，2026-09-11 实测踩坑）
    bets = [{"legs": [(ti, tlegs[ti]["picks"][0]) for ti in combo]} for combo in sk]
    n_bets = len(bets)
    mult = max(1, BUDGET // (n_bets * BET_UNIT))
    return {"tlegs": tlegs, "bets": bets, "n_bets": n_bets,
            "mult": mult, "cost": n_bets * BET_UNIT * mult}


def run_shape(chosen: list[dict], shape_name: str, shape: str):
    ticket = build_ticket(chosen, shape)
    payout = settle(make_legs(chosen), ticket)
    hits = sum(1 for m in chosen if m["pick"] == m["outcome"])
    return {"shape": shape_name, "cost": ticket["cost"], "payout": payout,
            "ret": payout / ticket["cost"], "hits": hits, "n": len(chosen)}


def summarize(runs: list[dict]) -> dict:
    cost = sum(r["cost"] for r in runs)
    pay = sum(r["payout"] for r in runs)
    rets = sorted(r["ret"] for r in runs)
    n = len(runs)
    eq = (lambda i: rets[min(i, n - 1)])

    def streak(rseq):
        mx = cur = 0
        for r in rseq:
            cur = cur + 1 if r["payout"] == 0 else 0
            mx = max(mx, cur)
        return mx

    nav = peak = dd = 0.0
    for r in runs:
        nav += r["payout"] - r["cost"]
        peak = max(peak, nav)
        dd = min(dd, nav - peak)
    return {
        "rounds": n, "cost": cost, "payout": round(pay, 2),
        "ret": round(pay / cost, 4) if cost else 0,
        "ret_med": round(eq(n // 2), 4), "ret_p25": round(eq(n // 4), 4),
        "ret_p75": round(eq(3 * n // 4), 4),
        "hit_all": sum(1 for r in runs if r["hits"] == r["n"]),
        "hit_3": sum(1 for r in runs if r["hits"] == 3),
        "hit_le2": sum(1 for r in runs if r["hits"] <= 2),
        "pay_rounds": sum(1 for r in runs if r["payout"] > 0),
        "max_streak0": streak(runs), "max_dd": round(dd, 2),
        "net": round(pay - cost, 2),
    }


def boot_ci(vals: list[float], boots: int, seed=42):
    rng = random.Random(seed)
    n = len(vals)
    means = []
    for _ in range(boots):
        means.append(sum(vals[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return round(means[int(0.025 * boots)], 4), round(means[int(0.975 * boots)], 4)


def main() -> None:
    refresh = "--refresh" in sys.argv
    boots = 300
    if "--boots" in sys.argv:
        boots = int(sys.argv[sys.argv.index("--boots") + 1])

    print("[shape-backtest] 拉取 fd 四季 CSV（缓存 engine/cache/shape_backtest/）")
    matches = load_matches(refresh)
    print(f"[shape-backtest] 有效场次 {len(matches)}（B365 收盘齐备口径）")

    by_day: dict[tuple, list[dict]] = defaultdict(list)
    for m in matches:
        by_day[(m["season"], m["date"])].append(m)

    report = {"generatedAt": datetime.now().isoformat(timespec="minutes"),
              "design": "孪生票配对：同日同腿三形状；BUDGET=30 等成本；shapes.py 结算复用",
              "bias": "B365 抽水 ~5% < 体彩 12.9%，绝对回收率偏乐观；相对排序同源有效",
              "avgMarginB365": round(sum(m["margin"] for m in matches) / len(matches), 4),
              "variants": {}}

    for tag, min_p in (("A_无门槛top4", 0.0), ("B_p55门槛", 0.55)):
        rounds = []
        for (season, date), pool in sorted(by_day.items()):
            pool = [m for m in pool if m["p_main"] >= min_p]
            if len(pool) < 4:
                continue
            chosen = sorted(pool, key=lambda m: -m["p_main"])[:4]
            runs = [run_shape(chosen, name, shape) for name, shape in SHAPES]
            rounds.append({"season": season, "date": date, "legs": chosen, "runs": runs})
        # 汇总
        var = {"rounds": len(rounds), "byShape": {}}
        for i, (name, _) in enumerate(SHAPES):
            runs = [r["runs"][i] for r in rounds]
            var["byShape"][name] = summarize(runs)
        # 配对差 bootstrap（4串11−4串1 / 全2关-4−4串1，轮级回收率差）
        for j, other in (("4串11", 1), ("全2关-4", 2)):
            diffs = [r["runs"][1 if j == "4串11" else 2]["ret"] - r["runs"][0]["ret"]
                     for r in rounds]
            lo, hi = boot_ci(diffs, boots)
            var[f"pair_{other}_minus_4串1_retDiff"] = {
                "mean": round(sum(diffs) / len(diffs), 4), "ci95": [lo, hi]}
        # 随机选腿基线（同轮合格池重抽，分离选腿 α）
        rng = random.Random(7)
        base = {name: [] for name, _ in SHAPES}
        for (season, date), pool in sorted(by_day.items()):
            pool = [m for m in pool if m["p_main"] >= min_p]
            if len(pool) < 4:
                continue
            for _ in range(10):            # 每轮 10 次重抽 × 轮数 ≈ 300+ 样本/形状
                chosen = rng.sample(pool, 4)
                for i, (name, shape) in enumerate(SHAPES):
                    base[name].append(run_shape(chosen, name, shape))
        var["randomBaseline"] = {name: summarize(rs) for name, rs in base.items()}
        report["variants"][tag] = var
        print(f"[shape-backtest] 口径{tag}: {len(rounds)} 轮")
        for name, s in var["byShape"].items():
            print(f"  {name:<8} 回收率 {s['ret']*100:>6.1f}% 净 {s['net']:>+9.2f} "
                  f"全中 {s['hit_all']}/{s['rounds']} 断≤2 {s['hit_le2']} "
                  f"连灭 {s['max_streak0']} 回撤 {s['max_dd']}")

    # 敏感性：Pinnacle 子样本（口径B）
    pin_rounds = []
    for (season, date), pool in sorted(by_day.items()):
        pool = [m for m in pool if m["p_main"] >= 0.55 and m["pin"]]
        if len(pool) < 4:
            continue
        chosen = sorted(pool, key=lambda m: -m["p_main"])[:4]
        saved = [m["b365"] for m in chosen]
        for m in chosen:
            m["b365"] = m["pin"]      # 临时换 Pinnacle 收盘
        pin_rounds.append([run_shape(chosen, n, s) for n, s in SHAPES])
        for m, o in zip(chosen, saved):
            m["b365"] = o
    report["pinSensitivity"] = {
        "rounds": len(pin_rounds),
        "byShape": {name: summarize([rr[i] for rr in pin_rounds])
                    for i, (name, _) in enumerate(SHAPES)},
    }
    print(f"[shape-backtest] Pinnacle 敏感性子样本: {len(pin_rounds)} 轮")

    # 分季分段（口径B）
    report["bySeason"] = {}
    for season in SEASONS:
        var = report["variants"]["B_p55门槛"]
        # 重新按季过滤（rounds 不入 report，重算轻量版：只按日期前缀）
        sub = defaultdict(list)
        for (s, d), pool in sorted(by_day.items()):
            if s != season:
                continue
            pool = [m for m in pool if m["p_main"] >= 0.55]
            if len(pool) < 4:
                continue
            chosen = sorted(pool, key=lambda m: -m["p_main"])[:4]
            for i, (name, shape) in enumerate(SHAPES):
                sub[name].append(run_shape(chosen, name, shape))
        report["bySeason"][season] = {name: summarize(v) for name, v in sub.items()}

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[shape-backtest] → {OUT}")


if __name__ == "__main__":
    main()
