"""大小球盘因子实验 v2（2026-08-29 · 修 ②③）：
② 修 bug——原版把每场 over/under 都入样(镜像对)，回报率天然互补→机械趋近基线。
   v2 每场只取一个方向（按因子分数决定买哪边），样本 = 场数。
③ 权重让数据选——训练集逻辑回归拟合，留出集验证（时间切分，无泄漏）。
判据同款事先说死：留出集高分组 >1.0 且 CI 下界 > 基线 → 立项；<0.9 → 证伪；之间 → 噪声。
开发者 sszhang
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
FACTORS = ["f_form_tot", "f_luck", "f_att", "f_shot", "f_overrate"]
TRAIN_FRAC = 0.6          # 时间前 60% 训练，后 40% 留出


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
                         "hg": int(r["FTHG"]), "ag": int(r["FTAG"]),
                         "over": float(r["PC>2.5"]), "under": float(r["PC<2.5"]),
                         "hst": float(r["HST"]), "ast": float(r["AST"]),
                         "hs": float(r["HS"]), "a_s": float(r["AS"])}
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
    return {"gf": sum(r["gf"] for r in sub) / n,
            "ga": sum(r["ga"] for r in sub) / n,
            "tot": sum(r["gf"] + r["ga"] for r in sub) / n,
            "stf": sum(r["stf"] for r in sub) / n,
            "shf": sum(r["shf"] for r in sub) / n,
            "over_rate": sum(1 for r in sub if r["gf"] + r["ga"] > 2.5) / n}


def build_rows(matches: list) -> list:
    """每场输出 1 行（不是 2 行）：因子按 over 方向定义，label = 实际是否 over。"""
    hist = defaultdict(list)
    rows = []
    for m in matches:
        hp, ap = hist[m["home"]], hist[m["away"]]
        if len(hp) >= MIN_HISTORY and len(ap) >= MIN_HISTORY:
            hf, af = _agg(hp), _agg(ap)
            total = m["hg"] + m["ag"]
            rows.append({
                "date": m["date"], "league": m["league"],
                "over_odds": m["over"], "under_odds": m["under"],
                "y": 1 if total > 2.5 else 0,
                "f_form_tot": (hf["tot"] + af["tot"]) / 2 - 2.5,
                "f_luck": (hf["stf"] + af["stf"]) * LUCK_RATE - (hf["gf"] + af["gf"]),
                "f_att": hf["gf"] + af["gf"] - (hf["ga"] + af["ga"]),
                "f_shot": (hf["shf"] + af["shf"]) / 2 - 12.0,
                "f_overrate": (hf["over_rate"] + af["over_rate"]) / 2 - 0.5,
            })
        for team, gf, ga, stf, shf in (
            (m["home"], m["hg"], m["ag"], m["hst"], m["hs"]),
            (m["away"], m["ag"], m["hg"], m["ast"], m["a_s"]),
        ):
            hist[team].append({"gf": gf, "ga": ga, "stf": stf, "shf": shf})
    return rows


def standardize(rows: list, ref: list) -> None:
    """按 ref（训练集）的均值方差标准化全体，写入 rows[f+'_z']。"""
    for f in FACTORS:
        vals = [r[f] for r in ref]
        mu = sum(vals) / len(vals)
        sd = (sum((v - mu) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
        for r in rows:
            r[f + "_z"] = (r[f] - mu) / sd


def fit_logistic(train: list, lr: float = 0.1, epochs: int = 300) -> dict:
    """无依赖梯度下降逻辑回归：P(over) ~ σ(b + Σ w_f·z_f)。"""
    import math
    w = {f: 0.0 for f in FACTORS}
    b = 0.0
    n = len(train)
    for _ in range(epochs):
        gw = {f: 0.0 for f in FACTORS}
        gb = 0.0
        for r in train:
            z = b + sum(w[f] * r[f + "_z"] for f in FACTORS)
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            e = p - r["y"]
            gb += e
            for f in FACTORS:
                gw[f] += e * r[f + "_z"]
        b -= lr * gb / n
        for f in FACTORS:
            w[f] -= lr * gw[f] / n
    return {"w": w, "b": b}


def predict(model: dict, r: dict) -> float:
    import math
    z = model["b"] + sum(model["w"][f] * r[f + "_z"] for f in FACTORS)
    return 1 / (1 + math.exp(-max(-30, min(30, z))))


def devig_ou(over_odds: float, under_odds: float) -> float:
    """O/U 两向去水 → 市场 P(over)。"""
    io, iu = 1 / over_odds, 1 / under_odds
    return io / (io + iu)


def evaluate(rows: list, model: dict, title: str) -> None:
    """每场按模型 vs 市场的边际决定买 over 还是 under，按边际大小分组看回报率。"""
    picks = []
    for r in rows:
        p_model = predict(model, r)
        p_mkt = devig_ou(r["over_odds"], r["under_odds"])
        edge_over = p_model * r["over_odds"] - 1
        edge_under = (1 - p_model) * r["under_odds"] - 1
        if edge_over >= edge_under:
            picks.append({"edge": edge_over, "odds": r["over_odds"],
                          "hit": 1.0 if r["y"] == 1 else 0.0,
                          "dev": p_model - p_mkt})
        else:
            picks.append({"edge": edge_under, "odds": r["under_odds"],
                          "hit": 1.0 if r["y"] == 0 else 0.0,
                          "dev": p_mkt - p_model})

    n = len(picks)
    baseline = sum(p["odds"] * p["hit"] for p in picks) / n
    picks.sort(key=lambda p: -p["edge"])

    print()
    print(f"=== {title} ===  n={n}  基线={baseline:.4f}")
    print(f"{'分组':<16}{'n':>7}{'回报率':>10}{'CI下界':>10}{'CI上界':>10}  判定")
    print("-" * 64)
    groups = [("TOP 5% edge", picks[: n // 20]),
              ("TOP 10% edge", picks[: n // 10]),
              ("TOP 20% edge", picks[: n // 5]),
              ("TOP 30% edge", picks[: int(n * .3)]),
              ("BOTTOM 30%", picks[int(n * .7):])]
    for label, sub in groups:
        rets = [p["odds"] * p["hit"] for p in sub]
        mean = sum(rets) / len(rets)
        lo, hi = bootstrap_ci(rets)
        verdict = ("立项" if lo > baseline and mean > 1.0
                   else "证伪" if mean < 0.9 else "噪声")
        print(f"{label:<16}{len(sub):>7}{mean:>10.4f}{lo:>10.4f}{hi:>10.4f}  {verdict}")

    # EV 门槛版（⑤ 顺带修）：只买模型边际 > 0 的
    pos = [p for p in picks if p["edge"] > 0]
    if pos:
        rets = [p["odds"] * p["hit"] for p in pos]
        mean = sum(rets) / len(rets)
        lo, hi = bootstrap_ci(rets)
        print(f"{'edge>0 门槛':<16}{len(pos):>7}{mean:>10.4f}{lo:>10.4f}{hi:>10.4f}"
              f"  {'立项' if lo > baseline and mean > 1.0 else '证伪' if mean < 0.9 else '噪声'}")


def main() -> None:
    print("[1/4] 拉取 fd O/U 数据...")
    matches = load_matches()
    print(f"      比赛 {len(matches)} 场")

    print("[2/4] 建因子（walk-forward，每场 1 行·修镜像对 bug）...")
    rows = build_rows(matches)
    rows.sort(key=lambda r: r["date"])
    cut = int(len(rows) * TRAIN_FRAC)
    train, hold = rows[:cut], rows[cut:]
    print(f"      样本 {len(rows)} 行 → 训练 {len(train)}（~{train[-1]['date'].date()}）"
          f" / 留出 {len(hold)}")

    print("[3/4] 逻辑回归拟合权重（训练集标准化，无泄漏）...")
    standardize(rows, train)
    model = fit_logistic(train)
    print("      权重:", " ".join(f"{f}={model['w'][f]:+.3f}" for f in FACTORS),
          f"b={model['b']:+.3f}")

    print("[4/4] 留出集评估...")
    evaluate(train, model, "训练集（参考·必然乐观）")
    evaluate(hold, model, "留出集（★ 判据以此为准）")

    hi = [r for r in hold if max(r["over_odds"], r["under_odds"]) >= 2.2]
    if len(hi) > 300:
        evaluate(hi, model, "留出集·高赔子集（≥2.2 · CRS 形态代理）")


if __name__ == "__main__":
    main()
