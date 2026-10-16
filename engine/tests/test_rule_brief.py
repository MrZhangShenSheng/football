# -*- coding: utf-8 -*-
"""rule_brief 测试（Task 2）。开发者 sszhang"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rule_brief as rb  # noqa: E402
import rules_registry as rr  # noqa: E402


@pytest.fixture()
def reg(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "REGISTRY_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(rb, "BRIEFS_DIR", tmp_path / "briefs")
    doc = {"schemaVersion": 1,
           "candidates": [{"id": "C01", "stage": "candidate", "form": "checklist",
                           "text": "候选甲", "record": dict.fromkeys(rr.RECORD_KEYS, 0)}],
           "active_shadow": [
               {"id": f"R{i:03d}", "stage": "shadow", "form": "checklist",
                "text": f"规则{i}", "why": f"因{i}",
                "record": {"rounds": 0, "blocked": 0, "saved": 15 - i, "harmed": 0}}
               for i in range(1, 19)],                        # 18 条 → 触发 15 硬顶
           "retired": [], "gates": {}}
    rr.save(doc)
    return doc


def test_brief_compiles_shadow_with_cap(reg):
    out = rb.compile_brief("2026-10-16")
    text = out.read_text(encoding="utf-8")
    assert "R001 |" in text and "候选甲" in text
    assert "溢出未注入 3 条" in text
    assert "R016" in text.split("溢出")[1]           # saved 最低三条(16,17,18)被截
    assert text.count(" | ") >= 15


def test_brief_saved_priority_order(reg):
    text = rb.compile_brief("2026-10-16").read_text(encoding="utf-8")
    head = text.split("\n\n")[0]
    assert text.index("R001 |") < text.index("R015 |")   # saved 高者在前


def test_brief_with_receipt_not_overwritten(reg):
    rb.compile_brief("2026-10-16")
    rb.add_receipt("2026-10-16", "R001", "拦下 周四013 HAFU hh(拟入3串)")
    with pytest.raises(SystemExit, match="拒覆盖"):
        rb.compile_brief("2026-10-16")


def test_receipt_parse_and_read(reg):
    rb.compile_brief("2026-10-16")
    rb.add_receipt("2026-10-16", "R002", "拦下 周四001 TTG s2(凑数腿)")
    rows = rb.read_receipts("2026-10-16")
    assert len(rows) == 1
    assert rows[0] == {"rule": "R002", "at": rows[0]["at"],
                       "action": "拦下 周四001 TTG s2(凑数腿)",
                       "blocked_code": "周四001", "blocked_market": "TTG",
                       "blocked_pick": "s2"}


def test_receipt_non_block_action_kept_without_code(reg):
    rb.compile_brief("2026-10-16")
    rb.add_receipt("2026-10-16", "R004", "执行：周四006 伤停已网络二次核验(Ünal可用)")
    rows = rb.read_receipts("2026-10-16")
    assert rows[0]["blocked_code"] is None and rows[0]["rule"] == "R004"


def test_receipt_unknown_rule_rejected(reg):
    rb.compile_brief("2026-10-16")
    with pytest.raises(SystemExit, match="无 R999"):
        rb.add_receipt("2026-10-16", "R999", "拦下 周四001 TTG s2")
