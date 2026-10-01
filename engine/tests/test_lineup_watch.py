# -*- coding: utf-8 -*-
"""lineup_watch 临场首发采集器测试（全离线：scoreboard/summary/results/injury 注入假源，落 tmp_path）。

覆盖：五池 diff/replay 往返 / ESPN 对场（时刻容差+单侧命中+无别名+歧义）/ 首发解析（未公布判空）/
伤停轨（全在售窗建档 → 节流复查 → 变更快照 → 终窗每tick → 失败隔离 → 开赛后停查）/
tick 全链（窗口外不落盘 → 快照 → 首发查询 → 调价 diff 压缩 → 首发公布 → 停售检测 → 结算）。
开发者 sszhang
"""
from datetime import datetime

import lineup_watch as lw

KICK = datetime(2026, 10, 3, 21, 0)
ZH = {"曼城": "Manchester City", "阿森纳": "Arsenal"}


def _sub(mid, code, home, away, h="2.10", date_="2026-10-03", time_="21:00:00"):
    return {"matchId": mid, "matchNumStr": code, "leagueAbbName": "英超",
            "homeTeamAbbName": home, "awayTeamAbbName": away,
            "matchDate": date_, "matchTime": time_, "sellStatus": 1,
            "had": {"h": h, "d": "3.30", "a": "3.40"},
            "hhad": {"h": "4.10", "d": "3.80", "a": "1.62", "goalLine": "-1"},
            "crs": {"s01s00": "7.00", "s01s01": "6.50", "s1sh": "30.0"},
            "ttg": {"s0": "9.50", "s1": "4.60"},
            "hafu": {"hh": "3.30", "dd": "4.60"}}


def _calc(*subs):
    return {"value": {"matchInfoList": [{"businessDate": "2026-10-03", "subMatchList": list(subs)}]}}


def _ev(eid, when, home, away):
    return {"id": eid, "date": when, "competitions": [{"competitors": [
        {"homeAway": "home", "team": {"displayName": home}},
        {"homeAway": "away", "team": {"displayName": away}}]}]}


def _ros(side, team, n_starters):
    return {"homeAway": side, "team": {"displayName": team}, "formation": "4-3-3",
            "roster": [{"starter": i < n_starters, "jersey": str(i + 1),
                        "athlete": {"displayName": f"P{i}", "id": str(1000 + i)},
                        "position": {"abbreviation": "M"}} for i in range(18)]}


def test_diff_replay_roundtrip():
    prev = {"had": {"h": 2.1, "d": 3.3, "a": 3.4}, "crs": {"1:0": 7.0}}
    cur = {"had": {"h": 2.0, "d": 3.3, "a": 3.4}, "ttg": {"s0": 9.5}}
    d = lw.diff_pools(prev, cur)
    assert d == {"had": {"h": 2.0}, "crs": None, "ttg": {"s0": 9.5}}
    assert lw.replay([{"odds": prev}, {"odds": d}]) == cur
    shrink = lw.diff_pools({"crs": {"1:0": 7.0, "1:1": 6.5}}, {"crs": {"1:0": 7.0}})
    assert shrink == {"crs": {"1:1": None}}
    assert lw.replay([{"odds": {"crs": {"1:0": 7.0, "1:1": 6.5}}}, {"odds": shrink}]) == {"crs": {"1:0": 7.0}}
    assert lw.diff_pools(cur, cur) == {}


def test_match_event_rules():
    evs = [_ev("401", "2026-10-03T13:00Z", "Manchester City", "Arsenal"),
           _ev("402", "2026-10-03T13:00Z", "Liverpool", "Chelsea"),
           _ev("403", "2026-10-03T16:00Z", "Manchester City", "Arsenal")]
    assert lw.match_event(KICK, "曼城", "阿森纳", evs, ZH) == ("401", "both")
    assert lw.match_event(KICK, "曼城", "某未收录队", evs, ZH) == ("401", "home")
    assert lw.match_event(KICK, "韩国亚", "中国亚", evs, ZH) == (None, "no_alias")
    assert lw.match_event(datetime(2026, 10, 3, 23, 0), "曼城", "阿森纳", evs, ZH) == (None, "no_event")
    dup = evs + [_ev("404", "2026-10-03T13:10Z", "Manchester City", "Everton")]
    assert lw.match_event(KICK, "曼城", "阿森纳", dup, ZH) == (None, "ambiguous")


def test_parse_lineup_published_vs_not():
    lu = lw.parse_lineup({"rosters": [_ros("home", "Manchester City", 11), _ros("away", "Arsenal", 11)]})
    assert len(lu["home"]["starters"]) == 11 and lu["away"]["formation"] == "4-3-3"
    assert lu["home"]["starters"][0] == {"name": "P0", "id": "1000", "jersey": "1", "pos": "M"}
    assert lw.parse_lineup({"rosters": [{"homeAway": "home", "roster": []},
                                         {"homeAway": "away", "roster": []}]}) is None
    assert lw.parse_lineup({"rosters": [_ros("home", "X", 11), _ros("away", "Y", 5)]}) is None


