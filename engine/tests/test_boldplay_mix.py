"""boldplay A-MIX 跨池选腿测试（v5.1 混串合法后新默认）。开发者 sszhang"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import boldplay  # noqa: E402
from boldplay import mix_candidates  # noqa: E402


def _odds_day():
    """2 场构造：001 有 DC（TTG 出正值），002 无 DC 缓存（CRS 经验频率腿）。"""
    return {"matches": [
        {"matchNumStr": "001", "league": "西甲", "home": "皇马", "away": "社会",
         "had": {"h": 1.35, "d": 5.0, "a": 9.0},
         "crs": {"2:0": 8.0, "2:1": 8.5, "1:1": 7.0},
         "ttg": {"s0": 9.5, "s1": 4.0, "s2": 3.2, "s3": 3.5, "s4": 6.5, "s5": 11.0, "s6": 20.0, "s7": 30.0}},
        {"matchNumStr": "002", "league": "欧冠", "home": "凯尔特人", "away": "LASK",  # 无 DC 映射 → None
         "had": {"h": 1.9, "d": 3.4, "a": 4.0},
         "crs": {"2:0": 8.0, "1:1": 6.0}, "ttg": {}},
    ]}


def test_mix_odds_range_and_no_dc_skip():
    """赔率域过滤：ODDS_RANGE 外的极端长尾不入选（550 级假阳性拦截）；无 DC 场次整场跳过。"""
    day = _odds_day()
    day["matches"][0]["crs"]["4:0"] = 550.0   # DC 全压2:0下4:0概率≈0,但即使EV假正也被赔率域拦
    zh = {"皇马": "real-madrid", "社会": "real-sociedad"}

    def fake_dc(m, z):
        return (1.8, 0.9, -0.1) if m["matchNumStr"] == "001" else None

    legs = mix_candidates(day, {}, zh, {}, dc_params_fn=fake_dc)
    assert all(l["odds"] <= boldplay.ODDS_RANGE[1] for l in legs)
    assert all(l["source"] == "dc" for l in legs)          # freq 经验腿不进 A-MIX
    assert all(l["matchNumStr"] == "001" for l in legs)    # 002 无 DC → 跳过


def test_mix_ttg_positive_ev_wins():
    """双门槛窗口验证：中高赔档（市场占比<~20%）才有 EV>0 且分歧<5pp 的窗口（低赔档数学不可能双过）。"""
    day = _odds_day()
    zh = {"皇马": "real-madrid", "社会": "real-sociedad"}
    hafu = {}
    import numpy as _np
    p2 = _np.zeros((7, 7)); p2[5, 0] = 1.0
    boldplay.score_matrix = lambda lh, la, rho: p2
    boldplay.ttg_dist = lambda p: [0.0, 0.0, 0.0, 0.0, 0.0, 0.10, 0.0, 0.0]  # 5球 10%：EV=0.1×11-1=+0.1, 市场≈7% 分歧3pp

    def fake_dc(m, z):
        return 1.8, 0.9, -0.1 if m["matchNumStr"] == "001" else None

    legs = mix_candidates(day, {"spain-laliga": {"__n": 10, "2:0": 1}}, zh, hafu, dc_params_fn=fake_dc)
    leg1 = next((l for l in legs if l["matchNumStr"] == "001"), None)
    assert leg1 is not None and leg1["play"] == "ttg" and leg1["pick"] == "5球"
    assert leg1["odds"] == 11.0 and leg1["ev"] > 0


def test_dc_params_team_matching(tmp_path):
    """队名宽松匹配：中文→tid→缓存键（本地库'al-ahli'风格 与 fd'Aston Villa'风格）。"""
    cache = tmp_path
    (cache / "saudi_dc.json").write_text(
        '{"teams": {"al-ahli": {"attack": 0.3, "defense": -0.1}, "al-hilal": {"attack": 0.5, "defense": -0.3}},'
        ' "homeAdv": 0.2, "rho": -0.05}', encoding="utf-8")
    zh = {"吉达联合": "al-ahli", "利雅新月": "al-hilal"}
    params = boldplay._dc_params({"league": "沙职", "home": "吉达联合", "away": "利雅新月"}, zh, cache_dir=cache)
    assert params is not None
    lh, la, rho = params
    assert abs(lh - 2.718281828 ** (0.3 - 0.3 + 0.2)) < 1e-9
    # 无映射联赛/未入库队 → None
    assert boldplay._dc_params({"league": "欧冠", "home": "x", "away": "y"}, zh, cache_dir=cache) is None
