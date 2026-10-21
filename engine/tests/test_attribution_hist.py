# engine/tests/test_attribution_hist.py
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from attribution_hist import hist_match_record, fd_date
from attribution_dims import dim_red, dim_shots, dim_finishing

ROW = {"Date": "11/08/2023", "HomeTeam": "Burnley", "AwayTeam": "Man City",
       "FTHG": "0", "FTAG": "3", "HTHG": "0", "HTAG": "1",
       "HS": "7", "AS": "16", "HST": "2", "AST": "7",
       "HR": "0", "AR": "0", "B365H": "6.5", "B365D": "4.33", "B365A": "1.44",
       "PSCH": "", "PSCD": "", "PSCA": "", "HxG": "", "AxG": ""}

class TestHist(unittest.TestCase):
    def test_fd_date(self):
        self.assertEqual(fd_date("11/08/2023"), "2023-08-11")

    def test_hist_record(self):
        rec = hist_match_record(dict(ROW), "england-premier", "英超")
        self.assertEqual(rec["date"], "2023-08-11")
        self.assertEqual(rec["result"], {"hg": 0, "ag": 3, "ht_hg": 0, "ht_ag": 1})
        self.assertEqual(rec["odds"], [6.5, 4.33, 1.44])       # PSCH 空→B365
        self.assertEqual(rec["team_h"], "burnley")             # 三键匹配
        self.assertEqual(rec["team_a"], "man-city")
        self.assertEqual(rec["red_h"], 0); self.assertEqual(rec["red_a"], 0)
        self.assertEqual(rec["hst"], 2); self.assertEqual(rec["ast"], 7)
        self.assertIsNone(rec["xg_h"])                          # HxG 空→None

    def test_unmatched_team_none(self):
        bad = dict(ROW); bad["FTHG"] = "9"; bad["FTAG"] = "9"   # 假比分必不匹配
        rec = hist_match_record(bad, "england-premier", "英超")
        self.assertIsNone(rec["team_h"]); self.assertIsNone(rec["team_a"])

class TestEventDims(unittest.TestCase):
    def test_dim_red(self):
        off = dim_red({"red_h": 0, "red_a": 1})
        self.assertEqual(off["dim"], "E1.red")
        self.assertAlmostEqual(off["h"], 0.35, delta=0.001)   # 客队红牌→主队+
        self.assertAlmostEqual(off["a"], 0.0, delta=0.001)
        self.assertIsNone(dim_red({"red_h": 0, "red_a": 0}))

    def test_dim_shots(self):
        # 主 5 射正进 3（转化 0.6）客 7 射正进 0（0.0）→ 主超额 (0.6-0.3)*5=1.5 客亏 -2.1
        off = dim_shots({"hst": 5, "ast": 7, "result": {"hg": 3, "ag": 0}})
        self.assertEqual(off["dim"], "E4.shots")
        self.assertAlmostEqual(off["h"], 1.5, delta=0.01)
        self.assertAlmostEqual(off["a"], -2.1, delta=0.01)
        self.assertIsNone(dim_shots({"hst": 0, "ast": 0, "result": {"hg": 1, "ag": 1}}))

    def test_dim_finishing(self):
        off = dim_finishing({"xg_h": 1.2, "xg_a": 0.8, "result": {"hg": 3, "ag": 0}})
        self.assertEqual(off["dim"], "E5.finishing")
        self.assertAlmostEqual(off["h"], (3-1.2)*0.5, delta=0.01)
        self.assertAlmostEqual(off["a"], (0-0.8)*0.5, delta=0.01)
        self.assertIsNone(dim_finishing({"xg_h": None, "xg_a": 1.0, "result": {"hg": 1, "ag": 1}}))

if __name__ == "__main__":
    unittest.main()
