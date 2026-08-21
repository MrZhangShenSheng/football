# -*- coding: utf-8 -*-
"""联赛画像测试：合成赛果 → 积分榜/统计/战意格局正确性。"""
import json

import pytest

import league_profile as lp


@pytest.fixture(scope="module")
def profile(tmp_path_factory, monkeypatch):
    """用合成缓存数据构造一个 4 队小联赛画像。"""
    tmp = tmp_path_factory.mktemp("cache")
    # A 队 2 连胜（含 1 场高赔冷门），B/C/D 常规
    matches = [
        {"date": "01/08/2026", "home": "A", "away": "B", "fthg": 2, "ag": 0, "pin_h": 2.5, "pin_d": 3.2, "pin_a": 2.8},
        {"date": "08/08/2026", "home": "C", "away": "D", "fthg": 1, "ag": 1, "pin_h": 2.2, "pin_d": 3.2, "pin_a": 3.3},
        {"date": "15/08/2026", "home": "C", "away": "A", "fthg": 0, "ag": 1, "pin_h": 2.1, "pin_d": 3.3, "pin_a": 3.6},
        {"date": "16/08/2026", "home": "B", "away": "D", "fthg": 0, "ag": 2, "pin_h": 1.9, "pin_d": 3.4, "pin_a": 4.0},
    ]
    (tmp / "odds_test-league_2627.json").write_text(
        json.dumps({"matches": matches}), encoding="utf-8")
    monkeypatch.setattr(lp, "CACHE_DIR", tmp)
    monkeypatch.setattr(lp, "OUT_DIR", tmp)
    return lp.build("test-league", ["2627"])


class TestLeagueProfile:

    def test_standings_leader_and_pts(self, profile):
        s = profile["standings"]
        assert s[0]["team"] == "A" and s[0]["pts"] == 6  # A 两连胜
        assert s[0]["form"] == "WW"

    def test_goal_difference(self, profile):
        by_team = {r["team"]: r for r in profile["standings"]}
        assert by_team["A"]["gd"] == 3   # 2-0, 1-0
        assert by_team["B"]["gd"] == -4  # 0-2, 0-2

    def test_avg_goals_per_match(self, profile):
        # 4 场共 9 球 → 场均 2.25（口径：每场比赛）
        assert profile["leagueStats"]["avgGoals"] == pytest.approx(2.25)

    def test_home_win_rate(self, profile):
        # 4 场：2 主胜 1 平 1 客胜
        assert profile["leagueStats"]["homeWinRate"] == pytest.approx(0.5)
        assert profile["leagueStats"]["drawRate"] == pytest.approx(0.25)

    def test_upset_rate_uses_closing_odds(self, profile):
        # A 赢 C 时 A 收盘 3.6>2.5 → 冷门 1 场 / 有赔率场 4 场 = 0.25
        assert profile["leagueStats"]["upsetRate"] == pytest.approx(0.25)

    def test_top_scores(self, profile):
        top = [t["score"] for t in profile["leagueStats"]["topScores"]]
        assert top[0] == "1-0"  # 出现2次

    def test_context_title_race_and_relegation(self, profile):
        assert profile["context"]["titleRace"]["leader"] == "A"
        assert len(profile["context"]["relegationZone"]) == 3  # 默认最后3

    def test_rounds_played(self, profile):
        assert profile["roundsPlayed"] == 2
