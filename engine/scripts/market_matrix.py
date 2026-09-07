"""market_matrix: 体彩五池→市场比分分布参数(设计§三·P2).
分池幂次去水(alpha=侦察1选型) + DC参数(λm主,λm客,ρm)拟合复现五池价.
CRS池降级开关: 侦察1实测无冷热抽水扭曲(JSD 0.0004/0.0072), USE_CRS=True. 开发者 sszhang"""
import numpy as np
from scipy.optimize import minimize

from dc_predict import score_matrix

ALPHA_DEFAULT = 1.0        # 侦察1 ALPHA_SELECTED: 等比例去水最优(HAD边缘JSD中位0.0004)
USE_CRS = True             # 侦察1: CRS池不降级
FIT_ERR_GATE = 0.02        # 拟合误差门槛, 超出调用方降级 dc_only(Task 5 import此常量)
LAMBDA_BOUNDS = (0.2, 4.5) # 与 freq_band LAMBDA_CLAMP 同源
RHO_BOUNDS = (-0.2, 0.2)
W_CRS, W_HAD, W_TTG = 1.0, 1.0, 0.5   # 池权重: CRS信息最多, TTG边缘部分冗余
TTG_BINS = 8               # s0~s6 各一档 + s7=7+ 合并档(与 dc_predict.ttg_dist 同口径)
OUTCOME_OF_OTHER = {'胜其他': 'h', '平其他': 'd', '负其他': 'a'}


def devig_pool(odds, alpha=ALPHA_DEFAULT):
    """幂次去水: p_i ∝ (1/o_i)^alpha 归一(侦察1 water_probe 同式)."""
    w = {k: (1.0 / float(v)) ** alpha for k, v in odds.items()}
    s = sum(w.values())
    return {k: v / s for k, v in w.items()}


def _matrix_prices(lh, la, rho, crs_domain):
    """DC矩阵 → (crs_dist|None, hda_dist, ttg_dist) 与体彩池键对齐供loss比对.

    CRS口径: 体彩31项 = 主域"h:a"格 + 胜/平/负其他兜底. 主域=市场crs键集(自洽:
    矩阵生成价与市场价同一套聚合); 域外7x7格按h>a/h==a/a>h归对应兜底——(6,6)归
    平其他、4:3/5:3等超域主胜归胜其他. 7x7之外(双7+球)截断忽略(近似, docstring注明).
    ttg: s0~s6=总进球各档, s7=7+合并(与体彩s7=7+封顶对齐)."""
    mat = score_matrix(lh, la, rho)
    crs = None
    if crs_domain:
        crs = {}
        otherOfOutcome = {v: k for k, v in OUTCOME_OF_OTHER.items()}
        for h in range(7):
            for a in range(7):
                key = f"{h}:{a}"
                if key in crs_domain:
                    crs[key] = float(mat[h, a])
                else:
                    outcome = 'h' if h > a else ('d' if h == a else 'a')
                    bucket = otherOfOutcome[outcome]
                    crs[bucket] = crs.get(bucket, 0.0) + float(mat[h, a])
    hda = {'h': float(np.tril(mat, -1).sum()), 'd': float(np.trace(mat)),
           'a': float(np.triu(mat, 1).sum())}
    ttgBins = [0.0] * TTG_BINS
    for h in range(7):
        for a in range(7):
            ttgBins[min(h + a, TTG_BINS - 1)] += float(mat[h, a])
    ttg = {f"s{t}": ttgBins[t] for t in range(TTG_BINS)}
    return crs, hda, ttg


def fit_lambdas(crs, had, ttg, alpha=ALPHA_DEFAULT):
    """→ {'lh','la','rho','fit_err'}; Nelder-Mead, x0=联赛默认先验.

    fit_err=加权分池平均绝对误差(池内Σ|模型-市场|/对比键数): 三池键数悬殊
    (CRS 31/HAD 3/TTG 8)且真实市场五池非联合DC一致(CRS格级冷暖结构, 实测裸和
    L1全局最优~0.11), 分池均值使FIT_ERR_GATE语义=「平均每键模型-市场偏差<2pp」."""
    p_had = devig_pool(had, alpha)
    p_ttg = devig_pool(ttg, alpha)
    p_crs = devig_pool(crs, alpha) if (USE_CRS and crs) else None
    crs_domain = {k for k in p_crs if ':' in k} if p_crs else set()

    def loss(x):
        lh, la, rho = x
        if not (LAMBDA_BOUNDS[0] <= lh <= LAMBDA_BOUNDS[1] and
                LAMBDA_BOUNDS[0] <= la <= LAMBDA_BOUNDS[1]):
            return 1e3
        rho = max(RHO_BOUNDS[0], min(RHO_BOUNDS[1], rho))
        m_crs, m_hda, m_ttg = _matrix_prices(lh, la, rho, crs_domain)
        hadKeys = [k for k in m_hda if k in p_had]
        e = W_HAD * sum(abs(m_hda[k] - p_had[k]) for k in hadKeys) / max(1, len(hadKeys))
        ttgKeys = [k for k in p_ttg if k in m_ttg]
        e += W_TTG * sum(abs(m_ttg[k] - p_ttg[k]) for k in ttgKeys) / max(1, len(ttgKeys))
        if p_crs:
            crsKeys = [k for k in m_crs if k in p_crs]
            e += W_CRS * sum(abs(m_crs[k] - p_crs[k]) for k in crsKeys) / max(1, len(crsKeys))
        return e

    res = minimize(loss, x0=[1.3, 1.1, -0.05], method='Nelder-Mead',
                   options={'xatol': 1e-4, 'fatol': 1e-6, 'maxiter': 800})
    lh, la, rho = (float(res.x[0]), float(res.x[1]),
                   max(RHO_BOUNDS[0], min(RHO_BOUNDS[1], float(res.x[2]))))
    return {'lh': lh, 'la': la, 'rho': rho, 'fit_err': float(res.fun)}
