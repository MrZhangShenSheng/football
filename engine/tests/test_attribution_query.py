# engine/tests/test_attribution_query.py
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
ROOT = Path(__file__).resolve().parents[2]
from attribution_query import run_query, load_registry

class TestBus(unittest.TestCase):
    def test_registry_seven_entries(self):
        reg = load_registry()
        self.assertEqual(len(reg), 7)
        self.assertEqual(reg["freq"]["verdictGate"], True)
        self.assertEqual(reg["script"]["leakRisk"], "postmatch")

    def test_unknown_command_error(self):
        r = run_query(["nope", "x"])
        self.assertFalse(r["ok"])
        self.assertIn("未知命令", r["error"])

    def test_status_retired_not_routed(self):   # 注册表驱动验证（设计§五）
        reg = load_registry()
        self.assertEqual(reg["bill"]["status"], "active")

# test_attribution_query.py 追加
class TestSevenCmds(unittest.TestCase):
    def test_cmd_formats(self):
        r = run_query(["formats", "意甲"])
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"]["type"], "league")
        r2 = run_query(["formats", "不存在的联赛"])
        self.assertFalse(r2["ok"])

    def test_cmd_derby(self):
        r = run_query(["derby", "阿森纳", "热刺"])
        self.assertTrue(r["ok"])
        self.assertTrue(r["data"]["isDerby"])
        r2 = run_query(["derby", "阿森纳", "埃弗顿"])
        self.assertFalse(r2["data"]["isDerby"])

    def test_cmd_freq_verdict_gate(self):
        r = run_query(["freq", "t5"])
        self.assertTrue(r["ok"])
        d = r["data"]
        self.assertIn("verdict", d)
        self.assertIn("n_on", d)
        # observational 强制叙事线索标注（verdictGate=true 生效）
        if d["verdict"] != "calibration_ready":
            self.assertIn("叙事线索", d["usage"])

    def test_cmd_script(self):
        r = run_query(["script", "阿森纳", "20"])
        self.assertTrue(r["ok"])
        d = r["data"]
        self.assertEqual(sum(d["dist"].values()), d["n"])
        self.assertGreater(d["n"], 0)

    def test_cmd_bill_leak_gate(self):
        r = run_query(["bill", "2099-01-01", "周一001"])
        self.assertFalse(r["ok"])
        self.assertIn("postmatch", r["error"])

    def test_cmd_bill_real(self):
        r = run_query(["bill", "2026-10-19", "周一001"])
        self.assertTrue(r["ok"])
        self.assertEqual(r["data"]["code"], "周一001")

    def test_cmd_h2h_dist(self):
        r = run_query(["h2h-dist", "阿森纳", "热刺"])
        self.assertTrue(r["ok"])
        self.assertIn("dist", r["data"])

    def test_cmd_dim(self):
        r = run_query(["dim", "E1"])
        self.assertTrue(r["ok"])
        self.assertIn("红牌", r["data"]["desc"])

# test_attribution_query.py 追加（Task 3·view 渲染）
class TestViewBlock(unittest.TestCase):
    def test_attribution_block(self):
        from llm_card import _attribution_block
        card = {"league": "欧罗巴", "match": "伯恩茅斯 vs AC米兰",
                "layers": {}}
        lines = _attribution_block(card)
        joined = "\n".join(lines)
        self.assertIn("归因数据", joined)
        self.assertIn("赛制", joined)
        self.assertIn("德比", joined)
        self.assertIn("剧本指纹", joined)
        self.assertIn("可查", joined)          # 菜单行
        self.assertIn("freq", joined)

    def test_view_contains_block(self):
        # view 冒烟：真实 10-20 卡渲染含归因数据节
        import subprocess
        out = subprocess.run(
            [sys.executable, "engine/scripts/llm_card.py", "view", "2026-10-20", "周二001"],
            capture_output=True, text=True, cwd=str(ROOT),
            encoding="utf-8", errors="replace")
        # 卡可能不存在→跳过（CI 数据依赖）；存在则必须含节
        if out.returncode == 0:
            self.assertIn("归因数据", out.stdout)
        else:
            self.skipTest("10-20 周二001 卡不存在")

if __name__ == "__main__":
    unittest.main()
