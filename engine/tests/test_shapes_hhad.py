# -*- coding: utf-8 -*-
"""让球腿判腿测试（_pick_price hhad 分支·goalLine 主队视角口径）。开发者 sszhang"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shadow"))
import shapes

ODDS = {"h-1": 4.3, "d-1": 4.35, "a-1": 1.5, "a+1": 1.5, "h+1": 1.3, "d+1": 4.5}


def _price(pick, score, goal_line=None):
    h, a = score
    outcome = 0 if h > a else (1 if h == a else 2)
    tleg = {"kind": "hhad", "odds": ODDS, "goalLine": goal_line}
    return shapes._pick_price(tleg, pick, outcome, (h, a))


def test_p493_away_win_on_home_minus_one():
    # P493 实况：诺维奇 goalLine=-1，0:1 客胜 → 让后 -1:1 → 让球客胜命中
    assert _price("a+1", (0, 1), goal_line=-1.0) == 1.5
    assert _price("a-1", (0, 1), goal_line=-1.0) == 1.5


def test_home_minus_one_needs_two_goal_margin():
    assert _price("h-1", (2, 0), goal_line=-1.0) == 4.3     # 让后 1:0 主胜
    assert _price("h-1", (1, 0), goal_line=-1.0) is None    # 让后 0:0 平 → 主胜不中


def test_draw_after_handicap():
    assert _price("d-1", (1, 0), goal_line=-1.0) == 4.35
    assert _price("d+1", (0, 1), goal_line=+1.0) == 4.5


def test_goal_line_wins_over_pick_literal():
    # pick 字面 '+1' 与 goalLine=-1 冲突时以 goalLine 为准（P493 记法笔误形态）
    assert _price("a+1", (1, 1), goal_line=-1.0) == 1.5     # 让后 0:1 客胜
    assert _price("a+1", (1, 1), goal_line=+1.0) is None    # 让后 2:1 主胜


def test_missing_goal_line_falls_back_to_pick():
    assert _price("a+1", (0, 1), goal_line=None) is None    # 退回字面 +1 → 让后 1:1 平
    assert _price("d+1", (0, 1), goal_line=None) == 4.5


def test_malformed_pick_is_dead():
    assert _price("a", (0, 1), goal_line=-1.0) is None
    assert _price("a+x", (0, 1), goal_line=-1.0) is None


def test_hafu_is_explicitly_dead():
    tleg = {"kind": "hafu", "odds": {"dd": 5.1}}
    assert shapes._pick_price(tleg, "dd", 1, (1, 1)) is None
