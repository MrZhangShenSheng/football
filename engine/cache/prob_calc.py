import json

# ============ T018: 混串2串1 (001平 × 002 CRS 0:0) ============
p001 = 0.281   # 001 平局轨融合口径
p002 = 0.092   # 002 0:0 freq q 口径 (DC交叉印证12.3%, 市场隐含~6.1%)

print("=" * 64)
print("T018 混串2串1: 001平 @3.10 × 002 CRS 0:0 @16.50")
print("=" * 64)
both = p001 * p002
only001 = p001 * (1 - p002)
only002 = (1 - p001) * p002
none = (1 - p001) * (1 - p002)
print(f"全中(001平 且 002 0:0): {both*100:.2f}%  -> 派彩 511.50元")
print(f"恰中1腿-001平(002非0:0): {only001*100:.2f}%  -> 0元")
print(f"恰中1腿-002 0:0(001非平): {only002*100:.2f}%  -> 0元")
print(f"全挂: {none*100:.2f}%  -> 0元")
print(f"合计验证: {(both+only001+only002+none)*100:.2f}%")
ev18 = both * 511.50 - 10
print(f"EV = {both*100:.2f}% x 511.50 - 10 = {ev18:+.2f}元 ({ev18/10*100:+.1f}%)")

# ============ T019: 8串1 分布 ============
legs = [
    ("①周五002", "里昂主胜",  0.691),
    ("②周五009", "科莫客胜",  0.588),
    ("③周五004", "新月客胜",  0.763),
    ("④周五011", "皇马客胜",  0.761),
    ("⑤周五006", "鹿斯巴达主胜", 0.588),
    ("⑥周五008", "斯图加特主胜", 0.636),
    ("⑦周五010", "利物浦客胜", 0.646),
    ("⑧周五001", "汉诺威主胜", 0.625),
]

print()
print("=" * 64)
print("T019 HAD 8串1: 各腿命中概率(p_修正口径)")
print("=" * 64)
for code, name, p in sorted(legs, key=lambda x: x[2]):
    print(f"{code} {name:12s} p={p*100:.1f}%  挂掉概率={100-p*100:.1f}%")

# DP 算 k 腿命中分布
dist = {0: 1.0}
for _, _, p in legs:
    new = {}
    for k, prob in dist.items():
        new[k] = new.get(k, 0) + prob * (1 - p)
        new[k + 1] = new.get(k + 1, 0) + prob * p
    dist = new

print()
print("-" * 64)
print("命中腿数分布(8串1只有全中才派彩):")
print("-" * 64)
for k in range(8, -1, -1):
    pct = dist[k] * 100
    bar = "#" * int(pct / 2)
    tag = " -> 派彩194.51元" if k == 8 else ""
    print(f"中{k}腿: {pct:6.2f}%  {bar}{tag}")

exp_legs = sum(p for _, _, p in legs)
print(f"\n期望中腿数: {exp_legs:.2f} / 8")
print(f"全中(派彩): {dist[8]*100:.2f}%  EV = {dist[8]*194.51-10:+.2f}元 ({(dist[8]*194.51-10)/10*100:+.1f}%)")
print(f"断1+腿: {(1-dist[8])*100:.2f}%")

# 恰断1腿的细分:哪条腿挂
print()
print("-" * 64)
print("恰好断1腿(中7腿)场景细分:")
print("-" * 64)
for code, name, p in legs:
    # 其他7腿全中 x 本腿挂
    other = 1.0
    for c2, n2, p2 in legs:
        if c2 != code:
            other *= p2
    pct = other * (1 - p) * 100
    print(f"断{code} {name:12s}: {pct:5.2f}%")

# ============ T017: 单关 ============
print()
print("=" * 64)
print("T017 CRS 0:0 单关 @16.50")
print("=" * 64)
q, dc, mkt = 0.092, 0.123, 1/16.50
print(f"命中(0:0): freq q={q*100:.1f}% / DC={dc*100:.1f}% / 市场隐含={mkt*100:.1f}%")
print(f"未命中: {(1-q)*100:.1f}%")
ev17 = q * 165.0 - 10
print(f"EV(freq口径) = 9.2% x 165 - 10 = {ev17:+.2f}元 ({ev17/10*100:+.1f}%)")

# ============ 三票总账 ============
print()
print("=" * 64)
print("三票总账")
print("=" * 64)
total_ev = ev17 + ev18 + (dist[8] * 194.51 - 10)
print(f"总投入 30元 · 总期望 {total_ev:+.2f}元 ({total_ev/30*100:+.1f}%)")
print(f"至少一票回款 = 1-(1-9.2%)x(1-2.59%)x(1-3.56%) ≈ {(1-(1-q)*(1-both)*(1-dist[8]))*100:.1f}%")
