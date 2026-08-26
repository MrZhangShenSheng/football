import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # engine/scripts/

import numpy as np
from dc_predict import (score_matrix, hafu_approx, ttg_dist,
                        reweight_matrix, reweight_hafu, temper)


class TestReweight(unittest.TestCase):
    def test_matrix_three_way_exact(self):
        p = score_matrix(1.5, 1.1, -0.05)
        target = [0.5, 0.3, 0.2]
        q = reweight_matrix(p, target)
        s = lambda m, f: float(sum(m[i, j] for i in range(7) for j in range(7) if f(i, j)))
        self.assertAlmostEqual(s(q, lambda i, j: i > j), 0.5, places=9)
        self.assertAlmostEqual(s(q, lambda i, j: i == j), 0.3, places=9)
        self.assertAlmostEqual(s(q, lambda i, j: i < j), 0.2, places=9)

    def test_matrix_shape_conserved(self):
        """域内 odds ratio 不变（IPF structure conservation）。"""
        p = score_matrix(1.8, 0.9, -0.08)
        q = reweight_matrix(p, [0.6, 0.25, 0.15])
        or_p = p[2, 0] / p[2, 1]
        or_q = q[2, 0] / q[2, 1]
        self.assertAlmostEqual(or_p, or_q, places=9)

    def test_matrix_identity_when_target_equals_current(self):
        p = score_matrix(1.4, 1.2, -0.05)
        s = lambda m, f: float(sum(m[i, j] for i in range(7) for j in range(7) if f(i, j)))
        q = reweight_matrix(p, [s(p, lambda i, j: i > j), s(p, lambda i, j: i == j), s(p, lambda i, j: i < j)])
        self.assertTrue(np.allclose(p, q, atol=1e-9))

    def test_hafu_ft_margin_aligned(self):
        h = hafu_approx(1.6, 1.0)
        target = [0.55, 0.25, 0.2]
        q = reweight_hafu(h, target)
        for d, ft in enumerate("hda"):
            cur = sum(q[k] for k in q if k[1] == ft)
            self.assertAlmostEqual(cur, target[d], places=9)

    def test_temper_identity_at_one_and_flattens(self):
        ps = [0.6, 0.3, 0.1]
        self.assertTrue(all(abs(a - b) < 1e-12 for a, b in zip(temper(ps, 1.0), ps)))
        t2 = temper(ps, 2.0)
        self.assertLess(max(t2) - min(t2), max(ps) - min(ps))  # T>1 平滑
        self.assertAlmostEqual(sum(t2), 1.0, places=9)

    def test_temper_sharpens_below_one(self):
        ps = [0.6, 0.3, 0.1]
        t06 = temper(ps, 0.6)
        self.assertGreater(max(t06) - min(t06), max(ps) - min(ps))  # T<1 锐化
        self.assertAlmostEqual(sum(t06), 1.0, places=9)

    def test_hafu_params_default_equals_legacy(self):
        """默认参数 = 现状行为（零破坏验收）。"""
        h_new = hafu_approx(1.5, 1.1)                      # 新签名默认值
        lh1, la1 = 1.5 * 0.45, 1.1 * 0.45                  # 旧算法手写复现
        import math
        def pois(k, lam): return math.exp(-lam) * lam ** k / math.factorial(k)
        keys = [a + b for a in "hda" for b in "hda"]
        ref = {k: 0.0 for k in keys}
        for x in range(6):
            for y in range(6):
                p1 = pois(x, lh1) * pois(y, la1)
                ht = "h" if x > y else ("d" if x == y else "a")
                for u in range(6):
                    for v in range(6):
                        p2 = pois(u, 1.5 - lh1) * pois(v, 1.1 - la1)
                        fx, fy = x + u, y + v
                        ft = "h" if fx > fy else ("d" if fx == fy else "a")
                        ref[ht + ft] += p1 * p2
        tot = sum(ref.values())
        for k in keys:
            self.assertAlmostEqual(h_new[k], ref[k] / tot, places=9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
