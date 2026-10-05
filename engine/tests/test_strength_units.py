# -*- coding: utf-8 -*-
"""实力链各步单元测试。开发者 sszhang"""
import pytest
import strength_loaders as sl
import paper_strength as ps

def _st(**kw):
    base = {"team": "t", "league": "test-lg", "elo": 1520.0, "xg_att": 1.8, "xg_def": 1.0,
            "n_xg": 10, "dc_att": 0.3, "dc_def": -0.2, "flags": []}
    base.update(kw)
    return base

def test_raw_strength_full_layers():
    att, df = ps.raw_strength(_st())
    assert att > 0 and df > 0                       # 攻正防正（本链口径：def正=强·DC def已翻号）

def test_raw_strength_degrade_no_xg():
    """降级：无xG → DC代理，flags 传递，输出仍有效。"""
    att, df = ps.raw_strength(_st(xg_att=None, xg_def=None, n_xg=0, flags=["no_xg"]))
    assert isinstance(att, float) and isinstance(df, float)

def test_raw_strength_layer_weights():
    """三层贡献都非零：xG 层与 DC 层权重各 0.4/0.4，Elo 同权 0.2（固定值·预注册舱纪律不调参）。"""
    st = _st()
    att, _ = ps.raw_strength(st)
    assert abs(att - (0.4 * (1.8 - 1.35) + 0.4 * st["dc_att"] + 0.2 * ps.elo_z(st))) < 1e-9

def test_shrink_weight_curve():
    assert ps.shrink_weight(0) == 0.0                    # 无实际数据=纯名气先验
    assert ps.shrink_weight(20) == pytest.approx(0.5)    # n=k → 各半
    assert ps.shrink_weight(100) == pytest.approx(100/120)

def test_devig_fame_monotone():
    """实际产出越多，名气（Elo虚高）权重越小：n_xg↑ → 收缩后的att对xG的敏感度↑。"""
    low = ps.devig_fame(_st(n_xg=2,  xg_att=2.5, elo=1600))
    high = ps.devig_fame(_st(n_xg=20, xg_att=2.5, elo=1600))
    baseline = ps.devig_fame(_st(n_xg=10, xg_att=1.35, elo=1500))   # 全中性
    # n大的版本对xG变化更敏感：low/high 到 baseline 的距离应 high 更远
    assert abs(high[0] - baseline[0]) >= abs(low[0] - baseline[0])

def test_devig_fame_no_xg_pure_elo():
    """n_xg=0 → w=0 → 完全退回 raw（名气先验兜底）。"""
    st = _st(n_xg=0, xg_att=None, xg_def=None, flags=["no_xg"])
    assert ps.devig_fame(st) == ps.raw_strength(st)
