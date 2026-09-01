"""goal_engine 单测：T3 特征层（滚动构造/收缩/开季降权）。"""
from goal_engine import build_history, early_weight, league_sides, match_features, pad_rate

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
