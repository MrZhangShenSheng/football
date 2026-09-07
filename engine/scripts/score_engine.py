"""score_engine: 统一比分分布出口(设计§三).
λ空间log加权融合(市场主导) → 7×7矩阵. source=fused|dc_only, 全程带dcVersion(防对照期版本混杂·设计§七). 开发者 sszhang"""
import json
import math
import dc_predict
from market_matrix import FIT_ERR_GATE   # 与拟合侧同源门槛(勿复写字面量)

W_DC_INIT = 0.29   # 初始启发式: HAD侧0.4/1.0归一移植, 段A回测重标定(设计§三权重声明)

def _latest_dc_version(league):
    """latest.json = {联赛slug: int版本} → 按联赛键取; 读不到返回None. 开发者 sszhang"""
    if not league:
        return None
    try:
        with open('engine/cache/models/latest.json', encoding='utf-8') as f:
            latest = json.load(f)
        v = latest.get(league)
        return v if isinstance(v, int) else None
    except (OSError, json.JSONDecodeError):
        return None

def matrix(lh_dc, la_dc, rho_dc, mkt=None, w_dc=W_DC_INIT, dc_version=None):
    """统一出口: mkt=None或fit_err超门槛 → dc_only降级(设计§三降级链)."""
    if mkt is None or mkt.get('fit_err', FIT_ERR_GATE) > FIT_ERR_GATE:
        return {'matrix': dc_predict.score_matrix(lh_dc, la_dc, rho_dc),
                'source': 'dc_only', 'lambda': {'lh': lh_dc, 'la': la_dc},
                'w_dc': 0.0, 'dc_version': dc_version}
    w_m = 1.0 - w_dc
    lh = math.exp(w_dc * math.log(lh_dc) + w_m * math.log(mkt['lh']))
    la = math.exp(w_dc * math.log(la_dc) + w_m * math.log(mkt['la']))
    rho = mkt.get('rho', rho_dc)
    return {'matrix': dc_predict.score_matrix(lh, la, rho),
            'source': 'fused', 'lambda': {'lh': lh, 'la': la},
            'w_dc': w_dc, 'dc_version': dc_version}
