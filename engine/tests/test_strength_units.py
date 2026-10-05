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

def test_hfa_shrink_to_league():
    """队样本<30 → 向联赛均值收缩；n=0 → 纯联赛值。"""
    assert ps.hfa_value(0.30, 0, None) == pytest.approx(0.30)
    mid = ps.hfa_value(0.30, 15, 0.50)                 # 半收缩
    assert 0.30 < mid < 0.50
    assert ps.hfa_value(0.30, 30, 0.50) == pytest.approx(0.50)   # 达门槛用队值

def test_hfa_none_team_obs():
    assert ps.hfa_value(0.30, 200, None) == pytest.approx(0.30)  # 无观测再多样本也是联赛值

def test_league_offsets_min_bridge():
    """同一联赛对 ≥30 场才出 offset；不足 → 键在 'uncalibrated' 列表。"""
    rows = [{"homeLeague": "lgA", "awayLeague": "lgB", "hg": 2, "ag": 1,
             "lamH0": 1.5, "lamA0": 1.5}] * 30        # 模型无偏时 offset≈0（exp=obs=3·pre-flight裁定值）
    out = ps.league_offsets(rows, min_bridge=30)
    assert abs(out["offsets"]["lgA|lgB"]) < 0.05

def test_league_offsets_uncalibrated():
    rows = [{"homeLeague": "lgA", "awayLeague": "lgC", "hg": 1, "ag": 1, "lamH0": 1.4, "lamA0": 1.3}] * 10
    out = ps.league_offsets(rows, min_bridge=30)
    assert "lgA|lgC" in out["uncalibrated"]

def test_league_offsets_detects_inflation():
    """lgA 球队进攻被系统性高估（实际总进球恒高于模型）→ offset 为正（校准方向正确）。"""
    rows = [{"homeLeague": "lgA", "awayLeague": "lgB", "hg": 3, "ag": 2, "lamH0": 1.0, "lamA0": 1.0}] * 30
    out = ps.league_offsets(rows, min_bridge=30)
    assert out["offsets"]["lgA|lgB"] > 0.1

def test_compare_symmetry_and_hfa():
    c = ps.compare(1.0, 0.5, 0.5, 0.4, hfa=0.3, env=2.7)
    assert c["d_att"] == pytest.approx(1.0 + 0.15 - 0.4)     # att_h + hfa/2 - def_a
    assert c["d_def"] == pytest.approx(0.5 - 0.15 - 0.5)     # att_a - hfa/2 - def_h
    assert c["T"] == 2.7
    # 主客互换+同HFA → 攻防通道交换且平移一个hfa：d_att'=d_def+hfa、d_def'=d_att-hfa
    # （brief原断言-d_def/-d_att是测试假红·Task8 λ_a=exp(d_def)钉死d_def=客攻击通道口径·现场裁定改断言留实现）
    c2 = ps.compare(0.5, 0.4, 1.0, 0.5, hfa=0.3, env=2.7)
    assert c2["d_att"] == pytest.approx(c["d_def"] + 0.3)
    assert c2["d_def"] == pytest.approx(c["d_att"] - 0.3)
