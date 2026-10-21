# engine/tests/test_attribution_hist.py
import json
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from attribution_hist import hist_match_record, fd_date, FD_LEAGUES
from attribution_dims import dim_red, dim_shots, dim_finishing, classify_script
from score_attribution import build_bill, LEAGUE_DIR_MAP

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

    def test_odds_both_missing_none(self):   # T1 遗留：PSC+B365 双缺 → None
        no_odds = dict(ROW)
        no_odds["B365H"] = no_odds["B365D"] = no_odds["B365A"] = ""
        self.assertIsNone(hist_match_record(no_odds, "england-premier", "英超")["odds"])

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

class TestScriptSymmetric(unittest.TestCase):
    def test_away_crush(self):
        rec = {"result": {"hg": 0, "ag": 3, "ht_hg": 0, "ht_ag": 1}}
        s = classify_script(rec)
        self.assertEqual(s["type"], "crush")          # 期1 此场走 press_counter——对称修正
        self.assertIn("away_win", s["reason"])

    def test_home_crush_no_tag(self):   # 主胜 reason 保持期1 兼容（不加 away_win）
        s = classify_script({"result": {"hg": 4, "ag": 0, "ht_hg": 1, "ht_ag": 0}})
        self.assertEqual(s["type"], "crush")
        self.assertNotIn("away_win", s["reason"])

    def test_comeback_not_press(self):
        # 主队半场 1:0 领先被客队下半场 0:2 反超（1:2）＝逆转局非压上反杀；
        # 守对称帧翻转（half_diff 与 s2_net 须同帧），防 press_counter 条件做反
        s = classify_script({"result": {"hg": 1, "ag": 2, "ht_hg": 1, "ht_ag": 0}})
        self.assertEqual(s["type"], "open_play")

class TestLeagueDirMap(unittest.TestCase):
    def test_covers_fd_leagues(self):   # LEAGUE_DIR_MAP 以 FD_LEAGUES 16 键对齐补齐
        for code, (league_dir, zh) in FD_LEAGUES.items():
            self.assertEqual(LEAGUE_DIR_MAP.get(zh), league_dir, f"{code} {zh} 未对齐")

HIST_REC = {"date": "2023-08-11", "kickoff": "2023-08-11", "code": "hist:t",
    "league": "英超", "match": "x vs y", "team_h": None, "team_a": None,
    "league_dir": "england-premier", "odds": [6.5, 4.33, 1.44], "pin_close": None,
    "elo_h": None, "elo_a": None, "inj_n_h": 0, "inj_n_a": 0,
    "rest_days_h": None, "rest_days_a": None,
    "result": {"hg": 0, "ag": 3, "ht_hg": 0, "ht_ag": 1},
    "red_h": 0, "red_a": 1, "hst": 2, "ast": 7, "xg_h": None, "xg_a": None}

class TestHistBill(unittest.TestCase):
    def test_bill_with_events(self):
        bill = build_bill(HIST_REC)
        dims = [o["dim"] for o in bill["offsets"]]
        self.assertIn("E1.red", dims)      # 客红牌→主+0.35
        self.assertIn("E4.shots", dims)
        self.assertNotIn("E5.finishing", dims)   # xg 缺
        self.assertIn("E5.finishing", bill["missing"])
        # 解释力应显著优于纯结构期1：伯恩利0:3场 E2+E1+E4 全上
        self.assertGreater(bill["explained_pct"], 0.3)

class TestBatchHist(unittest.TestCase):
    def test_cli_single_csv(self):
        import subprocess
        from score_attribution import ROOT
        out = subprocess.run(
            [sys.executable, "engine/scripts/attribution_hist.py", "batch-hist",
             "--season", "2324", "--code", "E0"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(out.returncode, 0, out.stderr)
        p = ROOT / "data/04-summaries/score-attribution/hist/E0_2324-attribution.json"
        self.assertTrue(p.exists())
        bills = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(len(bills), 380)
        dims_seen = {o["dim"] for b in bills for o in b.get("offsets", [])}
        self.assertIn("E1.red", dims_seen)
        self.assertIn("E4.shots", dims_seen)

if __name__ == "__main__":
    unittest.main()
