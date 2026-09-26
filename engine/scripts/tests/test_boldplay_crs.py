# engine/scripts/tests/test_boldplay_crs.py
"""Task-8 测试：boldplay 三池卡 CRS 候选源切融合引擎（spec docs/2026-09-26-crs-fusion-redesign §5）。
覆盖四点：族字段透传 / 闸门剔除+关档原因 / HHAD 让位标注 / (h,a) 元组键转 "h:a" 串
（T6 移交项：families top1/top2 携元组键，进 boldplay/JSON 前必须转换）。
Task-10 增补：crsDist 族概率摘要落卡（trend ⑨ fused 口径生产者·T9 移交）。
开发者 sszhang"""
import json
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # engine/scripts/

import boldplay
from boldplay import (_apply_fused_crs, _crs_dist_summary, _fused_crs_candidates,
                      _pick_card_recs, build_three_tier)

CFG = {"r": 0.286, "w": 0.35, "alphaLidstone": 0.5, "familyGateThreshold": 0.28,
       "hhadCedeThreshold": 0.65, "enabled": True}


def _fam(name, prob, t1, t2):
    return {"family": name, "prob": prob, "top1": t1, "top2": t2}


def _fused_row(fams, gate_pass=True, mx=None):
    """fused_legs 单场输出玩具（families/gate/tailP4 同 schema·元组键保留）。"""
    top = fams[0]
    return {"code": "周六001", "match": "主队甲 vs 客队乙", "families": fams,
            "gate": {"pass": gate_pass,
                     "maxProb": round(mx if mx is not None else top["prob"], 4)},
            "p_final_top3": [top["top1"], top["top2"]],
            "tailP4": 0.18, "marketFused": True, "shrunk": False,
            "lambda": [1.4, 1.1]}


BALANCED_FAMS = [                       # 主胜族合计 0.33 < 0.65（不让位）
    _fam("draw", 0.42, ((1, 1), 0.13), ((0, 0), 0.09)),
    _fam("home_clean", 0.20, ((1, 0), 0.10), ((2, 0), 0.06)),
    _fam("away_clean", 0.15, ((0, 1), 0.08), ((0, 2), 0.04)),
    _fam("home_multi", 0.13, ((2, 1), 0.07), ((3, 1), 0.03)),
    _fam("away_multi", 0.10, ((1, 2), 0.05), ((1, 3), 0.02)),
]


def _card():
    """pools_card 输出玩具：模板 CRS 候选 + TTG 候选 + 双行推荐（CRS 引用待换）。"""
    mk = {"code": "周六001", "match": "主队甲 vs 客队乙"}
    crs = {**mk, "pool": "crs", "pick": "1:1", "q": 0.5, "odds": 7.5, "ev": 2.75}
    ttg = {**mk, "pool": "ttg", "pick": "s2", "q": 0.30, "odds": 3.5, "ev": 0.05}
    return {**mk, "candidates": [crs, ttg], "rec_base": crs, "rec_upset": crs,
            "flags": []}


