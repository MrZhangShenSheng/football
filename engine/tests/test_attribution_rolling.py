# engine/tests/test_attribution_rolling.py
"""滚动特征时间线纪律测试——核心是证明窗口不含本场及未来场。"""
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import attribution_rolling as ar


def _hist(dates_vals):
    """构造单队历史：[(iso, gf), ...] → hist 结构。"""
    return {"T": [(d, {"gf": v, "ga": 0.0, "shots": 10.0, "shots_ag": 10.0,
                       "st": v, "st_ag": 3.0, "corners": 5.0, "fouls": 12.0,
                       "yellow": 2.0, "red": 0.0, "xg": v, "xg_ag": 1.0})
                  for d, v in dates_vals]}


class TestWindowTimeline(unittest.TestCase):
    def test_excludes_current_and_future(self):
        """窗口严格 < before_iso：本场与未来场都不得入窗。"""
        h = _hist([("2026-01-01", 1.0), ("2026-01-02", 2.0),
                   ("2026-01-03", 3.0), ("2026-01-04", 4.0),
                   ("2026-01-05", 5.0), ("2026-01-06", 99.0)])
        rows = ar._window(h, "T", "2026-01-06")
        self.assertEqual([r["gf"] for r in rows], [1.0, 2.0, 3.0, 4.0, 5.0],
                         "本场(01-06)不得入窗")

    def test_excludes_same_day(self):
        """同日场次一律排除（无法确定先后）。"""
        h = _hist([("2026-01-01", 1.0), ("2026-01-02", 2.0),
                   ("2026-01-03", 3.0), ("2026-01-04", 4.0),
                   ("2026-01-05", 5.0), ("2026-01-05", 50.0)])
        rows = ar._window(h, "T", "2026-01-05")
        self.assertEqual(len(rows), 4)
        self.assertNotIn(50.0, [r["gf"] for r in rows])
        self.assertNotIn(5.0, [r["gf"] for r in rows])

    def test_window_cap(self):
        """窗口上限 WINDOW 场，取最近的。"""
        h = _hist([(f"2026-01-{i:02d}", float(i)) for i in range(1, 21)])
        rows = ar._window(h, "T", "2026-01-21")
        self.assertEqual(len(rows), ar.WINDOW)
        self.assertEqual(rows[-1]["gf"], 20.0)
        self.assertEqual(rows[0]["gf"], 20.0 - ar.WINDOW + 1)

    def test_min_window_returns_none(self):
        """不足 MIN_WINDOW 场返回 None，不用均值填充。"""
        h = _hist([("2026-01-01", 1.0), ("2026-01-02", 2.0)])
        self.assertIsNone(ar.rolling_profile(h, "T", "2026-01-10"))

    def test_unknown_team_returns_none(self):
        self.assertIsNone(ar.rolling_profile({}, "NOBODY", "2026-01-10"))


class TestDims(unittest.TestCase):
    def setUp(self):
        self.h = _hist([(f"2026-01-{i:02d}", 2.0) for i in range(1, 11)])
        self.p = ar.rolling_profile(self.h, "T", "2026-02-01")

    def test_profile_shape(self):
        self.assertEqual(self.p["n"], ar.WINDOW)
        self.assertAlmostEqual(self.p["gf"], 2.0)

    def test_style_symmetric(self):
        """两队画像相同 → 偏移应为 0（对称性）。"""
        d = ar.dim_style(self.p, self.p)
        self.assertIsNotNone(d)
        self.assertEqual(d["h"], 0.0)
        self.assertEqual(d["a"], 0.0)

    def test_style_none_on_missing_side(self):
        self.assertIsNone(ar.dim_style(self.p, None))
        self.assertIsNone(ar.dim_style(None, self.p))

    def test_discipline_is_shape_factor(self):
        """纪律维度输出 tail_scale（形状型），不输出 h/a 偏移。"""
        d = ar.dim_discipline(self.p, self.p)
        self.assertIn("tail_scale", d)
        self.assertNotIn("h", d)

    def test_xg_form_uses_history_not_current(self):
        """A2 的 evidence 须标明赛前可算（与 E5 区分）。"""
        d = ar.dim_xg_form(self.p, self.p)
        self.assertIsNotNone(d)
        self.assertIn("赛前可算", d["evidence"])

    def test_xg_form_none_without_xg(self):
        """xG 缺失季（2324~2526）应返回 None 而非 0。"""
        h = {"T": [(f"2026-01-{i:02d}",
                    {"gf": 1.0, "ga": 1.0, "shots": 10.0, "shots_ag": 10.0,
                     "st": 4.0, "st_ag": 3.0, "corners": 5.0, "fouls": 12.0,
                     "yellow": 2.0, "red": 0.0, "xg": None, "xg_ag": None})
                   for i in range(1, 11)]}
        p = ar.rolling_profile(h, "T", "2026-02-01")
        self.assertIsNone(ar.dim_xg_form(p, p))


class TestRealData(unittest.TestCase):
    """真实 fd 数据上的时间线抽查（防构造数据过测但真数据泄漏）。"""

    @classmethod
    def setUpClass(cls):
        cls.hist = ar.build_team_history(codes=["E0"])

    def test_history_built(self):
        self.assertGreater(len(self.hist), 10, "E0 应有 20+ 队")

    def test_sorted_ascending(self):
        for team, rows in list(self.hist.items())[:5]:
            dates = [d for d, _ in rows]
            self.assertEqual(dates, sorted(dates), f"{team} 历史未按日期升序")

    def test_no_future_leak_real(self):
        """真实数据：任取一队中段某场，窗口内所有日期必 < 该场日期。"""
        team = next(iter(self.hist))
        rows = self.hist[team]
        if len(rows) < 12:
            self.skipTest("样本不足")
        pivot_date = rows[len(rows) // 2][0]
        win = ar._window(self.hist, team, pivot_date)
        for r_date, _ in [(d, m) for d, m in rows if m in win]:
            self.assertLess(r_date, pivot_date)


if __name__ == "__main__":
    unittest.main()
