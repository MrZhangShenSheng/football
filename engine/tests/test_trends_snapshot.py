# -*- coding: utf-8 -*-
"""intel-timeline 时序库测试：selftest 全链 + livescan 校验异常路径（v5.4）。

selftest（与 `python3 trends_snapshot.py --selftest` 同源）覆盖六段：extract_odds 五池提取 /
replay+diff 项级回放对比 / write_snapshot 原子追加+损坏降级 / intel 摘要+追加 /
livescan 校验落盘 / find_pre_snapshots 桥（比赛日锚定 + 跨周同码护栏）。
开发者 sszhang
"""
import pytest

import trends_snapshot as ts


def test_selftest_full_chain():
    """selftest 全段绿 = 六段核心断言全过（时序库回归主入口）。"""
    ts.selftest()


class TestLivescanValidation:
    """write_livescan 校验异常路径（独立于 selftest 再锁一遍，防 selftest 改动漂移）。"""

    def _with_tmp_dir(self, tmp_path):
        real = ts.__dict__["_TRENDS_DIR"]
        ts._set_trends_dir(tmp_path)
        return real

    def test_bad_trigger(self, tmp_path):
        real = self._with_tmp_dir(tmp_path)
        try:
            with pytest.raises(ValueError, match="trigger"):
                ts.write_livescan({"trigger": "胡乱触发", "verdict": "x", "matches": []}, day="2026-08-30")
        finally:
            ts._set_trends_dir(real)

    def test_bad_threat(self, tmp_path):
        real = self._with_tmp_dir(tmp_path)
        try:
            with pytest.raises(ValueError, match="threat"):
                ts.write_livescan({"trigger": "用户要求", "verdict": "x",
                                   "matches": [{"code": "周六027", "matchId": 1, "threat": "极高"}]},
                                  day="2026-08-30")
        finally:
            ts._set_trends_dir(real)

    def test_missing_match_id(self, tmp_path):
        real = self._with_tmp_dir(tmp_path)
        try:
            with pytest.raises(ValueError, match="matchId"):
                ts.write_livescan({"trigger": "用户要求", "verdict": "x",
                                   "matches": [{"code": "周六027", "threat": "high"}]},
                                  day="2026-08-30")
        finally:
            ts._set_trends_dir(real)