class TestFusedCrsCandidates(unittest.TestCase):
    def test_family_fields_passthrough(self):
        # 族字段透传：top1/top2 行带 family/familyProb/gate；q=族内该比分 P_final
        rows, info = _fused_crs_candidates(
            _fused_row(BALANCED_FAMS), {"1:1": 7.5, "0:0": 11.0, "1:0": 6.0}, CFG)
        self.assertEqual([r["slot"] for r in rows], ["top1", "top2"])
        r1 = rows[0]
        self.assertEqual(r1["pick"], "1:1")
        self.assertEqual(r1["family"], "draw")
        self.assertEqual(r1["familyProb"], 0.42)
        self.assertEqual(r1["gate"], {"pass": True, "maxProb": 0.42})
        self.assertEqual(r1["odds"], 7.5)
        self.assertEqual(r1["ev"], round(0.13 * 7.5 - 1, 4))
        self.assertTrue(r1["marketFused"])
        self.assertEqual(rows[1]["pick"], "0:0")
        self.assertTrue(info["pass"])
        self.assertEqual(info["topFamily"], "draw")
        self.assertNotIn("cedeHHAD", info)          # 主胜族 0.33 < 0.65

    def test_unquoted_pick_skipped(self):
        # 未挂牌比分诚实跳过（无赔率不可下注）：只挂 1:1 → 仅 top1 行
        rows, _ = _fused_crs_candidates(_fused_row(BALANCED_FAMS), {"1:1": 7.5}, CFG)
        self.assertEqual([r["pick"] for r in rows], ["1:1"])

    def test_gate_closed_reason(self):
        # 闸门联动：gate.pass=False → CRS 候选剔除 + 关档原因（铁律 8 空轮≠漏跑）
        rows, info = _fused_crs_candidates(
            _fused_row(BALANCED_FAMS, gate_pass=False), {"1:1": 7.5}, CFG)
        self.assertEqual(rows, [])
        self.assertFalse(info["pass"])
        self.assertIn("关档", info["reason"])
        self.assertIn("28%", info["reason"])


class TestApplyFusedCrs(unittest.TestCase):
    def test_gate_closed_removes_crs_and_marks_card(self):
        card = _apply_fused_crs(_card(), _fused_row(BALANCED_FAMS, gate_pass=False),
                                {"1:1": 7.5}, CFG)
        self.assertFalse([c for c in card["candidates"] if c["pool"] == "crs"])
        self.assertIn("crs_gate_closed", card["flags"])
        self.assertFalse(card["crsGate"]["pass"])
        self.assertIn("reason", card["crsGate"])
        # 模板 CRS 行剔除后双行推荐重选到 TTG，不留悬空引用
        self.assertEqual(card["rec_base"]["pool"], "ttg")
        self.assertEqual(card["rec_upset"]["pool"], "ttg")

    def test_swap_and_recompute_recs(self):
        # 换源落卡：模板 CRS 行被族 top1/top2 行替换；非 CRS 候选（TTG）零改动
        card = _apply_fused_crs(_card(), _fused_row(BALANCED_FAMS),
                                {"1:1": 7.5, "0:0": 11.0}, CFG)
        crs_rows = [c for c in card["candidates"] if c["pool"] == "crs"]
        self.assertEqual([r["pick"] for r in crs_rows], ["1:1", "0:0"])
        self.assertTrue(all("family" in r and "familyProb" in r and "gate" in r
                            for r in crs_rows))
        ttg = [c for c in card["candidates"] if c["pool"] == "ttg"]
        self.assertEqual(len(ttg), 1)

    def test_cede_hhad_marker(self):
        # HHAD 让位：主胜族（home_clean+home_multi）合计 ≥0.65 → CRS 行标 cedeHHAD
        home_fams = [
            _fam("home_clean", 0.42, ((1, 0), 0.20), ((2, 0), 0.12)),
            _fam("home_multi", 0.26, ((2, 1), 0.14), ((3, 1), 0.07)),   # 合计 0.68
            _fam("draw", 0.18, ((1, 1), 0.10), ((0, 0), 0.05)),
            _fam("away_clean", 0.08, ((0, 1), 0.05), ((0, 2), 0.02)),
            _fam("away_multi", 0.06, ((1, 2), 0.04), ((1, 3), 0.01)),
        ]
        rows, info = _fused_crs_candidates(_fused_row(home_fams),
                                           {"1:0": 6.0, "2:0": 9.0}, CFG)
        self.assertTrue(info["cedeHHAD"])
        self.assertTrue(all(r.get("cedeHHAD") is True for r in rows))
        # 阈值下不标注（BALANCED_FAMS 主胜族 0.33）
        rows2, info2 = _fused_crs_candidates(_fused_row(BALANCED_FAMS),
                                             {"1:1": 7.5, "0:0": 11.0}, CFG)
        self.assertNotIn("cedeHHAD", info2)
        self.assertTrue(all("cedeHHAD" not in r for r in rows2))

    def test_tuple_key_serializes(self):
        # (h,a) 元组键转 "h:a" 串：p_final/families 元组不落 JSON（T6 移交验收）
        card = _apply_fused_crs(_card(), _fused_row(BALANCED_FAMS),
                                {"1:1": 7.5, "0:0": 11.0}, CFG)
        crs_rows = [c for c in card["candidates"] if c["pool"] == "crs"]
        self.assertTrue(all(isinstance(r["pick"], str) and ":" in r["pick"]
                            for r in crs_rows))
        json.dumps(card)                            # 元组键残留会 TypeError


