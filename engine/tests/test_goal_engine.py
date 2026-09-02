"""goal_engine 单测：T3 特征层（滚动构造/收缩/开季降权）+ T4 矩阵层（λ乘子/DC矩阵重建）。"""
import json
import math

import numpy as np
import pytest

from goal_engine import (build_history, crs_rank, early_weight, lambda_from_dc, lambda_mult,
                         league_sides, match_features, pad_rate, score_matrix, ttg_probs)

M = lambda h, a, hg, ag: {"home": h, "away": a, "fthg": hg, "ftag": ag}


def test_pad_rate_shrinks_to_league():
    """小样本收缩：(队累计 + 联赛均值×k) / (n+k)，k=2。"""
    # 1 场进 0 球的队：先验 k=2 拉向联赛均值 1.5 → (0+3.0)/(1+2)=1.0
    assert abs(pad_rate(0, 1, 1.5) - 1.0) < 1e-9


def test_league_sides():
    """联赛两侧进球基线 = 各自主客场场均。"""
    ms = [M("A", "B", 2, 0), M("B", "A", 1, 1)]
    s = league_sides(ms)
    assert s["home_rate"] == 1.5 and s["away_rate"] == 0.5


def test_match_features_symmetric_and_clean():
    """特征只用历史场（该场之前），主客分侧；失球基准=对侧产出（home_def↔away_rate，away_def↔home_rate）。"""
    hist, sides = build_history([M("A", "C", 3, 0), M("D", "A", 1, 1), M("C", "B", 2, 2)])
    f = match_features(hist, sides, "A", "B")
    # A 近5主场场均 = pad(3, 1, home_rate=2.0) = (3+2.0*2)/3；ratio = pad/home_rate
    assert abs(f["home_att"] - (3 + 2.0 * 2) / 3 / 2.0) < 1e-9
    # B 近5客场失球 = pad(2, 1, home_rate=2.0)（失球基准=对侧产出：客场失球 ↔ 主队进球）
    # home_rate = (3+1+2)/3 = 2.0 → pad=(2+2.0*2)/3=2.0 → ratio = 2.0/2.0 = 1.0（B 恰为联赛均值防守→中性）
    assert abs(f["away_def"] - (2 + 2.0 * 2) / 3 / 2.0) < 1e-9


def test_early_season_weight():
    """开季降权：此前 <2 场 ×0.3，≥2 场恢复 1.0。"""
    assert early_weight(0) == 0.3 and early_weight(1) == 0.3 and early_weight(2) == 1.0


def test_side_rate_zero_guard():
    """基准不可信护栏：side_rate<=0 → ratio=1.0 不修正（揭幕战 1:0：away_rate=0，除零禁手）。"""
    hist, sides = build_history([M("X", "B", 1, 0)])
    f = match_features(hist, sides, "C", "B")
    # B 的 away_gf 簿非空（=[0]）但 away_rate=0 → 走护栏而非除零/负收缩
    assert f["away_att"] == 1.0 and f["away_def"] == 1.0


def test_window_truncates_to_last5():
    """窗口截断：账本 8 场只算近 5（sum=5, n=5），前 3 场不进特征。"""
    hist = {"home_gf": {"A": [2, 2, 2, 2, 2, 0, 0, 0]},
            "home_ga": {}, "away_gf": {}, "away_ga": {}}
    sides = {"home_rate": 2.0, "away_rate": 1.0}
    f = match_features(hist, sides, "A", "B")
    # 末5 = [2,2,0,0,0] → sum=4，pad(4,5,2.0)=(4+4)/7；不截断则 sum=10 → (10+4)/7=1.0，两者可辨
    assert abs(f["home_att"] - (4 + 2.0 * 2) / 7 / 2.0) < 1e-9


def test_empty_books_ratio_neutral():
    """空簿 ratio=1.0（None 哨兵语义锁定）：无任何历史 → 四 ratio 全中性不修正。"""
    hist = {"home_gf": {}, "home_ga": {}, "away_gf": {}, "away_ga": {}}
    f = match_features(hist, {"home_rate": 1.5, "away_rate": 1.0}, "A", "B")
    for key in ("home_att", "home_def", "away_att", "away_def"):
        assert f[key] == 1.0, key
    assert f["w_home"] == 0.3 and f["w_away"] == 0.3


def test_typical_league_all_neutral():
    """典型联赛全中性探针：每簿近5场均值恰=基准 → 四 ratio 全 ≈1.0。
    同时锁定 def 分母侧（错侧会得 1.0/1.4≈0.714 炸此断言）。"""
    pattern = [1, 2, 2, 1, 1] * 4          # 主队进球周期：和 7/5 场 → 均值 1.4；近5和恰=5×1.4
    ms = [M("A", "B", pattern[i], 1) for i in range(20)]
    ms += [M("B", "A", pattern[i], 1) for i in range(20)]
    hist, sides = build_history(ms)
    assert abs(sides["home_rate"] - 1.4) < 1e-9 and sides["away_rate"] == 1.0
    f = match_features(hist, sides, "A", "B")
    for key in ("home_att", "home_def", "away_att", "away_def"):
        assert abs(f[key] - 1.0) < 1e-6, key


