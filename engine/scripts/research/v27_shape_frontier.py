# -*- coding: utf-8 -*-
"""v27 诊断：腿级 alpha 对照 + 形状亏损结构（抽水复利）。

两问：
  ① 昨报腿级命中 69.1%——是判断力，还是只因选了低赔率腿？
     判法：realized 腿级 ROI。零 alpha 的预期 = −抽水（约 −14%）。
     命中率高但 ROI ≈ −14% → 命中率全由赔率解释，无 alpha（应与 v24 一致）。
  ② 形状回款率差异（长串全灭 vs 容错正收益）——噪声，还是抽水复利的必然？
     判法：各形状理论期望 = Σ(注的 r^串数)/注数（r = 实测返还率），与实测对照。

纪律：描述性诊断·不作 EV 呈报。期望恒负（抽水复利），本脚本不寻找正期望形状。
开发者 sszhang
"""
from __future__ import annotations

import collections
import datetime
import glob
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "shadow"))
import data as _d   # noqa: E402

PAPER = ROOT / "engine" / "shadow" / "paper_tickets.json"
OUT = ROOT / "data" / "04-summaries" / "v27-shape-frontier.json"


def build_results():
    """(date, code) → outcome，及扁平兜底。同 paper.py settle-all 口径。"""
    keyed, flat = {}, {}
    for r in _d.load_rounds():
        for l in r["legs"]:
            keyed[(r["date"], l["code"])] = l["outcome"]
            flat[l["code"]] = l["outcome"]
    for f in glob.glob(str(ROOT / "engine" / "cache" / "sporttery_results_*.json")):
        try:
            blob = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for m in blob.get("matches", []):
            sc = str(m.get("score") or "")
            if m.get("status") != "Played" or ":" not in sc:
                continue
            hg, ag = (int(x) for x in sc.split(":"))
            oc = 0 if hg > ag else (1 if hg == ag else 2)
            keyed[(m.get("matchDate"), m.get("code"))] = oc
            flat.setdefault(m.get("code"), oc)
    return keyed, flat


def lookup(keyed, flat, code, day):
    if (day, code) in keyed:
        return keyed[(day, code)]
    try:
        d0 = datetime.date.fromisoformat(day)
    except Exception:
        return flat.get(code)
    for delta in (-1, 1):
        k = ((d0 + datetime.timedelta(days=delta)).isoformat(), code)
        if k in keyed:
            return keyed[k]
    return flat.get(code)


