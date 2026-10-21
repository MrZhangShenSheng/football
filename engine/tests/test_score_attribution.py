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


from attribution_dims import dim_stage

class TestDimStage(unittest.TestCase):
    def test_league_regular_default(self):
        rec = {"league": "意甲", "date": "2026-10-19"}
        sf = dim_stage(rec)
        self.assertEqual(sf["dim"], "L0.stage")
        self.assertEqual(sf["tail_scale"], 1.0)
        self.assertEqual(sf["evidence"], "league_regular")

    def test_unknown_league_neutral(self):
        sf = dim_stage({"league": "某新联赛", "date": "2026-10-19"})
        self.assertEqual(sf["tail_scale"], 1.0)
        self.assertEqual(sf["evidence"], "no_format")

    def test_fifa_virus_window(self):
        # 10-20 窗口结束后 3 天内 → fifa_virus 命中（Task5 的 flag 也走档案日历）
        sf = dim_stage({"league": "意甲", "date": "2026-10-22"})
        self.assertIn("fifa_virus", sf["evidence"])


from attribution_dims import dim_derby
from score_attribution import fill_rest_days

class TestDerbyRest(unittest.TestCase):
    def test_derby_hit(self):
        off = dim_derby("英超", "arsenal", "tottenham")   # 真实ID：tottenham-hotspur→tottenham（zh_to_id 反查）
        self.assertEqual(off["dim"], "derby")
        self.assertGreater(off["tail_scale"], 1.0)   # 德比抬尾格

    def test_derby_miss(self):
        self.assertIsNone(dim_derby("英超", "arsenal", "everton"))

    def test_rest_days(self):
        rec = load_match_record("2026-10-19", "周一001")
        rec = fill_rest_days(rec)
        # 卡利亚里 10-15 有欧冠场(league库无欧冠行则 None 容忍)
        self.assertIn(rec["rest_days_h"], [None] + list(range(2, 15)))


from attribution_dims import dim_htft, dim_injury_coarse, classify_script

class TestScript(unittest.TestCase):
    def test_htft_second_half_surge(self):
        # 半场1:0 全场4:0 → 主队下半场+3
        rec = {"result": {"hg": 4, "ag": 0, "ht_hg": 1, "ht_ag": 0}}
        off = dim_htft(rec)
        self.assertEqual(off["dim"], "E2.htft")
        self.assertGreater(off["h"], 0)

    def test_htft_missing(self):
        self.assertIsNone(dim_htft({"result": {"hg": 4, "ag": 0, "ht_hg": None, "ht_ag": None}}))

    def test_script_early_crush(self):
        rec = {"result": {"hg": 4, "ag": 0, "ht_hg": 1, "ht_ag": 0}}
        s = classify_script(rec)
        self.assertIn(s["type"], {"crush", "early_crush"})

    def test_script_irongrid(self):
        rec = {"result": {"hg": 0, "ag": 0, "ht_hg": 0, "ht_ag": 0}}
        self.assertEqual(classify_script(rec)["type"], "iron_grid")

    def test_injury_coarse_marks_only(self):
        off = dim_injury_coarse({"inj_n_h": 0, "inj_n_a": 3})
        self.assertEqual(off["dim"], "R1.coarse")
        self.assertIsNone(off["h"])  # 期1不贡献球数
        self.assertIn("3", off["evidence"])


from score_attribution import build_bill

class TestBuildBill(unittest.TestCase):
    def test_bill_self_consistent(self):
        rec = load_match_record("2026-10-19", "周一001")
        rec = fill_rest_days(rec)
        bill = build_bill(rec)
        # 双栏量纲分离
        self.assertIn("offsets", bill); self.assertIn("shape_factors", bill)
        for off in bill["offsets"]:
            self.assertIsInstance(off["h"], float)      # offsets 全为数值（R1粗档 None 不进 offsets）
        # 自洽：reconstructed = baseline + Σoffsets
        lam_h, lam_a = bill["baseline"]["lam_h"], bill["baseline"]["lam_a"]
        sum_h = lam_h + sum(o["h"] for o in bill["offsets"])
        self.assertAlmostEqual(bill["reconstructed"]["hg"], round(sum_h, 3), delta=0.01)
        # 深挖档判定：4-0 vs 基线≈1.3:1.1 → 偏移 2.7+1.1>1.5
        self.assertEqual(bill["depth"], "deep")
        self.assertLess(bill["explained_pct"], 0.5)   # 期1缺画面维度，此场解释力必然不足
        self.assertEqual(bill["explain_flag"], "insufficient")
        self.assertIn("E5.finishing", bill["missing"])  # 缺项诚实标注
        self.assertEqual(bill["script"]["type"], "crush")

    def test_bill_light_when_market_nailed(self):
        # 构造：实际 1:1 vs 基线 1.2:1.0 → 总偏移 0.2+0.1<1.5 → light 档
        rec = {"date": "2026-10-19", "code": "测试", "league": "意甲",
               "match": "x vs y", "team_h": None, "team_a": None, "league_dir": "italy-serie-a",
               "odds": [2.25, 3.10, 2.76], "pin_close": None,
               "elo_h": None, "elo_a": None, "inj_n_h": 0, "inj_n_a": 0,
               "rest_days_h": None, "rest_days_a": None,
               "result": {"hg": 1, "ag": 1, "ht_hg": 0, "ht_ag": 0}}
        bill = build_bill(rec)
        self.assertEqual(bill["depth"], "light")


class TestCLI(unittest.TestCase):
    def test_main_single(self):
        import subprocess
        from score_attribution import ROOT
        out = subprocess.run(
            [sys.executable, "engine/scripts/score_attribution.py",
             "single", "2026-10-19", "周一001"],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(out.returncode, 0, out.stderr)
        bill = json.loads(out.stdout)
        self.assertEqual(bill["code"], "周一001")
