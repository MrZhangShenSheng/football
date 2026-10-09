# -*- coding: utf-8 -*-
"""v25：联赛质量分层普查（判据预注册于 fade-strategy-prereg v25·跑前写死）。

背景：v23/v24 证明合并口径下市场校准 ≤1.8pp、模型全端零增量——但**分联赛**
口径从未切过。接挂账「新联赛质量分层 + 意甲恶化调查」。三线之首（主公拍板）。

数据：v3 缓存（v2 + league/home/away/matchId 标签·独立文件 pre_days_cache_v3.pkl
·预测逻辑零改动·与 v2 缓存共存互不覆盖）。

四道防假阳闸（跑前写死）：
  ① 联赛段内 n≥100 才判（否则列「不判」）
  ② 弱联赛旗 = fit/val 两段同号（模型 LL 更差）且 val 段配对 bootstrap CI
     （1000 次·种子 20261009·逐场重抽样）不含 0
  ③ 多重比较对账：旗数须 > 期望假阳（0.05×有效检验数）且每旗过
     Bonferroni（α = 0.05/检验数）
  ④ a=0 三联赛（意甲/德甲/荷甲）单列对照——方向端已听市场·若仍弱则病灶在比分端

判定：
  出旗 ≥1 → v26 选场闸联赛分层另立预注册（v24.4 同款生产闸）
  全灭   → 「合并口径市场 ≤1.8pp」在分联赛下成立·挂账销案

纪律：本 census 描述性·不作任何 EV 呈报。
产出：data/04-summaries/v25-league-census.json
开发者 sszhang
"""
from __future__ import annotations

import json
import math
import pickle
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v11_s4_recalib import FIT_WINDOW, VAL_WINDOW, LEAGUES, HIST
from v24_sharpen_diag import real_dir

ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = ROOT / "data" / "04-summaries" / "v25-league-census.json"
CACHE_V3 = ROOT / "engine" / "cache" / "strength_chain" / "pre_days_cache_v3.pkl"
DIRS = ("h", "d", "a")
MIN_N = 100
BOOT_N = 1000
SEED = 20261009
A0_LEAGUES = ("意甲", "德甲", "荷甲")     # leagueOverrides a=0（方向端已听市场）


def load_v3():
    """v3 缓存：v2 预测 + 联赛标签。缺则重建（约 15~45 分钟）。"""
    key = f"v3|{max((f.stat().st_mtime for f in HIST.glob('crs_hist_*.json')), default=0):.0f}|{len(LEAGUES)}"
    if CACHE_V3.exists():
        try:
            obj = pickle.loads(CACHE_V3.read_bytes())
            if obj.get("key") == key:
                print("preload 命中 v3 磁盘缓存", flush=True)
                return obj["pre"]
        except Exception:
            pass
    import strength_loaders as sl
    from v11_s5_recalib import preload_days
    ctx = sl.build_ctx(LEAGUES)
    pre = preload_days(ctx, sl.zh_to_id(), {})
    CACHE_V3.parent.mkdir(parents=True, exist_ok=True)
    CACHE_V3.write_bytes(pickle.dumps({"key": key, "pre": pre}))
    print("v3 缓存已重建", flush=True)
    return pre


def boot_ci(diffs):
    """>0 = 模型更优（市场 LL − 模型 LL）。逐场重抽样。"""
    n = len(diffs)
    if n == 0:
        return [0.0, 0.0]
    rng = random.Random(SEED)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(BOOT_N))
    return [round(means[int(BOOT_N * 0.025)], 6), round(means[min(int(BOOT_N * 0.975), BOOT_N - 1)], 6)]


