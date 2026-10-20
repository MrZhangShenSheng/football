# -*- coding: utf-8 -*-
"""llm_pred v3.6 结构化生成测试（批一·设计 docs/plans/2026-10-20-llm-dist-structured-design.html）。

口径：market 参照=去水 pick 键分布（须含 other_* 三格·与 llm_eval 计分键集同源）。
开发者 sszhang"""
import itertools
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import llm_pred as lp                                              # noqa: E402
import test_llm_pred_v2 as v2                                      # noqa: E402
from llm_pred import (check_generated, dir_of_key, generate_dist,  # noqa: E402
                      tot_of_key, validate_structured)
from test_llm_pred_v2 import dirs                                  # noqa: E402,F401

# 近似 31 格市场形状（和≈0.94，带内比例是真参照——生成器按带归一故不要求和为 1）
MKT = {"1:0": 0.10, "2:0": 0.07, "2:1": 0.09, "1:1": 0.15, "0:0": 0.07,
       "0:1": 0.10, "1:2": 0.09, "0:2": 0.06, "0:3": 0.029, "1:3": 0.02,
       "other_h": 0.05, "other_d": 0.04, "other_a": 0.07}


def test_dir_and_tot_of_key():
    assert dir_of_key("3:1") == "h" and dir_of_key("2:2") == "d" and dir_of_key("0:3") == "a"
    assert dir_of_key("other_h") == "h" and dir_of_key("other_a") == "a"
    assert tot_of_key("2:1") == 3
    assert tot_of_key("other_d") > 5          # 兜底格代表 6+ 球质


def test_generate_dist_deterministic_and_banded():
    d1 = generate_dist({"h": .30, "d": .25, "a": .45}, MKT, {})
    d2 = generate_dist({"h": .30, "d": .25, "a": .45}, MKT, {})
    assert d1 == d2                                     # 可复现（P26）
    assert abs(sum(d1.values()) - 1.0) < 1e-9
    for d in "hda":
        band = sum(v for k, v in d1.items() if dir_of_key(k) == d)
        assert abs(band - {"h": .30, "d": .25, "a": .45}[d]) < 1e-9   # 三向质量精确落带


def test_generate_dist_inherits_band_shape():           # 003 反例的机制解
    d = generate_dist({"h": .20, "d": .30, "a": .50}, MKT, {})
    assert abs(d["0:3"] / d["1:2"] - MKT["0:3"] / MKT["1:2"]) < 1e-9  # 带内比例继承


def test_template_preserves_direction_mass():
    """方向与形状正交（终审 fix）：模板格集天然跨带（crush 抬 3:0 压 1:1），
    不按带归一则 direction 被模板改写——实测 crush 把 5.8pt 从平局带搬进主胜带,
    闸4 按入参 direction 算 shift 就测不出真实偏离。调形只许带内换形,不许跨带搬量。"""
    tgt = {"h": .45, "d": .27, "a": .28}
    mkt = dict(MKT, **{"3:0": 0.03, "3:1": 0.025, "2:2": 0.04, "0:4": 0.01})
    for t in ("crush", "lock", "leaky", "open"):
        d = generate_dist(tgt, mkt, {"type": t})
        for x in "hda":
            band = sum(v for k, v in d.items() if dir_of_key(k) == x)
            assert abs(band - tgt[x]) < 1e-9, f"{t} {x} 带漂移 {band - tgt[x]:+.4f}"


def test_shape_templates_diverge():
    dirs = {"h": .45, "d": .27, "a": .28}
    mkt = dict(MKT, **{"3:0": 0.03, "3:1": 0.025, "2:2": 0.04, "0:4": 0.01})
    dists = {t: generate_dist(dirs, mkt, {"type": t})
             for t in ("crush", "lock", "leaky", "open")}

    def kl(p, q):
        return sum(p[k] * math.log(p[k] / q[k]) for k in p)

    for a, b in itertools.combinations(dists, 2):
        assert kl(dists[a], dists[b]) > 0.02          # 四型两两分型（KL>0.02）
    assert dists["crush"]["3:0"] > dists["open"]["3:0"]          # 碾压型大胜格更肥
    assert dists["lock"]["0:0"] > dists["leaky"]["0:0"]          # 铁桶型 0:0 更肥


