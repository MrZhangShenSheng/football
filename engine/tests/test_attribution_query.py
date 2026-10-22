# engine/tests/test_attribution_query.py
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
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

if __name__ == "__main__":
    unittest.main()
