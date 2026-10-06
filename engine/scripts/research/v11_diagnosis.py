# -*- coding: utf-8 -*-
"""v12 前置诊断：三维误差归因——找当前链的误差集中区。
①按联赛分解 1b 差距（模型top1 vs 市场最低赔率）
②按概率桶分解校准偏差
③选场联赛分布（V3W-v2 的正 ROI 到底来自哪些联赛）
产出：误差热力图 + 优化优先级排序
开发者 sszhang
"""
import sys, json, math
from pathlib import Path
from datetime import date
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import strength_loaders as sl
import strength_chain_eval as sce

ROOT = Path(__file__).resolve().parents[3]
HIST = ROOT / "engine" / "cache" / "hist_odds"
LEAGUES = ["uefa-nations","england-premier","spain-laliga","germany-bundesliga","italy-serie-a",
           "france-ligue1","netherlands-eredivisie","portugal-primeira","korea","japan",
           "denmark","sweden","norway","brazil","saudi","usa","france-ligue2",
           "world-cup","world-cup-qual","euro-qual","uefa-champions","uefa-europa",
           "england-championship","germany-bundesliga2","spain-liga2","italy-serie-b",
           "belgium-first-a","turkey-super-lig","greece-super","SC0"]

def score_to_matrix_key(score):
    try: h, a = str(score).split(":"); h, a = int(h), int(a)
    except ValueError: return ""
    if h > 5 or a > 5: return "s1sh" if h > a else ("s1sa" if a > h else "s1sd")
    return f"s{h:02d}s{a:02d}"

def hist_crs_key_to_matrix(k):
    if k in ("胜其他","平其他","负其他"): return {"胜其他":"s1sh","平其他":"s1sd","负其他":"s1sa"}[k]
    try: h, a = k.split(":"); return f"s{int(h):02d}s{int(a):02d}"
    except ValueError: return ""

rows = []
for f in sorted(HIST.glob("crs_hist_*.json")):
    rows += json.loads(f.read_text(encoding="utf-8"))["matches"]
# 用验证段（当前段）做归因
ms = [m for m in rows if "2025-10-01" <= str(m.get("date",""))[:10] <= "2026-09-28"
      and m.get("crs") and m.get("score")]

ctx = sl.build_ctx(LEAGUES)
z2i = sl.zh_to_id()
memo = {}

# ①按联赛归因
lg_stat = defaultdict(lambda: {"n":0, "model_hit":0, "market_hit":0, "model_ll":0.0, "market_ll":0.0})
# ②按概率桶归因
p_bucket = defaultdict(lambda: {"n":0, "model_hit":0, "market_hit":0})
# ③收集 per-match 记录
records = []

for m in ms:
    hid, aid = z2i.get(m["home"]), z2i.get(m["away"])
    if not hid or not aid: continue
    pred = sce._predict_match(hid, aid, date.fromisoformat(str(m["date"])[:10]), ctx, memo, beta=0.05)
    if not pred or not pred.get("matrix"): continue
    matrix = pred["matrix"]
    real = score_to_matrix_key(str(m["score"]))
    if not real: continue
    lg = str(m.get("league",""))

    # 模型 top1
    model_top = max(matrix.items(), key=lambda kv: kv[1])
    model_hit = model_top[0] == real
    model_p = matrix.get(real, 1e-8)
    model_ll = -math.log(max(model_p, 1e-8))

    # 市场最低赔率选项
    crs = m.get("crs") or {}
    best_mkt = None; best_odds = 1e9
    for ck, ov in crs.items():
        mk = hist_crs_key_to_matrix(str(ck))
        if not mk or mk not in matrix: continue
        try: o = float(ov)
        except: continue
        if o < best_odds and o > 1.0:
            best_odds = o; best_mkt = mk
    mkt_hit = best_mkt == real if best_mkt else False
    # 市场 log-loss（比例去水近似）
    inv_sum = sum(1/float(v) for v in crs.values() if v and float(v) > 1.0)
    mkt_p = (1/best_odds) / inv_sum if best_odds < 1e8 and inv_sum > 0 else 0.05
    mkt_ll = -math.log(max(mkt_p, 1e-8))

    # HAD 方向命中
    had_probs = pred.get("had") or {}
    try:
        h, a = map(int, str(m["score"]).split(":"))
        actual_dir = "h" if h > a else ("a" if a > h else "d")
    except: actual_dir = "?"
    dir_hit = had_probs.get(actual_dir, 0) == max(had_probs.values()) if had_probs and actual_dir != "?" else False
    # 市场方向
    try:
        had_odds = {k: float(m["had"][k]) for k in ("h","d","a") if m["had"].get(k)}
        mkt_dir = min(had_odds, key=had_odds.get)
        mkt_dir_hit = mkt_dir == actual_dir
    except: mkt_dir_hit = False

    flags = pred.get("flags", [])
    hst = "hst" if "hst_source:proxy" in flags else ("xg" if any("xg" in f for f in flags if "no" not in f) else "rolling")

    records.append({"lg": lg, "model_hit": model_hit, "mkt_hit": mkt_hit,
                    "model_ll": model_ll, "mkt_ll": mkt_ll,
                    "model_p_top": model_top[1],
                    "dir_hit": dir_hit, "mkt_dir_hit": mkt_dir_hit,
                    "hst": hst})

    lg_stat[lg]["n"] += 1
    lg_stat[lg]["model_hit"] += model_hit
    lg_stat[lg]["market_hit"] += mkt_hit
    lg_stat[lg]["model_ll"] += model_ll
    lg_stat[lg]["market_ll"] += mkt_ll

    pb = round(model_top[1], 1)
    p_bucket[pb]["n"] += 1
    p_bucket[pb]["model_hit"] += model_hit
    p_bucket[pb]["market_hit"] += mkt_hit