def test_xg_params_lift_big_band():
    """xG 参数净效应须与同型基线比（计划原稿拿无型基线对比 open+xG，把模板效应
    混进来了：open 自身压 0:0 抬 2:1/1:2 使大球带先降 0.9pt，再被带内归一削掉跨带
    抬升分量，合成只剩 1.17 倍）。隔离口径=只换 xG 参数,型不动。"""
    dirs = {"h": .5, "d": .25, "a": .25}
    mkt = dict(MKT, **{"3:0": 0.03, "3:1": 0.025, "2:2": 0.04, "0:4": 0.01,
                       "4:0": 0.008, "4:1": 0.006, "2:3": 0.012})
    base = generate_dist(dirs, mkt, {"type": "open"})
    hi = generate_dist(dirs, mkt, {"type": "open", "xgSum": 3.8, "xgDiff": 0.0})
    big_b = sum(v for k, v in base.items() if tot_of_key(k) >= 4)
    big_h = sum(v for k, v in hi.items() if tot_of_key(k) >= 4)
    assert big_h > big_b * 1.2                    # xgSum 高 → 大球带抬升
    hi2 = generate_dist(dirs, mkt, {"type": "open", "xgSum": 3.8, "xgDiff": 1.5})
    assert hi2["3:0"] > hi["3:0"] * 1.1           # xgDiff 主向 → 大胜格再抬
    # 封顶 ×2：极端参数不失控
    hi3 = generate_dist(dirs, mkt, {"type": "open", "xgSum": 9.0, "xgDiff": 8.0})
    assert max(hi3.values()) < 0.5


def _kj3():
    """≥3 条独立判断（v3.4 门槛·与 test_llm_pred_v2 同口径）。"""
    return [{"judgment": f"j{i}", "evidence": f"结构层#{i}"} for i in (1, 2, 3)]


def test_validate_structured():
    good = {"code": "周二006", "direction": {"h": .3, "d": .25, "a": .45},
            "evidenceTier": "structural", "shape": {"type": "open"},
            "cellOverrides": {"0:3": .045}, "keyJudgments": _kj3(),
            "confidence": "medium"}
    ok, issues = validate_structured(good, list(MKT))
    assert ok and not issues
    # 和≠1：容差 ±0.02（手填三向的舍入余量）——0.99 放过，0.95 拒收
    assert validate_structured(dict(good, direction={"h": .3, "d": .25, "a": .44}),
                               list(MKT))[0]
    bad = dict(good, direction={"h": .3, "d": .25, "a": .40})            # 和 0.95 出容差
    assert not validate_structured(bad, list(MKT))[0]
    bad = dict(good, direction={"h": .4, "d": .6})                       # 缺 a 键
    assert not validate_structured(bad, list(MKT))[0]
    bad = dict(good, evidenceTier="gut")                                 # 等级枚举
    assert not validate_structured(bad, list(MKT))[0]
    bad = dict(good, shape={"type": "park_the_bus"})                     # 型枚举
    assert not validate_structured(bad, list(MKT))[0]
    bad = dict(good, cellOverrides={f"{i}:{j}": .01
                                    for i in range(3) for j in range(2)})  # >5 格
    assert not validate_structured(bad, list(MKT))[0]
    assert any("≤5" in i for i in validate_structured(bad, list(MKT))[1])
    bad = dict(good, cellOverrides={"9:9": .1})                          # 键∉市场
    assert not validate_structured(bad, list(MKT))[0]
    bad = dict(good, keyJudgments=_kj3()[:2])                            # kj<3 共用校验
    assert not validate_structured(bad, list(MKT))[0]
    bad = dict(good, confidence="很高")                                   # confidence 枚举
    assert not validate_structured(bad, list(MKT))[0]