def test_tick_full_chain(tmp_path):
    state = {"published": False}
    sb_calls, res_calls, inj_calls = [], [], []
    board = [_ev("401", "2026-10-03T13:00Z", "Manchester City", "Arsenal")]
    full = {"rosters": [_ros("home", "Manchester City", 11), _ros("away", "Arsenal", 11)]}
    empty = {"rosters": [{"homeAway": "home", "roster": []}, {"homeAway": "away", "roster": []}]}

    def sb(day):
        sb_calls.append(day)
        return board if day == "20261003" else []

    def summ(eid):
        return full if state["published"] else empty

    def res(d1, d2):
        res_calls.append((d1, d2))
        return {"900001": {"score": "2:1", "halfScore": "1:0"}}

    def inj(mid):
        inj_calls.append(mid)
        return {"h": [], "a": []}

    kw = dict(scoreboard=sb, summary=summ, results=res, zh_map=ZH, injury=inj)
    far = _sub(900009, "周一001", "曼城", "阿森纳", date_="2026-10-05")
    path = tmp_path / "2026-10-03-lineups.json"

    def at(hm, *subs):
        return lw.tick(datetime.strptime(f"2026-10-03 {hm}", lw.KICK_FMT), _calc(*subs), tmp_path, **kw)

    club = lambda h="2.10": _sub(900001, "周六001", "曼城", "阿森纳", h=h)
    asia = _sub(900002, "周六002", "韩国亚", "中国亚")

    at("17:30", club(), asia)                        # 开赛前 210 分：赔率窗外但伤停窗内 → 建档+伤停快照
    doc = lw.load_doc(tmp_path, "2026-10-03")
    assert set(doc["matches"]) == {"900001", "900002"}
    early = doc["matches"]["900001"]
    assert early["snapshots"] == [] and early["seenTicks"] == []          # 赔率轨不越窗
    assert len(early["injChecks"]) == 1 and len(early["injSnapshots"]) == 1
    assert early["closedDetectedAt"] is None                             # 在售场不得误判停售
    at("18:30", club(), asia)                        # 赔率窗内、首发窗外
    at("19:10", club(), asia)                        # 首发窗：对场成功但未公布
    doc = lw.load_doc(tmp_path, "2026-10-03")
    r1, r2 = doc["matches"]["900001"], doc["matches"]["900002"]
    assert len(r1["snapshots"]) == 1 and r1["lineup"] is None
    assert r1["espn"] == {"eventId": "401", "match": "both"}
    assert r1["lineupCheckedAt"] == "2026-10-03T19:10:00+08:00"
    assert r2["espn"] == {"eventId": None, "reason": "no_alias"} and r2["lineupCheckedAt"] is None
    assert len(sb_calls) == 3                        # 仅俱乐部场触发（无别名场不打 ESPN）

    state["published"] = True
    at("20:00", club(h="2.00"), asia)                # 调价 + 首发公布
    at("20:10", club(h="2.00"), asia)                # 首发已得：不再查；赔率未变：不加快照
    r1 = lw.load_doc(tmp_path, "2026-10-03")["matches"]["900001"]
    assert len(sb_calls) == 3                        # eventId 已缓存，不再扫 scoreboard
    assert [s["odds"] for s in r1["snapshots"]][1] == {"had": {"h": 2.0}}
    assert len(r1["snapshots"]) == 2 and len(r1["seenTicks"]) == 4
    assert r1["lineupSeenAt"] == "2026-10-03T20:00:00+08:00" and r1["lineupSource"] == "espn"
    assert r1["lineupSeenOdds"]["had"]["h"] == 2.0 and len(r1["lineup"]["away"]["starters"]) == 11

    at("21:05", far)                                  # 开赛后两场出清单 → 停售
    doc = lw.load_doc(tmp_path, "2026-10-03")
    assert doc["matches"]["900001"]["closedDetectedAt"] == "2026-10-03T21:05:00+08:00"
    assert doc["matches"]["900002"]["closedDetectedAt"] == "2026-10-03T21:05:00+08:00"
    assert not res_calls

    at("23:40", far)                                  # 开赛 160 分：结算
    doc = lw.load_doc(tmp_path, "2026-10-03")
    assert doc["matches"]["900001"]["result"] == {"score": "2:1", "halfScore": "1:0", "source": "sporttery-zqsgkj"}
    assert doc["matches"]["900002"]["result"] is None
    assert doc["matches"]["900002"]["resultCheckedAt"] == "2026-10-03T23:40:00+08:00"
    assert res_calls == [("2026-10-02", "2026-10-04")]
    at("23:50", far)                                  # 60 分内不重查未完赛场
    assert len(res_calls) == 1


