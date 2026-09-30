# -*- coding: utf-8 -*-
r"""最硬的拷问：我的模型概率 p_final 比 Pinnacle 更准吗？（log-loss 直接对撞）

动机（2026-09-30 大哥说"越来越不信任你了"）：与其再找新因素，先回答一个更根本的问题
——我们这套模型到底有没有超过公认最锐的市场（Pinnacle 去水概率）。若没有，
那么"提高准确率"的正确起点不是加因素，而是承认模型上限并改用市场概率做基准。

样本：corpus.json 中同时有 p_final（我的模型）、p_pinnacle（Pinnacle 去水）与赛果的记录。
指标：log-loss（概率预测的标准评分，越低越好）+ Brier score，两者同时报，
避免只报对自己有利的那个。

判据预注册（跑前写定）：
  ① 主指标 log-loss：模型 vs Pinnacle 的差值，bootstrap 95%CI 是否含 0
  ② 辅指标 Brier：同上，若与 log-loss 结论矛盾则一并披露、不取有利者
  ③ 只有 CI 完全在 0 以下（模型更低）才算"模型胜过市场"；含 0 = 打平（无优势）；
     完全在 0 以上 = 模型不如市场
  ④ 同时报"模型与 Pinnacle 概率的相关性"——若高度相关，说明模型基本在复述市场，
     那么任何"独立信息"的说法都不成立（铁律 13 集中度闸门正是栽在复刻市场排序）

时间线安全：corpus 为真前瞻记录（预测文件赛前入库、赛果次日回填，
已于 2026-09-30 用 git log 核实），不涉铁律 14 自泄漏。

用法：python engine/scripts/research/model_vs_pinnacle.py
开发者 sszhang
"""
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "data" / "04-summaries" / "corpus.json"
OUT = ROOT / "data" / "04-summaries" / "2026-09-30-model-vs-pinnacle.json"
SEED = 20260930
EPS = 1e-9


def as_triple(v):
    """把概率字段规范成 [pH, pD, pA]；不可用返回 None。"""
    if isinstance(v, dict):
        got = [v.get(k) for k in ("h", "d", "a")]
        if all(isinstance(x, (int, float)) for x in got) and sum(got) > 0:
            s = sum(got)
            return [x / s for x in got]
    if isinstance(v, (list, tuple)) and len(v) == 3:
        if all(isinstance(x, (int, float)) for x in v) and sum(v) > 0:
            s = sum(v)
            return [x / s for x in v]
    return None


def outcome_index(result):
    """赛果 → 0=主胜 1=平 2=客胜；解析不了返回 None。"""
    s = str(result or "").replace("：", ":").replace("-", ":")
    if ":" not in s:
        return None
    try:
        h, a = (int(x.strip()) for x in s.split(":")[:2])
    except ValueError:
        return None
    return 0 if h > a else (1 if h == a else 2)


def logloss(p, y):
    return -math.log(max(EPS, p[y]))


def brier(p, y):
    t = [0.0, 0.0, 0.0]
    t[y] = 1.0
    return sum((p[i] - t[i]) ** 2 for i in range(3))