# ── Task 5：生成结果四闸（拒冻级）──────────────────────────────────────
_MKT5 = dict(MKT, **{"3:0": 0.03, "3:1": 0.025, "2:2": 0.04, "0:4": 0.01,
                     "4:0": 0.008, "4:1": 0.006, "2:3": 0.012})
# _MKT5 归一后三向≈{h:.354, d:.280, a:.365}（探针实测）。_D5 的 a=.45 对市场 +8.5pt
# 已超 structural 上限 8pt——闸4 口径下它是「须 info 级」的叫板,不能当四闸通例基线。
_D5 = {"h": .30, "d": .25, "a": .45}
_D5_MILD = {"h": .32, "d": .26, "a": .42}        # +5.5pt：structural 级可承载的叫板


def _renorm(d):
    z = sum(d.values())
    return {k: v / z for k, v in d.items()}


def test_check_generated_passes_band_inheritance():
    d = generate_dist(_D5_MILD, _MKT5, {})
    ok, issues = check_generated(d, _MKT5, "structural", direction=_D5_MILD,
                                 shape={}, overrides=None)
    assert ok, issues                                  # 纯带内继承必过四闸
    # 同一分布换 state 级（≤2pt）即被幅度闸拦——等级挂钩生效
    ok2, iss2 = check_generated(d, _MKT5, "state", direction=_D5_MILD,
                                shape={}, overrides=None)
    assert not ok2 and any("幅度" in i for i in iss2)


def test_gate1_big_band_budget():
    d = generate_dist(_D5, _MKT5, {})
    flat = _renorm({k: (v * .5 if tot_of_key(k) >= 4 else v) for k, v in d.items()})
    ok, issues = check_generated(flat, _MKT5, "info", direction=_D5,
                                 shape={}, overrides=None)
    assert not ok and any("大球带" in i for i in issues)


def test_gate1_type_baseline_lets_shape_through():
    """闸1 按型基线而非市场裸比（112 场真卡标定）：选型本身推动大球带
    （lock ×0.54/leaky ×1.29），按裸比则三型在零叫板下即拒冻=形状判断无处可放。
    闸1 该管的是叫板对大球预算的附带改写（实测 ±1.7% 内）。"""
    mdir = {x: sum(v for k, v in _MKT5.items() if dir_of_key(k) == x) for x in "hda"}
    z = sum(mdir.values())
    mdir = {k: v / z for k, v in mdir.items()}
    for t in ("crush", "lock", "leaky", "open"):
        d = generate_dist(mdir, _MKT5, {"type": t})
        ok, issues = check_generated(d, _MKT5, "info", direction=mdir,
                                     shape={"type": t}, overrides=None)
        assert not any("大球带" in i for i in issues), (t, issues)
    # xgSum 在手=有证据抬带，闸1 豁免（带宽锚闸同口径）
    d = generate_dist(mdir, _MKT5, {"type": "open", "xgSum": 4.2})
    ok, issues = check_generated(d, _MKT5, "info", direction=mdir,
                                 shape={"type": "open", "xgSum": 4.2}, overrides=None)
    assert not any("大球带" in i for i in issues), issues


def test_gate2_cell_ratio_and_override_exemption():
    d = generate_dist(_D5, _MKT5, {})
    sq = _renorm({**d, "0:3": d["0:3"] * .3})          # 003 型：单格压到 0.3 倍
    ok, issues = check_generated(sq, _MKT5, "info", direction=_D5,
                                 shape={}, overrides=None)
    assert not ok and any("0:3" in i and "比值" in i for i in issues)
    # 豁免：覆盖格（有 kj 证据）不触发比值闸
    ok, issues = check_generated(sq, _MKT5, "info", direction=_D5,
                                 shape={}, overrides={"0:3": sq["0:3"]})
    assert ok, issues


