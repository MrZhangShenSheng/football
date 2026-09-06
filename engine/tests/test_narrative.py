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


# ── I-2 最小桥: 落卡→paper.register 登记 track="N" 影子票 ──
class _FakePaper:
    """影子层替身: 记录 register 调用, 不写真账本(final-fix I-2 测试口径)"""

    def __init__(self, existing=()):
        self.calls, self.existing = [], list(existing)

    def load_tickets(self, path=None):
        return self.existing

    def register(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": "P999", **kwargs}

LEG_A = {"code": "周日013", "match": "巴伦西亚-巴萨", "script": {"score": "1:0"}, "star": 5}
LEG_B = {"code": "周一004", "match": "卡尔马-佐加顿斯", "script": {"score": "1:0"}, "star": 5}

def _card_with(play):
    return {"date": "2026-09-06", "seq": 1, "candidates": [], "plays": [play], "track": "N"}

def _crs_pools():
    return {"周日013": {"crs": {"s01s00": "28.00", "s00s00": "8.5"}},
            "周一004": {"crs": {"s01s00": "14.00"}}}

def _play_crs():
    return {"name": "N-甲", "playType": "N-CRS-2x1", "legs": [LEG_A, LEG_B],
            "mult": None, "star": 5}

def test_shadow_bridge_registers_track_n():
    # 桥接: register 带 track="N"、spec_name 带 N- 前缀(幂等键独立命名)
    fake = _FakePaper()
    registered = nr.register_shadow(_card_with(_play_crs()), _crs_pools(), paper_mod=fake)
    assert registered == ["N-CRS-2x1-2026-09-06"]
    kw = fake.calls[0]
    assert kw["track"] == "N"
    assert kw["spec_name"].startswith("N-") and kw["date"] in kw["spec_name"]
    assert kw["date"] == "2026-09-06"
    assert kw["legs"][0] == {"code": "周日013", "match": "巴伦西亚-巴萨",
                             "market": "crs", "pick": ["1:0"], "odds": {"1:0": 28.0}}
    assert kw["bets"] == [{"legs": [[0, "1:0"], [1, "1:0"]]}]   # CRS 2串1 一注
    assert kw["mult"] == 1 and kw["cost"] == 2                  # mult=None→1倍, 1注×2元

def test_shadow_bridge_idempotent_by_spec_and_date():
    # register 自身不查重: 重复登记由桥内 (spec_name,date) 自查拦截
    fake = _FakePaper(existing=[{"spec_name": "N-CRS-2x1-2026-09-06",
                                 "date": "2026-09-06"}])
    assert nr.register_shadow(_card_with(_play_crs()), _crs_pools(), paper_mod=fake) == []
    assert fake.calls == []

def test_shadow_bridge_skips_leg_without_pool_price():
    # 腿 crs 池缺价: 整票跳过(不登记结算 odds[key] 取键会 KeyError 的残票)
    pools = _crs_pools()
    pools.pop("周一004")
    fake = _FakePaper()
    assert nr.register_shadow(_card_with(_play_crs()), pools, paper_mod=fake) == []
    assert fake.calls == []

def test_shadow_bridge_tolerates_missing_shapes(monkeypatch):
    # fresh clone 缺 shapes: _load_paper 抛 ImportError → 桥警告跳过不炸 CLI
    def _boom():
        raise ImportError("No module named 'shapes'")
    monkeypatch.setattr(nr, "_load_paper", _boom)
    assert nr._shadow_bridge(_card_with(_play_crs()), []) == []