class TestCrsDistSummary(unittest.TestCase):
    """task-10：crsDist 族概率摘要落卡（T9 移交——trend ⑨ fused 口径生产者）。
    {family: prob}×5 + tailP4（P(4+)）+ sum（5 族合计）+ marketFused（口径开关）。"""

    def test_summary_shape(self):
        d = _crs_dist_summary(_fused_row(BALANCED_FAMS))
        self.assertEqual(set(d), {"home_clean", "home_multi", "draw", "away_clean",
                                  "away_multi", "tailP4", "sum", "marketFused"})
        self.assertEqual(d["draw"], 0.42)
        self.assertEqual(d["tailP4"], 0.18)
        self.assertEqual(d["sum"], round(0.42 + 0.20 + 0.15 + 0.13 + 0.10, 4))
        self.assertTrue(d["marketFused"])
        self.assertTrue(0 < d["sum"] <= 1)             # 5 族合计=1−族外项 ≤1

    def test_card_carries_dist_pass_and_closed(self):
        # 开档/关档场照落 crsDist（闸门只管出腿不管监控——尾部监控要全量场）
        card = _apply_fused_crs(_card(), _fused_row(BALANCED_FAMS),
                                {"1:1": 7.5, "0:0": 11.0}, CFG)
        self.assertEqual(card["crsDist"]["draw"], 0.42)
        card2 = _apply_fused_crs(_card(), _fused_row(BALANCED_FAMS, gate_pass=False),
                                 {"1:1": 7.5}, CFG)
        self.assertIn("crsDist", card2)
        json.dumps(card)                                # 落盘无序列化错误

    def test_template_caliber_flagged(self):
        # marketFused=False（纯模板日）照落但口径标 False——resolver 不得按 fused 计分
        row = _fused_row(BALANCED_FAMS)
        row["marketFused"] = False
        d = _crs_dist_summary(row)
        self.assertFalse(d["marketFused"])

    def test_no_families_no_dist(self):
        self.assertIsNone(_crs_dist_summary({}))        # fused 无族输出 → 不落
        self.assertIsNone(_crs_dist_summary(None))


class TestPickCardRecs(unittest.TestCase):
    def test_top2_excluded_from_upset(self):
        # 翻身档 CRS 腿候选=族 top1：top2（单关双选材料·赔率更高）不入翻身
        top1 = {"pool": "crs", "pick": "1:1", "q": 0.13, "odds": 7.5, "ev": 0.0,
                "slot": "top1"}
        top2 = {"pool": "crs", "pick": "0:0", "q": 0.12, "odds": 16.0, "ev": 0.92,
                "slot": "top2"}
        ttg = {"pool": "ttg", "pick": "s2", "q": 0.30, "odds": 3.5, "ev": 0.05}
        _, ru = _pick_card_recs([top1, top2, ttg], [])
        self.assertIs(ru, top1)                     # 无 top2 过滤时会被 16.0 顶替
        # 全分歧退路：非 CRSCRS 候选兜底（pools_card I1 裁定同式）
        _, ru2 = _pick_card_recs([dict(top1, divergent=True), ttg], [])
        self.assertIs(ru2, ttg)

    def test_all_divergent_crs_yields_none(self):
        top1 = {"pool": "crs", "pick": "1:1", "q": 0.13, "odds": 7.5, "ev": 0.0,
                "slot": "top1", "divergent": True}
        top2 = {"pool": "crs", "pick": "0:0", "q": 0.12, "odds": 16.0, "ev": 0.1,
                "slot": "top2", "divergent": True}
        rb, ru = _pick_card_recs([top1, top2], [])
        self.assertEqual(ru, None)                  # 本场不出翻身腿（含 top2 不救场）


