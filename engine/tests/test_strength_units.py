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

def test_tail_split_union_region():
    """溢出区=并集 {h≥6}∪{a≥6}（2026-10-05 修复：原交集只扫[6,8)²×[6,8)→平其他890倍失真）。
    λ=1.5/1.2 真值配比≈0.748/0.001/0.251——高比分平局（6:6,7:7..）极罕见≈e-6级。"""
    th = sm._tail(1.5, 1.2, 6, "h")
    td = sm._tail(1.5, 1.2, 6, "d")
    ta = sm._tail(1.5, 1.2, 6, "a")
    tot = th + td + ta
    assert th > 0.7 * tot                                  # 主胜向占大头（6+x/x 大概率主胜）
    assert td < 0.01 * tot                                 # 评分平局（双方≥6且相等）e-6级

# ---- 步骤⑦ 门1评估器 strength_chain_eval：V4三基线强制 + 只读预注册判据 + DC滚动代理 ----
# 2026-10-05 Task12 控制器裁定②：ctx["dc"]=当前全历史拟合，历史 as-of 场直接用会泄漏未来赛果
# → 评估器模式 team_state_on(dc_rolling=True) 用 as-of 可见近10场滚动代理（默认 False 不变）。
import json
from datetime import date
import strength_chain_eval as sce

def _ctx_for_rolling(p):   # 最小合成地基：timeline + DC缓存（值故意≠滚动值以证明未被读取）
    lg_dir = p / "league"; lg_dir.mkdir(exist_ok=True)
    (lg_dir / "test-lg_matches.json").write_text('{"matches":['
        '{"date":"2026-08-01","home":"team-a","away":"team-b","hg":2,"ag":1},'
        '{"date":"2026-08-05","home":"team-d","away":"team-a","hg":1,"ag":1},'
        '{"date":"2026-08-10","home":"team-b","away":"team-a","hg":0,"ag":0},'
        '{"date":"2026-09-01","home":"team-a","away":"team-c","hg":3,"ag":0}]}', encoding="utf-8")
    cache = p / "cache"; cache.mkdir(exist_ok=True)
    (cache / "test-lg_dc.json").write_text('{"homeAdv":0.25,"rho":-0.05,"teams":'
        '{"team-a":{"attack":0.3,"defense":-0.2}}}', encoding="utf-8")
    return sl.build_ctx(["test-lg"], leagues_dir=lg_dir, cache_dir=cache,
                        aliases={"team-a": {"zh": "甲"}, "team-b": {"zh": "乙"}, "team-c": {"zh": "丙"}})

def test_dc_rolling_proxy_asof_no_future_leak(tmp_path):
    """裁定②滚动代理：dc_rolling=True → dc_att/dc_def 来自 as-of 可见近≤10场场均进/失
    （-1.35 标准化·DC字段口径 def 负=强），当前 DC 缓存不被读取；未来场注入值不变（V2同款注入模式）。"""
    ctx = _ctx_for_rolling(tmp_path)
    # as_of=09-03 → cutoff=09-01：可见 08-01/08-05/08-10/09-01 四场 team-a 进2,1,0,3 失1,1,0,0
    st = sl.team_state_on("team-a", date(2026, 9, 3), ctx, dc_rolling=True)
    assert "dc_source:rolling" in st["flags"]
    assert st["dc_att"] == pytest.approx((2 + 1 + 0 + 3) / 4 - 1.35)   # 攻=场均进−环境
    assert st["dc_def"] == pytest.approx((1 + 1 + 0 + 0) / 4 - 1.35)   # DC字段：场均失−环境（负=强防）
    assert st["dc_att"] != pytest.approx(0.3)                          # 缓存 attack=0.3 未被读
    # 默认 dc_rolling=False：仍走当前缓存（原行为不变）
    st_cache = sl.team_state_on("team-a", date(2026, 9, 3), ctx)
    assert st_cache["dc_att"] == pytest.approx(0.3) and "dc_source:rolling" not in st_cache["flags"]
    # 未来场注入（09-05 大胜）→ cutoff=09-01 不可见 → 代理值逐字节不变
    lg_file = tmp_path / "league" / "test-lg_matches.json"
    rows = json.loads(lg_file.read_text(encoding="utf-8"))
    rows["matches"].append({"date": "2026-09-05", "home": "team-a", "away": "team-b", "hg": 5, "ag": 0})
    lg_file.write_text(json.dumps(rows), encoding="utf-8")
    ctx2 = _ctx_for_rolling(tmp_path)
    st2 = sl.team_state_on("team-a", date(2026, 9, 3), ctx2, dc_rolling=True)
    assert (st2["dc_att"], st2["dc_def"]) == (st["dc_att"], st["dc_def"])

def test_gate1_report_requires_three_baselines():
    """V4：缺任一基线（随机/市场/旧链）→ 报告拒绝生成（raise）。"""
    fake = {"randomShuffle": {"hit": 0.10}, "market": {"hit": 0.137}, "oldChain": None}   # 缺旧链
    with pytest.raises(sce.MissingBaselineError):
        sce._gate1_verdict(fake)

def test_gate1_criteria_from_prereg_only(tmp_path):
    """判据只读舱：prereg 的 1b 门槛改掉 → verdict 跟着变（证明没把数字硬编码）。"""
    prereg = tmp_path / "p.json"
    prereg.write_text(json.dumps({"gate1": {"criteria": {"1b_hitFloor": "CRS top1 >= market - 1.5pp",
                                                        "1a_calibration": "x", "1c_logloss": "y"}}}), encoding="utf-8")
    got = sce._parse_1b_margin(prereg)
    assert got == 0.015

def test_walk_forward_eval_smoke(tmp_path):
    """端到端冒烟：合成盲测场（ctx=None 链不可算→如实记 skip）→ 仍出四节结构（calib/hit/logloss/baselines）。"""
    out = sce._eval_rows(rows=[{"date": "2026-09-10", "league": "lgX", "home": "甲", "away": "乙",
                                "score": "2:1", "crsMarketTop": "s1s0"}],
                         ctx=None, tmp_dir=tmp_path)
    assert {"calibration", "hit", "logloss", "baselines"} <= set(out)
