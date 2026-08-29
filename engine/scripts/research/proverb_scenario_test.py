"""联赛谚语 + 剧本规则联合验证（2026-08-29 · ①剧本合取 + 民间谚语）。开发者 sszhang

两组假设一起测，同款 walk-forward 纪律、同款事先判据：
A) 民间谚语：平局看西甲 / 主队看德甲 / 翻车看意甲 / 客队牛逼看英超 / 虚情假意是法甲
   —— 每条报两个数：实际频率（谚语描述对不对） + 押注回报率（能不能用）。频率高≠能赚钱。
B) 文献常识剧本 6 条（双慢热闷局/强强保守/对攻开放/一边倒屠杀/疲劳低分/主客场极化）
   —— 押 O/U 2.5（有历史收盘价），并报触发时的实际比分分布（B+C 要的东西）。

判据：回报率 >1.0 且 CI 下界 > 基线 → 立项；<0.9 → 证伪；之间 → 噪声。
多重比较：并列报 Bonferroni 校正提示（6 条规则并行，26% 概率至少一条假阳性）。
"""
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from band_calibration import DIVS, SEASONS, bootstrap_ci, devid, fetch_rows

FORM_WINDOW = 6
MIN_HISTORY = 4
TOP_LEAGUES = ["england-premier", "spain-laliga", "germany-bundesliga",
               "italy-serie-a", "france-ligue1", "netherlands-eredivisie",
               "portugal-primeira"]


def parse_date(s: str):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def load_matches() -> list:
    out = []
    for season in SEASONS:
        for div, league in DIVS.items():
            for r in fetch_rows(season, div):
                d = parse_date(r.get("Date"))
                if not d:
                    continue
                try:
                    m = {"date": d, "league": league,
                         "home": r["HomeTeam"], "away": r["AwayTeam"],
                         "hg": int(r["FTHG"]), "ag": int(r["FTAG"]), "ftr": r["FTR"],
                         "pch": float(r["PSCH"]), "pcd": float(r["PSCD"]),
                         "pca": float(r["PSCA"]),
                         "hst": float(r["HST"]), "ast": float(r["AST"]),
                         "hs": float(r["HS"]), "a_s": float(r["AS"])}
                    m["over"] = float(r["PC>2.5"]) if r.get("PC>2.5") else None
                    m["under"] = float(r["PC<2.5"]) if r.get("PC<2.5") else None
                except (KeyError, ValueError, TypeError):
                    continue
                if m["ftr"] not in ("H", "D", "A"):
                    continue
                out.append(m)
    out.sort(key=lambda m: m["date"])
    return out


# ─────────────────────────── A. 民间谚语 ───────────────────────────

