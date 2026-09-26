# engine/scripts/tests/test_family_monitor.py
"""Task-9 测试：familyHit 回填判定 + trend 族校准/尾部 4+ 专项区块（TDD 先测后写）。
覆盖：CRS 腿判定真/假/null 三路径 / 非 CRS 腿不动 / crsFamilies 优先与 boldplay
crsGate 兜底解析 / 尾部概率 fused 与 qcache 双口径 / 分桶与空数据（样本积累中）路径。
开发者 sszhang"""
import json
import sys
import unittest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))  # engine/scripts/

from crs_fusion import FAMILIES  # 族映射单一事实源（T9 判定 import 复用，勿复制）


def _fam(name, prob):
    return {"family": name, "prob": prob}


class TestFamilyHitJudging(unittest.TestCase):
    """apply_family_ctx：CRS pick → top1 族 → actual ∈ 族 判定三路径。"""

    def _apply(self, rec, score, pred_dir=None, qcache_dir=None):
        import tempfile
        from backfill import apply_family_ctx
        # 缺省空临时目录隔离真实 03-predictions/qcache（rec 无 code 时兜底不触真实文件）
        with tempfile.TemporaryDirectory() as td:
            from pathlib import Path
            empty = Path(td)
            return apply_family_ctx(rec, score, "2026-09-26", {}, {},
                                    pred_dir=pred_dir or empty,
                                    qcache_dir=qcache_dir or empty)

    def test_actual_in_top1_family_true(self):
        rec = {"pick": "CRS 1:1", "crsFamilies": [_fam("draw", 0.42), _fam("home_clean", 0.2)]}
        changed = self._apply(rec, (1, 1))
        self.assertTrue(changed)
        self.assertIs(rec["familyHit"], True)
        self.assertEqual(rec["familyName"], "draw")
        self.assertEqual(rec["familyProb"], 0.42)

    def test_actual_outside_top1_family_false(self):
        rec = {"pick": "crs 2:1", "crsFamilies": [_fam("draw", 0.42)]}
        self._apply(rec, (2, 0))
        self.assertIs(rec["familyHit"], False)   # (2,0)∈home_clean 不在 draw

    def test_no_family_data_null(self):
        import tempfile
        from pathlib import Path
        rec = {"code": "周六001", "pick": "CRS 1:1"}   # 无 crsFamilies，boldplay 目录为空
        with tempfile.TemporaryDirectory() as td:
            self._apply(rec, (1, 1), pred_dir=Path(td), qcache_dir=Path(td))
        self.assertIsNone(rec["familyHit"])
        self.assertIsNone(rec["familyName"])

    def test_non_crs_pick_untouched(self):
        rec = {"pick": "HAD 主胜", "crsFamilies": [_fam("draw", 0.42)]}
        changed = self._apply(rec, (2, 0))
        self.assertFalse(changed)
        self.assertNotIn("familyHit", rec)      # 非 CRS 腿不落字段


