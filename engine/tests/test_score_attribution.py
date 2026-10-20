# engine/tests/test_score_attribution.py 顶部
import json, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from score_attribution import load_match_record

class TestLoadMatchRecord(unittest.TestCase):
    def test_load_1019_monza(self):
        rec = load_match_record("2026-10-19", "周一001")
        self.assertIsNotNone(rec)
        # 02-results 原生字段
        self.assertEqual(rec["league"], "意甲")
        self.assertEqual(rec["result"], {"hg": 4, "ag": 0, "ht_hg": 1, "ht_ag": 0})
        self.assertEqual(rec["odds"], [2.25, 3.10, 2.76])
        # 画像补齐（蒙扎/卡利亚里在别名表内；elo 可能 None 但键必须在）
        self.assertIn("team_h", rec); self.assertIn("elo_h", rec)
        self.assertIn("rest_days_h", rec)
        # 伤停粗档来源（画像 injuries 或 lineup 轨，均无则空列表）
        # 实测末次快照（2026-10-20-lineups.json 键2042073·at 2026-10-19T01:26）：h=5（含停赛）/a=2
        self.assertEqual(rec["inj_n_h"], 5)
        self.assertEqual(rec["inj_n_a"], 2)

    def test_missing_match_returns_none(self):
        self.assertIsNone(load_match_record("2026-10-19", "不存在999"))


from attribution_dims import lambda_from_had, dim_elo

class TestBaseline(unittest.TestCase):
    def test_lambda_reasonable(self):
        lam = lambda_from_had([2.25, 3.10, 2.76], 2.4)  # 意甲场均总球≈2.4
        self.assertIsNotNone(lam)
        lh, la = lam
        self.assertGreater(lh, la)          # 主赔更低→主λ更高
        self.assertAlmostEqual(lh + la, 2.4, delta=0.01)
        self.assertTrue(0.3 < lh < 2.2)

    def test_lambda_bad_sum_none(self):
        self.assertIsNone(lambda_from_had([2.25, 3.10, 2.76], 0.1))


class TestDimElo(unittest.TestCase):
    def test_elo_offset(self):
        off = dim_elo(1490.0, 1450.0)   # 主强40 → net=0.1，对半分 h=+0.05/a=−0.05（controller 裁决修断言·实现语义正确）
        self.assertAlmostEqual(off["h"], 0.05, delta=0.01)
        self.assertAlmostEqual(off["a"], -0.05, delta=0.01)

    def test_elo_missing(self):
        self.assertIsNone(dim_elo(None, 1450.0))
