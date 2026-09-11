"""临时脚本 v2：CLV 按热度分档——体彩超热腿的隐性抽水是否更小
框架：语料 clv = 体彩出票价/(1/pinClose去水) - 1，天然含体彩抽水（负值主导是结构性）。
真问题 = clv 随热度的斜率：超热档接近 0 → 体彩超热腿便宜（热度α部分存活）；
         各档齐平 ≈ -13% → 体彩均摊抽水（α 不存活）。
"""
import json

d = json.load(open("data/04-summaries/corpus.json", encoding="utf-8"))
recs = d.get("records") or []

def pick_dir(pick: str, play: str):
    """pick 文本 → 方向索引 0/1/2（主胜/平/客胜）；非方向 pick → None。"""
    if play not in ("HAD", "HHAD") or not pick:
        return None
    p = pick.replace("(方案外)", "").strip()
    if "主胜" in p or p == "h":
        return 0
    if "平" in p or p == "d":
        return 1
    if "客胜" in p or "负" in p or p == "a":
        return 2
    return None

rows = []
for r in recs:
    if r.get("clv") is None:
        continue
    k = pick_dir(r.get("pick") or "", r.get("play") or "")
    if k is None or not r.get("pinClose"):
        continue
    odds = r.get("odds")
    if not odds or odds <= 1.01:
        continue
    inv = 1.0 / odds
    p_mkt = inv / (inv + (1 - inv) * 0) if False else None  # 单赔率无法去水三向，改用赔率档
    rows.append({
        "code": r["code"], "match": r["match"], "odds": odds, "k": k,
        "p_final": (r.get("p_final") or [None]*3)[k],
        "p_pin": r["pinClose"][k],
        "clv": r["clv"],
    })

print(f"HAD/HHAD 方向腿且有精确 clv：{len(rows)} 条")

# 热度口径 A：体彩赔率档（≤1.30 / 1.30-1.60 / 1.60-2.0 / >2.0）
# 热度口径 B：Pinnacle 收盘去水概率档（≥0.70 / 0.60-0.70 / 0.50-0.60 / <0.50）
BINS_A = [(0, 1.30, "≤1.30 超热"), (1.30, 1.60, "1.30-1.60 热"),
          (1.60, 2.00, "1.60-2.00 中"), (2.00, 99, ">2.00 冷")]
BINS_B = [(0.70, 1.01, "p_pin≥0.70 超热"), (0.60, 0.70, "0.60-0.70"),
          (0.50, 0.60, "0.50-0.60"), (0, 0.50, "<0.50 冷")]

def stat(sub):
    if not sub:
        return "n=0"
    clvs = sorted(x["clv"] for x in sub)
    n = len(clvs)
    med = clvs[n // 2]
    mean = sum(clvs) / n
    pos = sum(1 for c in clvs if c > 0)
    return (f"n={n:>2} 均值 {mean*100:>+6.1f}% 中位 {med*100:>+6.1f}% "
            f"正CLV {pos}/{n} ({pos/n*100:.0f}%)")

print("\n== 口径A：体彩赔率档 ==")
for lo, hi, name in BINS_A:
    sub = [x for x in rows if lo <= x["odds"] < hi]
    print(f"{name:<14} {stat(sub)}")

print("\n== 口径B：Pinnacle 去水概率档（该腿方向）==")
for lo, hi, name in BINS_B:
    sub = [x for x in rows if x["p_pin"] is not None and lo <= x["p_pin"] < hi]
    print(f"{name:<16} {stat(sub)}")

# 超热子样明细（p_pin≥0.65）
print("\n== 超热腿明细（p_pin≥0.65）==")
for x in sorted(rows, key=lambda r: -(r["p_pin"] or 0)):
    if x["p_pin"] is not None and x["p_pin"] >= 0.65:
        print(f"  {x['code']} {x['match'][:18]:<18} @{x['odds']:<5} p_pin={x['p_pin']:.3f}"
              f" p_final={x['p_final'] if x['p_final'] is None else round(x['p_final'],3)}"
              f" clv={x['clv']*100:+.1f}%")
