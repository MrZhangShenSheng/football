# engine/scripts/attribution_rolling.py
"""赛前可观测的滚动特征（A2/A4 档）——严格只用【本场之前】的场次。

与 E4/E5 的根本区别：
  E4/E5 读本场射正、本场 xG ⟹ 赛后才有 ⟹ 永不可进校准通道（见 t5 判废）。
  本模块读该队本场之前 N 场的滚动均值 ⟹ 赛前可算 ⟹ 可进频次表争取校准授权。

时间线纪律（research/README 规矩1 的同类要求）：
  窗口严格 < 本场日期，同日场次一律排除（同日多场=无法确定先后，宁缺勿漏看）。
  不足 MIN_WINDOW 场返回 None 进 missing，不用联赛均值填充（填充=制造虚假覆盖）。

开发者 sszhang
"""
import csv
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FD_DIR = ROOT / "cache" / "shape_backtest"

WINDOW = 10        # 滚动窗口场数（设计表 A2「近 10 场」）
MIN_WINDOW = 5     # 不足此数返回 None（赛季初窗口缺口·如实标缺）


def _iso(d: str) -> str:
    dd, mm, yy = d.split("/")
    return f"{yy}-{mm.zfill(2)}-{dd.zfill(2)}"


def _f(v) -> float | None:
    v = (v or "").strip()
    try:
        return float(v)
    except ValueError:
        return None


def build_team_history(codes: list[str] | None = None) -> dict:
    """fd 全量 → {team: [(iso, 该场该队的 per-match 指标 dict), ...]} 按日期升序。

    指标均为「该队在该场的表现」，主客已归一到 for/against 口径。
    """
    hist: dict[str, list] = defaultdict(list)
    files = sorted(FD_DIR.glob("*.csv"))
    for f in files:
        code = f.stem.split("_")[0]
        if codes and code not in codes:
            continue
        with open(f, encoding="latin-1") as fh:
            for row in csv.DictReader(fh):
                if not (row.get("FTHG") or "").strip():
                    continue
                iso = _iso(row["Date"])
                hg, ag = _f(row["FTHG"]), _f(row["FTAG"])
                for side, team in (("H", row["HomeTeam"]), ("A", row["AwayTeam"])):
                    opp = "A" if side == "H" else "H"
                    hist[team].append((iso, {
                        "gf": hg if side == "H" else ag,
                        "ga": ag if side == "H" else hg,
                        "shots": _f(row.get(f"{side}S")),
                        "shots_ag": _f(row.get(f"{opp}S")),
                        "st": _f(row.get(f"{side}ST")),
                        "st_ag": _f(row.get(f"{opp}ST")),
                        "corners": _f(row.get(f"{side}C")),
                        "fouls": _f(row.get(f"{side}F")),
                        "yellow": _f(row.get(f"{side}Y")),
                        "red": _f(row.get(f"{side}R")),
                        "xg": _f(row.get(f"{side}xG")),
                        "xg_ag": _f(row.get(f"{opp}xG")),
                    }))
    for team in hist:
        hist[team].sort(key=lambda t: t[0])
    return dict(hist)


def _window(hist: dict, team: str, before_iso: str) -> list[dict]:
    """该队严格早于 before_iso 的最近 WINDOW 场（同日排除）。"""
    rows = [m for d, m in hist.get(team, []) if d < before_iso]
    return rows[-WINDOW:]


def _mean(rows: list[dict], key: str) -> float | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


def rolling_profile(hist: dict, team: str, before_iso: str) -> dict | None:
    """该队赛前滚动画像。不足 MIN_WINDOW 场返回 None。"""
    rows = _window(hist, team, before_iso)
    if len(rows) < MIN_WINDOW:
        return None
    out = {"n": len(rows)}
    for k in ("gf", "ga", "shots", "shots_ag", "st", "st_ag",
              "corners", "fouls", "yellow", "red", "xg", "xg_ag"):
        out[k] = _mean(rows, k)
    return out


# ── 维度计算（输出与 attribution_dims 同构：dim/h/a/evidence）──

STYLE_SCALE = 0.10      # 射正差→进球期望偏移（先验占位·待频次表校准）
DISCIPLINE_SCALE = 0.15  # 纪律档→尾格系数（先验占位·待频次表校准）
XG_FORM_SCALE = 0.25    # 攻防画像差→偏移（先验占位·待频次表校准）


def dim_style(ph: dict | None, pa: dict | None) -> dict | None:
    """A4 风格对位：双方滚动射正 for/against 的相对强弱 → 偏移。

    口径：本队滚动射正均值 vs 对手滚动被射正均值，两侧各算一个"预期射正"，
    差值乘 STYLE_SCALE 入账。缺任一侧返回 None。
    """
    if not ph or not pa:
        return None
    if None in (ph.get("st"), pa.get("st_ag"), pa.get("st"), ph.get("st_ag")):
        return None
    exp_h = (ph["st"] + pa["st_ag"]) / 2
    exp_a = (pa["st"] + ph["st_ag"]) / 2
    net = (exp_h - exp_a) * STYLE_SCALE
    return {"dim": "A4.style", "h": round(net / 2, 3), "a": round(-net / 2, 3),
            "evidence": f"滚动预期射正 h{exp_h:.1f}/a{exp_a:.1f}"
                        f"(窗口 h{ph['n']}/a{pa['n']}场·赛前可算)"}


def dim_discipline(ph: dict | None, pa: dict | None) -> dict | None:
    """A4 纪律特性：滚动犯规+黄牌率 → 尾格系数（形状型·不入 offsets 求和）。"""
    if not ph or not pa:
        return None
    if None in (ph.get("fouls"), pa.get("fouls")):
        return None
    tot = (ph["fouls"] + pa["fouls"]) / 2
    y = ((ph.get("yellow") or 0) + (pa.get("yellow") or 0)) / 2
    # 犯规密集场 → 断球多/节奏碎 → 尾部略收；基准 12 犯规/场
    scale = round(1.0 + (12.0 - tot) * DISCIPLINE_SCALE / 12.0, 3)
    return {"dim": "A4.discipline", "tail_scale": scale,
            "evidence": f"滚动犯规 {tot:.1f}/场·黄 {y:.1f}"
                        f"(窗口 h{ph['n']}/a{pa['n']}场·赛前可算)"}


def dim_xg_form(ph: dict | None, pa: dict | None) -> dict | None:
    """A2 攻防画像：滚动 xG for/against → 期望进球偏移（主客分离）。

    注意与 E5 的区别：这里是【历史场次】xG 的滚动均值（赛前可算），
    E5 是【本场】xG（赛后才有·已判废进校准通道）。
    """
    if not ph or not pa:
        return None
    if None in (ph.get("xg"), pa.get("xg_ag"), pa.get("xg"), ph.get("xg_ag")):
        return None
    exp_h = (ph["xg"] + pa["xg_ag"]) / 2
    exp_a = (pa["xg"] + ph["xg_ag"]) / 2
    return {"dim": "A2.xg",
            "h": round((exp_h - 1.4) * XG_FORM_SCALE, 3),
            "a": round((exp_a - 1.2) * XG_FORM_SCALE, 3),
            "evidence": f"滚动期望 xG h{exp_h:.2f}/a{exp_a:.2f}"
                        f"(窗口 h{ph['n']}/a{pa['n']}场·赛前可算)"}