def proverbs(matches: list) -> None:
    stat = defaultdict(lambda: {"n": 0, "H": 0, "D": 0, "A": 0,
                                "retH": [], "retD": [], "retA": [],
                                "fav_n": 0, "fav_lose": 0,
                                "calib": []})
    for m in matches:
        s = stat[m["league"]]
        s["n"] += 1
        s[m["ftr"]] += 1
        ph, pd_, pa = devid(m["pch"], m["pcd"], m["pca"])
        s["retH"].append(m["pch"] if m["ftr"] == "H" else 0.0)
        s["retD"].append(m["pcd"] if m["ftr"] == "D" else 0.0)
        s["retA"].append(m["pca"] if m["ftr"] == "A" else 0.0)
        # 热门爆冷：隐含 >60% 的一方是否失手（未赢）
        for p, side in ((ph, "H"), (pa, "A")):
            if p > 0.60:
                s["fav_n"] += 1
                if m["ftr"] != side:
                    s["fav_lose"] += 1
        # 校准误差：三向 |市场概率 − 实际结果指示|（Brier 分量）
        for p, side in ((ph, "H"), (pd_, "D"), (pa, "A")):
            s["calib"].append((p - (1.0 if m["ftr"] == side else 0.0)) ** 2)

    print("\n" + "=" * 96)
    print("A. 民间谚语验证（频率 = 描述对不对 · 回报率 = 能不能用）")
    print("=" * 96)
    hdr = (f"{'联赛':<24}{'n':>6}{'主胜%':>8}{'平%':>7}{'客胜%':>8}"
           f"{'押主':>8}{'押平':>8}{'押客':>8}{'热门失手%':>10}{'Brier':>8}")
    print(hdr)
    print("-" * 96)
    rows = []
    for lg in TOP_LEAGUES:
        s = stat.get(lg)
        if not s or s["n"] < 200:
            continue
        n = s["n"]
        rows.append({
            "lg": lg, "n": n,
            "h": s["H"] / n, "d": s["D"] / n, "a": s["A"] / n,
            "rh": sum(s["retH"]) / n, "rd": sum(s["retD"]) / n, "ra": sum(s["retA"]) / n,
            "favlose": s["fav_lose"] / s["fav_n"] if s["fav_n"] else 0.0,
            "brier": sum(s["calib"]) / len(s["calib"]),
            "_retD": s["retD"],
        })
    for r in rows:
        print(f"{r['lg']:<24}{r['n']:>6}{r['h']*100:>7.1f}%{r['d']*100:>6.1f}%"
              f"{r['a']*100:>7.1f}%{r['rh']:>8.3f}{r['rd']:>8.3f}{r['ra']:>8.3f}"
              f"{r['favlose']*100:>9.1f}%{r['brier']:>8.4f}")

    print("\n谚语逐条裁决：")
    def rank(key, desc=True):
        return sorted(rows, key=lambda r: r[key], reverse=desc)

    checks = [
        ("平局看西甲", "spain-laliga", "d", "rd", "平局率"),
        ("主队看德甲", "germany-bundesliga", "h", "rh", "主胜率"),
        ("客队牛逼看英超", "england-premier", "a", "ra", "客胜率"),
    ]
    for name, lg, fkey, rkey, label in checks:
        order = rank(fkey)
        pos = [i for i, r in enumerate(order) if r["lg"] == lg]
        me = next(r for r in order if r["lg"] == lg)
        best = order[0]
        rk = pos[0] + 1 if pos else -1
        lo, hi = bootstrap_ci(me["_retD"]) if rkey == "rd" else (None, None)
        freq_ok = "✓" if rk == 1 else f"✗(第{rk}, 最高={best['lg']} {best[fkey]*100:.1f}%)"
        money = me[rkey]
        money_ok = "✓可赚" if money > 1.0 else "✗仍亏"
        print(f"  · {name:<16} {label}={me[fkey]*100:.1f}% {freq_ok:<44} "
              f"回报率={money:.3f} {money_ok}")

    order = rank("favlose")
    me = next(r for r in order if r["lg"] == "italy-serie-a")
    rk = [i for i, r in enumerate(order) if r["lg"] == "italy-serie-a"][0] + 1
    print(f"  · {'翻车看意甲':<16} 热门失手率={me['favlose']*100:.1f}% "
          f"{'✓' if rk == 1 else f'✗(第{rk}, 最高={order[0]['lg']} {order[0]['favlose']*100:.1f}%)'}")

    order = rank("brier")
    me = next(r for r in order if r["lg"] == "france-ligue1")
    rk = [i for i, r in enumerate(order) if r["lg"] == "france-ligue1"][0] + 1
    print(f"  · {'虚情假意是法甲':<15} Brier(越高越不准)={me['brier']:.4f} "
          f"{'✓' if rk == 1 else f'✗(第{rk}, 最不准={order[0]['lg']} {order[0]['brier']:.4f})'}")


# ─────────────────────────── B. 剧本规则 ───────────────────────────

def _agg(prev: list) -> dict:
    sub = prev[-FORM_WINDOW:]
    n = len(sub)
    return {"gf": sum(r["gf"] for r in sub) / n,
            "ga": sum(r["ga"] for r in sub) / n,
            "tot": sum(r["gf"] + r["ga"] for r in sub) / n,
            "shf": sum(r["shf"] for r in sub) / n,
            "cs": sum(1 for r in sub if r["ga"] == 0) / n,
            "vd": sum(r["gf"] - r["ga"] for r in sub if r["venue"] == sub[0]["venue"])
                  / max(1, sum(1 for r in sub if r["venue"] == sub[0]["venue"]))}


def _venue_diff(prev: list, venue: str) -> float:
    sub = [r for r in prev[-FORM_WINDOW * 2:] if r["venue"] == venue]
    if not sub:
        return 0.0
    return sum(r["gf"] - r["ga"] for r in sub) / len(sub)


