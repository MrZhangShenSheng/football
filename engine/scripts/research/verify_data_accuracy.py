# -*- coding: utf-8 -*-
"""验证数据完整性和计算准确性"""
import json
from pathlib import Path
from collections import defaultdict

print("=" * 100)
print("数据完整性和计算准确性验证")
print("=" * 100)

# 加载一场比赛的详细数据
ROOT = Path(".")
hist_files = sorted((ROOT / "engine/cache/hist_odds").glob("*.json"))

print(f"\n找到 {len(hist_files)} 个历史数据文件")

# 统计数据
total_matches = 0
matches_with_crs = 0
matches_with_ttg = 0
crs_sample = None
ttg_sample = None

for p in hist_files:
    d = json.loads(p.read_text(encoding="utf-8"))
    for m in d.get("matches", []):
        total_matches += 1
        crs = m.get("crs") or {}
        ttg = m.get("ttg") or {}

        if len(crs) >= 20:
            matches_with_crs += 1
            if crs_sample is None:
                crs_sample = {
                    "match": f"{m.get('home')} vs {m.get('away')}",
                    "date": m.get("date"),
                    "score": m.get("score"),
                    "crs": crs,
                }

        if len(ttg) >= 8:
            matches_with_ttg += 1
            if ttg_sample is None:
                ttg_sample = {
                    "match": f"{m.get('home')} vs {m.get('away')}",
                    "date": m.get("date"),
                    "score": m.get("score"),
                    "ttg": ttg,
                }

print(f"\n总比赛数：{total_matches}")
print(f"有比分赔率(>=20个)：{matches_with_crs} ({matches_with_crs/total_matches*100:.1f}%)")
print(f"有总进球数赔率(>=8个)：{matches_with_ttg} ({matches_with_ttg/total_matches*100:.1f}%)")

# 显示样本
print("\n" + "=" * 100)
print("比分赔率样本")
print("=" * 100)
if crs_sample:
    print(f"比赛：{crs_sample['match']}")
    print(f"日期：{crs_sample['date']}")
    print(f"实际比分：{crs_sample['score']}")

    # 解析实际比分
    try:
        actual_h, actual_a = (int(x) for x in crs_sample["score"].split(":")[:2])
        actual_score = (actual_h, actual_a)
        print(f"实际比分元组：{actual_score}")
    except:
        actual_score = None

    # 显示部分赔率
    crs = crs_sample["crs"]
    print(f"\n赔率字段数：{len(crs)}")
    print("\n部分比分赔率：")

    # 按赔率排序显示前10
    odds_list = []
    for k, v in crs.items():
        if str(k).startswith("other"):
            continue
        try:
            h, a = (int(x) for x in str(k).split(":")[:2])
            odds_list.append(((h, a), float(v)))
        except:
            continue

    odds_list.sort(key=lambda x: x[1])
    print("最低赔率前10：")
    for score, odds in odds_list[:10]:
        marker = " ← 实际结果" if actual_score and score == actual_score else ""
        print(f"  {score}: {odds}{marker}")

    # 检查实际比分的赔率
    if actual_score:
        actual_odds = None
        for score, odds in odds_list:
            if score == actual_score:
                actual_odds = odds
                break
        if actual_odds:
            print(f"\n实际比分 {actual_score} 的赔率：{actual_odds}")
        else:
            print(f"\n警告：实际比分 {actual_score} 没有找到赔率！")

# 总进球数样本
print("\n" + "=" * 100)
print("总进球数赔率样本")
print("=" * 100)
if ttg_sample:
    print(f"比赛：{ttg_sample['match']}")
    print(f"日期：{ttg_sample['date']}")
    print(f"实际比分：{ttg_sample['score']}")

    try:
        actual_h, actual_a = (int(x) for x in ttg_sample["score"].split(":")[:2])
        actual_total = actual_h + actual_a
        print(f"实际总进球：{actual_total}")
    except:
        actual_total = None

    ttg = ttg_sample["ttg"]
    print(f"\n总进球数赔率：")
    for k in ["0", "1", "2", "3", "4", "5", "6", "7"]:
        v = ttg.get(k) or ttg.get(int(k) if k.isdigit() else k)
        marker = " ← 实际结果" if actual_total is not None and str(actual_total) == k else ""
        if v:
            print(f"  {k}球: {v}{marker}")

    # 检查7+
    v7plus = ttg.get("7+") or ttg.get(7)
    if v7plus:
        marker = " ← 实际结果" if actual_total is not None and actual_total >= 7 else ""
        print(f"  7+球: {v7plus}{marker}")


# 验证计算逻辑
print("\n" + "=" * 100)
print("派彩计算验证")
print("=" * 100)

if crs_sample and actual_score:
    # 假设2串1双选
    print("\n假设场景：2串1双选，选中Top2比分")
    print("假设两场都选中(1,1)和(2,1)")

    # 获取赔率
    odds_11 = None
    odds_21 = None
    for score, odds in odds_list:
        if score == (1, 1):
            odds_11 = odds
        if score == (2, 1):
            odds_21 = odds

    print(f"\n(1,1)赔率：{odds_11}")
    print(f"(2,1)赔率：{odds_21}")

    if odds_11 and odds_21:
        # 4注组合
        combos = [
            ((1, 1), (1, 1)),  # 两场都1:1
            ((1, 1), (2, 1)),  # 第一场1:1，第二场2:1
            ((2, 1), (1, 1)),  # 第一场2:1，第二场1:1
            ((2, 1), (2, 1)),  # 两场都2:1
        ]

        print("\n4注组合：")
        for i, (s1, s2) in enumerate(combos, 1):
            o1 = odds_11 if s1 == (1, 1) else odds_21
            o2 = odds_11 if s2 == (1, 1) else odds_21
            combo_odds = o1 * o2
            print(f"  注{i}: {s1}×{s2} = {o1} × {o2} = {combo_odds:.2f}")

        print("\n如果实际结果是两场都1:1：")
        payout = 2 * odds_11 * odds_11
        print(f"  派彩 = 单注{2}元 × {odds_11} × {odds_11} = {payout:.2f}元")
        print(f"  成本 = 4注 × 2元 = 8元")
        print(f"  盈亏 = {payout:.2f} - 8 = {payout - 8:.2f}元")

print("\n" + "=" * 100)
print("验证完成")
print("=" * 100)
