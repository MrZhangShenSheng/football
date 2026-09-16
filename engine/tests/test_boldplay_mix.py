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


def test_mix_odds_range_lower_bound_only():
    """赔率域:上限已撤(175/550 级长尾放行),下限 4.0 挡低赔腿。

    两条断言都必须能证伪(审查实证:旧版 `1:1@3.2` 探针实际是被 DIVERGENCE_LIMIT 挡的,
    退回旧下限 2.0 断言照样通过,无区分力;同理旧版只断言常量自身，没有验证行为)：
    - 001 场 4:0@550 长尾腿：EV=17.38、分歧 2.87pp（p_dc=0.03342 vs p_mkt=0.00469），
      唯一能挡它的只有旧上限 40 ——
      若上限退回 40.0，`mix_candidates` 会转而选中 ttg 5球@11.0（同样 >=4.0），
      `any(l["odds"] == 550.0 ...)` 才是唯一能捕获"上限被撤销"的断言。
    - 002 场 0:0@3.6 干净低赔探针：DC 给该场强主队优势(λ主0.6/λ客0.3/ρ0)，0:0 概率天然
      偏高，实测 EV=+0.17、分歧仅 2.01pp（远低于 DIVERGENCE_LIMIT=0.05；本任务起分歧已
      降级为标注不再过滤，该腿不入选只由赔率下限 4.0 决定，与分歧值无关），除赔率外全部
      合规；退回旧下限 2.0 该腿会入选（已手工验证），故"该腿不入选"只能是下限 4.0 的作用。
      `6:6@1.58` 只是配平隐含概率之和的填充项，该比分 DC 概率≈0（EV=-1），永不会被选中。
      load_temperature mock 锚定 crs.T=1.3，脱离生产缓存 temperature.json 取值漂移
      （2026-09-16 复审：若 T 重拟合为 1.0，探针分歧会从 2.01pp 升到 10.16pp，但已不影响
      本测试断言，因为分歧不再是门槛；mock 仅为保持文档数值可复现）。
    """
    day = _odds_day()
    day["matches"][0]["crs"]["4:0"] = 550.0                    # 高赔长尾:现在必须放行
    day["matches"][1]["crs"] = {"0:0": 3.6, "6:6": 1.58}       # 干净低赔探针 + 配平填充
    day["matches"][1]["ttg"] = {}
    zh = {"皇马": "real-madrid", "社会": "real-sociedad"}
    boldplay.load_temperature = lambda: {"crs": 1.3, "ttg": 1.0, "hafu": 1.0}

    def fake_dc(m, z):
        if m["matchNumStr"] == "001":
            return (1.8, 0.9, -0.1)
        if m["matchNumStr"] == "002":
            return (0.6, 0.3, 0.0)   # 强主队优势(近0球) → 0:0 概率天然高,制造干净低赔探针
        return None

    legs = mix_candidates(day, {}, zh, {}, dc_params_fn=fake_dc)
    assert any(l["odds"] == 550.0 for l in legs), "撤上限后 550 级长尾必须能入选"
    assert not any(l["matchNumStr"] == "002" for l in legs), \
        "0:0@3.6 分歧<5pp 且 EV>0,唯一挡它的只能是下限 4.0"
    assert all(l["odds"] >= 4.0 for l in legs)
    assert all(l["source"] == "dc" for l in legs)   # freq 经验腿不进 A-MIX（既有行为，本任务未改动）
    assert boldplay.ODDS_RANGE[1] == float("inf")


def test_mix_ttg_positive_ev_wins():
    """双门槛窗口验证：中高赔档（市场占比<~20%）才有 EV>0 且分歧<5pp 的窗口（低赔档数学不可能双过）。"""
    day = _odds_day()
    zh = {"皇马": "real-madrid", "社会": "real-sociedad"}
    hafu = {}
    import numpy as _np
    p2 = _np.zeros((7, 7)); p2[5, 0] = 1.0
    boldplay.score_matrix = lambda lh, la, rho: p2
    boldplay.load_temperature = lambda: {"crs": 1.0, "ttg": 1.0, "hafu": 1.0}  # 关温度：单点mock分布会被T≠1放大分歧(temper后p=1.0,分歧93pp被5pp门槛挡)；本用例聚焦EV双门槛
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


def test_divergence_annotated_not_excluded():
    """分歧超 5pp 的腿必须入选并带标注（原为排除）。"""
    day = _odds_day()
    zh = {"皇马": "real-madrid", "社会": "real-sociedad"}
    # 锁 T=1.0：脱离生产缓存 temperature.json（同 test_mix_odds_range_lower_bound_only 的
    # mock 范式）——不锁的话 crs.T=1.3/ttg.T=1.3 会让 CRS 2:0(温度前EV0.37)被 TTG
    # 6球(温度后EV0.31)反超成为唯一候选,其分歧仅2.63pp<5pp,断言无区分力（复审实证）。
    boldplay.load_temperature = lambda: {"crs": 1.0, "ttg": 1.0, "hafu": 1.0}
    # 复位 score_matrix/ttg_dist：test_mix_ttg_positive_ev_wins 在本文件先跑且未还原
    # monkeypatch，跨测试污染会让本测试在全量跑时失败（隔离跑通过）；与本任务的
    # 分歧改动无关，这里只做自保不修那个既有问题（见任务报告 concerns）。
    import dc_predict as _dcp
    boldplay.score_matrix = _dcp.score_matrix
    boldplay.ttg_dist = _dcp.ttg_dist

    def fake_dc(m, z):
        return (2.6, 0.4, -0.1) if m["matchNumStr"] == "001" else None

    legs = mix_candidates(day, {}, zh, {}, dc_params_fn=fake_dc)
    assert legs, "分歧腿不应被滤光"
    assert all("divergence" in l and "divergenceFlag" in l for l in legs)
    assert any(l["divergenceFlag"] for l in legs), "λ=2.6 对市场应产生 >5pp 分歧腿"
