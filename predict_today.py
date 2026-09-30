# -*- coding: utf-8 -*-
"""
今日竞彩预测
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(".")))

from engine.predictor_v4b import PredictorV4b, team_stats_to_team_data
import engine.scripts.research.score_family_model as sfm

TODAY = "2026-09-30"
print("=" * 80)
print(f"今日竞彩预测 —— {TODAY}")
print("=" * 80)


# 加载数据
def load_hist():
    ROOT = Path(".")
    out = []
    seen = set()
    for p in sorted((ROOT / "engine/cache/hist_odds").glob("*.json")):
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
            score_odds = {}
            for kk, v in crs.items():
                if str(kk).startswith("other") or not v:
                    continue
                try:
                    hh, aa = (int(x) for x in str(kk).split(":")[:2])
                    score_odds[(hh, aa)] = float(v)
                except (ValueError, TypeError):
                    continue
            if len(score_odds) < 20:
                continue
            key = (str(m.get("date") or "")[:10], m.get("home"), m.get("away"))
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "date": str(m.get("date") or "")[:10],
                "home_zh": m.get("home"),
                "away_zh": m.get("away"),
                "actual": (h, a),
                "score_odds": score_odds,
            })
    out.sort(key=lambda x: x["date"])
    return out


zh = {}
for tid, srcs in sfm.load_aliases().items():
    if srcs.get("zh"):
        zh[srcs["zh"]] = tid

tl = sfm.league_timeline()
hist = load_hist()

# 检查今天的比赛
today_matches = [m for m in hist if m["date"] == TODAY]
print(f"今日比赛数: {len(today_matches)}")

if not today_matches:
    print("\n没有找到今天的比赛数据！")
    print("可能原因：")
    print("1. 今天没有竞彩比赛")
    print("2. 赔率数据尚未更新")

    # 显示最近几天的比赛
    recent_dates = sorted(set(m["date"] for m in hist))[-5:]
    print(f"\n最近有数据的日期: {recent_dates}")
else:
    # 构建统计数据
    merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
    merged += [("B", m["date"], zh.get(m["home_zh"]), zh.get(m["away_zh"]),
                m["actual"][0], m["actual"][1], i)
               for i, m in enumerate(hist) if m["date"] < TODAY and zh.get(m["home_zh"]) and zh.get(m["away_zh"])]
    merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

    stats = defaultdict(sfm.TeamStats)
    for r in merged:
        kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
        if kind == "L":
            stats[h].add(hg, ag, True, a)
            stats[a].add(ag, hg, False, h)

    # 预测今天的比赛
    predictor = PredictorV4b()
    predictions = []

    for m in today_matches:
        hid = zh.get(m["home_zh"])
        aid = zh.get(m["away_zh"])

        if not hid or not aid:
            print(f"跳过: {m['home_zh']} vs {m['away_zh']} (无法匹配球队ID)")
            continue

        if stats[hid].n < sfm.MIN_HIST or stats[aid].n < sfm.MIN_HIST:
            print(f"跳过: {m['home_zh']} vs {m['away_zh']} (历史数据不足)")
            continue

        home_data = team_stats_to_team_data(stats[hid])
        away_data = team_stats_to_team_data(stats[aid])

        pred = predictor.predict(home_data, away_data)

        predictions.append({
            "home": m["home_zh"],
            "away": m["away_zh"],
            "odds": m["score_odds"],
            "prediction": pred,
            "home_form": home_data.form_score(),
            "away_form": away_data.form_score(),
            "max_signal": pred[0][1],
        })

    if predictions:
        # 按信号强度排序
        predictions.sort(key=lambda x: -x["max_signal"])

        print(f"\n有效预测: {len(predictions)}场")
        print("\n" + "=" * 80)
        print("推荐投注（按信号强度排序）")
        print("=" * 80)

        for i, p in enumerate(predictions[:5], 1):
            print(f"\n【推荐{i}】{p['home']} vs {p['away']}")
            print(f"  状态: 主队{p['home_form']}分 vs 客队{p['away_form']}分")
            print(f"  Top2预测比分:")
            for score, sig in p["prediction"][:2]:
                odds = p["odds"].get(score, 0)
                print(f"    {score[0]}:{score[1]} 信号{sig:.4f} 赔率{odds}")

        # 如果有>=2场，给出2串1推荐
        if len(predictions) >= 2:
            print("\n" + "=" * 80)
            print("2串1双选推荐")
            print("=" * 80)

            top2_matches = predictions[:2]
            print(f"\n场1: {top2_matches[0]['home']} vs {top2_matches[0]['away']}")
            print(f"  选: {top2_matches[0]['prediction'][0][0]} 或 {top2_matches[0]['prediction'][1][0]}")

            print(f"\n场2: {top2_matches[1]['home']} vs {top2_matches[1]['away']}")
            print(f"  选: {top2_matches[1]['prediction'][0][0]} 或 {top2_matches[1]['prediction'][1][0]}")

            # 计算4注组合
            print("\n4注组合:")
            from itertools import product
            picks1 = [(s, p["odds"].get(s, 0)) for s, _ in top2_matches[0]["prediction"][:2]]
            picks2 = [(s, p["odds"].get(s, 0)) for s, _ in top2_matches[1]["prediction"][:2]]

            total_cost = 0
            for (s1, o1), (s2, o2) in product(picks1, picks2):
                combo_odds = o1 * o2
                payout = 2 * combo_odds
                total_cost += 2
                print(f"  {s1[0]}:{s1[1]}@{o1} × {s2[0]}:{s2[1]}@{o2} = 串关{combo_odds:.1f} 可得{payout:.1f}元")

            print(f"\n总成本: {total_cost}元")
    else:
        print("\n没有有效预测（可能所有比赛都缺少历史数据）")
