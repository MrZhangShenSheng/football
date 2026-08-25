from score_ev import shrink, ev_scan, norm_score, map_league

def test_shrink_pulls_small_n_to_prior():
    assert abs(shrink(0.20, n=10, prior=0.10) - (0.2*10 + 0.1*50)/60) < 1e-9
    # 收缩偏离 = prior*50/(n+50) = 0.1*50/1050 ≈ 0.0048，brief 原 1e-3 容差过紧，放宽为 5e-3
    assert abs(shrink(0.20, n=1000, prior=0.10) - 0.20) < 5e-3   # 大样本几乎不收缩

def test_norm_score():
    assert norm_score("1", "0") == "1:0" and norm_score("2", "1") == "2:1"

def test_ev_scan_ranking():
    day = {"matches": [{"matchNumStr": "周一001", "league": "意甲", "home": "A", "away": "B",
                        "had": {"h": 1.5, "d": 4.0, "a": 6.0},
                        "crs": {"1:0": 6.5, "1:1": 5.75, "0:2": 17.0}}]}
    freq = {"意甲": {"__n": 1000, "1:0": 115, "1:1": 115, "0:2": 20}}   # 计数结构
    rows = ev_scan(day, freq)
    top = rows[0]
    assert top["matchNumStr"] == "周一001" and top["score"] == "1:0"
    # 单一联赛时 prior=全局=0.115, shrink(0.115,1000,0.115)=0.115 → ev=0.115*6.5-1
    assert abs(top["ev"] - (0.115 * 6.5 - 1)) < 1e-9
    assert all(rows[i]["ev"] >= rows[i+1]["ev"] for i in range(len(rows)-1))   # 降序

def test_map_league_known():
    assert map_league("意甲") == "italy-serie-a"
    assert map_league("英超") == "england-premier"
    assert map_league("沙职") == "saudi" and map_league("瑞超") == "sweden"

def test_map_league_unknown_returns_none():
    assert map_league("欧冠") is None and map_league("巴甲") is None

def test_build_freq_table_reads_league_dict_structure(monkeypatch, tmp_path):
    import json as _json
    from score_ev import build_freq_table
    monkeypatch.setattr("score_ev.fetch_rows", lambda *a, **k: [])
    lib = tmp_path / "korea_matches.json"
    lib.write_text(_json.dumps({"league": "korea", "source": "espn-history",
                                "seasons": ["2025"], "fetchedAt": "x", "matches": [
        {"date": "2025-03-01", "home": "a", "away": "b", "hg": 1, "ag": 0},
        {"date": "2025-03-02", "home": "c", "away": "d", "hg": 2, "ag": 2}]}), encoding="utf-8")
    monkeypatch.setattr("glob.glob", lambda p: [str(lib)] if "league" in p else [])
    table = build_freq_table()
    assert table["korea"]["__n"] == 2 and table["korea"]["1:0"] == 1 and table["korea"]["2:2"] == 1
