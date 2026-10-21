# engine/tests/test_freq_stats.py
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from freq_stats import load_bills, freq_table

class TestFreqStats(unittest.TestCase):
    def test_freq_table_basic(self):
        bills = [{"league": "英超", "date": "2023-08-11", "result": {"hg": 1, "ag": 3},   # brief 原文 hg:0 总球3<4 与其断言 rate_on==1.0 自相矛盾，fixture 改 1:3 总球4 命中 band（impl 不动）
                  "shape_factors": [], "offsets": [{"dim": "E1.red", "h": 0.35, "a": 0.0}],
                  "coverage": ["E1.red"], "depth": "deep"}]
        t = freq_table(bills, on=lambda b: any(o["dim"] == "E1.red" for o in b["offsets"]),
                       band=lambda b: (b["result"]["hg"] + b["result"]["ag"]) >= 4)
        self.assertEqual(t["n_on"], 1); self.assertEqual(t["n_off"], 0)
        self.assertEqual(t["rate_on"], 1.0)

    def test_load_bills_reads_hist_dir(self):
        bills = load_bills()
        self.assertGreater(len(bills), 10000)   # 16415 场出账后

if __name__ == "__main__":
    unittest.main()