def main():
    print("══ v27 诊断：腿级 alpha 对照 + 形状亏损结构 ══\n", flush=True)
    keyed, flat = build_results()
    tickets = json.loads(PAPER.read_text(encoding="utf-8"))
    settled = [t for t in tickets if t.get("result") == "settled"]

    # ── ① 腿级 alpha：去重腿（同 code+pick 只算一次·影子票同腿反复组合非独立样本）──
    uniq = {}
    for t in settled:
        for lg in t.get("legs") or []:
            if lg.get("market") != "had":
                continue
            odds = lg.get("odds") or []
            pick = lg.get("pick") if isinstance(lg.get("pick"), list) else [lg.get("pick")]
            sel = [(i, odds[i]) for i in pick
                   if i is not None and i < len(odds) and isinstance(odds[i], (int, float))]
            if not sel:
                continue
            uniq.setdefault((t.get("date"), lg.get("code"), tuple(pick)), (sel, t.get("date"), lg.get("code")))

    rows = []
    for (day, code, pick), (sel, _, _) in uniq.items():
        oc = lookup(keyed, flat, code, day)
        if oc is None:
            continue
        q = sum(1.0 / o for _, o in sel)           # 含水隐含概率（多选求和）
        win_odds = next((o for i, o in sel if i == oc), None)
        # 本金 = 所选项数（双选腿押两注·首版按一注计本致 ROI 虚高 +47pp——务必 stake 加权）
        rows.append({"q": q, "hit": win_odds is not None, "stake": float(len(sel)),
                     "ret": (win_odds if win_odds else 0.0), "n_sel": len(sel)})

    n = len(rows)
    hits = sum(1 for r in rows if r["hit"])
    mean_q = sum(r["q"] for r in rows) / n
    stake_tot = sum(r["stake"] for r in rows)
    roi = (sum(r["ret"] for r in rows) - stake_tot) / stake_tot
    # 单选腿单列（多选腿 q 可比性差）
    s1 = [r for r in rows if r["n_sel"] == 1]   # 单选腿：stake=1·ROI 口径最干净
    roi1 = (sum(r["ret"] for r in s1) - len(s1)) / len(s1) if s1 else None
    hit1 = sum(1 for r in s1 if r["hit"]) / len(s1) if s1 else None
    q1 = sum(r["q"] for r in s1) / len(s1) if s1 else None

    print("① 腿级 alpha 对照（去重腿·had 池）", flush=True)
    print(f"   去重腿 {n} 条（命中 {hits}·{hits / n * 100:.1f}%）", flush=True)
    print(f"   平均含水隐含概率 Σ(1/odds) = {mean_q:.4f}", flush=True)
    print(f"   实际命中率 {hits / n:.4f} vs 含水隐含 {mean_q:.4f} → "
          f"差 {(hits / n - mean_q) * 100:+.2f}pp", flush=True)
    print(f"   ★ realized 腿级 ROI = {roi * 100:+.2f}%（stake 加权·零 alpha 预期 ≈ −7~−14%）",
          flush=True)
    if s1:
        print(f"   单选腿子集 {len(s1)} 条：命中 {hit1 * 100:.1f}%·含水隐含 {q1 * 100:.1f}%"
              f"·ROI {roi1 * 100:+.2f}%", flush=True)
    boot = None
    if n >= 30:
        import random
        rng = random.Random(20261009)
        def _one():
            smp = [rows[rng.randrange(n)] for _ in range(n)]
            st = sum(r["stake"] for r in smp)
            return (sum(r["ret"] for r in smp) - st) / st
        means = sorted(_one() for _ in range(2000))
        boot = [round(means[50], 4), round(means[1949], 4)]
        print(f"   ROI bootstrap 95%CI [{boot[0] * 100:+.2f}%, {boot[1] * 100:+.2f}%]", flush=True)

    # ── ② 返还率 r：取单选腿 1+ROI（口径最干净）；样本不足则退名义 0.88 ──
    # 注：r 同时含抽水与本样本 alpha/运气·故形状理论期望列为"按本样本 r 的参照线"非绝对真值
    # 理论线锚定名义 r=0.88：样本 r（单选腿 1+ROI）n 仅百余条·CI 极宽·用它作复利底会把
    # 14 串 1 算成 78%（与长串全灭实况矛盾）——样本估计只作对照报出，不进理论曲线
    r_had = 0.88
    r_sample = (1.0 + roi1) if roi1 is not None else None
    print(f"\n② 返还率 r：理论线用名义 {r_had:.2f}（竞彩 0.86~0.88）", flush=True)
    if r_sample:
        print(f"   本样本单选腿估计 r={r_sample:.4f}（n={len(s1)}·CI 宽·运气成分大·不作理论底）",
              flush=True)

    # ── ③ 形状理论期望 vs 实测 ──
    def theo(spec, legs_n):
        """按 spec_name 的串型结构算理论期望倍率（r^串数 按注平均）。"""
        r = r_had if 0.5 < r_had < 1.0 else 0.88
        table = {
            "单关": [1], "4串1": [4], "8串1": [8], "ev-8串1": [8],
            "ttg-4串1": [4], "crs-4串1": [4], "g-tail-贪心串1": [4],
            "4串11": [2] * 6 + [3] * 4 + [4], "ev-4串11": [2] * 6 + [3] * 4 + [4],
            "全2关-4": [2] * 6, "全2关-6": [2] * 15, "ev-双选全2关-4": [2] * 6,
            "ev-8串9": [8] * 9, "4串1+2双选": [4],
        }
        combos = table.get(spec)
        if combos is None:
            if "单关" in spec:
                combos = [1]
            elif "2串1" in spec:
                combos = [2]
            elif "3串1" in spec:
                combos = [3]
            elif "全2关" in spec:
                combos = [2] * 6
            else:
                return None
        return sum(r ** k for k in combos) / len(combos)

    agg = collections.defaultdict(lambda: [0, 0, 0.0, 0.0])
    for t in settled:
        a = agg[t.get("spec_name")]
        a[0] += 1
        a[1] += 1 if (t.get("payout") or 0) > 0 else 0
        a[2] += float(t.get("cost") or 0)
        a[3] += float(t.get("payout") or 0)

    print("\n③ 形状：理论期望（抽水复利）vs 实测回款率（≥5 张·按理论期望降序）", flush=True)
    print(f"   {'形状':<22}{'张':>4}{'理论期望':>10}{'实测回款':>10}{'偏差':>9}  中奖率", flush=True)
    shape_rows = []
    for spec, (cnt, win, cost, pay) in agg.items():
        if cnt < 5:
            continue
        th = theo(spec, None)
        actual = pay / cost if cost else None
        shape_rows.append({"spec": spec, "n": cnt, "win": win, "theo": th,
                           "actual": round(actual, 4) if actual is not None else None,
                           "cost": cost, "payout": pay})
    for r in sorted(shape_rows, key=lambda x: -(x["theo"] or 0)):
        th = f"{r['theo'] * 100:8.1f}%" if r["theo"] else "       —"
        dv = (f"{(r['actual'] - r['theo']) * 100:+8.1f}pp"
              if r["theo"] and r["actual"] is not None else "        —")
        print(f"   {r['spec']:<22}{r['n']:>4}{th}{r['actual'] * 100:>9.1f}%{dv}"
              f"  {r['win'] / r['n'] * 100:5.1f}%", flush=True)

    # 串数 → 理论期望曲线（纯数学·无需实验）
    print("\n④ 抽水复利曲线（r = 上列返还率）", flush=True)
    r = r_had if 0.5 < r_had < 1.0 else 0.88
    for k in (1, 2, 3, 4, 6, 8, 10, 14):
        print(f"   {k:>2} 串 1：期望 {r ** k * 100:6.2f}%"
              f"（每 100 元期望回 {r ** k * 100:.1f} 元）", flush=True)

    OUT.write_text(json.dumps({
        "ranAt": "2026-10-09",
        "legAlpha": {"nUniqLegs": n, "hitRate": round(hits / n, 4),
                     "meanImpliedQ": round(mean_q, 4), "realizedROI": round(roi, 4),
                     "roiCI95": boot, "singlePickROI": round(roi1, 4) if roi1 else None},
        "returnRateTheory": r_had,
        "returnRateSampleEstimate": round(r_sample, 4) if r_sample else None,
        "shapes": shape_rows,
        "discipline": "描述性诊断·期望恒负(抽水复利)·不作 EV 呈报",
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n归档 {OUT.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
