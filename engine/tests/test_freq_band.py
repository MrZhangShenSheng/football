"""freq-band 比分选法测试（2026-08-27 重设计：联赛频率+球队平移+形状带+q排序）。开发者 sszhang"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import freq_band  # noqa: E402


def test_league_base_rates():
    """频率 Counter → (主队场均, 客队场均)：Σh·c/n 与 Σa·c/n。"""
    blob = Counter({"__n": 3, "1:0": 1, "0:0": 1, "2:1": 1})
    assert freq_band.league_base_rates(blob) == (1.0, 1 / 3)   # (1+0+2)/3, (0+0+1)/3
    assert freq_band.league_base_rates(Counter()) == (0.0, 0.0)


def test_global_pool():
    """全局池 = 各联赛 Counter 汇总（欧冠等无映射联赛的模板）。"""
    table = {"a": Counter({"__n": 2, "1:0": 1, "0:0": 1}),
             "b": Counter({"__n": 2, "1:0": 2})}
    pool = freq_band.global_pool(table)
    assert pool["__n"] == 4 and pool["1:0"] == 3 and pool["0:0"] == 1


def test_build_team_form_fd_and_local(tmp_path):
    """fd 行 + 本地 league 库 → norm 键近况表；离网注入隔离（phase1 铁律）。"""
    fd_rows = [{"HomeTeam": "Arsenal", "AwayTeam": "Man City", "FTHG": "2", "FTAG": "1"},
               {"HomeTeam": "Man City", "AwayTeam": "Arsenal", "FTHG": "3", "FTAG": "0"}]
    lg = tmp_path / "korea_matches.json"
    lg.write_text('{"matches": [{"home": "ulsan", "away": "pohang-steelers", "hg": 2, "ag": 0}]}',
                  encoding="utf-8")
    form = freq_band.build_team_form(fetch_rows_fn=lambda s, d: fd_rows if (d == "E0" and s == "2425") else [],
                                     league_glob=str(tmp_path / "*_matches.json"))
    assert form["arsenal"] == [(2, 1), (0, 3)]        # (进球, 失球) 源内时序
    assert form["mancity"] == [(1, 2), (3, 0)]        # norm("Man City") == norm("man-city")
    assert form["ulsan"] == [(2, 0)] and form["pohangsteelers"] == [(0, 2)]


def test_team_strength_window_and_gate():
    """近 RECENT_WINDOW 场均值；<FORM_MIN_MATCHES → None（降级不平移）。"""
    form = {"x": [(1, 0)] * (freq_band.RECENT_WINDOW - 1) + [(3, 1)] * 2}
    gf, ga = freq_band.team_strength(form, "x")      # 12 场取近 10：8×(1,0)+2×(3,1)
    assert abs(gf - (8 + 6) / 10) < 1e-9 and abs(ga - 2 / 10) < 1e-9
    assert freq_band.team_strength({"y": [(1, 1)] * 4}, "y") is None
    assert freq_band.team_strength({}, "none") is None


def test_build_team_form_alias_dual_keys(tmp_path):
    """fd 行双键收录：norm(fd名) 与 norm(tid)（经 aliases.fd 对照）同数据——
    体彩中文→zh_map→tid 后可命中 fd 库数据（tid≠fd 名的队：bayern vs Bayern Munich）。"""
    fd_rows = [{"HomeTeam": "Bayern Munich", "AwayTeam": "Bayer Leverkusen", "FTHG": "3", "FTAG": "0"}]
    aliases = {"bayern": {"zh": "拜仁", "fd": "Bayern Munich"},
               "leverkusen": {"zh": "勒沃库森", "fd": "Bayer Leverkusen"}}
    form = freq_band.build_team_form(fetch_rows_fn=lambda s, d: fd_rows if (d == "D1" and s == "2526") else [],
                                     league_glob=str(tmp_path / "*_matches.json"),
                                     aliases=aliases)
    assert form["bayernmunich"] == [(3, 0)]      # fd 名键（原始收录）
    assert form["bayern"] == [(3, 0)]            # tid 键（别名对照复制）
    assert form["bayerleverkusen"] == [(0, 3)]
    assert form["leverkusen"] == [(0, 3)]


def test_league_coverage_fd_divs():
    """体彩常见次级联赛映射 + fd DIVS 拉取覆盖（2026-08-28 卡实测缺口：德乙/荷乙/英冠）。"""
    from score_ev import LEAGUE_MAP
    from band_calibration import DIVS
    for zh_name in ("德乙", "荷乙", "英冠", "西乙", "意乙"):
        assert zh_name in LEAGUE_MAP, f"{zh_name} 无 LEAGUE_MAP 映射"
        assert LEAGUE_MAP[zh_name] in DIVS.values(), f"{zh_name} 映射 {LEAGUE_MAP[zh_name]} 无对应 fd DIVS"
    assert set(DIVS) >= {"E1", "D2", "N2", "SP2"}   # 英冠/德乙/荷乙/西乙 CSV 拉取


def test_lambdas_multiplicative_and_clamp():
    """λh=主进×客失/模板主场场均；λa 对称；缺强度/零基准→None；clamp 生效。"""
    base = (1.5, 1.0)
    assert freq_band.lambdas(base, (2.0, 0.8), (0.9, 1.5)) == (2.0 * 1.5 / 1.5, 0.9 * 0.8 / 1.0)
    assert freq_band.lambdas(base, None, (0.9, 1.5)) is None      # 近况缺失降级
    assert freq_band.lambdas((0.0, 1.0), (2.0, 1.0), (1.0, 1.0)) is None  # 空基准
    assert freq_band.lambdas(base, (9.0, 0.1), (0.2, 9.0))[0] <= freq_band.LAMBDA_CLAMP[1]
    assert freq_band.lambdas(base, (0.05, 0.1), (0.05, 0.1)) == (freq_band.LAMBDA_CLAMP[0],) * 2


def _mini_blob():
    return Counter({"__n": 100, "1:1": 30, "1:0": 25, "0:0": 20, "2:0": 15, "0:1": 10})


def test_shifted_q_pure_mode():
    """lam=None → 纯联赛频率归一化（零破坏降级）；c=0 比分恒 0；空 Counter → 空。"""
    q = freq_band.shifted_q(_mini_blob(), None)
    assert abs(sum(q.values()) - 1.0) < 1e-9
    assert abs(q["1:1"] - 0.30) < 1e-9
    assert "3:3" not in q                                  # c=0 格子不出现（真实数据从未出现）
    assert freq_band.shifted_q(Counter(), None) == {}


def test_shifted_q_direction_and_conservation():
    """强主平移(λh 2.4/λa 0.5)：靠近目标的比分 q 抬升、远离的回落；归一化守恒。"""
    pure = freq_band.shifted_q(_mini_blob(), None)
    shifted = freq_band.shifted_q(_mini_blob(), (2.4, 0.5))   # T=2.9, D=1.9
    assert shifted["2:0"] > pure["2:0"]          # (t=2,d=2) 靠近目标
    assert shifted["0:1"] < pure["0:1"]          # (t=1,d=-1) 远离目标
    assert abs(sum(shifted.values()) - 1.0) < 1e-9


def _odds_day():
    return {"matches": [
        {"matchNumStr": "001", "league": "意甲", "home": "国际米兰", "away": "威尼斯",
         "had": {"h": 1.3, "d": 5.5, "a": 9.0},
         "crs": {"2:0": 12.0, "2:1": 15.0, "3:1": 22.0, "1:1": 6.5, "0:2": 26.0, "4:0": 40.0}},
        {"matchNumStr": "002", "league": "欧冠", "home": "X队", "away": "Y队",   # 无映射→全局池+无近况→纯模板
         "had": {"h": 2.0, "d": 3.2, "a": 3.6},
         "crs": {"2:0": 12.0, "0:2": 22.0}},
    ]}


def test_freq_legs_three_gates():
    """闸门①q≥1% ②形状带 ③带内q最高每场1腿；跨场q降序；胜其他过滤。"""
    table = {"italy-serie-a": _mini_blob()}
    form = {}                                        # 无近况 → 纯模板
    zh = {"国际米兰": "inter", "威尼斯": "venezia", "X队": "x", "Y队": "y"}
    legs = freq_band.freq_legs(_odds_day(), table, form, zh, band=(10.0, 17.0))
    assert [l["matchNumStr"] for l in legs] == ["001", "002"]  # 002 无映射→全局池=同模板,2:0@12.0 q=15% 并列入选(q 同值稳定序)
    leg = legs[0]
    assert leg["score"] == "2:0" and leg["odds"] == 12.0       # 带内 q 最高 15%；3:1/0:2 真实数据未出现→恒0出局
    assert leg["shifted"] is False and leg["q"] == 0.15


def test_freq_legs_survival_gate_and_shift_flag():
    """q<1% 的带内比分出局；有近况输入 → shifted 标记。"""
    table = {"italy-serie-a": Counter({"__n": 1000, "1:1": 300, "1:0": 280, "2:0": 5})}  # 2:0 q=0.5%
    zh = {"国际米兰": "inter", "威尼斯": "venezia"}
    legs = freq_band.freq_legs(_odds_day(), table,
                               {"inter": [(2, 0)] * 10, "venezia": [(0, 2)] * 10}, zh,
                               band=(10.0, 17.0))
    assert legs == []                                          # 001 唯一带内 2:0 被生存阈拦 → 空手
    table2 = {"italy-serie-a": Counter({"__n": 1000, "1:1": 300, "1:0": 280, "2:0": 50})}
    legs2 = freq_band.freq_legs(_odds_day(), table2,
                                {"inter": [(2, 0)] * 10, "venezia": [(0, 2)] * 10}, zh,
                                band=(10.0, 17.0))
    assert legs2 and legs2[0]["shifted"] is True


def test_build_ticket_freq_default_and_gate():
    """默认 method=freq：走 freq_legs；合格腿<4 → 关档空 upset（不硬凑）。"""
    import boldplay
    odds_day = {"matches": [
        {"matchNumStr": "001", "league": "意甲", "home": "A", "away": "B",
         "had": {"h": 1.6, "d": 4.0, "a": 6.0}, "crs": {"2:0": 12.0}}]}
    t = boldplay.build_ticket(odds_day, {"italy-serie-a": _mini_blob()}, seq=1,
                              method="freq", form={})
    assert t["method"] == "freq"
    assert t["tiers"]["upset"]["legs"] == [] and t["tiers"]["upset"]["cost"] == 0   # 1腿<4 关档
    assert "关档" in t["tiers"]["upset"]["note"]


def test_build_ticket_amix_unchanged():
    """amix 过渡路径行为不变：仍走 mix_candidates（monkeypatch 隔离 DC/网络）。"""
    import importlib
    import boldplay
    odds_day = {"matches": [
        {"matchNumStr": "001", "league": "意甲", "home": "A", "away": "B",
         "had": {"h": 1.6, "d": 4.0, "a": 6.0},
         "crs": {"2:0": 12.0}, "ttg": {"s0": 9.5, "s1": 4.0, "s2": 3.2, "s3": 3.5,
                                        "s4": 6.5, "s5": 11.0, "s6": 20.0, "s7": 30.0}}]}
    calls = []

    def fake_mix(od, ft, zh, hf, dc_params_fn=None, adjust_map=None):
        calls.append(1)
        return [{"play": "crs", "pick": "2:0", "odds": 12.0, "source": "dc",
                 "matchNumStr": "001", "match": "A-B", "ev": 0.1}] * 3

    orig = boldplay.mix_candidates
    boldplay.mix_candidates = fake_mix
    try:
        t = boldplay.build_ticket(odds_day, {"italy-serie-a": _mini_blob()}, seq=1, method="amix")
    finally:
        boldplay.mix_candidates = orig
    assert calls == [1] and len(t["tiers"]["upset"]["legs"]) == 3   # amix 链未动
    assert t["method"] == "amix"


def test_ttg_agg_buckets():
    from freq_band import ttg_agg
    out = ttg_agg({"1:0": 0.6, "2:2": 0.4})
    assert abs(out["s1"] - 0.6) < 1e-9 and abs(out["s4"] - 0.4) < 1e-9
    assert abs(sum(out.values()) - 1.0) < 1e-9


def test_hafu_agg_normalized():
    from freq_band import hafu_agg
    p_half = {"h": 0.4, "d": 0.35, "a": 0.25}
    alpha = {k: 1.0 for k in ("hh","hd","ha","dh","dd","da","ah","ad","aa")}
    out = hafu_agg({"1:1": 0.5, "2:2": 0.5}, p_half, alpha)
    assert abs(sum(out.values()) - 1.0) < 1e-9


def test_pools_card_divergence_flag(monkeypatch):
    """pools_card 分歧旗：CRS q=11.5%@24 vs 市场隐含 1/(24×1.13)≈3.7% → Δ7.8pp>5pp
    → 标 divergence 并降级 CRS（不再居首）。hafu_alpha/half_three_way 打桩隔离真实数据。"""
    monkeypatch.setattr(freq_band, "hafu_alpha",
                        lambda results_dir=None: {"alpha": {k: 1.0 for k in freq_band.HAFU_KEYS}, "n": 61})
    monkeypatch.setattr(freq_band, "half_three_way",
                        lambda results_dir=None: {"h": 0.4, "d": 0.3, "a": 0.3})
    m = {"code": "周日004", "league": "德乙", "home": "圣保利", "away": "凯泽",
         "crs": {"0:2": 24.0, "1:1": 7.5}, "ttg": {"s2": 3.75, "s3": 3.55},
         "hafu": {"dd": 6.25, "hh": 2.75}}
    ft = {"germany-2-bundesliga": Counter({"1:1": 40, "2:2": 20, "__n": 200})}
    card = freq_band.pools_card(m, {"0:2": 0.115, "1:1": 0.08}, {}, {}, ft)
    assert "divergence" in card["flags"]
    assert card["candidates"][0]["pool"] != "crs"                     # 降级后不居首
    assert all(c["code"] == "周日004" and c["match"] == "圣保利 vs 凯泽"
               for c in card["candidates"])                           # 候选自带 code/match（T4 契约）
    # EV=q×赔率−1（存档 4 位舍入容差）；1:1@7.5 低于带下限10出局；s3 无 q 出局
    assert {c["pick"] for c in card["candidates"]} == {"0:2", "s2", "dd"}
    assert all(abs(c["ev"] - (c["q"] * c["odds"] - 1)) < 1e-4 for c in card["candidates"])