def test_gate3_anchor_band_and_xg_exemption():
    # ×6 膨胀 TTG 偏离 +1.494 恰压在 ±1.5 线内（探针实测）——取 ×10 才真超线
    d3 = {"h": .34, "d": .33, "a": .33}
    fat = {k: (v * 10 if tot_of_key(k) >= 5 else v) for k, v in _MKT5.items()}
    hi = generate_dist(d3, fat, {"type": "leaky"})
    ok, issues = check_generated(hi, _MKT5, "info", direction=d3,
                                 shape={"type": "leaky"}, overrides=None)
    assert not ok and any("总球期望" in i for i in issues)
    # xgSum 在手=有证据，带宽锚豁免（大球带闸仍另行把关）
    ok, issues = check_generated(hi, _MKT5, "info", direction=d3,
                                 shape={"type": "leaky", "xgSum": 3.9}, overrides=None)
    assert not any("总球期望" in i for i in issues)


def test_gate4_tier_amplitude():
    hot = {x: (.44 if x == "a" else .28) for x in "hda"}
    d4 = generate_dist(hot, _MKT5, {})
    ok, issues = check_generated(d4, _MKT5, "state", direction=hot,
                                 shape={}, overrides=None)
    assert not ok and any("幅度" in i for i in issues)   # state 级 ≤2pt
    ok, _ = check_generated(d4, _MKT5, "info", direction=hot, shape={}, overrides=None)
    assert ok                                            # info 级无上限


def test_warn_level_never_blocks():
    """警告级只留痕不拒冻：>12pt 偏离/满 5 覆盖/state 级三条都带 [warn] 前缀。"""
    hot = {"h": .20, "d": .20, "a": .60}
    d = generate_dist(hot, _MKT5, {})
    ok, issues = check_generated(d, _MKT5, "info", direction=hot, shape={},
                                 overrides=None)
    assert ok                                            # 有 warn 但不拒
    assert any(i.startswith("[warn]") and "方向偏离" in i for i in issues)


# ── Task 6：freeze v3 端到端 ──────────────────────────────────────────
# 真实 31 格池（28 定格 + 中文 other 三键·与体彩存档同形）。v2 fixture 只 11 格,
# 过不了 market_lambda.MIN_CRS_CELLS=20 门槛——结构化链须市场参照,fixture 必须贴真。
_GRID_28 = ["0:0", "0:1", "0:2", "0:3", "0:4", "0:5", "1:0", "1:1", "1:2", "1:3",
            "1:4", "1:5", "2:0", "2:1", "2:2", "2:3", "2:4", "2:5", "3:0", "3:1",
            "3:2", "3:3", "4:0", "4:1", "4:2", "5:0", "5:1", "5:2"]


def _crs31():
    """赔率按总进球递增（1.0 球档最肥）——粗拟真实池形状。"""
    odds = {}
    for k in _GRID_28:
        t = sum(int(x) for x in k.split(":"))
        odds[k] = {0: 22.0, 1: 8.5, 2: 9.0, 3: 13.0, 4: 26.0,
                   5: 60.0, 6: 120.0, 7: 200.0}.get(t, 300.0)
    odds.update({"胜其他": 60.0, "平其他": 150.0, "负其他": 80.0})
    return odds


