# -*- coding: utf-8 -*-
"""联赛级融合系数 override 测试（2026-09-15 荷甲降 a 立项）。
load_fusion_ab(league)：全局 a/b + leagueOverrides 部分覆盖。开发者 sszhang"""
import json

import pytest
from common import load_fusion_ab


def _write_fusion(tmp_path, payload):
    (tmp_path / "fusion.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path / "fusion.json"


FULL = {"a": 0.4, "b": 1.0,
        "leagueOverrides": {"netherlands-eredivisie": {"a": 0.0}}}


class TestLoadFusionAb:
    def test_no_league_returns_global(self, tmp_path):
        assert load_fusion_ab(path=_write_fusion(tmp_path, FULL)) == (0.4, 1.0)

    def test_override_league_hits(self, tmp_path):
        assert load_fusion_ab("netherlands-eredivisie",
                              path=_write_fusion(tmp_path, FULL)) == (0.0, 1.0)

    def test_other_league_falls_back_to_global(self, tmp_path):
        assert load_fusion_ab("spain-laliga",
                              path=_write_fusion(tmp_path, FULL)) == (0.4, 1.0)

    def test_partial_override_inherits_b(self, tmp_path):
        # override 只写 a → b 继承全局
        assert load_fusion_ab("germany-bundesliga", path=_write_fusion(
            tmp_path, {"a": 0.4, "b": 1.0,
                       "leagueOverrides": {"germany-bundesliga": {"a": 0.2}}})) == (0.2, 1.0)

    def test_corrupt_file_falls_back_to_default(self, tmp_path):
        p = tmp_path / "fusion.json"
        p.write_text("{broken", encoding="utf-8")
        assert load_fusion_ab("netherlands-eredivisie", path=p) == (0.4, 1.0)

    def test_missing_file_falls_back_to_default(self, tmp_path):
        assert load_fusion_ab(path=tmp_path / "nope.json") == (0.4, 1.0)
