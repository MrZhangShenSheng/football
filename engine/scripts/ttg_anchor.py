# engine/scripts/ttg_anchor.py
"""TTG 锚定（2026-10-14 低分偏置修正 T6·大哥裁决 C 方案）。

分维度锚定：
- TTG 维度（总进球水平）锚定市场——该维度实测系统性偏低 −0.78 球、14 场无一偏高，
  是确证缺陷，无条件锚。
- 比分形状维度（同总进球内的主客分配）完全不锚——该维度无偏差证据，
  是独立信息来源（高比分主胜等冷门格子正来自此维度）。

机制：p'(s) ∝ p(s)·exp(k·t(s))，t(s)=该比分总进球，k 由二分法解出使期望命中目标。
同总进球格子间相对比例守恒（形状不变），k=0 退化为原分布（零破坏）。
设计文档：docs/plans/2026-10-14-crs-low-score-bias-design.md §三.3
开发者 sszhang
"""
import math

K_RANGE = (-2.0, 2.0)   # 二分域：覆盖期望 0.5~8 球
K_ITERS = 80            # 二分精度远超 1e-6 容差


def _total(score: str) -> int:
    h, a = (int(x) for x in score.split(":"))
    return h + a


def dist_expect(dist: dict) -> float:
    """分布期望总进球（分布无需预归一——按质量和加权）。"""
    z = sum(dist.values())
    return sum(p * _total(s) for s, p in dist.items()) / z if z else 0.0


def tilt_dist(dist: dict, k: float) -> dict:
    """按总进球指数倾斜并归一。k=0 → 仅归一化，形状与水平均不变。"""
    w = {s: p * math.exp(k * _total(s)) for s, p in dist.items()}
    z = sum(w.values())
    return {s: v / z for s, v in w.items()} if z else {}


def solve_tilt(dist: dict, target: float) -> float:
    """解 k 使倾斜后期望 = target。期望随 k 单调递增，二分必收敛。

    target 超出倾斜可达范围时夹在域端——调用方应对 k 贴端点的情况警惕
    （盘面异常或目标荒谬），anchor_to_market 会原样返回供检查。"""
    lo, hi = K_RANGE
    if dist_expect(tilt_dist(dist, hi)) < target:
        return hi
    if dist_expect(tilt_dist(dist, lo)) > target:
        return lo
    for _ in range(K_ITERS):
        mid = (lo + hi) / 2
        if dist_expect(tilt_dist(dist, mid)) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def market_ttg_expect(ttg_odds: dict) -> float | None:
    """体彩 TTG 赔率 {s0: 22.0, ...} → 去水期望总进球（s7 按 7 球计）。

    设计判据④：市场锚=体彩 TTG 去水（fd 断粮期无 Pinnacle 可用，全场统一口径）。"""
    if not ttg_odds:
        return None
    try:
        inv = {k: 1.0 / float(v) for k, v in ttg_odds.items() if float(v) > 1}
    except (TypeError, ValueError):
        return None
    if len(inv) < 3:
        return None
    z = sum(inv.values())
    return sum((7 if k == "s7" else int(k[1:])) * w / z for k, w in inv.items())


def anchor_to_market(dist: dict, ttg_odds: dict) -> dict:
    """池/模板分布 → TTG 锚定市场后的分布。

    返回 {dist: 新分布, k: 倾斜系数, before: 锚前期望, target: 市场}。
    市场不可得（无 ttg 赔率）时原样返回（仅归一），k=0——锚定是增益不是依赖。"""
    z = sum(dist.values())
    base = {s: p / z for s, p in dist.items()} if z else {}
    target = market_ttg_expect(ttg_odds)
    if base and target:
        k = solve_tilt(base, target)
        return {"dist": tilt_dist(base, k), "k": k,
                "before": dist_expect(base), "target": target}
    return {"dist": base, "k": 0.0, "before": dist_expect(base) if base else 0.0,
            "target": target}