# ---------- T4 矩阵层 ----------

DC = {"teams": {"A": {"attack": 0.30, "defense": -0.10}, "B": {"attack": 0.10, "defense": -0.20}},
      "homeAdv": 0.25, "rho": -0.05}
F1X = {"home_att": 1.0, "home_def": 1.0, "away_att": 1.0, "away_def": 1.0, "w_home": 1.0, "w_away": 1.0}


def test_lambda_mult_identity_when_no_signal():
    """全中性特征 → 乘子恒等 1.0（无信号不扰动 λ_DC）。"""
    assert abs(lambda_mult(F1X, side="home") - 1.0) < 1e-12


def test_lambda_mult_direction():
    """主攻强+客防烂 → λ_home 放大（exp(β·log att + β·log def)）。"""
    f = dict(F1X, home_att=1.5, away_def=1.2)          # 主攻强+客防烂 → λ_home 放大
    m = lambda_mult(f, side="home")
    assert m > 1.0 and abs(m - math.exp(0.3 * math.log(1.5) + 0.3 * math.log(1.2))) < 1e-12


def test_lambda_mult_disable_and_early_weight():
    """att 消融=不修正；开季权重只作用于它该作用的项（w_home=0.3 → att 项 ×0.3）。"""
    f = dict(F1X, home_att=1.5, w_home=0.3)
    assert abs(lambda_mult(f, side="home", disable=("att",)) - 1.0) < 1e-12
    assert abs(lambda_mult(f, side="home") - math.exp(0.09 * math.log(1.5))) < 1e-12


def test_lambda_mult_shrink_disable_and_cross_weight():
    # shrink 消融=两权重恒1；逐项加权：def 项用守方（对侧）权重 w_away
    f = dict(F1X, home_att=1.5, away_def=1.4, w_home=0.3, w_away=1.0)
    m_norm = lambda_mult(f, side="home")
    m_noshrink = lambda_mult(f, side="home", disable=("shrink",))
    assert abs(m_noshrink - math.exp(0.3 * math.log(1.5) + 0.3 * math.log(1.4))) < 1e-12
    assert abs(m_norm - math.exp(0.09 * math.log(1.5) + 0.3 * math.log(1.4))) < 1e-12


def test_score_matrix_normalized_and_ttg():
    """7×7 矩阵归一非负；TTG 13 桶（0..12，i+j 最大 12）和=1；CRS 49 项降序。"""
    m = score_matrix(1.5, 1.1, -0.05)
    assert abs(m.sum() - 1.0) < 1e-9 and m.shape == (7, 7) and (m >= 0).all()
    tp = ttg_probs(m)
    assert abs(sum(tp) - 1.0) < 1e-9 and len(tp) == 13
    rk = crs_rank(m)
    assert len(rk) == 49 and rk[0][1] >= rk[1][1]


def test_lambda_from_dc():
    """DC 参数 → λ：λ_home=exp(att_h + def_a + homeAdv)，λ_away=exp(att_a + def_h)。"""
    lh, la = lambda_from_dc(DC, "A", "B")
    assert abs(lh - math.exp(0.30 - 0.20 + 0.25)) < 1e-12 and abs(la - math.exp(0.10 - 0.10)) < 1e-12


def test_lambda_from_dc_missing_team():
    """缺队返回 None（调用侧降级：不修正直接用 λ_DC 基线）。"""
    assert lambda_from_dc(DC, "A", "NOPE") is None and lambda_from_dc(DC, "NOPE", "B") is None


def test_lambda_mult_away_symmetry():
    """away 对称分支：att=away_att×w_away、def=home_def×w_home（与 home 侧 cross-weight 探针配对）。"""
    f = dict(F1X, away_att=1.5, home_def=1.2, w_away=0.3)
    assert abs(lambda_mult(f, side="away") - math.exp(0.09 * math.log(1.5) + 0.3 * math.log(1.2))) < 1e-12


def test_lambda_mult_disable_typo_raises():
    """消融 typo 防呆：未知 disable 键抛 ValueError——防 'at'/'deff' 静默全量产出假零增益冤杀因子。"""
    with pytest.raises(ValueError):
        lambda_mult(F1X, side="home", disable=("at",))
    with pytest.raises(ValueError):
        lambda_mult(F1X, side="away", disable=("att", "deff"))


