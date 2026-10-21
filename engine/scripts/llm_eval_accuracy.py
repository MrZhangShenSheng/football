# -*- coding: utf-8 -*-
"""LLM 预测准确度明细：每场预测比分/方向 vs 实际，中没中。

用法:
  py -3 engine/scripts/llm_eval_accuracy.py              # 全部评估日
  py -3 engine/scripts/llm_eval_accuracy.py 2026-10-20   # 指定销售日

与 llm_eval_detail.py 的区别：那个看概率打分（log-loss 对市场），这个看朴素命中率。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_eval as le                                      # noqa: E402

DIRS = {"h": "主胜", "d": "平局", "a": "客胜"}


def dir_of_key(k: str) -> str:
    """31 格键 → 三向。other_h/d/a 直取后缀；"i:j" 比大小。"""
    if k.startswith("other_"):
        return k.split("_")[1]
    i, j = (int(x) for x in k.split(":"))
    return "h" if i > j else ("d" if i == j else "a")


def dir_probs(p: dict) -> dict:
    """31 格概率 → 三向概率（聚合,系统口径）。"""
    out = {"h": 0.0, "d": 0.0, "a": 0.0}
    for k, v in p.items():
        out[dir_of_key(k)] += v
    return out


def match_names(day: str) -> dict:
    """销售日 → {code: "主队 vs 客队"}（数据卡优先,赛果档兜底）。"""
    names = {}
    cdir = le.CARDS_ROOT / day
    if cdir.exists():
        for f in cdir.glob("*.json"):
            try:
                c = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if c.get("code") and c.get("match"):
                names[c["code"]] = c["match"]
    return names


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 预测准确度明细")
    ap.add_argument("days", nargs="*")
    args = ap.parse_args()
    days = args.days or le._default_days()

    g_sc = g_dir = g_top3 = g_n = 0
    per_day = []
    for d in days:
        rows = le._load_day_rows([d])
        if not rows:
            continue
        names = match_names(d)
        print(f"\n=== {d} · {len(rows)} 场 ===")
        print(f"{'场':9s}{'对阵':24s}{'预测比分':9s}{'实际':6s}{'比分':5s}"
              f"{'预测方向':9s}{'实际':6s}{'方向':5s}{'前3格':7s}")
        n_sc = n_dir = n_top3 = 0
        for r in sorted(rows, key=lambda x: x["code"]):
            p = {k: v for k, v in r["p_crs"].items() if k in r["_marketPool"]}
            top = sorted(p, key=lambda k: -p[k])
            top1, top3 = top[0], top[:3]
            actual = r["_actual"]                     # 真相格（6+ 球压进 other_*）
            score = r["_score"]                       # 原始比分
            dp = dir_probs(p)
            pd_ = max(dp, key=dp.get)
            ad = dir_of_key(actual)
            sc_hit = top1 == actual
            dir_hit = pd_ == ad
            t3_hit = actual in top3
            n_sc += sc_hit
            n_dir += dir_hit
            n_top3 += t3_hit
            print(f"{r['code']:9s}{names.get(r['code'], '')[:22]:24s}"
                  f"{top1:9s}{score:6s}{'✓' if sc_hit else '✗':5s}"
                  f"{DIRS[pd_] + str(round(dp[pd_] * 100)):9s}{DIRS[ad]:6s}"
                  f"{'✓' if dir_hit else '✗':5s}{'✓' if t3_hit else '✗':7s}")
        print(f"  小计 比分 {n_sc}/{len(rows)} · 方向 {n_dir}/{len(rows)}"
              f" · 前3格 {n_top3}/{len(rows)}")
        per_day.append((d, len(rows), n_sc, n_dir, n_top3))
        g_sc += n_sc
        g_dir += n_dir
        g_top3 += n_top3
        g_n += len(rows)

    if not g_n:
        print("无可结算行")
        return 0
    print(f"\n=== 总账 {g_n} 场 ===")
    print(f"{'销售日':12s}{'场数':>5s}{'比分中':>8s}{'方向中':>8s}{'前3格中':>9s}")
    for d, n, s, dr, t3 in per_day:
        print(f"{d:12s}{n:5d}{s:5d}/{n:<3d}{dr:5d}/{n:<3d}{t3:6d}/{n:<3d}")
    print(f"{'合计':12s}{g_n:5d}{g_sc:5d}/{g_n:<3d}{g_dir:5d}/{g_n:<3d}{g_top3:6d}/{g_n:<3d}")
    print(f"\n比分命中率 {g_sc / g_n:.1%}（31 格盲猜约 3.2%）")
    print(f"方向命中率 {g_dir / g_n:.1%}（三向盲猜约 33%）")
    print(f"前3格命中率 {g_top3 / g_n:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
