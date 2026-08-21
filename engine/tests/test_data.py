# -*- coding: utf-8 -*-
"""数据完整性测试：别名表结构 / 球队文件读写回环 / 索引可重建。"""
import json

import pytest

from common import load_aliases, load_team, save_team, team_path, TEAMS_DIR


class TestAliases:
    def test_aliases_load_and_count(self):
        aliases = load_aliases()
        assert len(aliases) >= 30  # 36 队起步，只增不减

    def test_every_team_has_zh_and_league(self):
        for team_id, info in load_aliases().items():
            assert info.get("zh"), f"{team_id} 缺中文名"
            assert info.get("league"), f"{team_id} 缺联赛目录"

    def test_ids_are_kebab_case(self):
        for team_id in load_aliases():
            assert team_id == team_id.lower().replace("_", "-"), f"{team_id} 命名不符 kebab-case"
            assert " " not in team_id

    def test_raw_aliases_wellformed(self):
        raw = json.loads((TEAMS_DIR / "_aliases.json").read_text(encoding="utf-8"))
        assert "_meta" in raw and "sources" in raw["_meta"]


class TestTeamIo:
    def test_roundtrip(self, tmp_path, monkeypatch):
        """写入 → 读回 → 字段保持（含中文）。"""
        monkeypatch.setattr("common.TEAMS_DIR", tmp_path)
        save_team("corinthians", "brazil", {"elo": {"rating": 1685}}, zh="科林蒂安")
        data = load_team("corinthians", "brazil")
        assert data["team"] == "科林蒂安"
        assert data["elo"]["rating"] == 1685
        assert data["league"] == "brazil"
        assert data["lastUpdated"]

    def test_load_missing_returns_skeleton(self, tmp_path, monkeypatch):
        monkeypatch.setattr("common.TEAMS_DIR", tmp_path)
        data = load_team("nobody", "nowhere")
        assert data["league"] == "nowhere" and data["team"] is None


class TestIndexRebuildable:
    def test_build_index_runs_on_empty(self, tmp_path, monkeypatch):
        """索引可重建（空目录也不崩）——'索引只是优化，glob 可兜底'。"""
        import build_index
        monkeypatch.setattr(build_index, "TEAMS_DIR", tmp_path)
        idx = build_index.build()
        assert idx["teams"] == {}
        assert idx["_meta"]["count"] == 0
