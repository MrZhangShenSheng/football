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
