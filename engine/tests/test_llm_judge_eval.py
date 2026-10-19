# -*- coding: utf-8 -*-
"""llm_judge_eval 阅卷器测试（v3 三判据·合成用例自证打分代码无误）。开发者 sszhang"""
import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import llm_judge as lj  # noqa: E402
import llm_judge_eval as ev  # noqa: E402
from llm_card import build_card, save_card  # noqa: E402

DATE = "2026-10-16"
# 卡面赔率设计（去水后 argmax 与 top1 可控）：
#   HAD 2.0/3.4/3.6 → devig 主≈0.466 argmax=主
#   CRS top1 设计为 s02s01（价最低=隐含概率最高）
CRS = {"s02s01": 6.5, "s01s01": 7.0, "s01s02": 8.0, "s00s00": 9.0, "s01s00": 9.5,
       "s05s03": 60.0, "胜其他": 35.0}
TTG = {"s0": 15.0, "s1": 5.8, "s2": 3.75, "s3": 3.7, "s4": 4.85,
       "s5": 7.35, "s6": 12.5, "s7": 17.0}


def _mk_card(cards_root, code, had=("2.0", "3.4", "3.6")):
    m = {"matchNumStr": code, "league": "德乙", "home": "A", "away": "B",
         "matchTime": "03:00:00", "had": {"h": had[0], "d": had[1], "a": had[2]},
         "crs": dict(CRS), "ttg": dict(TTG)}
    card = build_card(DATE, m, insight_path=None, lineups={},
                      now="2026-10-16T11:00:00", business_date="2026-10-17")
    save_card(card, DATE, root=cards_root)
    return card


def _pred(code, call="主胜", lo=2, hi=3, cells=("2:1",), conf="medium",
          against=None):
    return {"code": code,
            "direction": {"call": call, "evidence": "e"},
            "goalBand": {"lo": lo, "hi": hi, "evidence": "e"},
            "keyCells": [{"cell": c, "why": "w"} for c in cells],
            "against": against or [{"item": "i", "evidence": "e"}],
            "confidence": conf}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cards = tmp_path / "cards"
    judg = tmp_path / "judgments"
    results = tmp_path / "results"
    for d in (cards, results):
        d.mkdir()
    prereg = tmp_path / "prereg.json"
    prereg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(lj, "JUDGMENTS_ROOT", judg)
    monkeypatch.setattr(lj, "PREREG_PATH", prereg)
    monkeypatch.setattr(ev, "RESULTS_ROOT", results)
    monkeypatch.setattr(ev, "OUT_PATH", tmp_path / "eval.json")
    return {"cards": cards, "judg": judg, "results": results}


def _mk_results(results_root, code, score):
    p = results_root / f"{DATE}.json"
    doc = json.loads(p.read_text(encoding="utf-8")) if p.exists() else \
        {"date": DATE, "matches": []}
    doc["matches"].append({"code": code, "result": score})
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_direction_paired_scoring(env):
    """模型判客胜、市场 argmax 主胜、实际客胜 → diff=+1（打赢基线才计正分）。"""
    code = "周X001"
    _mk_card(env["cards"], code)
    _mk_results(env["results"], code, "1-2")
    lj.freeze(DATE, [_pred(code, call="客胜")],
              now="2026-10-16T23:00:00", cards_root=env["cards"])
    r = ev.eval_date(DATE, root=env["judg"], cards_root=env["cards"])
    s = r["scores"][0]
    assert s["direction"] == {"model": 1, "market": 0, "diff": 1}
    assert s["abstain"] is not True


def test_abstain_excluded_from_denominator(env):
    code = "周X001"
    _mk_card(env["cards"], code)
    _mk_results(env["results"], code, "1-1")
    lj.freeze(DATE, [_pred(code, call="不站边")],
              now="2026-10-16T23:00:00", cards_root=env["cards"])
    r = ev.eval_date(DATE, root=env["judg"], cards_root=env["cards"])
    assert r["scores"][0]["direction"] is None          # 弃权场不计入分母
    assert r["scores"][0]["abstain"] is True


def test_goalband_market_baseline_same_width(env):
    """基线=同宽度窗最大质量：宽度1的模型带 2-3 → 市场窗从 TTG 去水里找。
    TTG 设计下 2 球与 3 球几乎同价,实际总进球 3 → 模型中,市场窗含 3 也中 → diff=0。"""
    code = "周X001"
    _mk_card(env["cards"], code)
    _mk_results(env["results"], code, "1-2")
    lj.freeze(DATE, [_pred(code, lo=2, hi=3)],
              now="2026-10-16T23:00:00", cards_root=env["cards"])
    r = ev.eval_date(DATE, root=env["judg"], cards_root=env["cards"])
    s = r["scores"][0]["goalBand"]
    assert s["model"] == 1 and s["market"] == 1 and s["diff"] == 0