def _p(name):
    return {"name": name, "pos": "前锋", "injury": True, "susp": False, "apps": 8, "starts": 7}


def test_parse_injuries_slim_and_sort():
    value = {"home": {"injuriesAndSuspensionsList": [
        {"personName": "B球员", "playerPositionDesc": "前锋", "injuryFlag": True, "suspensionFlag": False,
         "appearanceCnt": 8, "startedMatchCnt": 7},
        {"personName": "A球员", "playerPositionDesc": "后卫", "injuryFlag": False, "suspensionFlag": True,
         "appearanceCnt": 5, "startedMatchCnt": 3}]},
        "away": {}}
    out = lw.parse_injuries(value)
    assert out == {"h": [{"name": "A球员", "pos": "后卫", "injury": False, "susp": True, "apps": 5, "starts": 3},
                         _p("B球员")],
                   "a": []}
    assert lw.parse_injuries({}) == {"h": [], "a": []}


def test_injury_track(tmp_path):
    lists = {"900002": ([_p("哈兰德")], [])}
    fail = set()
    calls = []

    def inj(mid):
        calls.append(mid)
        if mid in fail:
            raise ValueError("boom")
        h, a = lists.get(mid, ([], []))
        return {"h": h, "a": a}

    kw = dict(injury=inj)
    asia = lambda: _sub(900002, "周六002", "韩国亚", "中国亚")   # 无别名：绕开 ESPN，专测伤停轨
    path = tmp_path / "2026-10-03-lineups.json"

    def at(day_hm, *subs):
        return lw.tick(datetime.strptime(day_hm, lw.KICK_FMT), _calc(*subs), tmp_path, **kw)

    at("2026-10-01 20:00", asia())                     # 开赛前 2 天：全在售窗建档+首条快照
    rec = lw.load_doc(tmp_path, "2026-10-03")["matches"]["900002"]
    assert rec["injChecks"] == ["2026-10-01T20:00:00+08:00"]
    assert rec["injSnapshots"] == [{"at": "2026-10-01T20:00:00+08:00", "inj": {"h": [_p("哈兰德")], "a": []}}]
    assert rec["snapshots"] == [] and rec["seenTicks"] == []
    assert rec["closedDetectedAt"] is None

    at("2026-10-01 20:10", asia())                     # 节流：120 分钟内不复查
    assert len(calls) == 1
    rec = lw.load_doc(tmp_path, "2026-10-03")["matches"]["900002"]
    assert len(rec["injChecks"]) == 1 and rec["closedDetectedAt"] is None

    at("2026-10-01 22:30", asia())                     # 节流过期：复查·名单未变 → 只记查询不加快照
    rec = lw.load_doc(tmp_path, "2026-10-03")["matches"]["900002"]
    assert len(rec["injChecks"]) == 2 and len(rec["injSnapshots"]) == 1

    lists["900002"] = ([_p("哈兰德"), _p("德布劳内")], [])
    at("2026-10-02 00:40", asia())                     # 名单变更 → 追加快照（22:30 后 130 分·节流已过期）
    rec = lw.load_doc(tmp_path, "2026-10-03")["matches"]["900002"]
    assert len(rec["injChecks"]) == 3 and len(rec["injSnapshots"]) == 2
    assert rec["injSnapshots"][1]["at"] == "2026-10-02T00:40:00+08:00"
    assert rec["injSnapshots"][1]["inj"]["h"] == [_p("哈兰德"), _p("德布劳内")]

    at("2026-10-03 18:30", asia())                     # 终窗（≤开赛前180分）：每 tick 查
    at("2026-10-03 18:40", asia())
    rec = lw.load_doc(tmp_path, "2026-10-03")["matches"]["900002"]
    assert len(rec["injChecks"]) == 5

    fail.add("900002")
    stat = at("2026-10-03 18:50", asia())              # 采集失败：计数隔离·不崩·不记查询
    assert stat["injErr"] == 1
    rec = lw.load_doc(tmp_path, "2026-10-03")["matches"]["900002"]
    assert len(rec["injChecks"]) == 5

    far = _sub(900009, "周一001", "曼城", "阿森纳", date_="2026-10-05")
    at("2026-10-03 21:05", far)                        # 开赛后：不查伤停；出清单 → 判停售
    rec = lw.load_doc(tmp_path, "2026-10-03")["matches"]["900002"]
    assert len(rec["injChecks"]) == 5
    assert rec["closedDetectedAt"] == "2026-10-03T21:05:00+08:00"
