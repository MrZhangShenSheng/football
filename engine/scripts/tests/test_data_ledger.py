# engine/scripts/tests/test_data_ledger.py
# -*- coding: utf-8 -*-
"""v35 P2 数据账本测试。开发者 sszhang"""
import json

import data_ledger as dl


def test_scan_sporttery(tmp_path):
    (tmp_path / "2026-10-11.json").write_text(json.dumps(
        {"fetchedAt": "t", "matchDays": [{"businessDate": "2026-10-12",
          "matches": [{"matchNumStr": "周日001", "had": {"h": 2}, "oddsUpdatedAt": "t1"},
                      {"matchNumStr": "周日002", "had": {"h": 2}}]}]}, ensure_ascii=False), encoding="utf-8")
    r = dl.scan_sporttery(tmp_path)
    assert r["archives"] == 1 and r["matches"] == 2
    assert r["oddsUpdatedMatches"] == 1          # 调价率分子


def test_scan_pinclose(tmp_path):
    (tmp_path / "2026-10-11.json").write_text(json.dumps(
        {"matches": [{"pinClose": {"h": 2.0}}, {}, {"pinClose": {"h": 3.0}}]}), encoding="utf-8")
    r = dl.scan_pinclose(tmp_path)
    assert r["files"] == 1 and r["matches"] == 3 and r["withPinClose"] == 2


def test_scan_lineups(tmp_path):
    (tmp_path / "2026-10-13-lineups.json").write_text(json.dumps(
        {"matches": {"111": {"lineup": {"home": ["a"]}, "injuries": {"home": ["x"]}},
                     "222": {"lineup": None}}}, ensure_ascii=False), encoding="utf-8")
    r = dl.scan_lineups(tmp_path)
    assert r["files"] == 1 and r["matchEntries"] == 2
    assert r["lineupPublished"] == 1 and r["injuryEvents"] == 1


def test_scan_hst(tmp_path):
    (tmp_path / "hst_eng1.json").write_text(json.dumps(
        {"rows": [{"home": "A", "hst": 5, "date": "2026-10-01"},
                  {"home": "A", "hst": 4, "date": "2026-09-01"}]}), encoding="utf-8")
    r = dl.scan_hst(str(tmp_path))
    assert r["eng1"]["rows"] == 2 and r["eng1"]["lastDate"] == "2026-10-01"
