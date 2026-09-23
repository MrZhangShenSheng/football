# -*- coding: utf-8 -*-
"""euro_fetch 单测：日期页解析 / custom 解析 / merge / 降级 / euro_anchor 查询（fixture 基于真实页面结构）。"""
import json

import pytest

from euro_fetch import euro_anchor, fetch_text, merge_odds, parse_custom, parse_day_page

# 真实结构样本（2026-09-16 竞彩日期页，<div class="touzhu_1"> 行 + liansai 子块）
FIXTURE_HTML = """
<div class="touzhu_1" hover="hover2" data-end="1" id="match_1346979" data-mid="1346979"
  data-morder="3001" data-ordercn="周三001" data-rq="-1" filterdata="-1,474,2.03"
  data-hname="中国" data-aname="朝鲜" data-rev="N"> <div class="liansai">
  <span class="xulie" title="周三001">001</span>
  <a class="saiming aochao" href="//www.okooo.com/soccer/league/474/" title="亚运男足">亚运男足</a>
  <div class="shijian" mTime="18:00" title="比赛时间:2026-09-16 18:00:00">18:00</div> </div> </div>
<div class="touzhu_1" data-mid="1346981" data-ordercn="周三003" data-rq="-4"
  data-hname="日本" data-aname="中国"> <div class="liansai">
  <div class="shijian" title="比赛时间:2026-09-16 18:30:00">18:30</div> </div> </div>
<div class="touzhu_1" data-mid="999" data-hname="无编号行" data-aname="跳过"> </div>
"""

FIXTURE_CUSTOM = ('{"match_custom_response":{"1346979":{"home":"1.56","draw":"4.12","away":"5.24"},'
                  '"1346981":{"home":"bad","draw":"3.0","away":"2.0"}}}')


def test_parse_day_page():
    rows = parse_day_page(FIXTURE_HTML)
    assert len(rows) == 2                      # 无 data-ordercn 的行跳过
    r1 = rows[0]
    assert r1["orderCn"] == "周三001" and r1["matchId"] == "1346979"
    assert r1["home"] == "中国" and r1["away"] == "朝鲜" and r1["rq"] == -1
    assert r1["league"] == "亚运男足" and r1["leagueId"] == "474"
    assert r1["kickoff"] == "2026-09-16 18:00:00"
    r2 = rows[1]                               # 缺联赛：容忍置 None
    assert r2["league"] is None and r2["rq"] == -4


def test_parse_custom():
    odds = parse_custom(FIXTURE_CUSTOM)
    assert odds["1346979"] == {"home": 1.56, "draw": 4.12, "away": 5.24}
    assert "1346981" not in odds               # 非法数字整条丢弃
    assert parse_custom("not json") == {}
    assert parse_custom('{"match_custom_response": null}') == {}


def test_merge_odds():
    rows = parse_day_page(FIXTURE_HTML)
    odds = parse_custom(FIXTURE_CUSTOM)
    merged = merge_odds(rows, odds)
    assert merged[0]["euroAvg"]["home"] == 1.56
    assert merged[1]["euroAvg"] is None        # 场次保留、欧指置 null


def test_fetch_text_degrades(monkeypatch):
    import euro_fetch

    def _boom(url, timeout=20):
        raise IOError("boom")

    monkeypatch.setattr(euro_fetch, "_get", _boom)
    assert fetch_text("http://x", retries=0) is None   # 降级不抛异常


class TestEuroAnchor:
    """归因链查询接口：当日欧指存档 → 去水三向（缺档/缺场/坏数据 None）。"""

    def _setup_dir(self, tmp_path, monkeypatch):
        import euro_fetch
        monkeypatch.setattr(euro_fetch, "OUT_DIR", tmp_path)
        (tmp_path / "2026-09-16.json").write_text(json.dumps({
            "date": "2026-09-16",
            "matches": [{"orderCn": "周三001", "euroAvg": {"home": 2.0, "draw": 4.0, "away": 4.0}},
                        {"orderCn": "周三002", "euroAvg": None}]}, ensure_ascii=False), encoding="utf-8")
        return euro_fetch

    def test_hit(self, tmp_path, monkeypatch):
        ef = self._setup_dir(tmp_path, monkeypatch)
        out = euro_anchor("2026-09-16", "周三001")     # 1/2,1/4,1/4 归一
        assert out == pytest.approx([0.5, 0.25, 0.25])

    def test_missing_order(self, tmp_path, monkeypatch):
        self._setup_dir(tmp_path, monkeypatch)
        assert euro_anchor("2026-09-16", "周三002") is None   # 场次在但无欧指
        assert euro_anchor("2026-09-16", "周三999") is None   # 无此场
        assert euro_anchor("2026-09-16", None) is None

    def test_missing_day_or_bad_file(self, tmp_path, monkeypatch):
        self._setup_dir(tmp_path, monkeypatch)
        assert euro_anchor("2026-08-01", "周三001") is None   # 无存档日
        (tmp_path / "2026-09-17.json").write_text("not json", encoding="utf-8")
        assert euro_anchor("2026-09-17", "周三001") is None   # 坏文件降级
