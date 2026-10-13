# engine/tests/test_llm_card.py
import sys, json, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from llm_card import build_card, save_card, card_hash, card_paths


def _mini_archive(tmp_path):
    m = {"matchNumStr": "周二001", "league": "英超", "home": "阿森纳", "away": "切尔西",
         "matchTime": "2026-10-14 03:45", "sellStatus": "ON_SALE",
         "had": {"h": 1.5, "d": 4.0, "a": 5.0},
         "crs": {"s01s00": 8.0, "s01s01": 7.0}, "ttg": {"s0": 3.0}, "hafu": {}, "hhad": {}}
    doc = {"fetchedAt": "2026-10-13T10:00:00", "source": "sporttery",
           "matchDays": [{"businessDate": "2026-10-13", "matches": [m]}]}
    p = tmp_path / "2026-10-13.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


def test_build_card_layers_and_hash(tmp_path):
    card = build_card("2026-10-13",
                      {"matchNumStr": "周二001", "league": "英超", "home": "阿森纳",
                       "away": "切尔西", "matchTime": "2026-10-14 03:45",
                       "had": {"h": 1.5, "d": 4.0, "a": 5.0},
                       "crs": {"s01s00": 8.0}, "ttg": {"s0": 3.0}},
                      insight_path=None, lineups={})
    assert card["code"] == "周二001"
    assert set(card["layers"]) == {"market", "intel", "profile", "lineup", "context"}
    assert card["layers"]["market"]["crs"] == {"s01s00": 8.0}
    assert card["layers"]["intel"]["status"] == "未获取"          # insight_path=None 诚实降级
    assert card["layers"]["context"] == {"status": "pending", "entries": []}
    assert len(card_hash(card)) == 16
    # 同卡同 hash（createdAt 固定注入时）
    c2 = dict(card); c2["layers"] = dict(card["layers"])
    assert card_hash(c2) == card_hash(card)


def test_save_card_idempotent_no_overwrite(tmp_path):
    card = build_card("2026-10-13",
                      {"matchNumStr": "周二001", "league": "英超", "home": "A", "away": "B",
                       "matchTime": "t", "had": {}, "crs": {}, "ttg": {}},
                      insight_path=None, lineups={})
    p1 = save_card(card, "2026-10-13", root=tmp_path)
    # 篡改后再存 → 不覆写，返回原路径
    card["layers"]["market"]["crs"] = {"HACKED": 1.0}
    p2 = save_card(card, "2026-10-13", root=tmp_path)
    assert p1 == p2
    assert "HACKED" not in json.loads(p1.read_text(encoding="utf-8"))["layers"]["market"]["crs"]