def test_keycells_hit_and_other_semantics(env):
    """精确格命中 + other 桶语义（实际比分不在市场有价精确格内的该方向）。

    两场须一次冻结——freeze 是卷级幂等（同日主卷在即整卷返回,分批补冻结会静默丢失,
    修正须走 -rN 重述全量）。"""
    code, code2 = "周X001", "周X002"
    _mk_card(env["cards"], code)
    _mk_card(env["cards"], code2)
    _mk_results(env["results"], code, "2-1")
    _mk_results(env["results"], code2, "6-2")
    lj.freeze(DATE, [_pred(code, cells=("2:1",)), _pred(code2, cells=("other_h",))],
              now="2026-10-16T23:00:00", cards_root=env["cards"])
    scores = ev.eval_date(DATE, root=env["judg"], cards_root=env["cards"])["scores"]
    # 2:1 在 CRS 有价（s02s01）→ 精确格命中；市场 top1 也是 2:1 → diff=0
    assert scores[0]["keyCells"]["model"] == 1 and scores[0]["keyCells"]["diff"] == 0
    # 6:2 不在任何有价精确格 → other_h 命中；市场 top1=2:1 不含 → diff=+1
    assert scores[1]["keyCells"]["model"] == 1 and scores[1]["keyCells"]["diff"] == 1


def test_empty_keycells_abstain_and_afterkickoff_excluded(env):
    """空 keyCells 弃权 + afterKickoff 整场排除。

    afterKickoff 场走 -rN 修正卷重述全量（卷级幂等下补录的唯一合法路径）,
    正是「夜里冻结、凌晨补最后一场」的真实时序。"""
    code, code2 = "周X001", "周X002"
    _mk_card(env["cards"], code)
    _mk_card(env["cards"], code2)
    _mk_results(env["results"], code, "2-1")
    _mk_results(env["results"], code2, "2-1")
    # 主卷：只有周X001（23:00,开赛前·keyCells 空=弃权）
    lj.freeze(DATE, [_pred(code, cells=())], now="2026-10-16T23:00:00", cards_root=env["cards"])
    s = ev.eval_date(DATE, root=env["judg"], cards_root=env["cards"])["scores"][0]
    assert s["keyCells"] is None                      # 空 keyCells：该判据弃权
    # 补录周X002（开赛后 04:00）→ 修正卷重述全量
    lj.freeze(DATE, [_pred(code), _pred(code2)], revision=1, reason="补录",
              now="2026-10-17T04:00:00", cards_root=env["cards"])
    r = ev.eval_date(DATE, root=env["judg"], cards_root=env["cards"])
    assert r["afterKickoffExcluded"] == 1             # 开赛后冻结的整场排除
    assert len(r["scores"]) == 1 and r["scores"][0]["code"] == code


def test_verdict_rules():
    """prereg verdictRules：n<100 INSUFFICIENT / CI 下限>0 PASS / 跨0 按点估计分。"""
    assert ev._verdict(50, [0.1, 0.9]) == "INSUFFICIENT"
    assert ev._verdict(100, [0.05, 0.5]) == "PASS"
    assert ev._verdict(100, [-0.02, 0.08]) == "UNDETECTED"       # 跨0·点估计>0
    assert ev._verdict(100, [-0.08, 0.02]) == "FALSIFIED"        # 跨0·点估计<0
    assert ev._verdict(100, [-0.05, -0.01]) == "FALSIFIED"


def test_eval_all_cumulative_and_honest_gaps(env):
    """跨日累计 + 诚实降级：无赛果/缺卡的日子不造分母。"""
    for code, score in (("周X001", "2-1"), ("周X002", "0-0")):
        _mk_card(env["cards"], code)
        _mk_results(env["results"], code, score)
    lj.freeze(DATE, [_pred("周X001", call="主胜"), _pred("周X002", call="平")],
              now="2026-10-16T23:00:00", cards_root=env["cards"])
    (env["judg"] / "1999-01-01.json").write_text("{}", encoding="utf-8")   # 空卷噪声
    rep = ev.eval_all(root=env["judg"], cards_root=env["cards"])
    assert rep["nMatches"] == 2
    assert rep["direction"]["n"] == 2
    assert rep["perDate"][DATE]["nJudged"] == 2
    assert "skipped" in str(rep["perDate"].get("1999-01-01", "")) or \
        rep["perDate"].get("1999-01-01") is None
    # 报告落盘（monkeypatched 路径）
    assert json.loads(ev.OUT_PATH.read_text(encoding="utf-8"))["nMatches"] == 2


def test_missing_result_not_fabricated(env):
    """无赛果场：不入分母,记 noResult——阅卷器禁止拿未知赛果造 0 分。"""
    code = "周X001"
    _mk_card(env["cards"], code)
    lj.freeze(DATE, [_pred(code)], now="2026-10-16T23:00:00", cards_root=env["cards"])
    assert ev.eval_date(DATE, root=env["judg"], cards_root=env["cards"]) is None
    _mk_results(env["results"], code, "2-1")
    (env["results"] / f"{DATE}.json").write_text(
        json.dumps({"date": DATE, "matches": [{"code": "周X009", "result": "1-0"}]},
                   ensure_ascii=False), encoding="utf-8")
    r = ev.eval_date(DATE, root=env["judg"], cards_root=env["cards"])
    assert r["noResult"] == ["周X001"] and r["nJudged"] == 0
