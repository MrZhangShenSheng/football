# -*- coding: utf-8 -*-
"""web_preds 测试 10 例（设计 §3）。开发者 sszhang"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import web_preds as wp


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    """全测试重定向到 tmp（勿碰真实账本）。"""
    monkeypatch.setattr(wp, "TRENDS", tmp_path / "05-trends")
    monkeypatch.setattr(wp, "RESULTS", tmp_path / "02-results")
    monkeypatch.setattr(wp, "SUMMARIES", tmp_path / "04-summaries")
    monkeypatch.setattr(wp, "SOURCES_MAP", tmp_path / "web_sources.json")
    monkeypatch.setattr(wp, "MATCHES_CACHE", tmp_path / "sporttery_matches.json")
    for d in ("05-trends", "02-results", "04-summaries"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "sporttery_matches.json").write_text(json.dumps(
        {"matches": [{"code": "周三007", "matchTime": "2026-10-15 02:45"},
                     {"code": "周三014", "matchTime": "2026-10-15 08:30"},
                     {"code": "周三011", "matchTime": "2026-10-15 03:00"}]}), encoding="utf-8")
    return tmp_path


def _entry(**kw):
    base = {"code": "周三007", "source": "Sports Mole", "pick": "1:1",
            "publishedAt": "2026-10-13T18:00", "url": "https://x.test/a", "pickType": "source_exact"}
    base.update(kw)
    return base


def _write(tmp_path, rows):
    f = tmp_path / "entries.json"
    f.write_text(json.dumps({"entries": rows}, ensure_ascii=False), encoding="utf-8")
    return f


def test_schema_七条反例各拒(_iso):
    bad = [
        _entry(pick="1-1"),                                    # ① 格式
        _entry(url="ftp://x"),                                 # ② url
        _entry(publishedAt="2026-10-15T03:30"),                # ③ 赛后
        _entry(code="周三999"),                                 # ④ 不在售
        _entry(source=""),                                     # ⑤ 缺源
    ]
    for row in bad:
        assert wp.record("2026-10-14", _write(_iso, [row])) == 1
    # ⑥ 同批重复
    assert wp.record("2026-10-14", _write(_iso, [_entry(), _entry()])) == 1
    # 全批拒绝后不落盘
    assert not wp.preds_path("2026-10-14").exists()


def test_outcome派生_与_slug归一(_iso):
    rows = [_entry(source="Sports Mole", pick="1:1"),
            _entry(source="sportsmole", code="周三014", pick="2:1"),
            _entry(source="Statz.ai", code="周三011", pick="0:2")]
    assert wp.record("2026-10-14", _write(_iso, rows)) == 0
    doc = wp.load_preds("2026-10-14")
    got = {(e["source"], e["pick"], e["outcome"]) for e in doc["entries"]}
    assert ("Sports-Mole", "1:1", "draw") in got
    assert ("Sportsmole", "2:1", "home") in got      # slug 归一后不同源名各自成源
    assert ("Statz-Ai", "0:2", "away") in got


def test_冻结幂等_同批拒_二批增(_iso):
    assert wp.record("2026-10-14", _write(_iso, [_entry()])) == 0
    assert wp.record("2026-10-14", _write(_iso, [_entry()])) == 1          # 同源同场再录=拒
    assert wp.record("2026-10-14", _write(_iso, [_entry(source="Forebet")])) == 0   # 二批追加
    doc = wp.load_preds("2026-10-14")
    assert len(doc["entries"]) == 2 and len(doc["batches"]) == 2
    assert doc["batches"][0]["batch"] == 1 and doc["batches"][1]["batch"] == 2


def test_方向型pick_可录_且不拖比分率(_iso):
    """多数源只给方向——须能直录，且 scoreHit=None 不入比分率分母（008 转译失真教训）。"""
    wp.record("2026-10-14", _write(_iso, [
        _entry(source="DirOnly", pick="home", pickType="source_exact"),
        _entry(source="ScoreSrc", pick="1:2", pickType="source_exact")]))
    _results(_iso, "2026-10-15", [
        {"code": "周三007", "result": "1-2", "odds": {"h": 2.2, "d": 3.55, "a": 2.55}}])
    wp.settle("2026-10-14")
    doc = wp.load_preds("2026-10-14")
    by = {e["source"]: e for e in doc["entries"]}
    assert by["DirOnly"]["outcome"] == "home"                      # 方向型直通
    assert by["DirOnly"]["settled"]["directionHit"] is False       # 实际客胜
    assert by["DirOnly"]["settled"]["scoreHit"] is None            # 无比分可判→None
    board = wp.leaderboard()["sources"]
    assert board["DirOnly"]["n"] == 1 and board["DirOnly"]["n_score"] == 0
    assert board["DirOnly"]["scoreRate"] is None                   # 不被结构性拖低
    assert board["ScoreSrc"]["n_score"] == 1 and board["ScoreSrc"]["scoreRate"] == 1.0


def test_pickType必填_防转译失真(_iso):
    bad = dict(_entry())
    bad.pop("pickType")
    assert wp.record("2026-10-14", _write(_iso, [bad])) == 1
    assert wp.record("2026-10-14", _write(_iso, [_entry(pickType="瞎填")])) == 1
    assert wp.record("2026-10-14", _write(_iso, [_entry(pickType="session_derived")])) == 0


def test_共识检测_比分与方向(_iso):
    rows = [_entry(source="A", pick="1:1"), _entry(source="B", pick="1:1"),
            _entry(source="C", pick="1:2"), _entry(source="D", pick="0:3")]
    wp.record("2026-10-14", _write(_iso, rows))
    doc = wp.load_preds("2026-10-14")
    kinds = {(c["kind"], c["value"], tuple(c["members"])) for c in doc["consensus"]}
    assert ("score", "1:1", ("A", "B")) in kinds                      # ≥2 源同比分
    assert ("direction", "away", ("C", "D")) not in kinds             # 仅 2 源同方向→不成例
    rows2 = [_entry(source="E", pick="0:2")]
    wp.record("2026-10-14", _write(_iso, rows2))
    doc = wp.load_preds("2026-10-14")
    assert any(c["kind"] == "direction" and c["value"] == "away" and len(c["members"]) == 3
               for c in doc["consensus"])                             # 补第3源→方向共识成例


def _results(_iso, day, rows):
    (_iso / "02-results" / f"{day}.json").write_text(json.dumps(
        {"date": day, "matches": rows}, ensure_ascii=False), encoding="utf-8")


def test_settle_三判据(_iso):
    wp.record("2026-10-14", _write(_iso, [
        _entry(source="A", pick="1:1"),                  # 实际 1-2：方向错
        _entry(source="B", pick="1:2"),                  # 精确命中
        _entry(source="C", code="周三014", pick="2:1")]))  # 实际 2-1：精确命中
    _results(_iso, "2026-10-15", [
        {"code": "周三007", "result": "1-2", "odds": {"h": 2.2, "d": 3.55, "a": 2.55}},
        {"code": "周三014", "result": "2-1", "odds": {"h": 2.1, "d": 3.68, "a": 2.62}}])
    assert wp.settle("2026-10-14") == 0
    doc = wp.load_preds("2026-10-14")
    by = {(e["source"], e["code"]): e for e in doc["entries"]}
    a, b, c = by[("A", "周三007")], by[("B", "周三007")], by[("C", "周三014")]
    assert a["settled"]["directionHit"] is False and a["settled"]["scoreHit"] is False
    assert b["settled"]["directionHit"] is True and b["settled"]["scoreHit"] is True
    assert c["settled"]["scoreHit"] is True
    # 市场对标：007 热门=主(2.2 最低)，B 押客→偏离场且命中
    assert b["settled"]["vsMarket"] == {"favOutcome": "home", "isDeviation": True}
    # 014 热门=主(2.1)，C 押主→非偏离
    assert c["settled"]["vsMarket"]["isDeviation"] is False


def test_settle_赛果跨日_day正负1索引(_iso):
    wp.record("2026-10-14", _write(_iso, [_entry(source="A", pick="1:2")]))
    _results(_iso, "2026-10-14", [{"code": "周三007", "result": "1-2", "odds": {"h": 2.2, "d": 3.55, "a": 2.55}}])
    assert wp.settle("2026-10-14") == 0
    doc = wp.load_preds("2026-10-14")
    assert doc["entries"][0]["settled"]["scoreHit"] is True


def test_settle_幂等_不改已结算(_iso):
    wp.record("2026-10-14", _write(_iso, [_entry(source="A", pick="1:2")]))
    _results(_iso, "2026-10-15", [{"code": "周三007", "result": "1-2", "odds": {"h": 2.2, "d": 3.55, "a": 2.55}}])
    wp.settle("2026-10-14")
    first = wp.load_preds("2026-10-14")["entries"][0]["settled"]["settledAt"]
    _results(_iso, "2026-10-15", [{"code": "周三007", "result": "3-0", "odds": {"h": 2.2, "d": 3.55, "a": 2.55}}])
    wp.settle("2026-10-14")                                 # 赛果被篡改也不改既有结算
    again = wp.load_preds("2026-10-14")["entries"][0]["settled"]
    assert again["settledAt"] == first and again["actual"] == "1:2"


def test_leaderboard_n不足报积累中(_iso):
    wp.record("2026-10-14", _write(_iso, [_entry(source="A", pick="1:2")]))
    _results(_iso, "2026-10-15", [{"code": "周三007", "result": "1-2", "odds": {"h": 2.2, "d": 3.55, "a": 2.55}}])
    wp.settle("2026-10-14")
    board = wp.leaderboard()
    row = board["sources"]["A"]
    assert row["n"] == 1 and row["verdict"].startswith("积累中 1/30")
    assert row["dirRate"] == 1.0                       # 数字照出，判定留白（llm verdict 同款）
    assert row["vsMarket"] is None                       # n_dev<15 不报


def test_leaderboard_对标配对同集(_iso):
    """对标差=源偏离场方向率 − 同场次集上市场热门命中率（判据③配对口径）。"""
    rows, res = [], []
    for i in range(16):
        code = f"周三{i + 1:03d}"
        rows.append(_entry(source="A", code=code, pick="0:1"))        # 全押客=全偏离(热门主)
        res.append({"code": code, "result": "0-1" if i < 8 else "2-0",
                    "odds": {"h": 1.8, "d": 3.5, "a": 4.0}})
    (_iso / "sporttery_matches.json").write_text(json.dumps(
        {"matches": [{"code": f"周三{i + 1:03d}", "matchTime": "2026-10-15 03:00"} for i in range(16)]}),
        encoding="utf-8")
    wp.record("2026-10-14", _write(_iso, rows))
    _results(_iso, "2026-10-15", res)
    wp.settle("2026-10-14")
    row = wp.leaderboard()["sources"]["A"]
    assert row["n_dev"] == 16
    assert row["devHitRate"] == pytest.approx(0.5)            # 源偏离场命中 8/16
    assert row["marketHitOnDevSet"] == pytest.approx(0.5)     # 同集上热门(主)命中 8/16
    assert row["vsMarket"] == pytest.approx(0.0)          # 无信息差（复读机现形口径）


def test_why_三向回溯(_iso, capsys):
    wp.record("2026-10-14", _write(_iso, [
        _entry(source="A", pick="1:1"), _entry(source="B", code="周三014", pick="2:1")]))
    assert wp.why("2026-10-14") is not None                   # 按日
    assert wp.why("A") is not None                            # 按源
    assert wp.why("周三007") is not None                       # 按场
    out = capsys.readouterr().out
    assert "周三007" in out and "A" in out
