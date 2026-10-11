# engine/scripts/tests/test_evidence.py
# -*- coding: utf-8 -*-
"""v35 P1 证据链核心测试。开发者 sszhang"""
import json

import evidence


def _swap_root(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "EVIDENCE_ROOT", tmp_path)


def test_record_roundtrip(tmp_path, monkeypatch):
    _swap_root(tmp_path, monkeypatch)
    assert evidence.record("2026-10-12", "周日001", evidence.STEP_DC,
                           {"lambdaHome": 1.4}, source="code") is True
    doc = json.loads((tmp_path / "2026-10-12" / "周日001.json").read_text(encoding="utf-8"))
    assert doc["schema"] == evidence.SCHEMA_VERSION
    assert doc["steps"][evidence.STEP_DC]["payload"]["lambdaHome"] == 1.4


def test_record_idempotent_overwrite(tmp_path, monkeypatch):
    _swap_root(tmp_path, monkeypatch)
    evidence.record("2026-10-12", "k", evidence.STEP_DC, {"v": 1})
    evidence.record("2026-10-12", "k", evidence.STEP_DC, {"v": 2})   # 同键覆盖不重复
    doc = json.loads((tmp_path / "2026-10-12" / "k.json").read_text(encoding="utf-8"))
    assert list(doc["steps"]) == [evidence.STEP_DC]
    assert doc["steps"][evidence.STEP_DC]["payload"]["v"] == 2


def test_record_fail_open(tmp_path, monkeypatch):
    # EVIDENCE_ROOT 指向文件（非目录）→ 写必败 → 只返回 False 绝不抛
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(evidence, "EVIDENCE_ROOT", blocker)
    assert evidence.record("2026-10-12", "k", evidence.STEP_DC, {"v": 1}) is False


def test_norm_key_priority(tmp_path):
    assert evidence.norm_key(match_id="12345") == "12345"
    assert evidence.norm_key(code="周日003") == "周日003"
    assert evidence.norm_key(league="英超", home="A", away="B") == "英超|A|B"


def test_check_missing_steps(tmp_path, monkeypatch):
    _swap_root(tmp_path, monkeypatch)
    evidence.record("2026-10-12", "k", evidence.STEP_ODDS, {})
    missing = evidence.check("2026-10-12")["k"]
    assert set(missing) == set(evidence.ALL_STEPS) - {evidence.STEP_ODDS}


def test_check_empty_day(tmp_path, monkeypatch):
    _swap_root(tmp_path, monkeypatch)
    assert evidence.check("1999-01-01") == {}


def test_record_s1_from_archive(tmp_path, monkeypatch):
    _swap_root(tmp_path, monkeypatch)
    arch = tmp_path / "arch.json"
    arch.write_text(json.dumps({"fetchedAt": "T", "source": "sporttery", "matchDays": [
        {"businessDate": "2026-10-12", "matches": [
            {"matchNumStr": "周日001", "league": "英超", "home": "A", "away": "B",
             "had": {"h": 2.0, "d": 3.0, "a": 3.5}, "oddsUpdatedAt": "t1"}]}]},
        ensure_ascii=False), encoding="utf-8")
    n = evidence.record_s1_from_archive(str(arch))
    assert n == 1
    doc = json.loads((tmp_path / "2026-10-12" / "周日001.json").read_text(encoding="utf-8"))
    p = doc["steps"][evidence.STEP_ODDS]["payload"]
    assert p["pools"] == ["had"] and p["oddsUpdatedAt"] == "t1"