def build(pre, window):
    """→ {league: {"had": [(llM, llK)], "crs": [(llM, llK)], "hadDev": [(implied, hit)]}}"""
    lo, hi = window
    out: dict[str, dict] = {}
    n_label = n_all = 0
    for day, cands in pre.items():
        if not (lo <= day <= hi):
            continue
        for c in cands:
            n_all += 1
            lg = c.get("league") or "未标"
            if c.get("league"):
                n_label += 1
            slot = out.setdefault(lg, {"had": [], "crs": [], "hadDev": []})
            # HAD（v24.1 同构）
            hist, model = c.get("hadHist"), c.get("hadModel") or {}
            o = real_dir(c["real"])
            if (o is not None and hist
                    and all(isinstance(hist.get(k), (int, float)) and hist[k] > 1.0 for k in DIRS)
                    and all(isinstance(model.get(k), (int, float)) and model[k] >= 0 for k in DIRS)):
                sm, sk = sum(model[k] for k in DIRS), sum(1.0 / hist[k] for k in DIRS)
                if sm > 0 and sk > 0:
                    pm = [model[k] / sm for k in DIRS]
                    pk = [(1.0 / hist[k]) / sk for k in DIRS]
                    slot["had"].append((-math.log(max(pm[o], 1e-12)), -math.log(max(pk[o], 1e-12))))
                    for i in range(3):
                        slot["hadDev"].append((pk[i], 1.0 if i == o else 0.0))
            # CRS（v24.2 同构·同支撑集）
            cells = c.get("cells") or []
            if len(cells) >= 3:
                idx = next((i for i, x in enumerate(cells) if x["mk"] == c["real"]), None)
                if idx is not None:
                    sm = sum(max(x["p"], 0.0) for x in cells)
                    sk = sum(1.0 / x["odds"] for x in cells)
                    if sm > 0 and sk > 0:
                        pm = max(cells[idx]["p"], 0.0) / sm
                        pk = (1.0 / cells[idx]["odds"]) / sk
                        slot["crs"].append((-math.log(max(pm, 1e-12)), -math.log(max(pk, 1e-12))))
    return out, n_label, n_all


def census(seg_tables):
    """逐联赛逐池：n / LL差(市场−模型·>0模型优) / val CI / 旗。"""
    rows = []
    leagues = sorted({lg for t in seg_tables.values() for lg in t})
    n_tested = 0
    for lg in leagues:
        row = {"league": lg, "a0": lg in A0_LEAGUES}
        for pool in ("had", "crs"):
            fit = seg_tables["fit"].get(lg, {}).get(pool) or []
            val = seg_tables["val"].get(lg, {}).get(pool) or []
            row[f"{pool}_n"] = {"fit": len(fit), "val": len(val)}
            d_fit = (sum(k - m for m, k in fit) / len(fit)) if len(fit) >= MIN_N else None
            d_val = (sum(k - m for m, k in val) / len(val)) if len(val) >= MIN_N else None
            ci = boot_ci([k - m for m, k in val]) if len(val) >= MIN_N else None
            row[f"{pool}_diff"] = {"fit": round(d_fit, 4) if d_fit is not None else None,
                                   "val": round(d_val, 4) if d_val is not None else None,
                                   "valCI": ci}
            if d_fit is not None:
                n_tested += 1
        # 市场自校准 dev（HAD 腿·预测隐含 − 实际）
        dev_f = seg_tables["fit"].get(lg, {}).get("hadDev") or []
        dev_v = seg_tables["val"].get(lg, {}).get("hadDev") or []
        row["mktDev"] = {
            "fit": round(sum(p for p, _ in dev_f) / len(dev_f) - sum(h for _, h in dev_f) / len(dev_f), 4)
            if len(dev_f) >= MIN_N * 3 else None,
            "val": round(sum(p for p, _ in dev_v) / len(dev_v) - sum(h for _, h in dev_v) / len(dev_v), 4)
            if len(dev_v) >= MIN_N * 3 else None}
        rows.append(row)
    return rows, n_tested