print("══ ① 按联赛归因（验证段 2025-10~2026-09）══\n")
print(f"{'联赛':10s} {'n':>5s} {'模型命中':>8s} {'市场命中':>8s} {'差(pp)':>7s} {'模型LL':>7s} {'市场LL':>7s} {'LL差':>6s}")
print("─" * 75)
tot = defaultdict(float)
for lg in sorted(lg_stat, key=lambda l: lg_stat[l]["n"], reverse=True)[:15]:
    s = lg_stat[lg]
    if s["n"] < 50: continue
    mh = s["model_hit"]/s["n"]; kh = s["market_hit"]/s["n"]
    mll = s["model_ll"]/s["n"]; kll = s["market_ll"]/s["n"]
    print(f"{lg:10s} {s['n']:5d} {mh*100:7.1f}% {kh*100:7.1f}% {(mh-kh)*100:+6.1f} {mll:7.3f} {kll:7.3f} {mll-kll:+5.3f}")
    for k in ("n","model_hit","market_hit","model_ll","market_ll"):
        tot[k] += s[k]
print("─" * 75)
print(f"{'合计':10s} {int(tot['n']):5d} {tot['model_hit']/tot['n']*100:7.1f}% {tot['market_hit']/tot['n']*100:7.1f}% "
      f"{(tot['model_hit']-tot['market_hit'])/tot['n']*100:+6.1f} {tot['model_ll']/tot['n']:7.3f} {tot['market_ll']/tot['n']:7.3f} "
      f"{(tot['model_ll']-tot['market_ll'])/tot['n']:+5.3f}")

print(f"\n══ ② 按模型概率桶归因 ══\n")
print(f"{'P桶':8s} {'n':>5s} {'模型命中':>8s} {'市场命中':>8s} {'差(pp)':>7s}")
for pb in sorted(p_bucket):
    s = p_bucket[pb]
    if s["n"] < 30: continue
    mh = s["model_hit"]/s["n"]; kh = s["market_hit"]/s["n"]
    print(f"{pb:.1f}{'':4s} {s['n']:5d} {mh*100:7.1f}% {kh*100:7.1f}% {(mh-kh)*100:+6.1f}pp")

print(f"\n══ ③ 质量层 vs 纯rolling ══\n")
for hst_label in ("hst", "xg", "rolling"):
    subset = [r for r in records if r["hst"] == hst_label]
    if not subset: continue
    n = len(subset)
    mh = sum(r["model_hit"] for r in subset)/n
    kh = sum(r["mkt_hit"] for r in subset)/n
    mll = sum(r["model_ll"] for r in subset)/n
    kll = sum(r["mkt_ll"] for r in subset)/n
    print(f"  {hst_label:8s} n={n:5d} 模型{mh*100:.1f}% vs 市场{kh*100:.1f}% (差{(mh-kh)*100:+.1f}pp) LL差{mll-kll:+.3f}")

# 保存记录
Path(ROOT / "data/04-summaries/v12-diagnosis.json").write_text(
    json.dumps({"records": records, "lgStat": {k: dict(v) for k, v in lg_stat.items()},
                "pBucket": {str(k): dict(v) for k, v in p_bucket.items()}},
               ensure_ascii=False, indent=1), encoding="utf-8")
print("\n归档 data/04-summaries/v12-diagnosis.json")