def main():
    recs = json.loads(CORPUS.read_text(encoding="utf-8"))["records"]
    pairs = []
    for x in recs:
        pm, pp = as_triple(x.get("p_final")), as_triple(x.get("p_pinnacle"))
        y = outcome_index(x.get("result"))
        if pm and pp and y is not None:
            pairs.append((pm, pp, y, x))

    print(f"corpus {len(recs)} 条 → 三者齐备（我的概率 + Pinnacle + 赛果）{len(pairs)} 条\n")
    if len(pairs) < 30:
        print("⚠ 样本不足 30，不下结论")
        OUT.write_text(json.dumps({"ranAt": "2026-09-30", "n": len(pairs),
                                   "verdict": "样本不足，不下结论"},
                                  ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return

    ll_m = np.array([logloss(p, y) for p, _, y, _ in pairs])
    ll_p = np.array([logloss(q, y) for _, q, y, _ in pairs])
    br_m = np.array([brier(p, y) for p, _, y, _ in pairs])
    br_p = np.array([brier(q, y) for _, q, y, _ in pairs])

    rng = np.random.default_rng(SEED)
    n = len(pairs)

    def ci(d):
        bs = np.array([rng.choice(d, n, True).mean() for _ in range(20000)])
        return np.percentile(bs, [2.5, 97.5])

    print("=" * 68)
    print("① 主指标 log-loss（越低越好）")
    print("=" * 68)
    d_ll = ll_m - ll_p
    lo, hi = ci(d_ll)
    print(f"  我的模型 {ll_m.mean():.4f}   Pinnacle {ll_p.mean():.4f}")
    print(f"  差值（我−市场）{d_ll.mean():+.4f}  95%CI [{lo:+.4f}, {hi:+.4f}]")
    ll_verdict = ("模型更准" if hi < 0 else
                  "模型更差" if lo > 0 else "统计上打平（无优势）")
    print(f"  → {ll_verdict}")

    print("\n" + "=" * 68)
    print("② 辅指标 Brier（越低越好）")
    print("=" * 68)
    d_br = br_m - br_p
    lo2, hi2 = ci(d_br)
    print(f"  我的模型 {br_m.mean():.4f}   Pinnacle {br_p.mean():.4f}")
    print(f"  差值（我−市场）{d_br.mean():+.4f}  95%CI [{lo2:+.4f}, {hi2:+.4f}]")
    br_verdict = ("模型更准" if hi2 < 0 else
                  "模型更差" if lo2 > 0 else "统计上打平（无优势）")
    print(f"  → {br_verdict}")
    if ll_verdict != br_verdict:
        print("  ⚠ 两指标结论不一致，按预注册第②条一并披露，不取有利者")

    print("\n" + "=" * 68)
    print("④ 模型是否只在复述市场")
    print("=" * 68)
    mh = np.array([p[0] for p, _, _, _ in pairs])
    ph = np.array([q[0] for _, q, _, _ in pairs])
    r = float(np.corrcoef(mh, ph)[0, 1])
    mad = float(np.mean(np.abs(mh - ph)))
    print(f"  主胜概率相关性 r={r:.3f}   平均绝对差 {mad:.1%}")
    echo = ("高度相关，模型基本在复述市场（独立信息极少）" if r > 0.9 else
            "中度相关，有一定独立成分" if r > 0.7 else "相关性不高，模型有独立视角")
    print(f"  → {echo}")

    print("\n" + "=" * 68)
    print("判定")
    print("=" * 68)
    if ll_verdict == "模型更差":
        verdict = (f"模型 log-loss 显著高于 Pinnacle（{ll_m.mean():.4f} vs {ll_p.mean():.4f}）"
                   f" → 我们的概率不如市场；提高准确率的正确起点不是加新因素，"
                   f"而是以市场概率为基准、只在能证明有增量的局部偏离")
    elif ll_verdict == "模型更准":
        verdict = (f"模型 log-loss 显著低于 Pinnacle → 罕见结果，须立即做样本外复验"
                   f"（并核查是否有口径泄漏，n={n} 仍偏小）")
    else:
        verdict = (f"模型与 Pinnacle 统计上打平（差 {d_ll.mean():+.4f}，"
                   f"CI[{lo:+.4f},{hi:+.4f}] 含 0，n={n}）→ 没有证据说我们比市场准，"
                   f"也没有证据说更差；在此前提下，任何'提高准确率'的方案都必须先证明"
                   f"能稳定超过市场基准，否则只是噪声")
    print(f"  → {verdict}")
    print(f"\n  样本量 n={n}，为真前瞻记录（已核实预测赛前入库）；"
          f"\n  Pinnacle 概率覆盖率有限（corpus 779 条中 393 条有），结论仅对这批可比样本成立。")

    OUT.write_text(json.dumps({
        "ranAt": "2026-09-30", "n": n,
        "logloss": {"model": round(float(ll_m.mean()), 4),
                    "pinnacle": round(float(ll_p.mean()), 4),
                    "diff": round(float(d_ll.mean()), 4),
                    "ci": [round(float(lo), 4), round(float(hi), 4)],
                    "verdict": ll_verdict},
        "brier": {"model": round(float(br_m.mean()), 4),
                  "pinnacle": round(float(br_p.mean()), 4),
                  "diff": round(float(d_br.mean()), 4),
                  "ci": [round(float(lo2), 4), round(float(hi2), 4)],
                  "verdict": br_verdict},
        "echoCheck": {"corrHomeProb": round(r, 3), "meanAbsDiff": round(mad, 4),
                      "note": echo},
        "verdict": verdict,
        "preRegistered": "见文件头判据预注册段（跑前写定）",
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