def test_ttg_crs_placement():
    """构造性落位探针：2-1 与 0-3 同落 3 球桶；主客对调/下标错位（tp[3]≠1 或 1-2 居首）必炸。"""
    m = np.zeros((7, 7)); m[2, 1] = 0.6; m[0, 3] = 0.4
    tp = ttg_probs(m)
    assert abs(tp[3] - 1.0) < 1e-12 and sum(x for i, x in enumerate(tp) if i != 3) == 0.0
    assert crs_rank(m)[0] == ("2-1", 0.6)


# ---------- T5 对照统计 + 消融 ----------

from goal_engine import (LINES, METRIC_KEYS, _new_counter, bump, bump_naive,
                         evaluate_league, load_odds_matches)


def test_load_odds_matches(tmp_path, monkeypatch):
    """odds 缓存解析：str→int 转换；fthg None（未赛）/空串（脏行）跳过；文件缺失静默空。"""
    monkeypatch.setattr("goal_engine.CACHE_DIR", tmp_path)
    raw = {"fetchedAt": "2026-09-01", "source": "t", "season": "2526", "matches": [
        {"home": "A", "away": "B", "fthg": "2", "ftag": "1"},
        {"home": "C", "away": "D", "fthg": None, "ftag": None},      # 未赛跳过
        {"home": "E", "away": "F", "fthg": "", "ftag": "0"},         # 空串 int 失败跳过
        {"home": "G", "away": "H", "fthg": "0", "ftag": "0"},
    ]}
    (tmp_path / "odds_testlg_2526.json").write_text(json.dumps(raw), encoding="utf-8")
    ms = load_odds_matches("testlg")
    assert ms == [{"home": "A", "away": "B", "season": "2526", "fthg": 2, "ftag": 1},
                  {"home": "G", "away": "H", "season": "2526", "fthg": 0, "ftag": 0}]
    assert load_odds_matches("testlg", seasons=("9999",)) == []      # 缺文件静默


def test_bump_and_bump_naive():
    """矩阵命中：ttg3=概率前3档、crs 前 k 截取；naive 固定档同构（crs5 列表前 k 截取）。"""
    c = _new_counter()
    m = np.zeros((7, 7)); m[1, 1] = 0.5; m[1, 2] = 0.3; m[2, 1] = 0.2
    bump(c, m, actual_t=3, actual_s="1-2")
    # ttg 桶：桶2(1-1)=0.5、桶3(1-2/2-1)=0.5、其余 0 → 前3档={2,3,+并列最小档0}，含 3
    assert c["ttg3"] == [1, 1] and c["crs1"] == [0, 1] and c["crs3"] == [1, 1] and c["crs5"] == [1, 1]
    bump(c, m, actual_t=9, actual_s="1-1")                            # ttg 9 球必 miss；crs1 hit
    assert c["ttg3"] == [1, 2] and c["crs1"] == [1, 2]
    cn = _new_counter()
    bump_naive(cn, ttg3_set={1, 2, 3}, crs5_set=["1-1", "0-0", "2-2", "3-1", "0-1"],
               actual_t=1, actual_s="3-1")
    assert cn["ttg3"] == [1, 1] and cn["crs1"] == [0, 1] and cn["crs3"] == [0, 1] and cn["crs5"] == [1, 1]


def test_evaluate_league_synthetic(tmp_path, monkeypatch):
    """2 队 4 场合成联赛全链路：假 DC + 假 odds 跑通，四线结构齐、n 口径对（rolling 首场不计）。"""
    monkeypatch.setattr("goal_engine.CACHE_DIR", tmp_path)
    dc = {"teams": {"A": {"attack": 0.3, "defense": -0.1}, "B": {"attack": 0.1, "defense": -0.2}},
          "homeAdv": 0.25, "rho": -0.05}
    (tmp_path / "testlg_dc.json").write_text(json.dumps(dc), encoding="utf-8")
    scores = [(2, 1), (1, 1), (0, 2), (3, 1)]
    matches = [{"home": h, "away": a, "fthg": str(hg), "ftag": str(ag)}
               for (h, a), (hg, ag) in zip([("A", "B"), ("B", "A"), ("A", "B"), ("B", "A")], scores)]
    (tmp_path / "odds_testlg_2526.json").write_text(
        json.dumps({"season": "2526", "matches": matches}), encoding="utf-8")
    r = evaluate_league("testlg")
    assert r is not None
    for line in LINES:
        assert set(r[line]) == set(METRIC_KEYS)
        assert all(isinstance(v, list) and len(v) == 2 for v in r[line].values())
    for key in METRIC_KEYS:                                # DC 可算 4 场：三线 n=4
        assert r["corrected"][key][1] == 4 and r["bareDc"][key][1] == 4 and r["naiveStatic"][key][1] == 4
        assert r["naiveRolling"][key][1] == 3              # 首场无历史不计
    assert r["sdTtg3"][1] == 4                             # 设计同口径（比分降序去重档）互验锚计数
