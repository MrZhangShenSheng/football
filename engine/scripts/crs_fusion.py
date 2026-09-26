# engine/scripts/crs_fusion.py
"""CRS 融合引擎（spec: docs/2026-09-26-crs-fusion-redesign.html v2）。

四组件：power 去水 / Lidstone 平滑 / 对数意见池融合 / 比分族输出。
哲学：q=先验 × 市场=似然 → 后验；三向层 fusion.json 方法论推广到 31 维。
开发者 sszhang"""
import re

CRS_KEY = re.compile(r"^(\d+):(\d+)$")

# 二分求 k 区间：体彩真实 CRS 池 Σ1/o 实测 1.17~1.35（抽水，score_odds 存档）→ k 解 >1；
# 低估水池（Σ<1，如玩具/返水场景）k<1。上界 5.0 为实测需求（k≈1.1~1.5）3 倍以上冗余。
K_BISECT_LO, K_BISECT_HI = 0.05, 5.0

def solve_power_k(implied: dict[tuple, float]) -> float:
    """解 Σ p_i^k = 1 的 k（二分法）。implied 为原始倒数赔率（Σ>1 含抽水）。"""
    lo, hi = K_BISECT_LO, K_BISECT_HI
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
    业务规则，不进融合代数（零吸收违背贝叶斯支撑集代数）。

    池外折叠（审查移交 Important 修复）：真实联赛模板含 6:0/5:3/0:6 等池外比分
    （占历史 1.4%），原实现只进分母不进分子 → Σ=0.941。现按胜负方向折叠进
    兜底代表键（h>a→(4,3) 胜其他 / h==a→(4,4) 平其他 / h<a→(3,4) 负其他，
    与体彩 CRS 池兜底项语义一致），使全 31 项 Σ=1 精确成立。非比分键
    （如 "__n"）不进分母——进分母必须在分子有对应。"""
    keys = CRS_POOL[:n_items]
    keyset = set(keys)
    folded = {}
    for s, c in counts.items():
        if s in keyset:
            folded[s] = folded.get(s, 0) + c
        elif (isinstance(s, tuple) and len(s) == 2
              and all(isinstance(x, int) for x in s)):
            rep = (4, 3) if s[0] > s[1] else ((4, 4) if s[0] == s[1] else (3, 4))
            if rep in keyset:          # 截断池无代表键时弃计数（n_items=31 全池无此路径）
                folded[rep] = folded.get(rep, 0) + c
    n = sum(folded.values())
    z = n + alpha * len(keys)
    return {s: (folded.get(s, 0) + alpha) / z for s in keys}


# ---- 对数意见池融合 + λ 收缩（spec §2/§3）----

R_LOG_POOL = 0.286      # r = a/(a+b) = 0.4/1.4（spec §3 弱点4 比值不变性；fusion_crs.json r 同源）
EPS_MARKET = 0.001      # 市场缺项 ε 兜底（spec §3）
W_LAMBDA_SHRINK = 0.35  # λ 收缩模型权重（spec §2 James-Stein；fusion_crs.json w 同源）

def fuse_crs(q_t: dict, p_mkt: dict, r: float = R_LOG_POOL, eps: float = EPS_MARKET) -> dict:
    """对数意见池：P_final ∝ q^r · p^(1-r)（spec §3；比值不变性→只搜 r 一维）。

    r = a/(a+b)，默认 0.286 = 0.4/1.4。市场缺项 ε 兜底。q_t 须已平滑（全正）。"""
    keys = set(q_t) | set(p_mkt)
    raw = {}
    for s in keys:
        qv = max(q_t.get(s, eps), 1e-12)
        pv = max(p_mkt.get(s, eps), 1e-12)
        raw[s] = qv ** r * pv ** (1.0 - r)
    z = sum(raw.values())
    return {s: v / z for s, v in raw.items()}

def shrink_lambda(lam_sum_model: float, e_mkt, w: float = W_LAMBDA_SHRINK):
    """λ 总量向市场 E 收缩（spec §2 James-Stein 精神）。

    返回 (λ'sum, shrunk)；e_mkt=None 时纯模型降级。"""
    if e_mkt is None or e_mkt <= 0:
        return lam_sum_model, False
    return w * lam_sum_model + (1.0 - w) * e_mkt, True


# ---- 比分族输出 + 闸门（spec §4）----

# 5 族（spec §4）：族概率=ΣP_final，守恒到 1−族外项
FAMILIES = {
    "home_clean":  [(1,0), (2,0), (3,0)],
    "home_multi":  [(2,1), (3,1), (3,2)],
    "draw":        [(0,0), (1,1), (2,2)],
    "away_clean":  [(0,1), (0,2), (0,3)],
    "away_multi":  [(1,2), (1,3), (2,3)],
}
FAMILY_GATE_THRESHOLD = 0.28   # spec §4 数学审查错误3：max族概率<28%→关档（fusion_crs.json familyGateThreshold 同源）

def family_scores(p_final: dict) -> list:
    out = []
    for name, members in FAMILIES.items():
        inside = {s: p_final[s] for s in members if s in p_final}
        if not inside:
            continue
        ranked = sorted(inside.items(), key=lambda kv: -kv[1])
        out.append({"family": name, "prob": sum(inside.values()),
                    "top1": ranked[0], "top2": ranked[1] if len(ranked) > 1 else None})
    return sorted(out, key=lambda f: -f["prob"])

def family_gate(p_final: dict, threshold: float = FAMILY_GATE_THRESHOLD):
    fams = family_scores(p_final)
    if not fams:
        return False, 0.0
    mx = fams[0]["prob"]
    return mx >= threshold, mx


# ---- 参数文件加载（spec §6；降级不熔断）----

import json as _json
from pathlib import Path

FUSION_CRS_PATH = Path(__file__).resolve().parent.parent / "cache" / "fusion_crs.json"   # engine/cache/fusion_crs.json
FUSION_CRS_DEFAULTS = {"frozenAt": "2026-09-26", "enabled": True, "r": 0.286, "w": 0.35,
                       "alphaLidstone": 0.5, "familyGateThreshold": 0.28, "hhadCedeThreshold": 0.65,
                       "leagueOverrides": None}

def load_fusion_crs() -> dict:
    """读融合参数；文件缺失/损坏 → 冻结默认值 + degraded 标记（降级不熔断）；
    文件在但缺键 → setdefault 全默认键兜底（Minor-4：调用方 cfg["w"] 等不 KeyError）。"""
    try:
        cfg = _json.loads(FUSION_CRS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {**FUSION_CRS_DEFAULTS, "degraded": True}
    for k, v in FUSION_CRS_DEFAULTS.items():
        cfg.setdefault(k, v)
    cfg.setdefault("degraded", False)
    return cfg
