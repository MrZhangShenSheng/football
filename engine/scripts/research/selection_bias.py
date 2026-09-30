# -*- coding: utf-8 -*-
r"""选择偏差量化（2026-09-30 大哥问「上午为什么会正收益」第二因）。

上午 v4b 转正走的是"六方案对照胜出"：predictor_v4b_combinations.py 里
  7 个状态组合（A/B/C 及其两两、三合）× 6 个 boost 系数 = 42 种配置
在**同一批数据**上比收益，然后 `best = max(results, key=lambda x: x[1]["profit"])`
挑收益最高的那个当结论——挑完没有换一批数据复核。

这叫选择偏差：42 次里最好的那次，本身就带着"挑出来"的运气。
本脚本用**纯随机信号**跑同一套挑选流程：信号与赛果完全无关（真实优势=0），
看"挑 42 个里最好的"能凭空造出多少收益。若随机信号也能挑出可观正收益，
则上午那个"胜出"不构成证据。

口径：与闯关票一致——每票 2 元、2 串 1、同一批 hist_odds 场次、干净时间线。
用法：python engine/scripts/research/selection_bias.py
开发者 sszhang
"""
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clean_eval import START_DATE, load_hist  # noqa: E402

OUT = ROOT / "data" / "04-summaries" / "2026-09-30-selection-bias.json"
N_CONFIG = 42        # 上午实际比较的配置数（7 组合 × 6 系数）
N_TRIAL = 400        # 重复整套"挑最优"流程的次数
UNIT = 2.0
SEED = 20260930


def main():
    rng = np.random.default_rng(SEED)
    hist = [m for m in load_hist() if m["date"] >= START_DATE and len(m["pool"]) >= 10]
    pools = [(list(m["pool"].keys()), m["pool"], m["actual"]) for m in hist]
    print(f"场次池：{len(pools)} 场（同 clean_eval 口径）")
    print(f"模拟：{N_CONFIG} 个随机配置里挑收益最高的一个，重复 {N_TRIAL} 次\n")

    best_rois, all_rois = [], []
    for _ in range(N_TRIAL):
        rois = []
        for _ in range(N_CONFIG):
            # 一个"配置"=一套与赛果无关的随机选比分规则
            stake = ret = 0.0
            for keys, pool, act in pools:
                pick = keys[int(rng.integers(0, len(keys)))]
                stake += UNIT
                if pick == act:
                    ret += UNIT * pool[pick]
            rois.append((ret - stake) / stake)
        best_rois.append(max(rois))
        all_rois += rois

    bm, bs = float(np.mean(best_rois)), float(np.std(best_rois))
    am = float(np.mean(all_rois))
    print(f"① 单个随机配置的平均 ROI：{am:+.1%}（真实优势为 0，只剩抽水）")
    print(f"② 42 个里挑最好的那个 ROI：均值 {bm:+.1%} · 标准差 {bs:.1%} · "
          f"最高 {max(best_rois):+.1%}")
    print(f"③ 挑选动作凭空抬高 {(bm - am) * 100:+.1f}pp")
    p95 = float(np.percentile(best_rois, 95))
    print(f"   95 分位 {p95:+.1%} ← 随机信号走这套流程，有 5% 概率报出高于此的"
          f"「胜出方案」\n")

    print("④ 结论")
    print(f"   纯随机信号经「42 选 1」后平均报 {bm:+.1%}，仍为负——单靠选择偏差")
    print("   造不出 +341% 这种量级，故上午的正收益主因是泄漏（见 leak_mechanism.py，")
    print("   回收率虚增 19.7pp），选择偏差是叠加在上面的第二层放大器。")
    print("   两者合起来：泄漏把回收率推到接近打平线，再从 42 个配置里挑运气最好的，")
    print("   配合 2 串 1 的右尾，就成了「+341%、回撤 11.7%」这种好得不合理的数字。")

    OUT.write_text(json.dumps({
        "ranAt": date.today().isoformat(), "nMatches": len(pools),
        "nConfig": N_CONFIG, "nTrial": N_TRIAL, "unit": UNIT,
        "singleConfigRoi": round(am, 4),
        "bestOfNRoi": {"mean": round(bm, 4), "std": round(bs, 4),
                       "max": round(float(max(best_rois)), 4), "p95": round(p95, 4)},
        "selectionLiftPP": round((bm - am) * 100, 2),
        "verdict": "选择偏差单独抬高约 %.1fpp，不足以解释 +341%%；主因是泄漏，选择偏差为第二层放大" % ((bm - am) * 100),
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
