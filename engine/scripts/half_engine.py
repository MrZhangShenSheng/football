"""half_engine: 两阶段(HAFU)推演出口(wargame设计§二·P0-w 纯k版).
λ_fused(score_engine 市场主导融合) × k(half_share 联赛级) → 半场/下半场两段卷积
→ HAFU 9键 + 剧本树(半场三向分支 × 全场走向top路径).
与 dc_predict.hafu_approx(现状派生版)差异: ①输入λ升级融合口径 ②剧本树结构化出口;
主链不动(对照期冻结·设计§六), 段A五线对照两条线各自独立(同λ同k时9键数学同构,
差异仅来自λ来源——该同构性本身是单测断言).
开发者 sszhang"""
import json
import math
import sys

import dc_predict
import score_engine
from common import ROOT

CACHE_DIR = ROOT / "engine" / "cache"
SCORE_RANGE = dc_predict.SCORE_RANGE   # 0~5球截断(与hafu_approx同口径·对照可比)
OUTCOMES = "hda"
HAFU_KEYS = [a + b for a in OUTCOMES for b in OUTCOMES]


def _pois(k: int, lam: float) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _sign(h: int, a: int) -> str:
    return "h" if h > a else ("d" if h == a else "a")


def hafu_tree(lh: float, la: float, k: float, rho_half: float) -> dict:
    """两段卷积: 半场(λ×k, DC tau修) × 下半场(守恒(1-k)λ, 纯泊松) → 9键+树.
    树分支: {半场三向: {'p','ft':[条件全场三向],'top_ft','top_score'}}."""
    lh1, la1 = lh * k, la * k
    lh2, la2 = lh - lh1, la - la1
    hafu = {key: 0.0 for key in HAFU_KEYS}
    ft_of = {i: [0.0, 0.0, 0.0] for i in OUTCOMES}
    score_p = {}   # (半场i, 全场h, 全场a) → 联合概率(分支top比分用)
    for x in SCORE_RANGE:
        for y in SCORE_RANGE:
            p1 = _pois(x, lh1) * _pois(y, la1) * \
                max(dc_predict.dc_tau(x, y, lh1, la1, rho_half), 1e-12)
            if p1 < 1e-12:
                continue
            ht = _sign(x, y)
            for u in SCORE_RANGE:
                pu = _pois(u, lh2)
                for v in SCORE_RANGE:
                    p = p1 * pu * _pois(v, la2)
                    fx, fy = x + u, y + v
                    fts = _sign(fx, fy)
                    hafu[ht + fts] += p
                    ft_of[ht][OUTCOMES.index(fts)] += p
                    key = (ht, fx, fy)
                    score_p[key] = score_p.get(key, 0.0) + p
    total = sum(hafu.values()) or 1.0
    hafu = {kk: v / total for kk, v in hafu.items()}
    tree = {}
    for i in OUTCOMES:
        branch_p = sum(hafu[i + j] for j in OUTCOMES)
        cands = [(p, f"{h}:{a}") for (ht, h, a), p in score_p.items() if ht == i]
        p_top, s_top = max(cands, key=lambda t: t[0]) if cands else (0.0, "0:0")
        tree[i] = {
            "p": round(branch_p, 6),
            "ft": [round(ft_of[i][j] / total, 6) for j in range(3)],
            "top_ft": OUTCOMES[max(range(3), key=lambda j: ft_of[i][j])],
            "top_score": {"score": s_top, "p": round(p_top / total, 6)},
        }
    return {"hafu": hafu, "half": [tree[i]["p"] for i in OUTCOMES], "tree": tree}


def hafu(league, lh_dc, la_dc, rho_dc, mkt=None,
         w_dc=score_engine.W_DC_INIT, dc_version=None) -> dict:
    """统一入口: score_engine 融合λ → 两段卷积 → 9键+树+口径透传(source/k/dcVersion)."""
    se = score_engine.matrix(lh_dc, la_dc, rho_dc, mkt, w_dc, dc_version)
    k, rho_half = dc_predict.load_half_params(league)
    out = hafu_tree(se["lambda"]["lh"], se["lambda"]["la"], k, rho_half)
    out["source"] = se["source"]      # fused | dc_only
    out["lambda"] = se["lambda"]
    out["k"] = k
    out["rho_half"] = rho_half
    out["dc_version"] = se["dc_version"]
    return out


def main() -> None:
    """python half_engine.py <联赛slug> <主队fd名> <客队fd名>  (dc_only 演示口径;
    全链 fused 需体彩五池价, 段A/影子链经 hafu(mkt=...) 接, CLI 暂不重复接线)."""
    if len(sys.argv) < 4:
        print("用法: python half_engine.py <联赛slug> <主队> <客队>")
        return
    league, home, away = sys.argv[1], sys.argv[2], sys.argv[3]
    lam = dc_predict.match_lambdas(league, home, away)
    if lam is None:
        print(json.dumps({"error": f"λ不可得: {league} 缓存缺或队名未匹配"}, ensure_ascii=False))
        return
    dc_path = CACHE_DIR / f"{league}_dc.json"
    try:
        rho = float(json.loads(dc_path.read_text(encoding="utf-8")).get("rho", 0.0))
    except (OSError, json.JSONDecodeError, ValueError):
        rho = 0.0
    print(json.dumps(hafu(league, lam[0], lam[1], rho), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
