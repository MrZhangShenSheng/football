# -*- coding: utf-8 -*-
"""leg_gate 测试（Task 4）。开发者 sszhang"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import leg_gate as lg  # noqa: E402
import rules_registry as rr  # noqa: E402


@pytest.fixture()
def reg(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "REGISTRY_PATH", tmp_path / "registry.json")
    doc = {"schemaVersion": 1, "candidates": [],
           "active_shadow": [
               {"id": "G01", "stage": "gate", "form": "gate",
                "trigger": {"play": "HAFU", "parlayMin": 3}, "text": "t",
                "record": dict.fromkeys(rr.RECORD_KEYS, 0)},
               {"id": "G02", "stage": "gate", "form": "gate",
                "trigger": {"deviationPtMax": 2}, "text": "t",
                "record": dict.fromkeys(rr.RECORD_KEYS, 0)},
               {"id": "G03", "stage": "gate", "form": "gate",
                "trigger": {"poolPayoutMin": 0.85}, "text": "t",
                "record": dict.fromkeys(rr.RECORD_KEYS, 0)},
               {"id": "S01", "stage": "shadow", "form": "checklist",
                "trigger": {"play": "HAFU", "parlayMin": 3}, "text": "t",
                "record": dict.fromkeys(rr.RECORD_KEYS, 0)},
               {"id": "G09", "stage": "gate", "form": "gate",
                "trigger": {"requiresHypothesis": True}, "enforcedBy": "hypothesis",
                "text": "t", "record": dict.fromkeys(rr.RECORD_KEYS, 0)}],
           "retired": [], "gates": {}}
    rr.save(doc)
    return doc


def _leg(**kw):
    base = {"code": "周四001", "market": "HAD", "pick": ["h"], "odds": 2.0}
    base.update(kw)
    return base


def test_hafu_in_parlay3_blocked(reg):
    t = {"legs": [_leg(market="HAFU"), _leg(code="周四002"), _leg(code="周四003")]}
    vs = lg.check_ticket(t)
    assert any(v["rule"] == "G01" for v in vs)


def test_hafu_single_ok(reg):
    t = {"legs": [_leg(market="HAFU"), _leg(code="周四002")]}
    assert not any(v["rule"] == "G01" for v in lg.check_ticket(t))


def test_deviation_flat_leg_blocked(reg):
    t = {"legs": [_leg(q=0.495, odds=2.0)]}       # 1/2.0=0.5 → 偏离 0.5pt
    assert any(v["rule"] == "G02" for v in lg.check_ticket(t))


def test_deviation_absent_q_skipped(reg):
    t = {"legs": [_leg()]}                        # 无 q → skip 不误拦
    assert not any(v["rule"] == "G02" for v in lg.check_ticket(t))


def test_pool_payout_gate(reg):
    pools = {"ttg": {"s0": 6.25, "s1": 3.2, "s2": 3.05, "s3": 4.15, "s4": 7.95,
                     "s5": 20.0, "s6": 45.0, "s7": 65.0}}    # ≈0.797 深水
    t = {"legs": [_leg(market="TTG")]}
    assert any(v["rule"] == "G03" for v in lg.check_ticket(t, pool_odds_by_play=pools))
    pools_ok = {k: 9.2 for k in
                ("s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7")}  # 1/(8/9.2)=87% 浅水
    assert not any(v["rule"] == "G03" for v in lg.check_ticket(t, pool_odds_by_play=pools_ok))


def test_shadow_stage_not_enforced(reg):
    t = {"legs": [_leg(market="HAFU"), _leg(code="周四002"), _leg(code="周四003")]}
    """S01 同 trigger 但 stage=shadow → 不拦（影子期软约束）"""
    vs = [v for v in lg.check_ticket(t) if v["rule"] == "S01"]
    assert not vs


def test_upstream_gate_skipped(reg):
    t = {"legs": [_leg()]}
    assert not any(v["rule"] == "G09" for v in lg.check_ticket(t))


def test_pool_payout_math():
    assert lg.pool_payout({"a": 2.0, "b": 2.0}) == pytest.approx(1.0)
    assert lg.pool_payout({"goalLine": "-1", "h": 4.3, "d": 4.35, "a": 1.5}) is not None
