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

def test_latest_dc_version_cwd_independent(monkeypatch, tmp_path):
    # 终审Fix3回归: latest.json 走 ROOT 绝对路径常量, CWD 漂移(根目录外执行)仍可读;
    # 旧相对路径 'engine/cache/models/latest.json' 漂移后 OSError→None
    import common
    import score_engine
    assert score_engine.MODELS_LATEST_JSON.is_absolute()
    assert score_engine.MODELS_LATEST_JSON == common.ROOT / "engine" / "cache" / "models" / "latest.json"
    latest = tmp_path / "latest.json"
    latest.write_text('{"japan": 7}', encoding="utf-8")
    monkeypatch.setattr(score_engine, "MODELS_LATEST_JSON", latest)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    from score_engine import _latest_dc_version
    assert _latest_dc_version('japan') == 7
    assert _latest_dc_version('no-such-league') is None
