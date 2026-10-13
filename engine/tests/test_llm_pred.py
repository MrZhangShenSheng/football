# engine/tests/test_llm_pred.py
import sys, json, pathlib, pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from llm_pred import validate_pred, normalize_to_keys, freeze


def test_validate_and_issues():
    keys = ["1:1", "1:0", "0:1"]
    ok = {"code": "周二001", "p_crs": {"1:1": 0.5, "1:0": 0.3, "0:1": 0.2},
          "keyJudgments": [{"judgment": "j", "evidence": "语境层#1"}], "confidence": "medium"}
    good, issues = validate_pred(ok, keys)
    assert good and not issues
    bad = dict(ok, p_crs={"1:1": 0.9, "9:9": 0.1})                    # 9:9 不在键集
    good2, issues2 = validate_pred(bad, keys)
    assert not good2 and any("9:9" in i for i in issues2)
    bad2 = dict(ok, keyJudgments=[])                                   # 无判断
    good3, issues3 = validate_pred(bad2, keys)
    assert not good3


def test_normalize_to_keys():
    p, raw_sum = normalize_to_keys({"1:1": 0.5, "1:0": 0.25, "0:1": 0.25, "2:2": 0.5},
                                   ["1:1", "1:0", "0:1"])
    assert abs(raw_sum - 1.5) < 1e-9
    assert abs(sum(p.values()) - 1.0) < 1e-9 and "2:2" not in p


def test_freeze_idempotent_and_strict(tmp_path, monkeypatch):
    monkeypatch.setattr("llm_pred.PREDS_ROOT", tmp_path)
    # 造卡（freeze 校验 cardHash 链）
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    from llm_card import build_card, save_card
    # fixture 修正：迷你完整 crs 池（i,j∈0..4 全积 25 s 键 + other 三键 = 28 键），
    # 预测 p_crs 用其中 3 键（1:1=s01s01 / 1:0=s01s00 / 0:1=s00s01）——真实卡为 s 键，
    # freeze 的 sXXsYY→pick 转换后键集须完整覆盖预测键集
    crs = {f"s{i:02d}s{j:02d}": 5.0 + i + j for i in range(5) for j in range(5)}
    crs.update({"s1sh": 30.0, "s1sd": 30.0, "s1sa": 30.0})
    card = build_card("2026-10-13",
                      {"matchNumStr": "周二001", "league": "英超", "home": "A", "away": "B",
                       "matchTime": "t", "had": {}, "crs": crs, "ttg": {}},
                      insight_path=None, lineups={}, now="2026-10-13T18:00:00")
    save_card(card, "2026-10-13", root=tmp_path / "cards")
    monkeypatch.setattr("llm_pred.CARDS_ROOT", tmp_path / "cards")
    pred = {"code": "周二001", "p_crs": {"1:1": 0.5, "1:0": 0.3, "0:1": 0.2},
            "keyJudgments": [{"judgment": "j", "evidence": "e"}], "confidence": "medium"}
    p1 = freeze("2026-10-13", [pred])
    assert p1.exists()
    p2 = freeze("2026-10-13", [dict(pred, confidence="high")])     # 幂等不覆写
    doc = json.loads(p1.read_text(encoding="utf-8"))
    assert doc["preds"][0]["confidence"] == "medium"
    assert len(doc["preds"][0]["cardHash"]) == 16
