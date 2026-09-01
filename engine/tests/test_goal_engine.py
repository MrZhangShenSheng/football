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
    """特征只用历史场（该场之前），主客分侧；home_def/away_def 分母各用己方侧均值。"""
    hist, sides = build_history([M("A", "C", 3, 0), M("D", "A", 1, 1), M("C", "B", 2, 2)])
    f = match_features(hist, sides, "A", "B")
    # A 近5主场场均 = pad(3, 1, home_rate=2.0) = (3+2.0*2)/3；ratio = pad/home_rate
    assert abs(f["home_att"] - (3 + 2.0 * 2) / 3 / 2.0) < 1e-9
    # B 近5客场失球 = pad(2, 1, away_rate=1.0)；away_rate = (0+1+2)/3 = 1.0
    # → pad=(2+1.0*2)/(1+2)=4/3 → ratio = 4/3（分母 n+k=3 含 B 该 1 场客场）
    assert abs(f["away_def"] - (2 + 1.0 * 2) / 3 / 1.0) < 1e-9


def test_early_season_weight():
    """开季降权：此前 <2 场 ×0.3，≥2 场恢复 1.0。"""
    assert early_weight(0) == 0.3 and early_weight(1) == 0.3 and early_weight(2) == 1.0