class TestFamilyContextResolution(unittest.TestCase):
    """resolve_family_ctx：matches[].crsFamilies 优先 → boldplay crsGate 兜底。"""

    def test_crsfamilies_argmax_wins(self):
        from backfill import resolve_family_ctx
        rec = {"code": "周六001",
               "crsFamilies": [_fam("home_clean", 0.2), _fam("draw", 0.42)]}
        fam, prob = resolve_family_ctx(rec, "2026-09-26", {}, pred_dir=None)
        self.assertEqual((fam, prob), ("draw", 0.42))

    def test_boldplay_gate_fallback(self):
        import tempfile
        from pathlib import Path
        from backfill import resolve_family_ctx
        with tempfile.TemporaryDirectory() as td:
            pred = Path(td)
            (pred / "2026-09-26-boldplay.json").write_text(json.dumps({
                "cards": [{"code": "周日001", "crsGate": {"pass": True, "maxProb": 0.2933,
                                                          "topFamily": "draw"}},
                          {"code": "周六002", "crsGate": {"pass": False, "maxProb": 0.25,
                                                          "topFamily": "draw"}}]}), encoding="utf-8")
            cache = {}
            fam, prob = resolve_family_ctx({"code": "周日001"}, "2026-09-26", cache, pred_dir=pred)
            self.assertEqual((fam, prob), ("draw", 0.2933))
            fam2, prob2 = resolve_family_ctx({"code": "周六002"}, "2026-09-26", cache, pred_dir=pred)
            self.assertEqual((fam2, prob2), ("draw", 0.25))   # 关档场族上下文照读（监控不拦）
            fam3, prob3 = resolve_family_ctx({"code": "周六999"}, "2026-09-26", cache, pred_dir=pred)
            self.assertEqual((fam3, prob3), (None, None))     # 卡上无此场 → null 路径

    def test_unknown_family_name_null_hit(self):
        import tempfile
        from pathlib import Path
        from backfill import apply_family_ctx
        rec = {"pick": "CRS 1:1", "crsFamilies": [_fam("mystery", 0.5)]}
        with tempfile.TemporaryDirectory() as td:
            empty = Path(td)
            apply_family_ctx(rec, (1, 1), "2026-09-26", {}, {},
                             pred_dir=empty, qcache_dir=empty)
        self.assertIsNone(rec["familyHit"])     # 族名不在 FAMILIES → 无法判 → null


class TestTailProbResolution(unittest.TestCase):
    """resolve_tail_prob：fused（rec.crsDist）优先 → qcache ttg 并桶降级 → 无源 null。"""

    def test_fused_crs_dist(self):
        from backfill import resolve_tail_prob
        rec = {"crsDist": {"1:1": 0.3, "3:1": 0.1, "0:0": 0.2, "2:3": 0.05, "1:0": 0.35}}
        tp, src = resolve_tail_prob(rec, "2026-09-26", {})
        self.assertEqual(src, "fused")          # 3:1(4球)+2:3(5球)=0.15
        self.assertAlmostEqual(tp, 0.15)

    def test_qcache_ttg_fallback(self):
        import tempfile
        from pathlib import Path
        from backfill import resolve_tail_prob
        with tempfile.TemporaryDirectory() as td:
            qc = Path(td)
            (qc / "2026-09-27.json").write_text(json.dumps(
                {"周六001": {"ttg": [["s0", 0.07], ["s4", 0.15], ["s5", 0.05],
                                    ["s6", 0.02], ["s7", 0.01]]}}), encoding="utf-8")
            tp, src = resolve_tail_prob({"code": "周六001"}, "2026-09-26", {},
                                        qcache_dir=qc)
            self.assertEqual(src, "qcache")
            self.assertAlmostEqual(tp, 0.23)    # s4+s5+s6+s7

    def test_no_source_null(self):
        from backfill import resolve_tail_prob
        tp, src = resolve_tail_prob({"code": "周六001"}, "2026-09-26", {})
        self.assertEqual((tp, src), (None, None))


def _leg(prob, hit, result="1-1", tail=None, src=None):
    r = {"pick": "CRS 1:1", "familyHit": hit, "familyProb": prob, "result": result}
    if tail is not None:
        r["crsTailProb"], r["crsTailSource"] = tail, src
    return r


class TestFamilyCalibration(unittest.TestCase):
    """trend 区块①：族概率分桶校准 + 二项检验三数字（只出数不判）。"""

    def test_bins_and_summary(self):
        from trend_report import build_family_calibration
        legs = [_leg(0.30, True), _leg(0.32, False),        # 桶 0.28~0.35
                _leg(0.40, True), _leg(0.44, True),          # 桶 0.35~0.45
                _leg(0.50, False)] + [_leg(0.20, True)] * 2  # 闸门下不入桶只入汇总
        out = build_family_calibration(legs)
        b0, b1, b2 = out["buckets"]
        self.assertEqual(b0["n"], 2)
        self.assertAlmostEqual(b0["pred"], 0.31)
        self.assertAlmostEqual(b0["obs"], 0.5)
        self.assertEqual(b1["n"], 2)
        self.assertAlmostEqual(b1["obs"], 1.0)
        self.assertEqual(b2["n"], 1)
        self.assertEqual(out["n_legs"], 7)                   # 含闸门下腿
        self.assertEqual(out["hits"], 5)
        self.assertAlmostEqual(out["expected"], 0.30 + 0.32 + 0.40 + 0.44 + 0.50 + 0.40)

    def test_empty_returns_none(self):
        from trend_report import build_family_calibration
        self.assertIsNone(build_family_calibration([]))
        self.assertIsNone(build_family_calibration([{"pick": "HAD 主胜", "result": "2-0"}]))
        self.assertIsNone(build_family_calibration([{"familyHit": None, "familyProb": 0.3}]))

    def test_null_prob_leg_excluded_from_bins(self):
        from trend_report import build_family_calibration
        out = build_family_calibration([_leg(None, True)])
        self.assertEqual(out["n_legs"], 0)                   # 无概率不入监控池
        self.assertTrue(all(b["n"] == 0 for b in out["buckets"]))