SCENARIOS = [
    ("1 双慢热闷局", "under",
     lambda c: c["hf"]["tot"] < 2.3 and c["af"]["tot"] < 2.3
               and c["hf"]["cs"] >= 0.33 and c["af"]["cs"] >= 0.33),
    ("2 强强保守", "under",
     lambda c: abs(c["ph"] - c["pa"]) < 0.10 and c["m"]["pch"] < 2.6 and c["m"]["pca"] < 2.6),
    ("3 对攻开放", "over",
     lambda c: c["hf"]["ga"] >= 1.5 and c["af"]["ga"] >= 1.5
               and c["hf"]["shf"] >= 13 and c["af"]["shf"] >= 13),
    ("4 一边倒屠杀", "over",
     lambda c: (c["ph"] >= 0.65 and c["af"]["ga"] >= 2.0)
               or (c["pa"] >= 0.65 and c["hf"]["ga"] >= 2.0)),
    ("5 疲劳低分", "under",
     lambda c: (c["rest_h"] <= 3 and c["hf"]["gf"] < 1.3)
               or (c["rest_a"] <= 3 and c["af"]["gf"] < 1.3)),
    ("6 主客场极化", "over",
     lambda c: c["home_vd"] >= 1.0 and c["away_vd"] <= -1.0),
]


def scenarios(matches: list) -> None:
    hist = defaultdict(list)
    last = {}
    fired = {name: {"rets": [], "scores": Counter()} for name, _, _ in SCENARIOS}
    all_rets = []

    for m in matches:
        hp, ap = hist[m["home"]], hist[m["away"]]
        if (len(hp) >= MIN_HISTORY and len(ap) >= MIN_HISTORY
                and m["over"] and m["under"]):
            ph, _, pa = devid(m["pch"], m["pcd"], m["pca"])
            total = m["hg"] + m["ag"]
            ctx = {"m": m, "hf": _agg(hp), "af": _agg(ap), "ph": ph, "pa": pa,
                   "rest_h": (m["date"] - last[m["home"]]).days if m["home"] in last else 99,
                   "rest_a": (m["date"] - last[m["away"]]).days if m["away"] in last else 99,
                   "home_vd": _venue_diff(hp, "H"), "away_vd": _venue_diff(ap, "A")}
            all_rets.append(m["over"] if total > 2.5 else 0.0)
            for name, side, cond in SCENARIOS:
                try:
                    ok = cond(ctx)
                except (KeyError, TypeError, ZeroDivisionError):
                    ok = False
                if not ok:
                    continue
                if side == "over":
                    fired[name]["rets"].append(m["over"] if total > 2.5 else 0.0)
                else:
                    fired[name]["rets"].append(m["under"] if total < 2.5 else 0.0)
                fired[name]["scores"][f"{m['hg']}:{m['ag']}"] += 1

        for team, gf, ga, shf, venue in (
            (m["home"], m["hg"], m["ag"], m["hs"], "H"),
            (m["away"], m["ag"], m["hg"], m["a_s"], "A"),
        ):
            hist[team].append({"gf": gf, "ga": ga, "shf": shf, "venue": venue})
            last[team] = m["date"]

    baseline = sum(all_rets) / len(all_rets)
    k = len(SCENARIOS)
    print("\n" + "=" * 96)
    print(f"B. 剧本规则验证（押 O/U 2.5 · 基线={baseline:.4f} · "
          f"多重比较 k={k}，Bonferroni 提示见末列）")
    print("=" * 96)
    print(f"{'剧本':<18}{'押':<7}{'触发n':>7}{'回报率':>9}{'CI下界':>9}{'CI上界':>9}"
          f"  {'判定':<6}  TOP3 比分")
    print("-" * 96)
    for name, side, _ in SCENARIOS:
        rets = fired[name]["rets"]
        if len(rets) < 50:
            print(f"{name:<18}{side:<7}{len(rets):>7}   样本不足(<50)")
            continue
        mean = sum(rets) / len(rets)
        lo, hi = bootstrap_ci(rets)
        verdict = ("立项" if lo > baseline and mean > 1.0
                   else "证伪" if mean < 0.9 else "噪声")
        top3 = " ".join(f"{s}({c})" for s, c in fired[name]["scores"].most_common(3))
        print(f"{name:<18}{side:<7}{len(rets):>7}{mean:>9.4f}{lo:>9.4f}{hi:>9.4f}"
              f"  {verdict:<6}  {top3}")
    print(f"\n注：k={k} 并行测试，即使全为噪声也有约 "
          f"{1 - 0.95 ** k:.0%} 概率至少一条看似显著；"
          f"Bonferroni 后单条需 CI 更严（约 99.2% 水平）才算立项。")


def main() -> None:
    print("[1/3] 拉取 fd 数据...")
    matches = load_matches()
    print(f"      {len(matches)} 场")
    print("[2/3] 谚语验证...")
    proverbs(matches)
    print("\n[3/3] 剧本规则验证（walk-forward）...")
    scenarios(matches)


if __name__ == "__main__":
    main()
