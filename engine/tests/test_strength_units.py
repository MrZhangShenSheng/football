# -*- coding: utf-8 -*-
"""实力链各步单元测试。开发者 sszhang"""
import pytest
import strength_loaders as sl
import paper_strength as ps
import lambda_bridge as lb

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

def test_main_player_count():
    inj = [{"name": "a", "apps": 8, "starts": 7}, {"name": "b", "apps": 2, "starts": 1},
           {"name": "c", "apps": 6, "starts": 0}]
    assert lb.main_player_count(inj) == 2                     # apps>=5 记主力

def test_injury_multiplier_direction():
    assert lb.injury_multiplier(-2, beta=0.05) > 1.0          # 客队更缺主力 → 主λ上调
    assert lb.injury_multiplier(+2, beta=0.05) < 1.0
    assert lb.injury_multiplier(0, beta=0.05) == 1.0

def test_lambdas_accounting():
    c = {"d_att": 0.5, "d_def": -0.3, "T": 2.7}
    out = lb.lambdas(c, inj_h=1, inj_a=3, beta=0.05)
    assert out["lam_h"] > 0 and out["lam_a"] > 0
    assert set(out["contrib"]) == {"base", "hfa", "injury"}    # 三行账

def test_lambdas_missing_injury_neutral():
    c = {"d_att": 0.0, "d_def": 0.0, "T": 2.7}
    out = lb.lambdas(c, inj_h=None, inj_a=None, beta=0.05)
    assert out["contrib"]["injury"] == "neutral(no-data)"      # 缺数据=乘子1+标注

def test_fit_beta_moment():
    """矩估计回归：构造 Δ主力 与 ln(实际/期望) 成正比的合成样本 → β 恢复出真值±容差。"""
    import math
    beta_true = 0.06
    samples = [{"delta_main": d, "log_ratio": -beta_true * d + 0.01 * ((d % 3) - 1)}
               for d in range(-4, 5) for _ in range(20)]
    assert abs(lb.fit_beta(samples) - beta_true) < 0.02

# ---- 步骤⑤ score_matrix：DC修正比分矩阵 + TTG/HAD 出口 ----
# 现场裁定（2026-10-05 Task9）：计划文"31格"=投注页展示数；体彩 API/池键实测=39
# （36具体 s{i:02d}s{j:02d} + 胜/平/负其他 s1sh/s1sd/s1sa，见 sporttery_fetch.CRS_KEYS）。
# 键零填充 6 字符 → h=int(k[1:3]), a=int(k[4:6]) 解析成立（brief 原 's{h}s{a}' 单字符键
# 配双字符切片的系统性解析 bug 随键格式一并修正）。
import score_matrix as sm

def test_matrix_normalized_39_cells_crs_keys():
    """体彩 CRS 口径=39键，键集与 sporttery_fetch.CRS_KEYS 逐键对齐，概率和=1。"""
    m = sm.dc_matrix(1.5, 1.2, rho=-0.05)
    assert len(m) == 39
    assert abs(sum(m.values()) - 1.0) < 1e-6
    from sporttery_fetch import CRS_KEYS
    assert set(m) == set(CRS_KEYS)                            # 具体格+三其他逐键一致

def test_ttg_had_normalized_and_consistent():
    m = sm.dc_matrix(1.5, 1.2, rho=-0.05)
    ttg, had = sm.ttg_from(m), sm.had_from(m)
    assert abs(sum(ttg.values()) - 1.0) < 1e-6
    assert abs(sum(had.values()) - 1.0) < 1e-6
    assert set(ttg) == {f"s{i}" for i in range(9)}            # 9档 s0..s8（s8=8+开桶·三其他归此）
    assert ttg["s0"] == pytest.approx(m["s00s00"])            # TTG=0 档 = 0:0 矩阵元

def test_matrix_recovers_lambda():
    """已知λ恢复：大样本频率≈λ（Poisson机理自检·仅具体格·零填充两位可解析）。"""
    m = sm.dc_matrix(1.5, 1.2, rho=0.0)
    mean_h = sum(int(k[1:3]) * v for k, v in m.items()
                 if k[1:3].isdigit() and k[4:6].isdigit())
    assert 1.2 < mean_h < 1.8                                 # 逼近1.5（含其他档截断容差）

def test_rho_shifts_low_draws():
    """ρ<0（DC修正）→ 0:0/1:1 相对 ρ=0 抬高（低平局修正方向）。"""
    m0, mr = sm.dc_matrix(1.5, 1.2, rho=0.0), sm.dc_matrix(1.5, 1.2, rho=-0.05)
    assert mr["s00s00"] > m0["s00s00"] and mr["s01s01"] > m0["s01s01"]