class TestTailMonitor(unittest.TestCase):
    """trend 区块②：尾部 4+ 专项——实际占比 vs 尾部概率均值，按口径分组不并池。"""

    def test_groups_by_source_with_three_numbers(self):
        from trend_report import build_tail_monitor
        recs = [_leg(0.4, True, result="2-2", tail=0.10, src="fused"),    # 4球 → 4+ 命中
                _leg(0.4, False, result="1-0", tail=0.12, src="fused"),
                _leg(0.4, True, result="3-1", tail=0.23, src="qcache")]   # 4球
        out = build_tail_monitor(recs)
        self.assertEqual(set(out), {"fused", "qcache"})
        f = out["fused"]
        self.assertEqual(f["n_legs"], 2)
        self.assertEqual(f["hits"], 1)
        self.assertAlmostEqual(f["expected"], 0.22)
        self.assertAlmostEqual(f["pred_mean"], 0.11)
        self.assertAlmostEqual(f["actual_share"], 0.5)
        self.assertEqual(out["qcache"]["hits"], 1)

    def test_unpaired_excluded(self):
        from trend_report import build_tail_monitor
        recs = [_leg(0.4, True, result=None, tail=0.2, src="fused"),   # 无赛果 → 出局
                _leg(0.4, True, result="2-1", tail=None)]              # 无尾部概率 → 出局
        self.assertIsNone(build_tail_monitor(recs))

    def test_empty_returns_none(self):
        from trend_report import build_tail_monitor
        self.assertIsNone(build_tail_monitor([]))


class TestRenderBlocks(unittest.TestCase):
    """render 冒烟：空数据渲染'样本积累中'不崩；有数据出三数字。"""

    def _render(self, fam_cal, tail_mon):
        from trend_report import render
        series = {"rounds": [], "cum": {"n": 0, "n_dir": 0, "ll_model": 0.0, "ll_mkt": 0.0,
                                        "hit": 0, "score_hit": 0, "score_n": 0},
                  "rolling": [], "filled": []}
        html = render(series, [], {"league": [], "star": [], "grade": [],
                                   "pick_type": [], "plan": []},
                      "结论", {"generatedAt": "2026-09-27", "n_total": 0, "n_result": 0,
                               "n_rounds": 0}, {}, [], fam_cal, tail_mon)
        return html

    def test_empty_renders_accumulating(self):
        html = self._render(None, None)
        self.assertIn("样本积累中", html)

    def test_with_data_renders_three_numbers(self):
        fam = {"buckets": [{"bin": "28%~35%", "n": 2, "pred": 0.31, "obs": 0.5},
                            {"bin": "35%~45%", "n": 0, "pred": None, "obs": None},
                            {"bin": "45%~101%", "n": 0, "pred": None, "obs": None}],
               "hits": 1, "expected": 0.62, "n_legs": 2}
        tail = {"fused": {"n_legs": 2, "hits": 1, "expected": 0.22,
                          "pred_mean": 0.11, "actual_share": 0.5}}
        html = self._render(fam, tail)
        self.assertIn("累计命中", html)
        self.assertIn("n_legs", html)
        self.assertIn("样本积累中", html)          # 空桶（35%~45%/45%+）仍标积累中


if __name__ == "__main__":
    unittest.main()
