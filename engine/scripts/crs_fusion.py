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


# ---- Lidstone 平滑（spec §3 数学审查错误1修正）----

# 体彩 CRS 池实际 31 项（实测键集：engine/cache/score_odds/2026-09-25.json）：
# 28 个数值挂牌比分（主胜 12 + 平 4 + 客胜 12）+ 3 个兜底项（胜其他/平其他/负其他，非 "h:a" 数值键）。
# 裁决备注：裁决生成式 [(h,a) for h in range(6) for a in range(6) if h+a<=5] 实为 21 项（三角数 T(6)=21），
# 且缺 7 个真实挂牌比分（3:3/4:2/5:1/5:2/2:4/1:5/2:5）——按裁决主诉求"显式枚举实际 31 项并验证 len==31"执行。
# 兜底 3 项在元组键空间的规范代表：胜其他→(4,3)、平其他→(4,4)、负其他→(3,4)（各自最小未挂牌比分）；
# 市场侧 extract_mkt_dist 正则天然无此键 → 融合时 ε 兜底，代表键仅保证先验支撑集覆盖全池不吸收。
CRS_POOL = (
    tuple((h, a) for h in range(3) for a in range(6))   # 0:x / 1:x / 2:x 全列（官方挂牌 18 项）
    + ((3, 0), (3, 1), (3, 2), (3, 3))                  # 3:x 挂牌至 3:3
    + ((4, 0), (4, 1), (4, 2))                          # 4:x 挂牌至 4:2
    + ((5, 0), (5, 1), (5, 2))                          # 5:x 挂牌至 5:2
    + ((4, 3), (4, 4), (3, 4))                          # 胜其他/平其他/负其他 规范代表
)
N_CRS_POOL = 31   # 体彩 CRS 池挂牌总数（存档实测 2026-09-25）
assert len(CRS_POOL) == N_CRS_POOL   # 裁决要求验证 len==31

ALPHA_LIDSTONE = 0.5   # Jeffreys 先验（spec §3；fusion_crs.json alphaLidstone 同源）

def smooth_template(counts: dict, alpha: float = ALPHA_LIDSTONE, n_items: int = N_CRS_POOL) -> dict:
    """联赛模板计数 → Lidstone 平滑分布。

    q_t(s) = (count_s + α)/(N + 31α)——全 31 项>0，"c=0 永不入选"降级为输出层
    业务规则，不进融合代数（零吸收违背贝叶斯支撑集代数）。"""
    keys = CRS_POOL[:n_items]
    n = sum(counts.values())
    z = n + alpha * len(keys)
    return {s: (counts.get(s, 0) + alpha) / z for s in keys}
