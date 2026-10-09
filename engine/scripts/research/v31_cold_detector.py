# -*- coding: utf-8 -*-
"""v31（冷门多轨·轨1）：统计先验终审（判据预注册 fade-strategy-prereg v31·跑前写死）。

大哥立题「预测冷门不是瞎猜，总是有依据的」→ 设计 docs/2026-10-09-upset-multitrack-design.html。
本轨受审：射正转化回归(F1)/主客特化(F2)/防线恶化(F3)/波动率(F4)/联赛混乱度(F5)/
国际窗口(F6) 合分，能否识别「冷门命中率显著高于市场隐含」的场次（信息层侦测）。

判据（v31 预注册）：
  ① 主：val 段合分 top10% 冷门腿 命中−隐含 ≥ +2.5pp 且 bootstrap CI 下限 > +1pp → 成立
  ② 副：五分位单调（描述）
  ③ 利润层：top10% 回收率同报对照全池基线·不作立废；唯 >1.0 且 CI 下界>1.0 → 升级
  ④ 单因子表全同报·探索性不作立废
  ⑤ 不过 → 轨1 结案
纪律：walk-forward 只用该场之前比赛；z 参数只用 fit 段；期望不作承诺。
开发者 sszhang
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from band_calibration import DIVS, SEASONS, fetch_rows

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data" / "04-summaries" / "v31-cold-detector.json"
LONGSHOT_MAX = 0.20      # 冷门腿 = 市场去水隐含 < 20%
FORM_WINDOW = 6          # 因子回看窗口（场）
MIN_HISTORY = 4          # 建因子最少历史场数
FIT_SEASONS = {"2223", "2324"}
VAL_SEASONS = {"2425", "2526"}
LUCK_RATE = 0.30         # 射正→进球联赛经验转化率
FIFA_MONTHS = {9, 10, 11, 3}   # 国际窗口代理：联赛停摆所在月
GAP_DAYS = 7             # 联赛比赛日间隔 ≥7 日视为窗口停摆
BOOT_N = 2000
SEED = 20261009
FACTORS = ["F1_luck", "F2_venue", "F3_def", "F4_vol", "F5_chaos", "F6_fifa"]


def parse_date(s):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def load_matches():
    """fd 全量 → 时间序比赛列表（收盘价/射正/日期/赛季）。"""
    out = []
    for season in SEASONS:
        for div, league in DIVS.items():
            for r in fetch_rows(season, div):
                d = parse_date(r.get("Date"))
                if not d:
                    continue
                try:
                    m = {"date": d, "season": season, "league": league,
                         "home": r["HomeTeam"], "away": r["AwayTeam"],
                         "hg": int(r["FTHG"]), "ag": int(r["FTAG"]), "ftr": r["FTR"],
                         "pch": float(r["PSCH"]), "pcd": float(r["PSCD"]), "pca": float(r["PSCA"]),
                         "hst": float(r["HST"]), "ast": float(r["AST"])}
                except (KeyError, ValueError, TypeError):
                    continue
                if m["ftr"] not in ("H", "D", "A"):
                    continue
                out.append(m)
    out.sort(key=lambda m: m["date"])
    return out


def devid(oh, od_, oa):
    s = 1 / oh + 1 / od_ + 1 / oa
    return (1 / oh) / s, (1 / od_) / s, (1 / oa) / s


def _agg(prev):
    rows = prev[-FORM_WINDOW:]
    n = len(rows)
    return {"gf": sum(r["gf"] for r in rows) / n,
            "ga": sum(r["ga"] for r in rows) / n,
            "stf": sum(r["stf"] for r in rows) / n}


def build_rows(matches):
    """walk-forward 逐场：队史/联赛状态先查后写（防泄漏），产冷门腿行。"""
    hist = defaultdict(list)              # team -> [{date,gf,ga,stf,sta,venue}]
    lg_state = {}                         # (league,season) -> {last_day, n, fav_fail}
    rows = []
    for m in matches:
        lgk = (m["league"], m["season"])
        st = lg_state.setdefault(lgk, {"last_day": None, "n": 0, "fav_fail": 0})
        # F6 国际窗口：本月 ∈ FIFA 月 且 距该联赛上一比赛日 ≥7 日（当季内）
        f6 = 0.0
        if st["last_day"] is not None and m["date"].month in FIFA_MONTHS:
            gap = (m["date"] - st["last_day"]).days
            if GAP_DAYS <= gap < 30:
                f6 = 1.0
        # F5 联赛混乱度：当季 as-of 热门失手率（burn-in 100）
        f5 = (st["fav_fail"] / st["n"]) if st["n"] >= 100 else None
        # 三向隐含与热门判定
        ph, pd_, pa = devid(m["pch"], m["pcd"], m["pca"])
        fav_is_home = ph >= pa
        # 更新联赛状态（本场之后才计入——先读后写）
        fav_dir = "H" if ph >= max(pd_, pa) else ("A" if pa >= max(pd_, ph) else "D")
        st["n"] += 1
        if m["ftr"] != fav_dir:
            st["fav_fail"] += 1
        st["last_day"] = m["date"]

        h_prev, a_prev = hist[m["home"]], hist[m["away"]]
        if len(h_prev) >= MIN_HISTORY and len(a_prev) >= MIN_HISTORY:
            hf, af = _agg(h_prev), _agg(a_prev)
            if fav_is_home:
                fav, und, und_venue = hf, af, "A"
            else:
                fav, und, und_venue = af, hf, "H"
            # F1 射正透支（热门方近6场 进球−射正×0.30）
            f1 = fav["gf"] - fav["stf"] * LUCK_RATE
            # F2 主客特化（冷门方在此场场地的净胜 − 全场合净胜）
            und_here = [r for r in (a_prev if fav_is_home else h_prev)
                        if r["venue"] == und_venue]
            if und_here:
                uv = sum(r["gf"] - r["ga"] for r in und_here[-FORM_WINDOW:]) / len(und_here[-FORM_WINDOW:])
                f2 = uv - (und["gf"] - und["ga"])
            else:
                f2 = None
            # F3 防线恶化（热门方近3场均失 − 前3场均失·需≥6场）
            fav_hist = h_prev if fav_is_home else a_prev
            if len(fav_hist) >= FORM_WINDOW:
                last3 = fav_hist[-3:]
                prev3 = fav_hist[-6:-3]
                f3 = (sum(r["ga"] for r in last3) / 3) - (sum(r["ga"] for r in prev3) / 3)
            else:
                f3 = None
            # F4 波动率（热门方近6场净胜方差）
            gds = [r["gf"] - r["ga"] for r in fav_hist[-FORM_WINDOW:]]
            mu = sum(gds) / len(gds)
            f4 = sum((x - mu) ** 2 for x in gds) / len(gds)

            for side, p, odds in (("H", ph, m["pch"]), ("A", pa, m["pca"])):
                if p >= LONGSHOT_MAX:
                    continue
                rows.append({
                    "date": m["date"].date().isoformat(), "season": m["season"],
                    "league": m["league"], "side": side, "p": p, "odds": odds,
                    "hit": 1.0 if m["ftr"] == side else 0.0,
                    "F1_luck": f1, "F2_venue": f2, "F3_def": f3, "F4_vol": f4,
                    "F5_chaos": f5, "F6_fifa": f6, "fav_impl": max(ph, pa, pd_),
                })
        # 队史后写
        for team, gf, ga, stf, venue in ((m["home"], m["hg"], m["ag"], m["hst"], "H"),
                                         (m["away"], m["ag"], m["hg"], m["ast"], "A")):
            hist[team].append({"gf": gf, "ga": ga, "stf": stf, "venue": venue})
    # 缺失因子行剔除（F2 场地样本不足 / F5 burn-in 未满·当季前期）
    return [r for r in rows if all(r[f] is not None for f in FACTORS)]


def fit_z(rows_fit):
    """fit 段各因子 z 参数。"""
    zp = {}
    for f in FACTORS:
        vals = [r[f] for r in rows_fit]
        mu = sum(vals) / len(vals)
        sd = (sum((v - mu) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
        zp[f] = (mu, sd)
    return zp


def score_rows(rows, zp):
    for r in rows:
        r["score"] = sum((r[f] - zp[f][0]) / zp[f][1] for f in FACTORS)


def cal_gap(sub):
    """命中率 − 隐含（校准差·正=实际比市场隐含更常冷门）。"""
    return sum(r["hit"] - r["p"] for r in sub) / len(sub)


def boot_ci(vals, stat=lambda s: sum(s) / len(s)):
    rng = random.Random(SEED)
    n = len(vals)
    outs = sorted(stat([vals[rng.randrange(n)] for _ in range(n)]) for _ in range(BOOT_N))
    return outs[int(BOOT_N * 0.025)], outs[min(int(BOOT_N * 0.975), BOOT_N - 1)]


def seg_report(name, rows):
    n = len(rows)
    rows_s = sorted(rows, key=lambda r: -r["score"])
    top = rows_s[: n // 10]
    base_gap = cal_gap(rows)
    top_gap = cal_gap(top)
    g_lo, g_hi = boot_ci([r["hit"] - r["p"] for r in top])
    rets = [r["odds"] * r["hit"] for r in top]
    r_lo, r_hi = boot_ci(rets)
    pool_ret = sum(r["odds"] * r["hit"] for r in rows) / n
    # 五分位
    qint = max(n // 5, 1)
    quints = []
    for q in range(5):
        sub = rows_s[q * qint: (q + 1) * qint] if q < 4 else rows_s[4 * qint:]
        quints.append({"q": q + 1, "n": len(sub), "hit": round(sum(r["hit"] for r in sub) / len(sub), 4),
                       "impl": round(sum(r["p"] for r in sub) / len(sub), 4)})
    print(f"\n════ {name} 段（腿 {n}）════")
    print(f"  全池：命中 {sum(r['hit'] for r in rows) / n * 100:.2f}% · 隐含 {sum(r['p'] for r in rows) / n * 100:.2f}%"
          f" · 校准差 {base_gap * 100:+.2f}pp · 回收率 {pool_ret:.4f}")
    print(f"  top10%（{len(top)} 腿）：校准差 {top_gap * 100:+.2f}pp · CI[{g_lo * 100:+.2f},{g_hi * 100:+.2f}]pp"
          f" · 回收率 {sum(rets) / len(rets):.4f} CI[{r_lo:.4f},{r_hi:.4f}]")
    mono = all(quints[i]["hit"] <= quints[i + 1]["hit"] + 1e-9 for i in range(4))
    print(f"  五分位命中率：{' → '.join(format(q['hit'] * 100, '.1f') for q in quints)}（单调={'是' if mono else '否'}）")
    return {"nLegs": n, "nTop": len(top),
            "poolGap": round(base_gap, 4), "poolRet": round(pool_ret, 4),
            "topGap": round(top_gap, 4), "topGapCi": [round(g_lo, 4), round(g_hi, 4)],
            "topRet": round(sum(rets) / len(rets), 4), "topRetCi": [round(r_lo, 4), round(r_hi, 4)],
            "quintiles": quints, "monotone": mono}


def main():
    print("══ v31（轨1）统计先验终审 ══\n", flush=True)
    print("预注册: fade-strategy-prereg v31（跑前写死）·纪律: 信息层判侦测·利润层如实报", flush=True)
    matches = load_matches()
    print(f"fd 比赛 {len(matches)} 场", flush=True)
    rows = build_rows(matches)
    print(f"冷门腿（隐含<{LONGSHOT_MAX:.0%}·因子齐备）{len(rows)} 条", flush=True)
    fit = [r for r in rows if r["season"] in FIT_SEASONS]
    val = [r for r in rows if r["season"] in VAL_SEASONS]
    zp = fit_z(fit)
    score_rows(fit, zp)
    score_rows(val, zp)

    res_fit = seg_report("fit（参照）", fit)
    res_val = seg_report("val（判据）", val)

    # I1 强热门叠加（探索性·预注册作敏感性）
    i1 = [r for r in val if r["fav_impl"] >= 0.65 and
          any((r[f] - zp[f][0]) / zp[f][1] >= 1.0 for f in ("F1_luck", "F3_def", "F4_vol"))]
    i1_gap = cal_gap(i1) if i1 else None
    print(f"\n  I1 强热门×高危叠加（val·{len(i1)} 腿）：校准差 "
          f"{i1_gap * 100:+.2f}pp" if i1 else "  I1 无样本", flush=True)

    # 单因子边际（探索性·不作立废）
    print("\n  单因子边际（val·各自 top20% 校准差·探索性）：", flush=True)
    single = {}
    for f in FACTORS:
        sub = sorted(val, key=lambda r: -r[f])[: len(val) // 5]
        g = cal_gap(sub)
        lo, hi = boot_ci([r["hit"] - r["p"] for r in sub])
        single[f] = {"n": len(sub), "gap": round(g, 4), "ci": [round(lo, 4), round(hi, 4)]}
        print(f"    {f:<10} gap {g * 100:+.2f}pp CI[{lo * 100:+.2f},{hi * 100:+.2f}]", flush=True)

    v = res_val
    passed = (v["topGap"] >= 0.025 and v["topGapCi"][0] > 0.01)
    upgrade = (v["topRet"] > 1.0 and v["topRetCi"][0] > 1.0)
    verdict = ("判据①过——冷门侦测器成立（信息层）" if passed else
               "判据⑤——不过·轨1 结案：统计先验合分无市场外信息")
    if upgrade:
        verdict += "⚠且利润层>1 CI>1——升级重大发现另立前向"
    print(f"\n══ 判定: {verdict} ══", flush=True)

    OUT.write_text(json.dumps({
        "ranAt": "2026-10-09", "track": "轨1 统计先验终审", "prereg": "v31",
        "fit": res_fit, "val": res_val, "I1_strongFav": {"n": len(i1), "gap": round(i1_gap, 4) if i1 is not None else None},
        "singleFactorExploratory": single,
        "verdict": verdict,
        "discipline": "信息层判侦测·利润层如实报不作立废·z参数仅fit段·walk-forward",
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"归档 {OUT.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
