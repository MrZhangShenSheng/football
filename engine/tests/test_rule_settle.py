# -*- coding: utf-8 -*-
"""rule_settle 测试（Task 3）。开发者 sszhang"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rule_brief as rb  # noqa: E402
import rule_settle as rs  # noqa: E402
import rules_registry as rr  # noqa: E402

HERE = Path(__file__).resolve().parent


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "REGISTRY_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(rb, "BRIEFS_DIR", tmp_path / "briefs")
    monkeypatch.setattr(rs, "RESULTS_DIR", tmp_path / "02-results")
    monkeypatch.setattr(rs, "TICKETS", tmp_path / "tickets.json")
    monkeypatch.setattr(rs, "SHADOW", tmp_path / "paper.json")
    monkeypatch.setattr(rs, "SCORE_ODDS", tmp_path / "score_odds")
    (tmp_path / "02-results").mkdir()
    doc = {"schemaVersion": 1, "candidates": [
        {"id": "C01", "stage": "candidate", "form": "gate", "trigger": {"play": "HAFU"},
         "text": "HAFU候选", "addedAt": "2026-10-01",
         "evidence": [{"round": "2026-10-01", "legs": ["x"], "loss": 1}],
         "record": dict.fromkeys(rr.RECORD_KEYS, 0)},
        {"id": "C02", "stage": "candidate", "form": "gate",
         "trigger": {"deviationPtMax": 2}, "text": "贴平候选", "addedAt": "2026-09-01",
         "evidence": [], "record": dict.fromkeys(rr.RECORD_KEYS, 0)}],
        "active_shadow": [
            {"id": "R001", "stage": "shadow", "form": "gate",
             "trigger": {"play": "HAFU", "parlayMin": 3}, "text": "HAFU禁3串",
             "why": "t", "evidence": [], "record": dict.fromkeys(rr.RECORD_KEYS, 0)}],
        "retired": [], "gates": {}}
    rr.save(doc)
    return tmp_path


def _mk_results(env, day, rows):
    (env / "02-results" / f"{day}.json").write_text(json.dumps(
        {"matches": [{"code": c, "result": r, "half": h} for c, r, h in rows]},
        ensure_ascii=False), encoding="utf-8")


def test_blocked_leg_died_is_saved(env):
    _mk_results(env, "2026-10-16", [("周四001", "0-1", "0-0")])
    rb.compile_brief("2026-10-16")
    rb.add_receipt("2026-10-16", "R001", "拦下 周四001 HAD h(主胜)")
    st = rs.settle("2026-10-16")
    assert st["saved"] == 1 and st["harmed"] == 0
    assert rr.load()["active_shadow"][0]["record"] == {
        "rounds": 1, "blocked": 1, "saved": 1, "harmed": 0}


def test_blocked_leg_lived_is_harmed(env):
    _mk_results(env, "2026-10-16", [("周四001", "2-0", "1-0")])
    rb.compile_brief("2026-10-16")
    rb.add_receipt("2026-10-16", "R001", "拦下 周四001 HAD h(主胜)")
    st = rs.settle("2026-10-16")
    assert st["harmed"] == 1


def test_ttg_and_unknown(env):
    _mk_results(env, "2026-10-16", [("周四002", "1-1", "0-0"), ("周四003", "2-0", None)])
    rb.compile_brief("2026-10-16")
    rb.add_receipt("2026-10-16", "R001", "拦下 周四002 TTG s2(2球)")
    rb.add_receipt("2026-10-16", "R001", "拦下 周四003 HAFU hh(无半场)")
    st = rs.settle("2026-10-16")
    # 1-1=2球 → s2 命中 → 被拦腿活了 → harmed；HAFU 无半场 → unknown
    assert st["harmed"] == 1 and st["saved"] == 0 and st["unknown"] == 1


def test_hafu_with_half(env):
    _mk_results(env, "2026-10-16", [("周四004", "3-0", "0-0")])
    rb.compile_brief("2026-10-16")
    rb.add_receipt("2026-10-16", "R001", "拦下 周四004 HAFU dh")
    st = rs.settle("2026-10-16")
    assert st["harmed"] == 1            # 半0:0=平 全3:0=主 → dh 实际命中 → 拦错了


def test_pattern_hafu_and_stale(env):
    _mk_results(env, "2026-10-16", [("周四009", "2-3", "0-0")])
    (env / "paper.json").write_text(json.dumps([
        {"id": "P999", "date": "2026-10-15", "result": "settled", "stake": 20,
         "matchDays": ["2026-10-16"], "legs": [
             {"code": "周四009", "market": "hafu", "pick": ["dh"], "result": "miss",
              "odds": {"dh": 5.1}},
             {"code": "周四010", "market": "had", "pick": [0], "result": "miss",
              "odds": [2.0], "q": 0.5},
             {"code": "周四011", "market": "had", "pick": [0], "result": "hit",
              "odds": [1.8]}]}], ensure_ascii=False), encoding="utf-8")
    rb.compile_brief("2026-10-16")
    st = rs.settle("2026-10-16")
    doc = rr.load()
    c01 = [c for c in doc["candidates"] if c["id"] == "C01"][0]
    assert any(e.get("pattern") for e in c01["evidence"])       # HAFU 模式命中
    c02 = [c for c in doc["candidates"] if c["id"] == "C02"][0]
    assert "stale" in c02["flags"]                               # 09-01 至今 >30 天无证据


def test_demotion_flag(env):
    doc = rr.load()
    doc["active_shadow"][0]["record"]["harmed"] = 2
    rr.save(doc)
    rb.compile_brief("2026-10-16")
    rs.settle("2026-10-16")
    assert "red_review" in rr.load()["active_shadow"][0]["flags"]
