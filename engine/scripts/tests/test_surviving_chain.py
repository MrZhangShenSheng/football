# engine/scripts/tests/test_surviving_chain.py
# -*- coding: utf-8 -*-
"""v35-P4 D6 幸存链注册表测试。开发者 sszhang"""
import json

import surviving_chain as sc


def _swap(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "REGISTRY_PATH", tmp_path / "surviving_chain.json")


def test_check_pass(tmp_path, monkeypatch):
    _swap(tmp_path, monkeypatch)
    (tmp_path / "surviving_chain.json").write_text(json.dumps(
        {"schema": 1, "components": {"market_crs_devig": {
            "status": "keep", "auditedAt": "2026-10-11"}}}), encoding="utf-8")
    ok, why = sc.check_component("market_crs_devig")
    assert ok, why


def test_check_missing_component(tmp_path, monkeypatch):
    _swap(tmp_path, monkeypatch)
    (tmp_path / "surviving_chain.json").write_text(json.dumps(
        {"schema": 1, "components": {}}), encoding="utf-8")
    ok, why = sc.check_component("market_crs_devig")
    assert not ok and "不在注册表" in why


def test_check_stale_audit(tmp_path, monkeypatch):
    _swap(tmp_path, monkeypatch)
    (tmp_path / "surviving_chain.json").write_text(json.dumps(
        {"schema": 1, "components": {"market_crs_devig": {
            "status": "keep", "auditedAt": "2026-01-01"}}}), encoding="utf-8")
    ok, why = sc.check_component("market_crs_devig")
    assert not ok and "审计过期" in why


def test_check_demote_not_runnable(tmp_path, monkeypatch):
    _swap(tmp_path, monkeypatch)
    (tmp_path / "surviving_chain.json").write_text(json.dumps(
        {"schema": 1, "components": {"v3w_lambda": {
            "status": "demote", "auditedAt": "2026-10-11"}}}), encoding="utf-8")
    ok, _ = sc.check_component("v3w_lambda")
    assert not ok          # demote 组件不得作为主概率源（可作降级层·由调用方标注）


def test_require_component_exit_code(tmp_path, monkeypatch):
    import pytest
    _swap(tmp_path, monkeypatch)
    (tmp_path / "surviving_chain.json").write_text(json.dumps(
        {"schema": 1, "components": {}}), encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        sc.require_component("market_crs_devig")
    assert ei.value.code == 2


def test_check_future_audit_date_rejected(tmp_path, monkeypatch):
    _swap(tmp_path, monkeypatch)
    (tmp_path / "surviving_chain.json").write_text(json.dumps(
        {"schema": 1, "components": {"x": {
            "status": "keep", "auditedAt": "2027-01-01"}}}), encoding="utf-8")
    ok, why = sc.check_component("x")
    assert not ok and "未来" in why
