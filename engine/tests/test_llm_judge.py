# -*- coding: utf-8 -*-
"""llm_judge v3 冻结器测试（llm-autonomy-prereg 输出契约）。开发者 sszhang"""
import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import llm_judge as lj  # noqa: E402
from llm_card import build_card, save_card  # noqa: E402

DATE = "2026-10-16"
CRS = {"s00s00": 9.0, "s01s01": 8.0, "s02s01": 7.5, "s01s02": 8.5,
       "s04s00": 30.0, "胜其他": 35.0}


def _mk_card(cards_root, code="周X001"):
    m = {"matchNumStr": code, "league": "德乙", "home": "A", "away": "B",
         "matchTime": "03:00:00", "had": {"h": 2.0, "d": 3.4, "a": 3.6},
         "crs": dict(CRS), "ttg": {"s2": 3.6}}
    card = build_card(DATE, m, insight_path=None, lineups={},
                      now="2026-10-16T11:00:00", business_date="2026-10-17")
    save_card(card, DATE, root=cards_root)
    return card


def _pred(code="周X001"):
    return {"code": code,
            "direction": {"call": "主胜", "evidence": "客场0-3-7+双主力后卫伤"},
            "goalBand": {"lo": 2, "hi": 3, "evidence": "双方场均合计3.4球"},
            "keyCells": [{"cell": "2:1", "why": "主胜中低比分"}, {"cell": "other_h", "why": "大胜尾部"}],
            "against": [{"item": "H2H主胜仅22%", "evidence": "9场跨赛季样本"}],
            "confidence": "medium"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cards = tmp_path / "cards"
    judg = tmp_path / "judgments"
    cards.mkdir()
    prereg = tmp_path / "prereg.json"
    prereg.write_text(json.dumps({"registeredAt": "t"}), encoding="utf-8")
    monkeypatch.setattr(lj, "JUDGMENTS_ROOT", judg)
    monkeypatch.setattr(lj, "PREREG_PATH", prereg)
    _mk_card(cards)
    return {"cards": cards, "judg": judg}


def test_freeze_happy_path(env):
    card = json.loads((env["cards"] / DATE / "周X001.json").read_text(encoding="utf-8"))
    out = lj.freeze(DATE, [_pred()], now="2026-10-16T23:00:00", cards_root=env["cards"])
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["chain"] == "llm-autonomy"
    assert len(doc["preregHash"]) == 16
    p = doc["preds"][0]
    assert p["cardHash"] == card["cardHash"]                 # 判断钉卡（防卡漂移）
    assert p["kickoffAt"] == "2026-10-17 03:00:00"           # businessDate+matchTime 拼接
    assert p["afterKickoff"] is False
    assert "intel_missing" in p["gapFlags"]                  # 缺口机器自评（非自报）
    assert "context_missing" in p["gapFlags"]
    assert p["direction"]["call"] == "主胜"


def test_freeze_refuses_whole_batch_on_any_issue(env):
    bad = _pred()
    bad["direction"]["call"] = "胜"                          # 非法 call
    with pytest.raises(SystemExit):
        lj.freeze(DATE, [_pred(), bad], cards_root=env["cards"])
    assert not (env["judg"] / f"{DATE}.json").exists()       # 整批拒绝，半卷不留


def test_keycell_must_be_priced(env):
    p = _pred()
    p["keyCells"] = [{"cell": "3:3", "why": "x"}]            # CRS 无 s03s03=无价
    with pytest.raises(SystemExit):
        lj.freeze(DATE, [p], cards_root=env["cards"])


def test_keycells_max_five(env):
    p = _pred()
    p["keyCells"] = [{"cell": "2:1", "why": "x"}] * 6        # 超 prereg frozenParams 上限
    with pytest.raises(SystemExit):
        lj.freeze(DATE, [p], cards_root=env["cards"])


def test_against_forms(env):
    p = _pred()
    p["against"] = []                                        # 空列表=未给反对项
    with pytest.raises(SystemExit):
        lj.freeze(DATE, [p], cards_root=env["cards"])
    p["against"] = {"none": True, "why": "证据单向压倒性,无可信反对项"}
    out = lj.freeze(DATE, [p], cards_root=env["cards"])
    assert json.loads(out.read_text(encoding="utf-8"))["preds"][0]["against"]["none"] is True


def test_goalband_bounds(env):
    p = _pred()
    p["goalBand"] = {"lo": 3, "hi": 2, "evidence": "x"}
    with pytest.raises(SystemExit):
        lj.freeze(DATE, [p], cards_root=env["cards"])
    p["goalBand"] = {"lo": 0, "hi": 99, "evidence": "完全无把握时须如实给开放带"}
    lj.freeze(DATE, [p], cards_root=env["cards"])            # 开放带合法（诚实>好看）


def test_empty_keycells_is_legal(env):
    p = _pred()
    p["keyCells"] = []                                       # 判断不出具体比分=诚实产出
    out = lj.freeze(DATE, [p], cards_root=env["cards"])
    assert json.loads(out.read_text(encoding="utf-8"))["preds"][0]["keyCells"] == []


def test_idempotent_and_revision_load(env):
    a = lj.freeze(DATE, [_pred()], cards_root=env["cards"])
    b = lj.freeze(DATE, [_pred()], cards_root=env["cards"])
    assert a == b                                            # 主卷幂等只读
    r1 = lj.freeze(DATE, [{**_pred(), "confidence": "high"}],
                   revision=1, reason="伤停二次核验后修正", cards_root=env["cards"])
    doc = json.loads(r1.read_text(encoding="utf-8"))
    assert doc["revisionOf"] == DATE and "修正" in doc["revisionReason"]
    assert doc["preds"][0]["confidence"] == "high"
    lj.freeze(DATE, [{**_pred(), "confidence": "low"}],
              revision=2, reason="x", cards_root=env["cards"])
    best = lj.load_judgments(DATE)                           # -rN 数值序取最大
    assert best["preds"][0]["confidence"] == "low"
    assert lj.load_judgments("1999-01-01") is None           # 全缺诚实降级


def test_after_kickoff_flagged_not_refused(env):
    out = lj.freeze(DATE, [_pred()], now="2026-10-17T04:00:00", cards_root=env["cards"])
    p = json.loads(out.read_text(encoding="utf-8"))["preds"][0]
    assert p["afterKickoff"] is True                         # 留痕降 descriptiveOnly,不删不拒
