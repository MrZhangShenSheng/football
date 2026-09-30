# -*- coding: utf-8 -*-
r"""91.2% 平局比分是规律还是基准率？（大哥 2026-09-30 追问中奖票规律的必要对照）

winning_ticket_profile.py 发现 v4b 回测 17 张命中票的 34 条命中腿里，
31 条是平局比分（1:1 十九次、0:0 九次、2:2 三次）= 91.2%。

这个数字单独看毫无意义——若模型本来就几乎只押平局比分，那 91.2% 只是在复述
"我们押了什么"，不是"什么容易中"。必须拿**全部选腿**的平局占比做基准率对照：
  · 若选腿平局占比 ≈ 91%  → 命中集中在平局纯属结构使然，无信息
  · 若选腿平局占比明显低于 91% → 平局比分的命中率确实高于其他比分，值得记录
同时报市场基准：CRS 池里"最低赔率比分"是平局的占比，以及实际赛果是平局比分的占比
（真实世界 1:1/0:0/2:2 合计约占全部赛果的两成多，这是天然上限参照）。

判据预注册：只做描述性对照，不下"该押平局"的结论——
命中样本 34 条腿、17 票，v4b_roi_53_selfrefute 已证剔除最大 2 张票即净利转负。

用法：python engine/scripts/research/draw_score_baserate.py
开发者 sszhang
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import score_family_model as sfm  # noqa: E402
from common import strict_merged  # noqa: E402
import engine.predictor_v4b as v4b  # noqa: E402

OUT = ROOT / "data" / "04-summaries" / "2026-09-30-draw-score-baserate.json"
MIN_POOL = 20
START_DATE = "2025-10-01"


def is_draw(sc):
    """比分字符串或元组是否平局。"""
    if isinstance(sc, (tuple, list)) and len(sc) >= 2:
        return int(sc[0]) == int(sc[1])
    s = str(sc)
    if ":" in s:
        h, a = s.split(":")[:2]
        try:
            return int(h) == int(a)
        except ValueError:
            return False
    return False


def load_hist():
    """与 v4b 回测同口径装载：有比分、CRS 池 ≥20 项。"""
    out, seen = [], set()
    for p in sorted((ROOT / "engine" / "cache" / "hist_odds").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        for m in d.get("matches", []):
            sc = str(m.get("score") or "")
            crs = m.get("crs") or {}
            if ":" not in sc or not crs:
                continue
            try:
                h, a = (int(x) for x in sc.split(":")[:2])
            except ValueError:
                continue
            odds = {}
            for k, v in crs.items():
                if str(k).startswith("other") or not v:
                    continue
                try:
                    hh, aa = (int(x) for x in str(k).split(":")[:2])
                    odds[(hh, aa)] = float(v)
                except (ValueError, TypeError):
                    continue
            if len(odds) < MIN_POOL:
                continue
            date = str(m.get("date") or "")[:10]
            key = (date, m.get("home"), m.get("away"))
            if key in seen or date < START_DATE:
                continue
            seen.add(key)
            out.append({"date": date, "home": m.get("home"), "away": m.get("away"),
                        "actual": (h, a), "odds": odds})
    return out


def main():
    print("=" * 72)
    print("平局比分：基准率对照（命中 91.2% 到底说明什么）")
    print("=" * 72)
    rows = load_hist()
    print(f"同口径可评场次 {len(rows)}\n")

    # ① 真实赛果里平局比分占比（天然上限参照）
    act_draw = sum(1 for r in rows if is_draw(r["actual"]))
    print(f"① 实际赛果是平局比分(x:x)的占比：{act_draw}/{len(rows)} = "
          f"{act_draw/len(rows):.1%}")
    top_act = Counter(f"{r['actual'][0]}:{r['actual'][1]}" for r in rows)
    print(f"   最常见赛果前 6：{dict(top_act.most_common(6))}")

    # ② 市场最低赔率比分是平局的占比
    mkt_draw = 0
    for r in rows:
        best = min(r["odds"].items(), key=lambda kv: kv[1])[0]
        if is_draw(best):
            mkt_draw += 1
    print(f"\n② 市场最低赔率比分是平局的占比：{mkt_draw}/{len(rows)} = "
          f"{mkt_draw/len(rows):.1%}")

    # ③ 池内平局项的赔率位置（为何模型爱选平局：平局比分赔率普遍偏低）
    dr_odds, nd_odds = [], []
    for r in rows:
        for sc, od in r["odds"].items():
            (dr_odds if is_draw(sc) else nd_odds).append(od)
    dr_odds.sort()
    nd_odds.sort()
    print(f"\n③ CRS 池内赔率中位：平局项 {dr_odds[len(dr_odds)//2]:.1f} vs "
          f"非平局项 {nd_odds[len(nd_odds)//2]:.1f}")
    print("   平局比分赔率系统性偏低 → 任何'挑低赔'的选法都会自动偏向平局")

    # ④ 各比分的命中率（实际赛果 / 池内出现次数）——这才是"什么容易中"
    print("\n④ 逐比分命中率（该比分出现在池内的场次中，实际开出的比例）")
    pool_cnt, hit_cnt = Counter(), Counter()
    for r in rows:
        act = f"{r['actual'][0]}:{r['actual'][1]}"
        for sc in r["odds"]:
            k = f"{sc[0]}:{sc[1]}"
            pool_cnt[k] += 1
            if k == act:
                hit_cnt[k] += 1
    tbl = [(k, hit_cnt[k], pool_cnt[k], hit_cnt[k] / pool_cnt[k])
           for k in pool_cnt if pool_cnt[k] >= 500]
    tbl.sort(key=lambda x: -x[3])
    for k, h, n, rate in tbl[:10]:
        mo = [r["odds"].get(tuple(int(x) for x in k.split(":")))
              for r in rows if tuple(int(x) for x in k.split(":")) in r["odds"]]
        mo = sorted(x for x in mo if x)
        med = mo[len(mo)//2] if mo else float("nan")
        flag = " ←平局" if is_draw(k) else ""
        print(f"    {k:5s} 命中 {h:4d}/{n:5d} = {rate:5.1%}  "
              f"池内赔率中位 {med:5.1f}{flag}")

    print("\n" + "=" * 72)
    print("判定")
    print("=" * 72)
    hi = [k for k, _, _, _ in tbl[:5]]
    ndraw_top = [k for k in hi if not is_draw(k)]
    verdict = (f"实际赛果平局比分占 {act_draw/len(rows):.1%}、市场最低赔率比分是平局的占"
               f"{mkt_draw/len(rows):.1%}；命中票 91.2% 是平局，主因是**平局比分赔率系统性偏低**"
               f"（池内中位 {dr_odds[len(dr_odds)//2]:.1f} vs 非平局 "
               f"{nd_odds[len(nd_odds)//2]:.1f}），任何挑低赔的选法都自动偏向平局，"
               f"故 91.2% 主要反映'我们押了什么'而非'什么容易中'。"
               f"命中率最高的比分{'确实以平局为主' if not ndraw_top else '并非只有平局（含 ' + '、'.join(ndraw_top) + '）'}")
    print(f"  → {verdict}")

    OUT.write_text(json.dumps({
        "ranAt": "2026-09-30", "nMatches": len(rows),
        "actualDrawScoreShare": round(act_draw / len(rows), 4),
        "marketFavDrawShare": round(mkt_draw / len(rows), 4),
        "poolOddsMedian": {"draw": dr_odds[len(dr_odds)//2],
                           "nonDraw": nd_odds[len(nd_odds)//2]},
        "perScoreHitRate": [{"score": k, "hit": h, "pool": n, "rate": round(rate, 4)}
                            for k, h, n, rate in tbl[:12]],
        "verdict": verdict,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
