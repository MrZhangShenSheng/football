# engine/scripts/crs_fusion.py
"""CRS 融合引擎（spec: docs/2026-09-26-crs-fusion-redesign.html v2）。

四组件：power 去水 / Lidstone 平滑 / 对数意见池融合 / 比分族输出。
哲学：q=先验 × 市场=似然 → 后验；三向层 fusion.json 方法论推广到 31 维。
开发者 sszhang"""
import re

CRS_KEY = re.compile(r"^(\d+):(\d+)$")

def solve_power_k(implied: dict[tuple, float]) -> float:
    """解 Σ p_i^k = 1 的 k（二分法）。implied 为原始倒数赔率（Σ>1 含抽水）。"""
    lo, hi = 0.05, 1.0
    def over(k):
        return sum(v ** k for v in implied.values()) - 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if over(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2

def extract_mkt_dist(crs_odds: dict) -> dict[tuple[int, int], float]:
    """体彩 CRS 31 项赔率 → power 去水市场分布（spec §3, Clarke 2013）。

    p_mkt(s) ∝ (1/o_s)^k，k 解 Σp=1——高抽水 CRS 池优于朴素归一（抬长赔尾部）。
    无效项（0/非数字/格式错）静默跳过。"""
    implied = {}
    for k, v in crs_odds.items():
        m = CRS_KEY.match(str(k))
        try:
            w = 1.0 / float(v)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if m and w > 0:
            implied[(int(m.group(1)), int(m.group(2)))] = w
    if not implied:
        return {}
    k_exp = solve_power_k(implied)
    raw = {s: w ** k_exp for s, w in implied.items()}
    z = sum(raw.values())
    return {s: p / z for s, p in raw.items()}
