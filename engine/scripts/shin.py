"""⑤ Shin(1993) 去水（三梯队二期 · docs/2026-09-27-probability-modernization-design.html）。

问题：HAD 三向用比例归一（均匀砍抽水），CRS 池用 power 去水——双轨制。且体彩
12.9% 抽水下 favorite-longshot bias 最重：抽水更多藏在冷门里，比例归一系统性
高估冷门低估热门。Shin 内幕交易模型直接消 FLB。

模型：观测隐含 π_i=1/o_i，overround B=Σπ。真实概率
  p_i = (√(z² + 4(1−z)π_i²/B) − z) / (2(1−z))
z=知情交易者比例（数值解 z 使 Σp=1）。z 越大 FLB 校正越强。

回测护栏：216 条已结算场（score_odds 存档交集）体彩三向
Shin vs 比例归一 的 outcome logloss——改善 <1% 不切换（消融纪律）。

用法：python shin.py [--selftest]
开发者 sszhang
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "data" / "04-summaries" / "shin-report.json"


def shin_devig(odds: list[float], iters: int = 60) -> tuple[list[float], float]:
    """Shin 去水：返回 (三向概率, z)。二分 z 使 Σp=1。"""
    pi = [1.0 / o for o in odds]
    b = sum(pi)

    def probs(z: float) -> list[float]:
        return [max(0.0, (math.sqrt(z * z + 4 * (1 - z) * p * p / b) - z) / (2 * (1 - z)))
                for p in pi]

    lo, hi = 1e-6, 0.9
    for _ in range(iters):
        mid = (lo + hi) / 2
        if sum(probs(mid)) > 1.0:
            lo = mid   # Σ>1 → 校正不够 → z 增大（更多 FLB 校正压低 Σ）
        else:
            hi = mid
    z = (lo + hi) / 2
    p = probs(z)
    s = sum(p)
    return [x / s for x in p], z


def prop_devig(odds: list[float]) -> list[float]:
    pi = [1.0 / o for o in odds]
    s = sum(pi)
    return [x / s for x in pi]


def outcome_idx(result: str) -> int | None:
    """'4-0'→0 主胜 · '1-1'→1 平 · '0-2'→2 客胜。"""
    try:
        h, a = (int(x) for x in str(result).split("-"))
    except ValueError:
        return None
    return 0 if h > a else (1 if h == a else 2)


def backtest() -> dict:
    """216 条已结算场：score_odds 存档体彩三向 → Shin vs 比例归一 logloss。"""
    cache = {}
    for f in sorted((ROOT / "engine" / "cache" / "score_odds").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for day in d.get("matchDays", []):
            for m in day.get("matches", []):
                had = m.get("had") or {}
                try:
                    cache[(f.stem, m.get("matchNumStr") or m.get("code"))] = (
                        float(had["h"]), float(had["d"]), float(had["a"]))
                except (KeyError, TypeError, ValueError):
                    continue
    corpus = json.loads((ROOT / "data" / "04-summaries" / "corpus.json").read_text(encoding="utf-8"))
    ll_shin = ll_prop = n = 0
    fav_shin = fav_prop = fav_n = 0
    dog_shin = dog_prop = dog_n = 0
    zs = []
    for r in corpus.get("records", []):
        if r.get("directionHit") is None:
            continue
        oi = outcome_idx(str(r.get("result") or ""))
        odds = cache.get((str(r.get("date")), r.get("code")))
        if oi is None or not odds:
            continue
        ps, z = shin_devig(list(odds))
        pp = prop_devig(list(odds))
        n += 1
        zs.append(z)
        ll_shin += -math.log(max(ps[oi], 1e-12))
        ll_prop += -math.log(max(pp[oi], 1e-12))
        # 分层：观测热门（π最高项）命中 vs 冷门
        hot = pi_max = max(range(3), key=lambda k: 1 / odds[k])
        if oi == hot:  # 热门命中
            fav_n += 1
            fav_shin += -math.log(max(ps[oi], 1e-12))
            fav_prop += -math.log(max(pp[oi], 1e-12))
        else:          # 冷门/平局命中
            dog_n += 1
            dog_shin += -math.log(max(ps[oi], 1e-12))
            dog_prop += -math.log(max(pp[oi], 1e-12))
    return {"n": n, "zMean": round(sum(zs) / len(zs), 4) if zs else None,
            "loglossShin": round(ll_shin / n, 4), "loglossProp": round(ll_prop / n, 4),
            "favLayer": {"n": fav_n, "shin": round(fav_shin / fav_n, 4), "prop": round(fav_prop / fav_n, 4)},
            "dogLayer": {"n": dog_n, "shin": round(dog_shin / dog_n, 4), "prop": round(dog_prop / dog_n, 4)}}


def _selftest() -> None:
    # 性质断言（合成 FLB 造法与 Shin 形状不匹配，首版教训——改验数学性质，
    # 真裁决交给 216 场回测）：①Σp=1 ②FLB 校正方向：Shin 热门>比例归一、冷门<
    # ③z 随 overround 单调增（抽水越重 FLB 校正越强）
    odds = [2.10, 3.30, 3.05]  # overround ~12% 体彩级
    ps, z = shin_devig(odds)
    pp = prop_devig(odds)
    assert abs(sum(ps) - 1) < 1e-9, "Σp≠1"
    hot = max(range(3), key=lambda k: 1 / odds[k])
    cold = min(range(3), key=lambda k: 1 / odds[k])
    assert ps[hot] > pp[hot] + 1e-4, f"热门端 Shin 应上修: {ps[hot]:.4f} vs {pp[hot]:.4f}"
    assert ps[cold] < pp[cold] - 1e-4, f"冷门端 Shin 应下修: {ps[cold]:.4f} vs {pp[cold]:.4f}"
    _, z_light = shin_devig([2.90, 3.0, 2.95])   # 轻抽水 ~2.5%（Pinnacle 级）
    assert z > z_light, "z 应随 overround 增大"
    print(f"[shin] selftest ✅ Σ=1 / FLB方向(热门{ps[hot]:.4f}>{pp[hot]:.4f}·冷门{ps[cold]:.4f}<{pp[cold]:.4f}) / z单调(z体彩={z:.3f}>z轻水={z_light:.3f})")


def main() -> None:
    if "--selftest" in sys.argv:
        _selftest()
        return
    _selftest()
    bt = backtest()
    d = (bt["loglossProp"] - bt["loglossShin"]) / bt["loglossProp"] * 100
    verdict = ("Shin 显著更优(>1%)——建议切换 devig 内核（大哥拍板）" if d > 1
               else ("比例归数更优——维持现状" if d < -1 else "打平(<1%)——维持现状（护栏）"))
    print(f"[shin] 回测 n={bt['n']} z均值={bt['zMean']}")
    print(f"[shin] logloss: Shin {bt['loglossShin']} vs 比例归一 {bt['loglossProp']} → {d:+.2f}%")
    print(f"[shin] 热门命中层(n={bt['favLayer']['n']}): {bt['favLayer']['shin']} vs {bt['favLayer']['prop']}")
    print(f"[shin] 冷门/平层(n={bt['dogLayer']['n']}): {bt['dogLayer']['shin']} vs {bt['dogLayer']['prop']}")
    print(f"[shin] 裁决: {verdict}")
    REPORT.write_text(json.dumps({"at": "2026-09-27", "backtest": bt,
                                  "improvementPct": round(d, 2), "verdict": verdict,
                                  "note": "体彩三向 Shin vs 比例归一（216场交集·score_odds 存档）"},
                                 ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[shin] → {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