def main():
    print("══ v25 联赛质量分层普查 ══\n", flush=True)
    print("预注册: fade-strategy-prereg v25（跑前写死·四道防假阳闸）", flush=True)
    print("纪律: 描述性 census·不作任何 EV 呈报\n", flush=True)
    pre = load_v3()
    print(f"v3 缓存: {len(pre)} 日\n", flush=True)

    seg_tables = {}
    label_stat = {}
    for seg, w in (("fit", FIT_WINDOW), ("val", VAL_WINDOW)):
        t, nl, na = build(pre, w)
        seg_tables[seg] = t
        label_stat[seg] = (nl, na)
        print(f"[sanity] {seg} 段: 联赛标签覆盖 {nl}/{na} = {nl / max(na, 1) * 100:.1f}%"
              f"（≥95% 方过闸）", flush=True)
    cov = min(nl / max(na, 1) for nl, na in label_stat.values())
    if cov < 0.95:
        print(f"[sanity] ⚠️ 标签覆盖 {cov*100:.1f}% < 95% ——中止·查 hist 底表 league 字段", flush=True)
        return

    rows, n_tested = census(seg_tables)
    alpha_bonf = 0.05 / max(n_tested, 1)
    expected_fp = 0.05 * n_tested

    # 旗判定（闸②③）
    flags = []
    for r in rows:
        for pool in ("had", "crs"):
            d = r[f"{pool}_diff"]
            if d["fit"] is None or d["val"] is None:
                continue
            same_sign = (d["fit"] < 0 and d["val"] < 0)      # 两段模型皆更差
            ci_excl = d["valCI"] is not None and d["valCI"][1] < 0   # val CI 全负=模型显著更差
            if same_sign and ci_excl:
                flags.append({"league": r["league"], "pool": pool,
                              "diffFit": d["fit"], "diffVal": d["val"], "valCI": d["valCI"],
                              "bonferroniPass": None})   # CI 已是 95%·Bonferroni 需更严——见下
    # Bonferroni：要求 val CI 在 α/n_tested 水平仍不含 0 → 用正态近似复核
    import statistics
    for f in flags:
        lg, pool = f["league"], f["pool"]
        val = seg_tables["val"][lg][pool]
        diffs = [k - m for m, k in val]
        mu = sum(diffs) / len(diffs)
        sd = statistics.pstdev(diffs)
        se = sd / math.sqrt(len(diffs))
        z = mu / se if se > 0 else 0.0
        from math import erf, sqrt
        p_one = 1 - 0.5 * (1 + erf(abs(z) / sqrt(2)))     # 单侧 p（模型更差方向）
        f["z"] = round(z, 2)
        f["pOneSided"] = p_one
        f["bonferroniPass"] = p_one < alpha_bonf

    passed = [f for f in flags if f["bonferroniPass"]]
    verdict = ""
    if passed and len(passed) > expected_fp:
        verdict = (f"出旗 {len(passed)} 个联赛池（过全部四闸·超过期望假阳 {expected_fp:.1f}）"
                   f"——v26 选场闸联赛分层另立预注册")
    elif flags:
        verdict = (f"旗候选 {len(flags)} 个但未过 Bonferroni/未超期望假阳({expected_fp:.1f})"
                   f"——按噪声处理·分联赛口径与合并口径一致")
    else:
        verdict = f"全灭（无联赛过闸②）——合并口径市场 ≤1.8pp 在分联赛下成立·挂账销案"

    def _f4(x):
        return f"{x:<+11.4f}" if isinstance(x, (int, float)) else f"{'—':<11}"

    print(f"\n── 逐联赛表（HAD 池·按 val diff 升序=模型最差在前）──", flush=True)
    print(f"{'联赛':<8}{'n(fit/val)':<13}{'diff fit':<11}{'diff val':<11}{'val CI':<22}{'市场dev'}", flush=True)
    for r in sorted((r for r in rows if r["had_diff"]["val"] is not None),
                    key=lambda r: r["had_diff"]["val"]):
        d = r["had_diff"]
        tag = " ‹a0" if r["a0"] else ""
        md = r["mktDev"]["val"]
        print(f"{r['league'] + tag:<10}{str(r['had_n']['fit']) + '/' + str(r['had_n']['val']):<13}"
              f"{_f4(d['fit'])}{_f4(d['val'])}{str(d['valCI']):<22}"
              f"{md if md is not None else '—'}", flush=True)
    print(f"\n── 逐联赛表（CRS 池）──", flush=True)
    for r in sorted((r for r in rows if r["crs_diff"]["val"] is not None),
                    key=lambda r: r["crs_diff"]["val"]):
        d = r["crs_diff"]
        tag = " ‹a0" if r["a0"] else ""
        print(f"{r['league'] + tag:<10}{str(r['crs_n']['fit']) + '/' + str(r['crs_n']['val']):<13}"
              f"{_f4(d['fit'])}{_f4(d['val'])}{str(d['valCI'])}", flush=True)
    small = [r["league"] for r in rows
             if r["had_diff"]["fit"] is None and r["crs_diff"]["fit"] is None]
    print(f"\n不判联赛（n<{MIN_N}）: {small}", flush=True)
    print(f"有效检验数 {n_tested}·Bonferroni α={alpha_bonf:.5f}·期望假阳 {expected_fp:.1f}", flush=True)
    for f in flags:
        print(f"  旗候选: {f['league']}/{f['pool']} z={f['z']} p={f['pOneSided']:.4f}"
              f" Bonferroni{'过' if f['bonferroniPass'] else '不过'}", flush=True)
    print(f"\n══ 判定: {verdict} ══", flush=True)

    result = {"ranAt": "2026-10-09", "preReg": "fade-strategy-prereg v25",
              "sanity": {k: {"labeled": v[0], "all": v[1]} for k, v in label_stat.items()},
              "nTested": n_tested, "alphaBonferroni": round(alpha_bonf, 6),
              "expectedFalsePositive": round(expected_fp, 1),
              "leagues": rows, "flags": flags, "verdict": verdict,
              "discipline": "描述性 census·不作 EV 呈报"}
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"归档 {OUT_PATH.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