@pytest.fixture()
def dirs31(tmp_path, monkeypatch):
    """31 格真池卡 fixture（v3 专用）。"""
    import hashlib
    monkeypatch.setattr(lp, "CARDS_ROOT", tmp_path / "cards")
    monkeypatch.setattr(lp, "PREDS_ROOT", tmp_path / "preds")
    card = {"code": "周三009", "match": "A vs B", "league": "欧冠",
            "saleDate": "2026-10-15",
            "layers": {"market": {"had": {}, "crs": _crs31(), "ttg": dict(v2.TTG)},
                       "context": {"status": "ok", "entries": []}},
            "createdAt": "2026-10-15T10:00:00"}
    card["cardHash"] = hashlib.sha256(
        json.dumps(card, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    d = tmp_path / "cards" / "2026-10-15"
    d.mkdir(parents=True, exist_ok=True)
    (d / "周三009.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_market_ref_includes_other_cells(dirs31):
    """市场参照须含 other_* 三格（卡内 devig.crs 只 28 格,中文 other 键被
    crs_key 静默丢弃——这是 _market_ref 现算而非直读 devig 的理由）。"""
    card = json.loads((dirs31 / "cards" / "2026-10-15" / "周三009.json")
                      .read_text(encoding="utf-8"))
    mkt = lp._market_ref(card)
    assert len(mkt) == 31
    assert {"other_h", "other_d", "other_a"} <= set(mkt)
    assert abs(sum(mkt.values()) - 1.0) < 1e-9
    assert sum(v for k, v in mkt.items() if tot_of_key(k) >= 4) > 0.05   # 大球带非空


def _mkt_dir_of(dirs31):
    card = json.loads((dirs31 / "cards" / "2026-10-15" / "周三009.json")
                      .read_text(encoding="utf-8"))
    mkt = lp._market_ref(card)
    raw = {x: sum(v for k, v in mkt.items() if dir_of_key(k) == x) for x in "hda"}
    z = sum(raw.values())
    return {k: v / z for k, v in raw.items()}


def _structured(code="周三009", **kw):
    """默认方向=市场三向 +3pt 主胜叫板（structural 上限 8pt 内·fixture 等赔池
    平局带仅 ~21%，硬写 .30 会造出 +13.9pt 的假叫板）。"""
    sp = {"code": code, "direction": kw.pop("direction", None) or
          {"h": .38, "d": .24, "a": .38},
          "evidenceTier": "structural", "shape": {"type": "open"},
          "cellOverrides": {}, "keyJudgments": _kj3(), "confidence": "medium"}
    sp.update(kw)
    return sp


def test_freeze_v3_structured(dirs31):
    out = lp.freeze("2026-10-15", [_structured()])
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["chain"] == "llm-31grid-v3"
    assert doc["genCohort"] == "v3-structured"
    e = doc["preds"][0]
    assert len(e["p_crs"]) == 31
    assert abs(sum(e["p_crs"].values()) - 1.0) < 1e-5    # 落卷 round(6) 的累积余量
    assert "checks" in e and "valueCells" in e
    assert e["direction"] and e["evidenceTier"] == "structural"
    assert e["shape"]["type"] == "open"


def test_freeze_v2_path_untouched(dirs):
    """v2 手填路径零破坏（同日另号，避开幂等）。"""
    out = lp.freeze("2026-10-15", [v2._pred()])
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["chain"] == "llm-31grid-v2"
    assert "genCohort" not in doc
    assert "direction" not in doc["preds"][0]


def test_freeze_v3_rejects_over_tier_cap(dirs31, capsys):
    """state 级大幅叫板 → 拒冻且逐项打印（禁静默：异常消息给计数,细项进 stdout）。"""
    hot = {"h": .10, "d": .20, "a": .70}
    with pytest.raises(SystemExit, match="未过闸拒冻结"):
        lp.freeze("2026-10-15", [_structured(direction=hot, evidenceTier="state")])
    printed = capsys.readouterr().out
    assert "幅度" in printed and "超等级上限" in printed
    assert not (lp.PREDS_ROOT / "2026-10-15.json").exists()      # 拒冻不留半卷


def test_freeze_v3_value_cells_and_checks(dirs31):
    """valueCells=比值>1.5 且 p>3% 的格（右尾定位下的选腿参考）。"""
    out = lp.freeze("2026-10-15", [_structured(shape={"type": "crush"})], revision=3)
    e = json.loads(out.read_text(encoding="utf-8"))["preds"][0]
    for vc in e["valueCells"]:
        assert vc["ratio"] > 1.5 and vc["p_llm"] > 0.03
        assert vc["k"] in e["p_crs"]
    assert isinstance(e["checks"]["issues"], list) and e["checks"]["passed"] is True
