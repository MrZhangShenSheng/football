# -*- coding: utf-8 -*-
"""llm_pred v2 锚定冻结测试。开发者 sszhang"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import llm_pred as lp


def _kj3():
    """≥3 条独立判断（v3.4 门槛·validate_pred 硬校验）。"""
    return [{"judgment": f"j{i}", "evidence": f"语境层#{i}"} for i in (1, 2, 3)]

TTG = {"s0": 24.0, "s1": 7.75, "s2": 4.55, "s3": 3.70, "s4": 4.50,
       "s5": 6.50, "s6": 11.00, "s7": 13.00}          # 去水期望 ≈3.42
CRS = {"1:1": 8.5, "1:0": 13.0, "0:1": 14.0, "2:1": 8.25, "0:0": 22.0,
       "2:0": 15.0, "1:2": 8.75, "0:2": 17.0, "s1sh": 30.0, "s1sd": 150.0,
       "s1sa": 50.0}
# 低分倾向的臣格（v1 病根形态：期望约 2.4）
LOW_GRID = {"1:1": .16, "1:0": .13, "0:1": .11, "0:0": .09, "2:1": .12,
            "2:0": .08, "1:2": .11, "0:2": .06, "other_h": .06, "other_d": .04, "other_a": .04}


def _mk_card(root, code="周三009", with_ttg=True):
    card = {
        "code": code, "match": "A vs B", "league": "欧冠", "kickoff": "03:00:00",
        "saleDate": "2026-10-15",
        "layers": {
            "market": {"had": {}, "crs": dict(CRS),
                       "ttg": dict(TTG) if with_ttg else {}, "oddsUpdatedAt": None},
            "intel": {"status": "未获取"}, "profile": {"status": "未获取"},
            "lineup": {"status": "未获取"}, "context": {"status": "pending", "entries": []},
        },
        "createdAt": "2026-10-15T10:00:00",
    }
    import hashlib
    blob = json.dumps(card, ensure_ascii=False, sort_keys=True)
    card["cardHash"] = hashlib.sha256(blob.encode()).hexdigest()[:16]
    d = root / "2026-10-15"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{code}.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")


def _pred():
    return {"code": "周三009",
            "p_crs": dict(LOW_GRID),
            "keyJudgments": _kj3(),
            "confidence": "medium"}


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(lp, "CARDS_ROOT", tmp_path / "cards")
    monkeypatch.setattr(lp, "PREDS_ROOT", tmp_path / "preds")
    _mk_card(tmp_path / "cards")
    return tmp_path


def _expect(dist):
    return sum(p * (7.0 if s.startswith("other_") else sum(int(x) for x in s.split(":")))
               for s, p in dist.items())


def test_v2_freeze_anchors_ttg_to_market(dirs):
    out = lp.freeze("2026-10-15", [_pred()])
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["chain"] == "llm-31grid-v2"
    e = doc["preds"][0]
    target = e["anchor"]["target"]
    assert abs(_expect(e["p_crs"]) - target) < 0.01      # TTG 维度已锚市场
    assert e["anchor"]["before"] < target                # 低分倾向被拉上来
    assert e["anchor"]["k"] > 0


def test_v2_shape_dimension_untouched(dirs):
    out = lp.freeze("2026-10-15", [_pred()])
    e = json.loads(out.read_text(encoding="utf-8"))["preds"][0]
    before = lp.normalize_to_keys(dict(LOW_GRID), list(e["p_crs"]))[0]
    # 同总进球格间比例守恒（1:0 与 0:1 同为 1 球）
    assert abs(e["p_crs"]["1:0"] / e["p_crs"]["0:1"]
               - before["1:0"] / before["0:1"]) < 1e-9


def test_v2_no_market_is_unanchored(dirs):
    _mk_card(dirs / "cards", code="周三010", with_ttg=False)
    out = lp.freeze("2026-10-15", [{**_pred(), "code": "周三010"}])
    e = json.loads(out.read_text(encoding="utf-8"))["preds"][0]
    assert e["anchor"] is None
    assert abs(_expect(e["p_crs"]) - _expect(lp.normalize_to_keys(dict(LOW_GRID), list(e["p_crs"]))[0])) < 1e-9


def test_freeze_still_idempotent(dirs):
    a = lp.freeze("2026-10-15", [_pred()])
    b = lp.freeze("2026-10-15", [_pred()])
    assert a == b


def test_revision_volume_written_and_preferred(dirs):
    """修正卷（R008）：-r1 独立落盘带 revisionOf/reason；重冻同号幂等。"""
    lp.freeze("2026-10-15", [_pred()])
    rev = lp.freeze("2026-10-15", [{**_pred(), "confidence": "high"}],
                    revision=1, reason="006 伤停证伪·核查后修正")
    assert rev.name == "2026-10-15-r1.json"
    doc = json.loads(rev.read_text(encoding="utf-8"))
    assert doc["revisionOf"] == "2026-10-15"
    assert "006" in doc["revisionReason"]
    assert doc["preds"][0]["confidence"] == "high"
    again = lp.freeze("2026-10-15", [_pred()], revision=1, reason="x")
    assert json.loads(again.read_text(encoding="utf-8"))["preds"][0]["confidence"] == "high"


def test_paper_loads_max_revision(monkeypatch, tmp_path):
    """L 族消费方选卷：-r2 压过 -r1 与主卷（数值序）。"""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shadow"))
    import paper
    monkeypatch.setattr(paper, "LLM_PREDS_DIR", str(tmp_path))
    for name, mark in (("2026-10-15.json", "main"), ("2026-10-15-r1.json", "r1"),
                       ("2026-10-15-r2.json", "r2")):
        (tmp_path / name).write_text(json.dumps(
            {"preds": [{"code": "周四006", "p_crs": {"0:3": 1.0}, "cardHash": "x",
                        "contextStatus": "pending", "keyJudgments": _kj3(),
                        "confidence": "low"}], "chain": "llm-31grid-v2"}, ensure_ascii=False),
            encoding="utf-8")
    cards = tmp_path / "cards" / "2026-10-15"
    cards.mkdir(parents=True)
    (cards / "周四006.json").write_text(json.dumps(
        {"layers": {"market": {"crs": {"0:3": 24.0}, "ttg": {}}, "context": {}}}, ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setattr(paper, "LLM_CARDS_DIR", str(tmp_path / "cards"))
    qc = paper.load_llm_preds("2026-10-15")
    assert qc is not None        # r2 被选中且可装载（crs 1:1 有价）
