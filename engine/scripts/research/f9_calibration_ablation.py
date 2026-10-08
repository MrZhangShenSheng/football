# -*- coding: utf-8 -*-
"""F9 消融回测：方向端低概率段温度校准实验（corpus 全样本·A/B 自证伪）。

背景：归因引擎 F9 主因 54 场（avgGap 0.37）触发消融候选；trend 校准警示
0~40% 桶实际 59% vs 预测 38%（低估 22pp·09-25 时 18pp/51 场持续恶化）。
嫌疑：既往 F9 低估皆算自错题子集——错题里低概率方向天然多发，选择偏差
可能造出假警报。本实验用 corpus 全 798 条 HAD 场全样本校准曲线对照。

方法：温度缩放幂变换 p_i' = p_i^(1/T) / Σ p_j^(1/T)（保场内排序·T=1 恒等）。
预注册判据（跑前写死·沿 calibrate 1% 护栏惯例）：
  ① 拟合段(date<=2026-09-30)网格搜 T 最小化 log-loss
  ② 验证段(date>=2026-10-01) log-loss 改善 ≥1% 方采纳·恶化即弃
  ③ 方向命中率为完整性校验（温度保排序·若变即 bug）
  ④ 全样本 vs 错题子集两口径对照——若全样本校准缺陷远小于错题口径，
     F9 警报定性为错题选择偏差·关灯
产出：data/04-summaries/f9-calibration-ablation.json
开发者 sszhang
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = ROOT / "data" / "04-summaries" / "f9-calibration-ablation.json"
FIT_CUT = "2026-09-30"
T_GRID = [0.8, 0.9] + [round(1.0 + 0.1 * i, 1) for i in range(16)]


def dir_of(result: str) -> int | None:
    try:
        h, a = str(result).replace(":", "-").split("-")
        h, a = int(h), int(a)
    except (ValueError, AttributeError):
        return None
    return 0 if h > a else (1 if h == a else 2)


def load_had_records():
    c = json.loads((ROOT / "data/04-summaries/corpus.json").read_text(encoding="utf-8"))
    out = []
    for r in c.get("records", []):
        if str(r.get("play", "")).upper() != "HAD" or not r.get("p_final") or len(r["p_final"]) != 3:
            continue
        oi = dir_of(r.get("result"))
        if oi is None:
            continue
        out.append({"date": str(r.get("date", ""))[:10], "p": r["p_final"], "oi": oi,
                    "pick": r.get("pick")})
    return out


def temp_scale(p: list[float], t: float) -> list[float]:
    if t == 1.0:
        return p
    w = [x ** (1.0 / t) for x in p]
    s = sum(w)
    return [x / s for x in w]


def logloss(rows, t):
    n, ll = 0, 0.0
    for r in rows:
        q = temp_scale(r["p"], t)
        ll -= math.log(max(q[r["oi"]], 1e-12))
        n += 1
    return ll / n if n else None


def rps_mean(rows, t):
    vals = []
    for r in rows:
        q = temp_scale(r["p"], t)
        cum_p, cum_o, s = 0.0, 0.0, 0.0
        for i in range(3):
            cum_p += q[i]
            cum_o += 1.0 if i == r["oi"] else 0.0
            s += (cum_p - cum_o) ** 2
        vals.append(s / 2)
    return sum(vals) / len(vals) if vals else None


def hit_rate(rows, t):
    n = hit = 0
    for r in rows:
        q = temp_scale(r["p"], t)
        n += 1
        hit += 1 if max(range(3), key=lambda i: q[i]) == r["oi"] else 0
    return hit / n if n else None


def calibration_table(rows, t=1.0):
    """10 桶·全格口径（每场 3 个方向格 p_ij vs 1{result==j}——防 max 选择偏差）。"""
    buckets = [[] for _ in range(10)]
    for r in rows:
        q = temp_scale(r["p"], t)
        for j in range(3):
            b = min(9, int(q[j] * 10))
            buckets[b].append(1 if r["oi"] == j else 0)
    out = []
    for i, b in enumerate(buckets):
        if len(b) >= 30:
            out.append({"bucket": f"{i/10:.1f}-{(i+1)/10:.1f}", "n": len(b),
                        "pred": round((i + 0.5) / 10, 3),
                        "act": round(sum(b) / len(b), 3),
                        "dev": round((i + 0.5) / 10 - sum(b) / len(b), 3)})
    return out


def main():
    print("══ F9 消融：方向端温度校准（corpus 全样本·A/B 自证伪）══\n")
    rows = load_had_records()
    fit_rows = [r for r in rows if r["date"] <= FIT_CUT]
    val_rows = [r for r in rows if r["date"] > FIT_CUT]
    wrong = [r for r in rows if max(range(3), key=lambda i: temp_scale(r["p"], 1.0)[i]) != r["oi"]]
    print(f"HAD 场 {len(rows)}（拟合 {len(fit_rows)} / 验证 {len(val_rows)}）·错题 {len(wrong)}\n")

    print("── A) 拟合段温度网格（log-loss）──")
    best = None
    for t in T_GRID:
        ll = logloss(fit_rows, t)
        tag = " ←现状" if t == 1.0 else ""
        print(f"  T={t:.1f}  log-loss {ll:.4f}{tag}")
        if best is None or ll < best[1]:
            best = (t, ll)
    t_best = best[0]
    print(f"\n拟合段最优 T={t_best:.1f} (log-loss {best[1]:.4f})")

    print("\n── B) 验证段复验 ──")
    import random
    if len(val_rows) < 100:
        rng = random.Random(20261008)
        shuffled = rows[:]
        rng.shuffle(shuffled)
        cut = int(len(shuffled) * 0.7)
        weak_rows = shuffled[cut:]
        ll1_v, llb_v = logloss(weak_rows, 1.0), logloss(weak_rows, t_best)
        improve = (ll1_v - llb_v) / ll1_v
        hit1, hitb = hit_rate(weak_rows, 1.0), hit_rate(weak_rows, t_best)
        val_note = f"⚠️弱验证(随机30%切分n={len(weak_rows)}·有拟合泄漏嫌疑·非时间外推)"
        val_rows_eff = weak_rows
        print(f"  {val_note}")
    else:
        ll1_v, llb_v = logloss(val_rows, 1.0), logloss(val_rows, t_best)
        improve = (ll1_v - llb_v) / ll1_v
        hit1, hitb = hit_rate(val_rows, 1.0), hit_rate(val_rows, t_best)
        val_note = f"时间外推验证(n={len(val_rows)})"
        val_rows_eff = val_rows
    print(f"  现状T=1:    log-loss {ll1_v:.4f} · RPS {rps_mean(val_rows_eff,1.0):.4f} · 命中 {hit1*100:.1f}%")
    print(f"  T={t_best:.1f}: log-loss {llb_v:.4f} · RPS {rps_mean(val_rows_eff,t_best):.4f} · 命中 {hitb*100:.1f}%")
    print(f"  log-loss 改善 {improve*100:+.2f}%（护栏 ≥1% 方采纳）· 命中率不变={'是' if abs(hit1-hitb)<1e-9 else '否(BUG!)'}")

    print("\n── C) 全样本校准曲线（全格口径·验证/弱验证段·T=1）──")
    for b in calibration_table(val_rows_eff, 1.0):
        print(f"  {b['bucket']}: n={b['n']} pred={b['pred']:.2f} act={b['act']:.2f} dev={b['dev']:+.2f}")

    print("\n── D) 错题子集口径对照（F9 警报重现·拟合段·全格口径）──")
    all_cells = [(x["p"][j], 1 if x["oi"] == j else 0) for x in fit_rows for j in range(3)]
    low_cells = [c for c in all_cells if c[0] < 0.40]
    wrong_set = {id(r) for r in fit_rows if max(range(3), key=lambda i: r["p"][i]) != r["oi"]}
    low_cells_wrong = [c for x in fit_rows if id(x) in wrong_set for c in [(x["p"][j], 1 if x["oi"] == j else 0) for j in range(3)] if c[0] < 0.40]
    act_a = sum(c[1] for c in low_cells) / len(low_cells) if low_cells else None
    act_w = sum(c[1] for c in low_cells_wrong) / len(low_cells_wrong) if low_cells_wrong else None
    if act_a is not None:
        print(f"  低概率格(<0.40): 全样本实际发生率 {act_a*100:.1f}% (n={len(low_cells)}格)"
              + (f" vs 错题场子集 {act_w*100:.1f}% (n={len(low_cells_wrong)}格)" if act_w is not None else ""))
        print(f"  → 错题口径偏差 {(act_w-act_a)*100:+.1f}pp·全格口径 pred≈0.35 桶 dev 见 C 表（max选择偏差已除）")

    adopt = improve >= 0.01 and t_best != 1.0
    verdict = ("ADOPT 候选（改善≥1%·待主公拍板入链）" if adopt else "KEEP T=1（不过护栏）")
    if adopt and "弱验证" in val_note:
        verdict += "·⚠️弱验证须样本积累后复验"
    print(f"\n══ 判定: {verdict} ══")
    result = {"ranAt": "2026-10-08", "preReg": "脚本头跑前写死(calibrate 1%护栏惯例)",
              "n": {"all": len(rows), "fit": len(fit_rows), "valTime": len(val_rows), "wrong": len(wrong)},
              "valNote": val_note,
              "bestT": t_best, "fitLogloss": {str(t): logloss(fit_rows, t) for t in T_GRID},
              "val": {"ll_t1": ll1_v, "ll_best": llb_v, "improve": round(improve, 4),
                      "hitT1": hit1, "hitBest": hitb,
                      "rpsT1": rps_mean(val_rows_eff, 1.0), "rpsBest": rps_mean(val_rows_eff, t_best)},
              "calibrationAllSample": calibration_table(val_rows_eff, 1.0),
              "selectionBias": {"lowAllAct": round(act_a, 4) if act_a is not None else None,
                                "lowWrongAct": round(act_w, 4) if act_w is not None else None,
                                "biasPP": (round(act_w - act_a, 4) if (act_a is not None and act_w is not None) else None)},
              "verdict": verdict}
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"归档 {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