TOY_FREQ = {"england-premier": Counter({"1:1": 30, "1:0": 25, "2:1": 20, "0:1": 15,
                                        "0:0": 10, "2:0": 8, "1:2": 7, "__n": 115})}


def _day():
    # 挂 1:1@3.4：fused q(1:1)≈23.4% vs 隐含 1/(3.4×1.512)≈19.5% → Δ3.9pp<5pp 不分歧；
    # 模板链 band(≥4.0) 下原 CRS 候选=1:0@4.2（换源后应消失）
    return {"matches": [{
        "matchNumStr": "周六001", "league": "英超", "home": "主队甲", "away": "客队乙",
        "had": {"h": 2.1, "d": 3.2, "a": 3.4},
        "crs": {"1:1": 3.4, "1:0": 4.2}, "ttg": {}, "hafu": {}}]}


class TestBuildThreeTierFused(unittest.TestCase):
    def test_integration_fused_cards_and_legs(self):
        out = build_three_tier(_day(), TOY_FREQ, seq=2, zh={}, form={}, hafu_map={})
        self.assertEqual(out["crsSource"], "fused")
        card = out["cards"][0]
        self.assertTrue(card["crsGate"]["pass"])
        self.assertEqual(card["crsGate"]["topFamily"], "draw")   # 平局族≈31.8% 居首
        crs_rows = [c for c in card["candidates"] if c["pool"] == "crs"]
        self.assertEqual([r["pick"] for r in crs_rows], ["1:1"])  # top2 未挂牌跳过
        self.assertEqual(crs_rows[0]["family"], "draw")
        up_crs = [l for l in out["tiers"]["upset"]["legs"] if l["play"] == "crs"]
        self.assertTrue(up_crs)                                  # 唯一候选 → 翻身腿
        self.assertEqual(up_crs[0]["modelSupport"], "fused")
        self.assertEqual(up_crs[0]["family"], "draw")
        json.dumps(out)                                          # 落盘无序列化错误
        self.assertIn("周六001", boldplay.render_ticket(out))
        # task-10：crsDist 落卡（玩具市场 2 项 <20 → 纯模板口径，标 False 防口径混淆）
        dist = card["crsDist"]
        self.assertTrue(0 <= dist["tailP4"] <= 1)
        self.assertFalse(dist["marketFused"])

    def test_rollback_enabled_false_keeps_template_chain(self):
        # fusion_crs.enabled=false 一键回滚：卡零改动（无 crsGate/族字段/crsDist），CRS 候选
        # 仍是 pools_card 模板 q 链（band 过滤后 1:0@4.2）
        import unittest.mock as mock
        with mock.patch.object(boldplay, "load_fusion_crs_safe",
                               return_value={**CFG, "enabled": False}):
            out = build_three_tier(_day(), TOY_FREQ, seq=2, zh={}, form={}, hafu_map={})
        self.assertEqual(out["crsSource"], "template")
        self.assertTrue(all("crsGate" not in c and "crsDist" not in c
                            for c in out["cards"]))
        crs_rows = [c for c in out["cards"][0]["candidates"] if c["pool"] == "crs"]
        self.assertEqual([r["pick"] for r in crs_rows], ["1:0"])
        self.assertTrue(all("family" not in r for r in crs_rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
