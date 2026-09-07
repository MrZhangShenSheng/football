"""score_engine 统一融合出口测试：dc_only降级链 / λ几何加权融合 / fit_err门 / 版本按联赛取。开发者 sszhang"""
import numpy as np
from score_engine import matrix, W_DC_INIT

def test_dc_only_when_no_market():
    out = matrix(1.5, 1.0, -0.05, mkt=None)
    assert out['source'] == 'dc_only'
    assert abs(out['matrix'].sum() - 1.0) < 1e-6
    assert out['lambda'] == {'lh': 1.5, 'la': 1.0}

def test_fused_lambda_geometric_weight():
    mkt = {'lh': 2.0, 'la': 0.5, 'rho': 0.0, 'fit_err': 0.001}
    out = matrix(1.0, 1.0, 0.0, mkt=mkt, w_dc=0.29)
    assert out['source'] == 'fused'
    wm = 1.0 - 0.29
    assert abs(out['lambda']['lh'] - 1.0**0.29 * 2.0**wm) < 1e-9
    assert abs(out['lambda']['la'] - 0.5**wm) < 1e-9

def test_market_fit_err_gate_falls_back():
    from score_engine import FIT_ERR_GATE
    mkt = {'lh': 2.0, 'la': 0.5, 'rho': 0.0, 'fit_err': FIT_ERR_GATE + 1}
    out = matrix(1.5, 1.0, -0.05, mkt=mkt)
    assert out['source'] == 'dc_only'

def test_latest_dc_version_by_league():
    from score_engine import _latest_dc_version
    v = _latest_dc_version('japan')   # latest.json 实测含 japan
    assert isinstance(v, int) and v >= 1
    assert _latest_dc_version('不存在的联赛') is None
