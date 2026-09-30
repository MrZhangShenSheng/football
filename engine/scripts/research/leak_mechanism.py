# -*- coding: utf-8 -*-
r"""泄漏机制量化（2026-09-30 大哥问「上午为什么会正收益」）。

还原那一行排序键造成的后果，并量化它到底喂进了多少未来信息。

  原写法（2026-09-29 15:36 7b5a87a 引入，一直沿用到 09-30 14:27）：
      merged = [("L", 联赛库每场) ...] + [("B", 待预测场) ...]
      merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))
                                  ↑同一天内 L 排在 B 前面
  后果：体彩待预测场 X 若同时也在联赛库里（同一场比赛的两个数据源），
  排序后 X 的 ("L",…) 记录位于 ("B",…) 之前 → 预测 X 时 X 的真实赛果
  已经进了 TeamStats。等于开卷考试。

  修法（common.strict_merged）：联赛库记录只在其日期 + lag 天之后才可见。

本脚本输出三件事：
  ① 有多少比例的待预测场能在联赛库里找到自己（泄漏触达率）。
  ② 泄漏场 vs 干净场的 top1 命中率差（泄漏值多少个百分点的"虚假准确率"）。
  ③ 只押泄漏场 vs 只押干净场的回收率差（虚假收益的来源）。
用法：python engine/scripts/research/leak_mechanism.py
开发者 sszhang
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import score_family_model as sfm  # noqa: E402
from clean_eval import START_DATE, keep, load_hist  # noqa: E402
from common import load_aliases, strict_merged  # noqa: E402
import engine.predictor_v4b as v4b  # noqa: E402

N_BOOT = 2000
SEED = 20260930


def leaky_merged(tl, blind):
    """复刻 7b5a87a 的原始排序（同日 L 先于 B）——即泄漏版时间线。"""
    merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
    merged += [("B", d, h, a, hg, ag, i) for d, h, a, hg, ag, i in blind]
    merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))
    return merged


def main():
    zh = {s["zh"]: tid for tid, s in load_aliases().items() if s.get("zh")}
    blind = [dict(m, hid=zh.get(m["home"]), aid=zh.get(m["away"]))
             for m in load_hist() if m["date"] >= START_DATE]
    blind = [m for m in blind if m["hid"] and m["aid"]]
    tl = sfm.league_timeline()

    # ① 泄漏触达率：待预测场在联赛库里能否找到同日同队的自己
    lg_keys = defaultdict(set)
    for d, h, a, hg, ag in tl:
        lg_keys[d].add((h, a))
    hit = sum(1 for m in blind if (m["hid"], m["aid"]) in lg_keys.get(m["date"], ()))
    print(f"① 泄漏触达率：体彩可映射待预测场 {len(blind)} 场，"
          f"其中 {hit} 场（{hit / len(blind):.1%}）同日同队也在联赛库里")
    print("   → 这些场在原排序下，预测自己时自己的赛果已先入统计（开卷）\n")

    # ② 两条时间线同配置对拍：同一批场次，逐场配对比较
    blind_tuples = [(m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
                    for i, m in enumerate(blind)]

    def run(merged):
        """返回 {盲测场下标: (top1, 命中, 回款)}——同一批场次可配对比较。"""
        p4 = v4b.PredictorV4b(boost_factor=1.25)
        stats = defaultdict(sfm.TeamStats)
        out = {}
        for kind, d, h, a, hg, ag, idx in merged:
            if kind == "L":
                stats[h].add(hg, ag, True, a)
                stats[a].add(ag, hg, False, h)
                continue
            if stats[h].n < sfm.MIN_HIST or stats[a].n < sfm.MIN_HIST:
                continue
            m = blind[idx]
            pool = m["pool"]
            rk = keep(p4.predict(v4b.team_stats_to_team_data(stats[h]),
                                 v4b.team_stats_to_team_data(stats[a])), pool)
            if not rk:
                continue
            t1, act = rk[0], m["actual"]
            out[idx] = (t1, float(t1 == act), pool[t1] if t1 == act else 0.0)
        return out

    leak = run(leaky_merged(tl, blind_tuples))
    clean = run(strict_merged(tl, blind_tuples))
    both = sorted(set(leak) & set(clean))
    print(f"② 同配置两条时间线对拍（v4b×1.25），两版都可评的 {len(both)} 场配对比较")
    print(f"   {'时间线':<22}{'top1命中':>10}{'回收率':>9}")
    lh = np.array([leak[i][1] for i in both])
    ch = np.array([clean[i][1] for i in both])
    lp = np.array([leak[i][2] for i in both])
    cp = np.array([clean[i][2] for i in both])
    print(f"   {'泄漏版（原排序）':<22}{lh.mean():>9.1%}{lp.mean():>9.1%}")
    print(f"   {'干净版（strict_merged）':<22}{ch.mean():>9.1%}{cp.mean():>9.1%}")

    diff_pick = sum(1 for i in both if leak[i][0] != clean[i][0])
    print(f"\n   两版选出不同比分的场次：{diff_pick} 场（{diff_pick / len(both):.1%}）"
          f" ← 泄漏实际改变选择的比例")
    rng = np.random.default_rng(SEED)
    d = lh - ch
    idx = rng.integers(0, len(d), size=(N_BOOT, len(d)))
    lo, hi = np.percentile(d[idx].mean(axis=1), [2.5, 97.5])
    print(f"   配对 Δtop1 命中 {d.mean():+.2%} [{lo:+.2%}, {hi:+.2%}]"
          f"{' ← 泄漏凭空造出的虚假准确率' if lo > 0 else ' ← 区间跨0'}")
    dp = lp - cp
    idxp = rng.integers(0, len(dp), size=(N_BOOT, len(dp)))
    lop, hip = np.percentile(dp[idxp].mean(axis=1), [2.5, 97.5])
    print(f"   配对 Δ回收率 {dp.mean() * 100:+.1f}pp [{lop * 100:+.1f}, {hip * 100:+.1f}]"
          f"{' ← 虚假收益' if lop > 0 else ' ← 区间跨0'}")

    # ③ 可评场次数差异：泄漏版因统计更充足而多评场次，这也是收益差的来源之一
    print(f"\n③ 可评场次：泄漏版 {len(leak)} 场 vs 干净版 {len(clean)} 场"
          f"（差 {len(leak) - len(clean)} 场）")
    print(f"   泄漏触达率 {hit / len(blind):.1%} 是上限口径（同日同队）；"
          "strict_merged 另把前 2 天赛果一并挡掉，故实际影响面更大。")
    print(f"\n④ 归因：回收率 {lp.mean():.1%} → {cp.mean():.1%}，"
          f"虚增 {dp.mean() * 100:.1f}pp 全部来自那一行排序键。")
    print(f"   泄漏版 {lp.mean():.1%} 已接近 100%（看着像能打平甚至小赚），"
          f"配上高赔选腿与 2 串 1 放大，就成了上午看到的 +341%/+20.1%。")

    import json
    from datetime import date as _date
    out = ROOT / "data" / "04-summaries" / "2026-09-30-leak-mechanism.json"
    out.write_text(json.dumps({
        "ranAt": _date.today().isoformat(),
        "touchRate": {"nBlind": len(blind), "nSameDaySameTeams": hit,
                      "rate": round(hit / len(blind), 4)},
        "paired": {"n": len(both),
                   "leak": {"top1": round(float(lh.mean()), 4), "recovery": round(float(lp.mean()), 4)},
                   "clean": {"top1": round(float(ch.mean()), 4), "recovery": round(float(cp.mean()), 4)},
                   "dTop1": [round(float(d.mean()), 4), round(float(lo), 4), round(float(hi), 4)],
                   "dRecovery": [round(float(dp.mean()), 4), round(float(lop), 4), round(float(hip), 4)],
                   "pickChanged": diff_pick},
        "introducedBy": "7b5a87a 2026-09-29 15:36（闯关票转正）· 修于 04a1206 2026-09-30 14:27",
        "rootCause": 'merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1)) 同日联赛库行排在待预测场之前',
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n→ {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
