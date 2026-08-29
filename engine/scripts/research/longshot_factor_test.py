"""冷门方向因子增量实验（2026-08-29）：市场隐含 <20% 的方向，因子打分能否筛出正 EV 子集。开发者 sszhang

判据（事先说死）：高分组回报率 >1.0 且 bootstrap CI 下界 > 基线 → 立项；<0.9 → 证伪；0.9~1.0 → 噪声。
纪律：所有因子严格只用该场之前的比赛（walk-forward，无未来函数）；成交按 Pinnacle 收盘价。
"""
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from band_calibration import DIVS, SEASONS, bootstrap_ci, devid, fetch_rows

LONGSHOT_MAX = 0.20     # 冷门定义：市场去水隐含概率上限
FORM_WINDOW = 6         # 因子回看窗口（场）
MIN_HISTORY = 4         # 建因子最少历史场数


def parse_date(s: str):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def load_matches() -> list:
    """fd 全量 → 按时间排序的比赛列表（含收盘价/开盘价/射正/日期）。"""
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
                        "hg": int(r["FTHG"]), "ag": int(r["FTAG"]), "ftr": r["FTR"],
                        "pch": float(r["PSCH"]), "pcd": float(r["PSCD"]), "pca": float(r["PSCA"]),
                        "hst": float(r["HST"]), "ast": float(r["AST"]),
                    }
                    m["poh"] = float(r["PSH"]) if r.get("PSH") else None
                    m["poa"] = float(r["PSA"]) if r.get("PSA") else None
                except (KeyError, ValueError, TypeError):
                    continue
                if m["ftr"] not in ("H", "D", "A"):
                    continue
                out.append(m)
    out.sort(key=lambda m: m["date"])
    return out


def build_factors(matches: list) -> list:
    """逐场推进，为每场的主/客队算 5 因子（只用此前比赛）。返回冷门方向候选列表。"""
    hist = defaultdict(list)      # team -> [{date, gf, ga, stf, sta, venue}]
    rows = []

    for m in matches:
        h_prev, a_prev = hist[m["home"]], hist[m["away"]]
        if len(h_prev) >= MIN_HISTORY and len(a_prev) >= MIN_HISTORY:
            rows.append(make_row(m, h_prev, a_prev))
        for team, gf, ga, stf, sta, venue in (
            (m["home"], m["hg"], m["ag"], m["hst"], m["ast"], "H"),
            (m["away"], m["ag"], m["hg"], m["ast"], m["hst"], "A"),
        ):
            hist[team].append({"date": m["date"], "gf": gf, "ga": ga,
                               "stf": stf, "sta": sta, "venue": venue})
    return [r for r in rows if r]


def _agg(prev: list, venue: str | None = None) -> dict:
    rows = prev[-FORM_WINDOW:]
    sub = [r for r in rows if venue is None or r["venue"] == venue] or rows
    n = len(sub)
    return {
        "gf": sum(r["gf"] for r in sub) / n,
        "ga": sum(r["ga"] for r in sub) / n,
        "stf": sum(r["stf"] for r in sub) / n,
        "sta": sum(r["sta"] for r in sub) / n,
        "cs": sum(1 for r in sub if r["ga"] == 0) / n,
        "n": n,
    }


def make_row(m: dict, h_prev: list, a_prev: list) -> dict | None:
    ph, pd_, pa = devid(m["pch"], m["pcd"], m["pca"])
    cands = []
    for side, p, odds in (("H", ph, m["pch"]), ("A", pa, m["pca"])):
        if p < LONGSHOT_MAX:
            cands.append((side, p, odds))
    if not cands:
        return None

    hf, af = _agg(h_prev), _agg(a_prev)
    hf_v, af_v = _agg(h_prev, "H"), _agg(a_prev, "A")
    rest_h = (m["date"] - h_prev[-1]["date"]).days
    rest_a = (m["date"] - a_prev[-1]["date"]).days

    # 射正→进球转化率（联赛经验值 ≈0.30）作运气代理：实得低于射正预期 = 运气差，均值回归候选
    LUCK_RATE = 0.30
    luck_h = hf["gf"] - hf["stf"] * LUCK_RATE
    luck_a = af["gf"] - af["stf"] * LUCK_RATE

    out = []
    for side, p, odds in cands:
        under = "home" if side == "H" else "away"
        me, opp = (hf, af) if side == "H" else (af, hf)
        me_v = hf_v if side == "H" else af_v
        rest_me, rest_opp = (rest_h, rest_a) if side == "H" else (rest_a, rest_h)
        luck_me = luck_h if side == "H" else luck_a
        out.append({
            "date": m["date"], "league": m["league"], "side": side, "under": under,
            "p": p, "odds": odds, "hit": 1.0 if m["ftr"] == side else 0.0,
            "f_luck": -luck_me,                       # 越正 = 运气越差，越该反弹
            "f_venue": me_v["gf"] - me_v["ga"] - (me["gf"] - me["ga"]),   # 主客场特化优势
            "f_rest": rest_me - rest_opp,             # 休息天数优势
            "f_cs": me["cs"] - opp["cs"],             # 零封率差（防线状态）
            "f_drift": drift(m, side),                # 开→收赔率反向移动（钱在动）
        })
    return out


def drift(m: dict, side: str) -> float:
    """开盘→收盘赔率变动率：负值 = 赔率下降 = 该方向被买入（聪明钱信号）。"""
    o_open = m["poh"] if side == "H" else m["poa"]
    o_close = m["pch"] if side == "H" else m["pca"]
    if not o_open or not o_close:
        return 0.0
    return (o_close - o_open) / o_open


def zscore(vals: list) -> list:
    n = len(vals)
    mu = sum(vals) / n
    sd = (sum((v - mu) ** 2 for v in vals) / n) ** 0.5 or 1.0
    return [(v - mu) / sd for v in vals]


def main() -> None:
    print("[1/3] 拉取 fd 数据...")
    matches = load_matches()
    print(f"      比赛 {len(matches)} 场")

    print("[2/3] 建因子（walk-forward）...")
    nested = build_factors(matches)
    rows = [r for group in nested for r in group]
    print(f"      冷门方向候选 {len(rows)} 个（市场隐含 <{LONGSHOT_MAX:.0%}）")

    baseline = sum(r["odds"] * r["hit"] for r in rows) / len(rows)
    print(f"      基线回报率（全部冷门方向）= {baseline:.4f}")

    print("[3/3] 因子打分 + 分组回报率...")
    factors = ["f_luck", "f_venue", "f_rest", "f_cs", "f_drift"]
    zs = {f: zscore([r[f] for r in rows]) for f in factors}
    for i, r in enumerate(rows):
        r["score"] = sum(zs[f][i] for f in factors)

    rows.sort(key=lambda r: -r["score"])
    n = len(rows)
    print()
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

    print()
    print("单因子边际（各自 TOP 20% 回报率）：")
    for f in factors:
        s = sorted(rows, key=lambda r: -r[f])[: n // 5]
        rets = [r["odds"] * r["hit"] for r in s]
        mean = sum(rets) / len(rets)
        lo, _ = bootstrap_ci(rets)
        print(f"  {f:<10} ret={mean:.4f}  ci_lo={lo:.4f}  {'✓' if lo > baseline else '·'}")


if __name__ == "__main__":
    main()
