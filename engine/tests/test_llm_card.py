# engine/tests/test_llm_card.py
import sys, json, pathlib, pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from llm_card import build_card, save_card, card_hash, card_paths, match_id_index, write_context


def _mini_archive(tmp_path):
    m = {"matchNumStr": "周二001", "league": "英超", "home": "阿森纳", "away": "切尔西",
         "matchTime": "2026-10-14 03:45", "sellStatus": 1,
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


def test_match_id_index_reverse_lookup(tmp_path):
    # 迷你 sporttery_matches.json：每场含 matchId+code，code=matchNumStr 口径
    doc = {"fetchedAt": "2026-10-13T10:00:00", "source": "sporttery",
           "matches": [{"matchId": 2041921, "code": "周一001", "home": "A", "away": "B"},
                       {"matchId": 2041922, "code": "周一002", "home": "C", "away": "D"},
                       {"code": "周一003"},                       # 无 matchId → 跳过
                       {"matchId": 2041924}]}                     # 无 code → 跳过
    f = tmp_path / "sporttery_matches.json"
    f.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert match_id_index(f) == {"周一001": 2041921, "周一002": 2041922}
    # 文件缺失 → 空 dict（gen 里 mid=None → intel 层诚实降级"未获取"）
    assert match_id_index(tmp_path / "nope.json") == {}


def _mk_card(tmp_path):
    card = build_card("2026-10-13",
                      {"matchNumStr": "周二001", "league": "英超", "home": "A", "away": "B",
                       "matchTime": "t", "had": {}, "crs": {}, "ttg": {}},
                      insight_path=None, lineups={}, now="2026-10-13T18:00:00")
    save_card(card, "2026-10-13", root=tmp_path)
    return card


def test_context_filters_post_match(tmp_path):
    _mk_card(tmp_path)
    entries = [
        {"title": "Team news: key midfielder doubts", "url": "u1",
         "publishedAt": "2026-10-13T12:00:00", "excerpt": "preview of rotation plans"},   # 收
        {"title": "FT: Arsenal 3-0 Chelsea", "url": "u2",
         "publishedAt": "2026-10-13T12:00:00", "excerpt": "full-time report"},           # 赛后词→丢
        {"title": "Preview piece", "url": "u3",
         "publishedAt": "2026-10-13T19:00:00", "excerpt": "fine"},                        # 晚于 createdAt→丢
    ]
    r = write_context("2026-10-13", "周二001", entries, root=tmp_path)
    assert r["accepted"] == 1 and len(r["dropped"]) == 2
    card = json.loads((tmp_path / "2026-10-13" / "周二001.json").read_text(encoding="utf-8"))
    assert card["layers"]["context"]["status"] == "ok"
    assert card["layers"]["context"]["entries"][0]["url"] == "u1"


def test_context_frozen_after_written(tmp_path):
    _mk_card(tmp_path)
    write_context("2026-10-13", "周二001", [{"title": "t", "url": "u",
                                             "publishedAt": "2026-10-13T10:00:00", "excerpt": "x"}],
                  root=tmp_path)
    with pytest.raises(RuntimeError):
        write_context("2026-10-13", "周二001", [{"title": "t2", "url": "u2",
                                                 "publishedAt": "2026-10-13T10:00:00", "excerpt": "x"}],
                      root=tmp_path)   # 终态禁二次改写
