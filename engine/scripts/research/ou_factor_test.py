"""大小球盘因子实验（2026-08-29 · A2 代理验证）：CRS 池无历史赔率无法直接验证，
用 fd 有全量收盘价的 O/U 2.5 盘做下限探针——连大小球都打不过，CRS 更没戏。开发者 sszhang

与 longshot_factor_test 的区别：目标不是"方向冷门"而是"进球数偏离"，因子换成进球侧。
判据同款事先说死：高分组 >1.0 且 CI 下界 > 基线 → CRS 值得建；<0.9 → 证伪；之间 → 噪声。
"""
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from band_calibration import DIVS, SEASONS, bootstrap_ci, fetch_rows

FORM_WINDOW = 6
MIN_HISTORY = 4
LUCK_RATE = 0.30


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
                    m = {
                        "date": d, "league": league,
                        "home": r["HomeTeam"], "away": r["AwayTeam"],
                        "hg": int(r["FTHG"]), "ag": int(r["FTAG"]),
                        "over": float(r["PC>2.5"]), "under": float(r["PC<2.5"]),
                        "hst": float(r["HST"]), "ast": float(r["AST"]),
                        "hs": float(r["HS"]), "a_s": float(r["AS"]),
                    }
                except (KeyError, ValueError, TypeError):
                    continue
                if m["over"] <= 1 or m["under"] <= 1:
                    continue
                out.append(m)
    out.sort(key=lambda m: m["date"])
    return out


def _agg(prev: list) -> dict:
    sub = prev[-FORM_WINDOW:]
    n = len(sub)
    return {
        "gf": sum(r["gf"] for r in sub) / n,
        "ga": sum(r["ga"] for r in sub) / n,
        "tot": sum(r["gf"] + r["ga"] for r in sub) / n,
        "stf": sum(r["stf"] for r in sub) / n,
        "sta": sum(r["sta"] for r in sub) / n,
        "shf": sum(r["shf"] for r in sub) / n,
        "over_rate": sum(1 for r in sub if r["gf"] + r["ga"] > 2.5) / n,
        "n": n,
    }


def build_rows(matches: list) -> list:
    hist = defaultdict(list)
    rows = []
    for m in matches:
        hp, ap = hist[m["home"]], hist[m["away"]]
        if len(hp) >= MIN_HISTORY and len(ap) >= MIN_HISTORY:
            rows.extend(make_row(m, _agg(hp), _agg(ap)))
        for team, gf, ga, stf, sta, shf in (
            (m["home"], m["hg"], m["ag"], m["hst"], m["ast"], m["hs"]),
            (m["away"], m["ag"], m["hg"], m["ast"], m["hst"], m["a_s"]),
        ):
            hist[team].append({"gf": gf, "ga": ga, "stf": stf, "sta": sta, "shf": shf})
    return rows


def make_row(m: dict, hf: dict, af: dict) -> list:
    total = m["hg"] + m["ag"]
    # 因子（对 over 方向为正、对 under 取反）
    f_form_tot = (hf["tot"] + af["tot"]) / 2 - 2.5        # 近况总进球偏离 2.5
    f_luck = ((hf["stf"] + af["stf"]) * LUCK_RATE
              - (hf["gf"] + af["gf"]))                    # 射正预期高于实得 = 进球被压抑，该反弹
    f_att = hf["gf"] + af["gf"] - (hf["ga"] + af["ga"])   # 攻强守弱
    f_shot = (hf["shf"] + af["shf"]) / 2 - 12.0           # 射门量（节奏代理）
    f_overrate = (hf["over_rate"] + af["over_rate"]) / 2 - 0.5

    base = {"date": m["date"], "league": m["league"]}
    out = []
    for side, odds, hit, sign in (
        ("over", m["over"], 1.0 if total > 2.5 else 0.0, 1.0),
        ("under", m["under"], 1.0 if total < 2.5 else 0.0, -1.0),
    ):
        out.append({**base, "side": side, "odds": odds, "hit": hit,
                    "f_form_tot": sign * f_form_tot, "f_luck": sign * f_luck,
                    "f_att": sign * f_att, "f_shot": sign * f_shot,
                    "f_overrate": sign * f_overrate})
    return out


def zscore(vals: list) -> list:
    n = len(vals)
    mu = sum(vals) / n
    sd = (sum((v - mu) ** 2 for v in vals) / n) ** 0.5 or 1.0
    return [(v - mu) / sd for v in vals]


def report(rows: list, factors: list, title: str) -> None:
    n = len(rows)
    baseline = sum(r["odds"] * r["hit"] for r in rows) / n
    zs = {f: zscore([r[f] for r in rows]) for f in factors}
    for i, r in enumerate(rows):
        r["score"] = sum(zs[f][i] for f in factors)
    rows.sort(key=lambda r: -r["score"])

    print()
    print(f"=== {title} ===  n={n}  基线={baseline:.4f}")
    print(f"{'分组':<14}{'n':>7}{'回报率':>10}{'CI95下界':>11}{'CI95上界':>11}  判定")
    print("-" * 66)
    for label, sub in (("TOP 10%", rows[: n // 10]),
                       ("TOP 20%", rows[: n // 5]),
                       ("TOP 30%", rows[: int(n * 0.3)]),
                       ("中间 40%", rows[int(n * 0.3): int(n * 0.7)]),
                       ("BOTTOM 30%", rows[int(n * 0.7):])):
        rets = [r["odds"] * r["hit"] for r in sub]
        mean = sum(rets) / len(rets)
        lo, hi = bootstrap_ci(rets)
        verdict = ("立项" if lo > baseline and mean > 1.0
                   else "证伪" if mean < 0.9 else "噪声")
        print(f"{label:<14}{len(sub):>7}{mean:>10.4f}{lo:>11.4f}{hi:>11.4f}  {verdict}")

    print("单因子边际（各自 TOP 20%）：")
    for f in factors:
        s = sorted(rows, key=lambda r: -r[f])[: n // 5]
        rets = [r["odds"] * r["hit"] for r in s]
        mean = sum(rets) / len(rets)
        lo, _ = bootstrap_ci(rets)
        print(f"  {f:<12} ret={mean:.4f}  ci_lo={lo:.4f}  {'✓' if lo > baseline else '·'}")


def main() -> None:
    print("[1/2] 拉取 fd O/U 数据...")
    matches = load_matches()
    print(f"      有 O/U 收盘价的比赛 {len(matches)} 场")

    print("[2/2] 建因子（walk-forward）+ 分组...")
    rows = build_rows(matches)
    factors = ["f_form_tot", "f_luck", "f_att", "f_shot", "f_overrate"]
    report(rows, factors, "全部 O/U 选项（over + under）")

    # 高赔子集：CRS 池的赔率形态更接近高赔端，单独看
    hi_rows = [r for r in rows if r["odds"] >= 2.2]
    if len(hi_rows) > 300:
        report(hi_rows, factors, "高赔子集（收盘 ≥2.2，CRS 赔率形态代理）")


if __name__ == "__main__":
    main()
