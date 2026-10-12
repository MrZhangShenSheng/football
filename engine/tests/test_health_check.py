# engine/tests/test_health_check.py
# -*- coding: utf-8 -*-
"""健康巡检器测试（2026-10-12 批一）：探测器 fixture 单测 + fail-open 铁律。开发者 sszhang"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import health_check as hc  # noqa: E402


def _mk_results(root, day, rows):
    d = root / "data" / "02-results"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{day}.json").write_text(json.dumps({"matches": rows}, ensure_ascii=False), encoding="utf-8")


def test_none_ratio_excludes_no_pick_history(tmp_path, monkeypatch):
    """早期 schema 无 pick 场与排除场不计分母（10-05 七场假黄实证）；真漂移才计数。"""
    monkeypatch.setattr(hc, "ROOT", tmp_path)
    rows = [
        {"code": "hist", "result": "1-1", "pick": None,
         "directionHit": None, "scoreHit": None, "optionHit": None},      # 历史 schema：不计分母
        {"code": "excl", "result": "1-1", "pick": "排除:x", "directionHit": None},  # 排除：不计
        {"code": "drift", "result": "1-1", "pick": "TTG 3球",
         "directionHit": None, "scoreHit": None, "optionHit": None},      # 真漂移：计
    ] + [{"code": f"ok{i}", "result": "1-0", "pick": "HAD 主胜", "directionHit": True,
          "scoreHit": None, "optionHit": True} for i in range(4)]        # 正常 4 场
    _mk_results(tmp_path, "2026-10-07", rows)
    lvl, msg = hc.check_none_ratio()
    # 分母=drift+4 正常=5，漂移 1 → 20% > 10% 黄；若无 pick/排除不剔除则分母 7、1/7=14% 仍黄但数字不同
    assert lvl == hc.YELLOW
    assert "5 场" in msg and "1（20%）" in msg


def test_none_ratio_green_when_normal(tmp_path, monkeypatch):
    monkeypatch.setattr(hc, "ROOT", tmp_path)
    rows = [{"code": f"m{i}", "result": "1-0", "pick": "HAD 主胜", "directionHit": True,
             "scoreHit": None, "optionHit": True} for i in range(5)]
    _mk_results(tmp_path, "2026-10-08", rows)
    lvl, _ = hc.check_none_ratio()
    assert lvl == hc.GREEN                                   # 0/5 全判定 → 绿


def test_stale_pending_note_suppresses(tmp_path, monkeypatch):
    """久挂票：pendingNote 入账本的挂账票不报；无 note 才黄。"""
    monkeypatch.setattr(hc, "ROOT", tmp_path)
    sd = tmp_path / "engine" / "shadow"
    sd.mkdir(parents=True)
    sd.joinpath("paper_tickets.json").write_text(json.dumps([
        {"id": "P1", "result": "pending", "date": "2026-09-01",
          "pendingNote": "胜负彩挂账·正确 pending"},                        # 有 note：不报
        {"id": "P2", "result": "pending", "date": "2026-09-01"},           # 无 note：报
        {"id": "P3", "result": "pending", "date": "2099-01-01"},           # 未到期：不报
    ], ensure_ascii=False), encoding="utf-8")
    lvl, msg = hc.check_stale_pending_tickets()
    assert lvl == hc.YELLOW and "P2" in msg and "P1" not in msg


def test_corrupt_piles_levels(tmp_path, monkeypatch):
    monkeypatch.setattr(hc, "ROOT", tmp_path)
    (tmp_path / "engine" / "cache").mkdir(parents=True)
    lvl0, _ = hc.check_corrupt_piles()
    assert lvl0 == hc.GREEN
    for i in range(3):
        (tmp_path / "engine" / "cache" / f"x.json.corrupt-{i}").write_text("{}", encoding="utf-8")
    lvl3, msg3 = hc.check_corrupt_piles()
    assert lvl3 == hc.YELLOW and "3" in msg3


def test_run_failopen_single_probe_crash(tmp_path, monkeypatch):
    """fail-open 铁律：单探测器炸 → 该项 ERR、整体不抛异常。"""
    monkeypatch.setattr(hc, "ROOT", tmp_path)

    def boom():
        raise RuntimeError("炸了")

    monkeypatch.setattr(hc, "CHECKS", [("boom", boom)])
    doc = hc.run()
    assert doc["results"]["boom"]["level"] == hc.ERR
    assert "探测器异常" in doc["results"]["boom"]["msg"]
    assert doc["overall"] in (hc.RED, hc.ERR)
