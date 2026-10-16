# -*- coding: utf-8 -*-
"""规则册 schema 校验测试（Task 1）。开发者 sszhang"""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rules_registry as rr


@pytest.fixture(scope="module")
def doc():
    return rr.load()


def test_registry_valid(doc):
    assert not rr.validate(doc), rr.validate(doc)


def test_first_batch_seeded(doc):
    ids = set(rr.by_id(doc))
    for expect in ("R001", "R002", "R003", "R004", "R005", "R006", "R007", "R008",
                   "R009", "R017"):
        assert expect in ids, f"首批缺 {expect}"


def test_t048_lessons_in_shadow(doc):
    for rid in ("R001", "R002", "R003"):
        r = rr.by_id(doc)[rid]
        assert r["stage"] == "shadow" and r["form"] == "gate" and r["trigger"], rid


def test_gate_with_upstream_notation(doc):
    r = rr.by_id(doc)["R009"]
    assert r["stage"] == "gate"
    assert r.get("enforcedBy"), "已由上游代码强制的 gate 规则须标 enforcedBy"


def test_validate_catches_misplacement(tmp_path):
    d = {"candidates": [{"id": "X1", "stage": "shadow", "form": "checklist",
                         "text": "t", "record": dict.fromkeys(rr.RECORD_KEYS, 0)}]}
    assert any("不在段" in i for i in rr.validate(d))


def test_validate_catches_gate_without_trigger():
    d = {"active_shadow": [{"id": "X2", "stage": "shadow", "form": "gate",
                            "text": "t", "record": dict.fromkeys(rr.RECORD_KEYS, 0)}]}
    assert any("trigger" in i for i in rr.validate(d))


def test_record_tick(doc):
    d = copy.deepcopy(doc)
    assert rr.record_tick(d, "R001", blocked=1, saved=1, rounds=1)
    assert rr.by_id(d)["R001"]["record"]["saved"] == \
        rr.by_id(doc)["R001"]["record"]["saved"] + 1
    assert not rr.record_tick(d, "R999")
