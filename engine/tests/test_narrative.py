# engine/tests/test_narrative.py
import narrative as nr

FAKE_MATCH = {"matchNumStr": "周六016", "league": "西甲", "home": "毕尔巴鄂", "away": "马竞",
              "had": {"h": 2.8, "d": 3.1, "a": 2.5}}
FAKE_PROFILE = {"standings": [], "scoreTop": {"2-0": 9, "1-0": 12, "1-1": 10},
                "drawRate": 0.24, "upsetRate": 0.18}
FAKE_TEAM = {"formSummary": {"last10": "6胜2平2负", "goalAvg": "1.9"}}

def test_five_layers_all_present():
    cand = nr.build_candidate(FAKE_MATCH, FAKE_PROFILE,
                              {"毕尔巴鄂": FAKE_TEAM, "马竞": FAKE_TEAM})
    names = [l["layer"] for l in cand["layers"]]
    assert names == ["strength", "form", "absence", "style", "script"]

def test_script_from_score_matrix():
    cand = nr.build_candidate(FAKE_MATCH, FAKE_PROFILE,
                              {"毕尔巴鄂": FAKE_TEAM, "马竞": FAKE_TEAM})
    assert cand["script"]["score"] in {"1:0", "2:0", "2:1", "0:0", "1:1", "0:1", "1:2", "2:2"}
    assert cand["script"]["hafu"] in {"hh", "hd", "ha", "dh", "dd", "da", "ah", "ad", "aa"}

def test_star_range_1_to_5():
    cand = nr.build_candidate(FAKE_MATCH, FAKE_PROFILE,
                              {"毕尔巴鄂": FAKE_TEAM, "马竞": FAKE_TEAM})
    assert 1 <= cand["star"] <= 5

def test_divergence_is_not_ev():
    # 分歧度=剧本概率-市场隐含(设计§十一): 只用于排序, 不算期望收益
    cand = nr.build_candidate(FAKE_MATCH, FAKE_PROFILE,
                              {"毕尔巴鄂": FAKE_TEAM, "马竞": FAKE_TEAM})
    assert isinstance(cand["divergence"], float)

def test_plays_carry_playtype():
    # 票面形状标记(设计§十二): playType必须落 N-CRS-2x1/N-HAFU-3x1/N-MIX-2x1
    card = nr.build_narrative([FAKE_MATCH], {"西甲": FAKE_PROFILE},
                              {"毕尔巴鄂": FAKE_TEAM, "马竞": FAKE_TEAM}, seq=1)
    for p in card["plays"]:
        assert p["playType"] in ("N-CRS-2x1", "N-HAFU-3x1", "N-MIX-2x1")
