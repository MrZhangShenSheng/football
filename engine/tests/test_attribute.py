# -*- coding: utf-8 -*-
"""归因引擎单元测试。"""
import pytest

from attribute import pick_to_index, result_to_idx


class TestIndexParse:
    """pick→方向下标 / result→结果下标。"""

    def test_pick_had_away(self):
        assert pick_to_index("HAD", "客胜") == 2

    def test_pick_had_home(self):
        assert pick_to_index("HAD", "主胜") == 0

    def test_pick_had_draw(self):
        assert pick_to_index("HAD", "平") == 1

    def test_non_had_returns_none(self):
        assert pick_to_index("CRS", "2-1") is None

    def test_result_home_win(self):
        assert result_to_idx("3-1") == 0

    def test_result_away_win(self):
        assert result_to_idx("0-2") == 2

    def test_result_draw(self):
        assert result_to_idx("2-2") == 1

    def test_result_invalid(self):
        assert result_to_idx("弃赛") is None
        assert result_to_idx(None) is None
