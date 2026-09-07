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


# ── P1 剧本层接DC矩阵(设计§五①): matrix口径/风格降级/JSD分歧度 ──
import dc_predict


def _fake_matrix(lh=1.8, la=0.9, rho=0.0):
    return dc_predict.score_matrix(lh, la, rho)


def test_script_layer_matrix_beats_style():
    dc = {"lh": 1.8, "la": 0.9, "rho": 0.0}
    out = nr._script_layer({"home": "x", "away": "y"}, style={}, dc=dc)
    mat = _fake_matrix()
    best = max(nr.SCRIPT_UNIVERSE, key=lambda s: mat[int(s[0]), int(s[2])])
    assert out["score"] == best and out["source"] == "matrix"
    assert 0.0 < out["prob"] <= 1.0


def test_script_layer_fallback_style():
    # style 实际结构=_style_layer 产出 {'topScores': {'1-1': 10}} 短横线键(brief 示意的
    # 'top' 元组不存在, 以现有代码为准): 域内最高频模板比分胜出, source 标记降级口径
    style = {"layer": "style", "topScores": {"1-1": 10, "2-0": 9}}
    out = nr._script_layer({"home": "x", "away": "y"}, style, dc=None)
    assert out["source"] == "style" and out["score"] == "1:1"


def test_divergence_zero_when_identical():
    mat = _fake_matrix()
    # 用矩阵自身剧本域分布构造 crs_odds, JSD 应为 0
    p = {s: mat[int(s[0]), int(s[2])] for s in nr.SCRIPT_UNIVERSE}
    odds = {s: 1.0 / v for s, v in p.items()}
    assert nr.divergence(mat, odds) < 1e-9


def test_build_candidate_records_script_source():
    # 接线: dc 有效→matrix 口径; dc 缺省→降级 style(旧路径), script_source 逐场可追溯
    teams = {"毕尔巴鄂": FAKE_TEAM, "马竞": FAKE_TEAM}
    with_dc = nr.build_candidate(FAKE_MATCH, FAKE_PROFILE, teams,
                                 dc={"lh": 1.8, "la": 0.9, "rho": 0.0})
    without_dc = nr.build_candidate(FAKE_MATCH, FAKE_PROFILE, teams)
    assert with_dc["script_source"] == "matrix"
    assert without_dc["script_source"] == "style"


# ── 终审Fix1: _dc_context ρ二次裸读坏缓存护栏(健康缓存行为零变化) ──
import json

import common

_DC_FIXTURE = {"teams": {"alpha-fc": {"attack": 0.2, "defense": -0.1},
                         "beta-fc": {"attack": -0.3, "defense": 0.15}},
               "homeAdv": 0.25, "rho": -0.1}
_DC_MATCH = {"league": "西甲", "home": "甲队", "away": "乙队", "had": {"h": 2.5}}


def _wire_dc(monkeypatch, tmp_path, lambda_text, rho_text):
    """λ 读取(dc_predict.CACHE_DIR)与 ρ 补读(nr.ROOT)分别指向 tmp fixture, 别名表同挂
    tmp(中文队名走别名级)——真实 engine/cache 与 data/01-teams 零接触."""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "testliga_dc.json").write_text(lambda_text, encoding="utf-8")
    root = tmp_path / "root"
    (root / "engine" / "cache").mkdir(parents=True)
    (root / "engine" / "cache" / "testliga_dc.json").write_text(rho_text, encoding="utf-8")
    aliases = tmp_path / "_aliases.json"
    aliases.write_text(json.dumps(
        {"testliga": {"alpha-fc": {"zh": "甲队"}, "beta-fc": {"zh": "乙队"}}},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(dc_predict, "CACHE_DIR", cache)
    monkeypatch.setattr(nr, "ROOT", root)
    monkeypatch.setattr(nr, "map_league", lambda name: "testliga")
    monkeypatch.setattr(common, "ALIASES_PATH", aliases)


def test_dc_context_healthy_cache_zero_change(monkeypatch, tmp_path):
    # 健康缓存: λ+ρ 读通 → {'lh','la','rho'}, 行为零变化
    body = json.dumps(_DC_FIXTURE, ensure_ascii=False)
    _wire_dc(monkeypatch, tmp_path, body, body)
    lam = dc_predict.match_lambdas("testliga", "甲队", "乙队")
    assert nr._dc_context(_DC_MATCH) == {"lh": lam[0], "la": lam[1], "rho": -0.1}


def test_dc_context_corrupt_rho_read_degrades(monkeypatch, tmp_path):
    # Fix1: λ 已取到但 ρ 二次裸读撞坏缓存(两次读之间被写坏) → 不炸整卡,
    # _dc_context 返回 None → 剧本层降级风格模板旧路径(script_source=style)
    healthy = json.dumps(_DC_FIXTURE, ensure_ascii=False)
    _wire_dc(monkeypatch, tmp_path, healthy, '{"rho": ')   # 截断 JSON
    assert nr._dc_context(_DC_MATCH) is None
    cand = nr.build_candidate(_DC_MATCH, FAKE_PROFILE, {}, dc=nr._dc_context(_DC_MATCH))
    assert cand["script_source"] == "style"
