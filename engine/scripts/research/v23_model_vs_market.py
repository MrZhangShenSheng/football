# -*- coding: utf-8 -*-
"""v23 Phase1：模型概率 vs 市场概率 校准对撞（混串模型的原料勘探）。

背景：业界共识=混串盈利前提是+EV腿源（模型在某段比市场准）。v20.1 只测了
模型自身校准，从未与市场概率对撞。本实验补上这块基石。

数据：v2 缓存（841日·CRS全格+HAD三向·模型概率+体彩赔率+真果）。
方法：按市场 devig 隐含概率分带，带内双校准（market dev vs model dev）。
预注册判据（跑前写死）：
  ① 模型优势带：|dev_model| < |dev_mkt| − 0.02 且 n≥500 → +EV腿源候选
  ② 全线市场更准或打平（|dev_model| ≥ |dev_mkt| − 0.02）→ "模型无优势"
     延伸至玩法端确认·混串模型先换引擎（伤停轨）
  ③ HAD/CRS 两池分别报告
产出：data/04-summaries/v23-model-vs-market-calib.json
开发者 sszhang
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v21_shape_policy import load_pre
from v11_s4_recalib import VAL_WINDOW, FIT_WINDOW

ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = ROOT / "data" / "04-summaries" / "v23-model-vs-market-calib.json"
BANDS = ((0.0, 0.10, "<10%"), (0.10, 0.20, "10-20%"), (0.20, 0.35, "20-35%"),
         (0.35, 0.55, "35-55%"), (0.55, 1.01, ">=55%"))


def devig(probs_odds):
    inv = [1.0 / o for _, o, _ in probs_odds]
    s = sum(inv)
    return [(mk, iv / s, hit) for (mk, o, hit), iv in zip(probs_odds, inv)]


def dual_calib(rows):
    """rows: [(implied, p_model, hit)] → 带内双校准。"""
    out = []
    for lo, hi, name in BANDS:
        b = [r for r in rows if lo <= r[0] < hi]
        if len(b) < 50:
            continue
        mkt = sum(r[0] for r in b) / len(b)
        mod = sum(r[1] for r in b) / len(b)
        act = sum(1 for r in b if r[2]) / len(b)
        out.append({"band": name, "n": len(b),
                    "mktImplied": round(mkt, 4), "model": round(mod, 4),
                    "realized": round(act, 4),
                    "devMkt": round(mkt - act, 4), "devModel": round(mod - act, 4),
                    "modelEdge": round(abs(mod - act) - abs(mkt - act), 4)})
    return out


def main():
    print("══ v23 Phase1：模型 vs 市场 校准对撞 ══\n", flush=True)
    print("预注册: 脚本头跑前写死\n", flush=True)
    pre = load_pre()
    print(f"缓存载入: {len(pre)}日\n", flush=True)

    result = {}
    for seg, (lo, hi) in (("fit", FIT_WINDOW), ("val", VAL_WINDOW)):
        had_rows, crs_rows = [], []
        for day, cands in pre.items():
            if not (lo <= day <= hi):
                continue
            for c in cands:
                d = c.get("hadHist")
                hm = c.get("hadModel") or {}
                if d and all(isinstance(d.get(k), (int, float)) and d[k] > 1.0 for k in ("h", "d", "a")):
                    triple = [("h", d["h"], None), ("d", d["d"], None), ("a", d["a"], None)]
                    realized_dir = None
                    r = str(c["real"])
                    if r.startswith("s1s"):
                        realized_dir = {"s1sh": 0, "s1sd": 1, "s1sa": 2}.get(r)
                    elif len(r) >= 5:
                        try:
                            g, t = int(r[1:3]), int(r[4:6])
                            realized_dir = 0 if g > t else (1 if g == t else 2)
                        except ValueError:
                            pass
                    if realized_dir is not None:
                        dv = devig(triple)
                        for mk, implied, _ in dv:
                            had_rows.append((implied, hm.get(mk, implied), realized_dir == {"h": 0, "d": 1, "a": 2}[mk]))
                for cell in c["cells"]:
                    crs_rows.append((1.0 / cell["odds"], cell["p"], cell["mk"] == c["real"]))

        crs_dv = devig(crs_rows) if False else None
        # CRS devig 需按场归一——重建：按场分组
        crs_by_match = {}
        for day, cands in pre.items():
            if not (lo <= day <= hi):
                continue
            for ci, c in enumerate(cands):
                crs_by_match[(day, ci)] = [(cell["mk"], cell["odds"], cell["p"],
                                            cell["mk"] == c["real"]) for cell in c["cells"]]
        crs_rows = []
        for _, g in crs_by_match.items():
            if len(g) < 3:
                continue
            for mk, implied, hit in devig([(mk2, o2, h2) for mk2, o2, _, h2 in g]):
                p_model = next(p2 for m2, _, p2, _ in g if m2 == mk)
                crs_rows.append((implied, p_model, hit))

        had_cal = dual_calib(had_rows)
        crs_cal = dual_calib(crs_rows)
        result[seg] = {"had": had_cal, "crs": crs_cal}
        for pool, cal in (("HAD", had_cal), ("CRS", crs_cal)):
            print(f"── {seg}段 {pool} 池（市场隐含带）──", flush=True)
            print(f"{'带':<10}{'n':<7}{'市场':<8}{'模型':<8}{'实际':<8}{'devMkt':<9}{'devModel':<9}{'modelEdge'}", flush=True)
            for b in cal:
                print(f"{b['band']:<10}{b['n']:<7}{b['mktImplied']:<8.4f}{b['model']:<8.4f}"
                      f"{b['realized']:<8.4f}{b['devMkt']:<+9.4f}{b['devModel']:<+9.4f}{b['modelEdge']:<+.4f}", flush=True)

        edges = [b for b in had_cal + crs_cal if b["modelEdge"] < -0.02 and b["n"] >= 500]
        result[seg]["advantageBands"] = edges
        print(f"  → 模型优势带（edge<-0.02·n≥500）: {len(edges)}个 {[b['band']+'/'+p for p in ('HAD','CRS') for b in (had_cal if p=='HAD' else crs_cal) if b.get('modelEdge',0) < -0.02 and b['n']>=500]}\n", flush=True)

    all_edges = result["fit"].get("advantageBands", []) + result["val"].get("advantageBands", [])
    verdict = (f"发现{len(all_edges)}个优势带·+EV腿源候选·可进Phase2" if all_edges else
               "全线市场更准或打平——模型无优势延伸至玩法端·混串模型先换引擎（伤停轨）")
    print(f"══ 判定: {verdict} ══")
    out = {"ranAt": "2026-10-08", "preReg": "脚本头跑前写死", "seg": result, "verdict": verdict}
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"归档 {OUT_PATH.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
