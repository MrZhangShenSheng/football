# -*- coding: utf-8 -*-
"""v35-P3 · 步骤消融审计（预注册: fade-strategy-prereg v35Foundation.P3 · 2026-10-11）

单元（判据跑前写死·全报不挑）:
  U1 融合a存废: 模型项=V3W had(交底口径) vs null a=0 纯市场 · 裁判=逐场RPS配对差分联赛CI
  U2 λ源vs双null: 矩法反解λ(体彩) + Pinnacle锚 · 裁判=CRS log-loss配对差（单独会话跑）
  U3 去水k: k二分 vs k=1 · 裁判=31格log-loss配对差
  U4 ρ: 现役ρ vs ρ=0 · 裁判=低分带四格log-loss和+校准误差
  U5 假设层首账: verdict×赛果 描述性·无判据（只建账）

时间线: v32.1 五道纯净闸 + 富化缓存重放（lam/lg/rho/had3 as-of）
审计三件套: 逐场stdout汇总 + per-match落盘 + --audit 复算

用法:
  python engine/scripts/research/v35_step_audit.py cache        # 富化缓存重建(~13分钟·幂等)
  python engine/scripts/research/v35_step_audit.py u3|u1|u4|u5  # 单元跑
  python engine/scripts/research/v35_step_audit.py --audit      # 复算重放
开发者 sszhang
"""
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import strength_loaders as sl          # noqa: E402
import strength_chain_eval as sce      # noqa: E402
import score_matrix as sm              # noqa: E402
from v18_dual_signal_backtest import LEAGUES  # noqa: E402
from v32_feature_sieve_backtest import load_hist, seg_of  # noqa: E402

SUMM = ROOT / "data" / "04-summaries"
OUT_JSON = SUMM / "v35-step-audit.json"
ENRICH_CACHE = ROOT / "engine" / "cache" / "strength_chain" / "v35_enrich_cache.json"
N_BOOT, BOOT_SEED = 2000, 20261012
RNG = np.random.RandomState(BOOT_SEED)
LOW_BAND = ("s00s00", "s01s00", "s00s01", "s01s01")   # 0:0/1:0/0:1/1:1


def boot_ci(diffs):
    arr = np.array(diffs, dtype=float)
    means = sorted(float(arr[rng.randint(0, len(arr), len(arr))].mean())
                   for rng in (np.random.RandomState(BOOT_SEED + i) for i in range(N_BOOT)))
    return [round(means[int(0.025 * N_BOOT)], 5), round(means[int(0.975 * N_BOOT)], 5)]


def sign_flip_null_ci(diffs):
    """铁律14③自证伪: 差值符号随机翻转的 null CI（优势应消失）。"""
    arr = np.array(diffs, dtype=float)
    rng = np.random.RandomState(BOOT_SEED)
    means = []
    for _ in range(200):
        flip = arr * rng.choice([-1.0, 1.0], size=len(arr))
        means.append(float(flip.mean()))
    means.sort()
    return [round(means[10], 5), round(means[189], 5)]


# ---------- 富化缓存（lam + lg + rho + had3 · as-of 重放·幂等指纹） ----------

def build_enrich():
    matches = load_hist()
    fp = f"{len(matches)}|{matches[0]['date']}|{matches[-1]['date']}"
    if ENRICH_CACHE.exists():
        d = json.loads(ENRICH_CACHE.read_text(encoding="utf-8"))
        if d.get("fingerprint") == fp:
            print(f"富化缓存命中: {len(d['rows'])} 场")
            return d["rows"]
    print("重放 V3W 链（lam/lg/rho/had3·~13分钟）...")
    z2i = sl.zh_to_id()
    ctx = sl.build_ctx(LEAGUES)
    memo = {}
    rows = {}
    for m in matches:
        hid, aid = z2i.get(m["home"]), z2i.get(m["away"])
        if not (hid and aid):
            continue
        pred = sce._predict_match(hid, aid, date.fromisoformat(m["date"]), ctx, memo, beta=0.05)
        if not (pred and pred.get("lam")):
            continue
        lg = pred.get("league")
        rho = float((ctx["dc"].get(lg) or {}).get("rho", -0.05))
        rows[f"{m['date']}|{m['home']}|{m['away']}"] = {
            "lam": [pred["lam"][0], pred["lam"][1]], "lg": lg, "rho": rho,
            "had3": {k: round(v, 4) for k, v in (pred.get("had") or {}).items()}}
    ENRICH_CACHE.write_text(
        json.dumps({"fingerprint": fp, "rows": rows}, ensure_ascii=False), encoding="utf-8")
    print(f"富化缓存落盘: {len(rows)} 场")
    return rows


def load_hist_odds():
    """v32 load_hist 变体：保留 HAD 原始三赔（U1 需要逐场去水）。"""
    import copy
    from v32_feature_sieve_backtest import HIST as _H
    out = []
    for f in sorted(_H.glob("crs_hist_*.json")):
        for m in json.loads(f.read_text(encoding="utf-8"))["matches"]:
            had, sc = m.get("had"), str(m.get("score") or "")
            if not (had and had.get("h") and ":" in sc):
                continue
            try:
                h, a = map(int, sc.split(":"))
            except ValueError:
                continue
            out.append({"date": str(m.get("date", ""))[:10], "home": m.get("home", "?"),
                        "away": m.get("away", "?"), "league": m.get("league", "?"),
                        "h": h, "a": a,
                        "oh": float(had["h"]), "od": float(had["d"]), "oa": float(had["a"]),
                        "ttg": m.get("ttg") or {}, "crs": m.get("crs") or {}})
    return out


# ---------- U3 去水 k（只依赖 crs 赔率·无需重放） ----------

def cell_of(h, a):
    if h <= 5 and a <= 5:
        return f"{h}:{a}"
    return "other_h" if h > a else ("other_d" if h == a else "other_a")


def unit_u3(store):
    rows = []
    for m in load_hist():
        cells = {}
        for k, v in (m.get("crs") or {}).items():
            try:
                o = float(v)
            except (TypeError, ValueError):
                continue
            if o > 1.0 and (":" in str(k) or str(k).startswith("other")):
                cells[str(k)] = o
        if len(cells) < 20:
            continue
        true_cell = cell_of(m["h"], m["a"])
        if true_cell not in cells:
            continue
        inv = np.array([1 / o for o in cells.values()])
        p_k1 = inv / inv.sum()                          # k=1 即 1/o 归一
        lo, hi = 0.5, 5.0
        for _ in range(40):                             # k 二分解 Σ(1/o)^k=1（2026-10-11 修:原目标误写归一sum恒≈1·k终值漂到3.4）
            mid = (lo + hi) / 2
            s = float((inv ** mid).sum())
            lo, hi = (mid, hi) if s > 1 else (lo, mid)
        pw = inv ** ((lo + hi) / 2)
        p_k2 = pw / pw.sum()
        i_true = list(cells.keys()).index(true_cell)
        rows.append({"date": m["date"], "seg": seg_of(m["date"]),
                     "ll_k1": round(float(-np.log(max(p_k1[i_true], 1e-12))), 5),
                     "ll_k2": round(float(-np.log(max(p_k2[i_true], 1e-12))), 5)})
    seg_stat = {}
    for seg in ("fit", "val"):
        rs = [r for r in rows if r["seg"] == seg]
        if not rs:
            continue
        diffs = [r["ll_k2"] - r["ll_k1"] for r in rs]   # 负 = k二分优
        seg_stat[seg] = {"n": len(rs),
                         "meanDiff": round(float(np.mean(diffs)), 5),
                         "ci": boot_ci(diffs), "signFlipNull": sign_flip_null_ci(diffs)}
    store["U3_devig"] = {"judge": "31格log-loss配对差(k2-k1·负=k二分优)",
                         "segments": seg_stat, "perMatch": rows}
    for seg, s in seg_stat.items():
        print(f"[U3|{seg}] n={s['n']} Δ均值{s['meanDiff']} CI{s['ci']} (符号翻转null{s['signFlipNull']})")
    verdict_k2_keep = any(s["ci"][1] < 0 for s in seg_stat.values())
    print(f"[U3] 终判: {'k二分显著优·留任' if verdict_k2_keep else 'k二分无显著优势·建议简化k=1进P4清单'}")
    store["U3_devig"]["verdict"] = "k2_keep" if verdict_k2_keep else "k1_simplify"


def cell_of(h, a):
    if h <= 5 and a <= 5:
        return f"{h}:{a}"
    return {"1": "other_h", "0": "other_d"}.get(str(h - a) if h > a else ("0" if h == a else "-1"),
                                                "other_a") if h != a else "other_d"


# ---------- U1 融合 a 存废 ----------

def logit(p):
    return float(np.log(p / (1 - p)))


def fuse3(p_model, p_mkt, a, b):
    z = [a * logit(max(pm, 1e-6)) + b * logit(max(pk, 1e-6))
         for pm, pk in zip(p_model, p_mkt)]
    e = np.exp(np.array(z) - np.max(z))
    p = e / e.sum()
    return [float(x) for x in p]


def rps3(p, outcome):
    o = [1.0 if outcome == i else 0.0 for i in range(3)]
    cp, co = np.cumsum(p), np.cumsum(o)
    return float(((cp - co) ** 2).sum() / 2)


def pin_map():
    """(date,home,away)→pinClose 去水三向概率（fd 覆盖场金标准锚·日期=文件名）。"""
    out = {}
    for f in sorted((ROOT / "data" / "02-results").glob("*.json")):
        day = f.stem[:10]
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                  # noqa: BLE001
            continue
        for m in d.get("matches") or []:
            pc = m.get("pinClose")
            home = m.get("home") or str(m.get("match") or "").split(" vs ")[0].strip()
            away = m.get("away") or str(m.get("match") or "").split(" vs ")[-1].strip()
            key = (day, str(home or ""), str(away or ""))
            if isinstance(pc, list) and len(pc) >= 3:      # 实测形态: 去水三向 [h,d,a]
                p = np.array([float(x) for x in pc[:3]])
                out[key] = [float(x) for x in (p / p.sum())]
            elif isinstance(pc, dict) and pc.get("h"):     # 兜底: 原始赔率形态
                inv = np.array([1 / float(pc["h"]), 1 / float(pc["d"]), 1 / float(pc["a"])])
                out[key] = [float(x) for x in (inv / inv.sum())]
    return out


def unit_u1(store, enrich):
    pms = pin_map()
    rows = []
    for m in load_hist_odds():
        e = enrich.get(f"{m['date']}|{m['home']}|{m['away']}")
        had3 = (e or {}).get("had3") or {}
        if not (e and all(had3.get(k) is not None for k in ("h", "d", "a"))):
            continue
        oh, od, oa = m["oh"], m["od"], m["oa"]
        inv = np.array([1 / oh, 1 / od, 1 / oa])
        p_mkt = inv / inv.sum()
        p_model = [had3["h"], had3["d"], had3["a"]]
        s = sum(p_model)
        p_model = [x / s for x in p_model]
        outcome = 0 if m["h"] > m["a"] else (1 if m["h"] == m["a"] else 2)
        r_fused = rps3(fuse3(p_model, p_mkt, 0.4, 1.0), outcome)
        r_null = rps3(p_mkt, outcome)                  # a=0 即纯市场
        anchor = pms.get((m["date"], m["home"], m["away"]))
        r_fused_a = r_null_a = None
        if anchor:
            pa = np.array(anchor)
            r_fused_a = rps3(fuse3(p_model, pa, 0.4, 1.0), outcome)
            r_null_a = rps3(pa, outcome)
        lg = e.get("lg") or "?"
        rows.append({"date": m["date"], "seg": seg_of(m["date"]), "lg": lg,
                     "league": m["league"], "outcome": outcome,
                     "rps_fused": round(r_fused, 5), "rps_null": round(r_null, 5),
                     "rps_fused_anchor": round(r_fused_a, 5) if r_fused_a is not None else None,
                     "rps_null_anchor": round(r_null_a, 5) if r_null_a is not None else None})
    by_lg = {}
    for lg in sorted({r["lg"] for r in rows}):
        rs = [r for r in rows if r["seg"] == "val" and r["lg"] == lg]
        if len(rs) < 30:
            continue
        diffs = [r["rps_fused"] - r["rps_null"] for r in rs]       # 负=a=0.4优
        by_lg[lg] = {"n": len(rs), "meanDiff": round(float(np.mean(diffs)), 5),
                     "ci": boot_ci(diffs)}
    # 锚口径（fd 覆盖场·val）
    anchor_rows = [r for r in rows if r["seg"] == "val" and r["rps_fused_anchor"] is not None]
    anchor_stat = None
    if anchor_rows:
        d_ = [r["rps_fused_anchor"] - r["rps_null_anchor"] for r in anchor_rows]
        anchor_stat = {"n": len(anchor_rows), "meanDiff": round(float(np.mean(d_)), 5),
                       "ci": boot_ci(d_)}
    verdict = {lg: ("keep_a" if s["ci"][1] < 0 else
                    ("a0" if s["ci"][0] > 0 else "neutral"))
               for lg, s in by_lg.items() if s["n"] >= 100}
    store["U1_fusion"] = {"judge": "逐场RPS配对差(fused-a0.4减null-a0·负=留a优)·val段分联赛",
                          "byLeague_val": by_lg, "anchor_val": anchor_stat,
                          "verdict_n100": verdict, "perMatch": rows}
    print(f"[U1] val 联赛级（n≥30 报·n≥100 判）:")
    for lg, s in sorted(by_lg.items(), key=lambda kv: kv[1]["meanDiff"]):
        tag = verdict.get(lg, "n<100只报")
        print(f"  {lg:22s} n={s['n']:4d} Δ{s['meanDiff']:+.5f} CI{s['ci']} → {tag}")
    if anchor_stat:
        print(f"[U1] Pinnacle锚口径: n={anchor_stat['n']} Δ{anchor_stat['meanDiff']:+.5f} CI{anchor_stat['ci']}")
    any_keep = any(v == "keep_a" for v in verdict.values())
    print(f"[U1] 终判: {'存在联赛a=0.4显著优→留任该联赛' if any_keep else '无联赛a显著优→推广a=0进P4拆迁清单'}")


# ---------- U4 ρ 消融 ----------

def unit_u4(store, enrich):
    rows = []
    n_skip = 0
    key_sample = None
    for m in load_hist():
        e = enrich.get(f"{m['date']}|{m['home']}|{m['away']}")
        if not e:
            continue
        key = cell_key(m["h"], m["a"])
        m_rho = sm.dc_matrix(e["lam"][0], e["lam"][1], e["rho"])
        m_zero = sm.dc_matrix(e["lam"][0], e["lam"][1], 0.0)
        if key_sample is None:
            key_sample = sorted(m_rho.keys())[:8]
        if key not in m_rho:
            n_skip += 1
            continue
        ll_rho = sum(-np.log(max(m_rho[c], 1e-12)) for c in LOW_BAND if c in m_rho)
        ll_zero = sum(-np.log(max(m_zero[c], 1e-12)) for c in LOW_BAND if c in m_zero)
        in_band = key in LOW_BAND
        p_band_rho = sum(m_rho[c] for c in LOW_BAND if c in m_rho)
        p_band_zero = sum(m_zero[c] for c in LOW_BAND if c in m_zero)
        rows.append({"date": m["date"], "seg": seg_of(m["date"]),
                     "ll_band_rho": round(float(ll_rho), 4), "ll_band_zero": round(float(ll_zero), 4),
                     "p_band_rho": round(float(p_band_rho), 4),
                     "p_band_zero": round(float(p_band_zero), 4), "in_band": in_band})
    out = {}
    for seg in ("fit", "val"):
        rs = [r for r in rows if r["seg"] == seg]
        if not rs:
            continue
        diffs = [r["ll_band_rho"] - r["ll_band_zero"] for r in rs]   # 负=ρ优
        band_freq = float(np.mean([r["in_band"] for r in rs]))
        out[seg] = {"n": len(rs), "meanDiff": round(float(np.mean(diffs)), 5),
                    "ci": boot_ci(diffs), "signFlipNull": sign_flip_null_ci(diffs),
                    "bandFreq": round(band_freq, 4),
                    "calibGap_rho": round(abs(float(np.mean([r['p_band_rho'] for r in rs])) - band_freq), 4),
                    "calibGap_zero": round(abs(float(np.mean([r['p_band_zero'] for r in rs])) - band_freq), 4)}
    store["U4_rho"] = {"judge": "低分带(0:0/1:0/0:1/1:1)四格log-loss和配对差(ρ减ρ0·负=ρ优)+带级校准",
                       "matrixKeySample": key_sample, "skippedCellNotFound": n_skip,
                       "lowBandKeys": list(LOW_BAND),
                       "segments": out, "perMatch": rows}
    for seg, s in out.items():
        print(f"[U4|{seg}] n={s['n']} Δ{s['meanDiff']} CI{s['ci']} null{s['signFlipNull']} · "
              f"带频{s['bandFreq']} 校准差 ρ{s['calibGap_rho']} vs ρ0{s['calibGap_zero']}")
    rho_keep = out.get("val", {}).get("ci", [0, 1])[1] < 0
    print(f"[U4] 终判: {'ρ显著优·留任' if rho_keep else 'ρ无显著优势·建议ρ=0简化进P4清单'}")
    store["U4_rho"]["verdict"] = "rho_keep" if rho_keep else "rho_zero"


def cell_key(h, a):
    return f"s{min(h,5):02d}s{min(a,5):02d}"


# ---------- U2 λ源 vs 双null（矩法反解·预注册 units/U2_lambda·单独会话批） ----------
#
# 交底(2026-10-11 跑前): U3 的 power 二分目标误写为归一后求和(恒≈1·浮点噪声驱动·k 终值≈3.4 非 ~1.14)——
# 本单元 λsum 的 power 去水用正确目标 Σ(1/o)^k=1；U3 一行修复重跑待大哥过目后补。

TTG_KEYS = tuple(str(i) for i in range(8))     # 体彩总进球 0..7（"7"=7+ 档）
TTG_PLUS_VAL = 7.0                              # "7+"期望替身·保守下界（p7 通常<1%影响可忽略）


def lam_sum_from_ttg(ttg: dict) -> float:
    """E[总进球] = TTG power 去水（k 二分解 Σ(1/o)^k=1）后期望。确定性无优化。"""
    ks = sorted((str(k) for k in ttg if str(k) in TTG_KEYS and float(ttg[k]) > 1.0),
                key=float)
    inv = np.array([1.0 / float(ttg[k]) for k in ks])
    lo, hi = 0.5, 5.0
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if (inv ** mid).sum() > 1 else (lo, mid)
    p = inv ** ((lo + hi) / 2)
    p = p / p.sum()
    return float(sum((TTG_PLUS_VAL if k == "7" else float(k)) * pi for k, pi in zip(ks, p)))


def solve_lamh(lam_sum: float, p_h: float, rho: float) -> float:
    """40 次二分解 P(H>A)(λh, λsum−λh)=p_h（had_from 聚合·同 V3W ρ 同链·λh∈(0.05, λsum−0.05)）。"""
    lo, hi = 0.05, max(0.06, lam_sum - 0.05)
    for _ in range(40):
        mid = (lo + hi) / 2
        p = sm.had_from(sm.dc_matrix(mid, lam_sum - mid, rho))["h"]
        lo, hi = (mid, hi) if p < p_h else (lo, mid)
    return (lo + hi) / 2


def _top_hit(mat: dict, key: str, k: int) -> bool:
    return key in sorted(mat, key=lambda c: -mat[c])[:k]


def unit_u2(store, enrich):
    pms = pin_map()
    rng_pick = np.random.RandomState(BOOT_SEED)     # 随机选格自证伪(v34 先例口径)
    rows = []
    for m in load_hist_odds():                      # 变体=带 HAD 原始三赔+ttg（load_hist 不含 had）
        e = enrich.get(f"{m['date']}|{m['home']}|{m['away']}")
        if not (e and e.get("lam")):
            continue
        if not (m.get("ttg") and m.get("oh") and m.get("od") and m.get("oa")):
            continue                                # TTG/HAD 盘缺→双 null 均不可造
        lam_sum = lam_sum_from_ttg(m["ttg"])
        invh = np.array([1 / m["oh"], 1 / m["od"], 1 / m["oa"]])
        p_mkt = invh / invh.sum()
        rho = e["rho"]
        key = cell_key(m["h"], m["a"])
        m_v3w = sm.dc_matrix(e["lam"][0], e["lam"][1], rho)
        if key not in m_v3w:
            continue
        lh_a = solve_lamh(lam_sum, float(p_mkt[0]), rho)
        m_na = sm.dc_matrix(lh_a, lam_sum - lh_a, rho)
        rec = {"date": m["date"], "seg": seg_of(m["date"]), "lg": e.get("lg") or "?",
               "key": key, "lamSum": round(lam_sum, 3), "lamH_nullA": round(lh_a, 3),
               "lam_v3w": [round(e["lam"][0], 3), round(e["lam"][1], 3)],
               "ll_v3w": round(float(-np.log(max(m_v3w[key], 1e-12))), 4),
               "ll_nullA": round(float(-np.log(max(m_na.get(key, 1e-12), 1e-12))), 4),
               "top1_v3w": _top_hit(m_v3w, key, 1), "top3_v3w": _top_hit(m_v3w, key, 3),
               "top1_nullA": _top_hit(m_na, key, 1), "top3_nullA": _top_hit(m_na, key, 3)}
        rand_k = sorted(m_v3w)[int(rng_pick.randint(len(m_v3w)))]
        rec["ll_randPick"] = round(float(-np.log(max(m_v3w[rand_k], 1e-12))), 4)
        anchor = pms.get((m["date"], m["home"], m["away"]))
        if anchor:                                  # nullB: Pinnacle 锚·λsum 仍体彩 TTG(混合源交底)
            lh_b = solve_lamh(lam_sum, float(anchor[0]), rho)
            m_nb = sm.dc_matrix(lh_b, lam_sum - lh_b, rho)
            rec.update({"ll_nullB": round(float(-np.log(max(m_nb.get(key, 1e-12), 1e-12))), 4),
                        "lamH_nullB": round(lh_b, 3),
                        "top1_nullB": _top_hit(m_nb, key, 1), "top3_nullB": _top_hit(m_nb, key, 3)})
        rows.append(rec)
    if not rows:
        print("[U2] 异常: 0 场入账——enrich join 或盘口字段断裂，拒绝落盘（铁律8 空结果禁静默）")
        return
    seg_stat = {}
    for seg in ("fit", "val"):
        rs = [r for r in rows if r["seg"] == seg]
        if not rs:
            continue
        dA = [r["ll_v3w"] - r["ll_nullA"] for r in rs]
        seg_stat[seg] = {"n": len(rs),
                         "meanDiffA": round(float(np.mean(dA)), 5),
                         "ciA": boot_ci(dA), "signFlipNullA": sign_flip_null_ci(dA),
                         "top1_v3w": round(float(np.mean([r["top1_v3w"] for r in rs])), 4),
                         "top1_nullA": round(float(np.mean([r["top1_nullA"] for r in rs])), 4),
                         "top3_v3w": round(float(np.mean([r["top3_v3w"] for r in rs])), 4),
                         "top3_nullA": round(float(np.mean([r["top3_nullA"] for r in rs])), 4),
                         "ll_randPick": round(float(np.mean([r["ll_randPick"] for r in rs])), 4),
                         "lamSumMean": round(float(np.mean([r["lamSum"] for r in rs])), 3)}
    anchor_stat = None
    anch = [r for r in rows if r["seg"] == "val" and "ll_nullB" in r]
    if anch:
        dB = [r["ll_v3w"] - r["ll_nullB"] for r in anch]
        anchor_stat = {"n": len(anch), "meanDiffB": round(float(np.mean(dB)), 5),
                       "ciB": boot_ci(dB), "signFlipNullB": sign_flip_null_ci(dB),
                       "top1_nullB": round(float(np.mean([r["top1_nullB"] for r in anch])), 4),
                       "top3_nullB": round(float(np.mean([r["top3_nullB"] for r in anch])), 4),
                       "top3_v3w_锚子样本": round(float(np.mean([r["top3_v3w"] for r in anch])), 4)}
    val_ci = seg_stat.get("val", {}).get("ciA") or [0, 0]
    b_ci = (anchor_stat or {}).get("ciB") or [0, 0]
    if b_ci[1] < 0:
        verdict = "lam_keep"                       # 显著优 Pinnacle 锚→λ 留任主概率源
    elif val_ci[1] < 0:
        verdict = "win_tcg_only_not_market"        # 只赢体彩 null=不算赢市场(v33 教训)
    else:
        verdict = "lam_demote_structure_prior"     # 双 null 都不显著优→降级结构先验调味
    store["U2_lambda"] = {
        "judge": "val段CRS log-loss配对差(V3W减null·负=V3W优)bootstrap95%CI上限+比分带命中top1/top3",
        "nullA": "矩法反解:λsum=E[T](TTG power去水)·λh二分解P(H>A)=p_h(体彩HAD去水)·同ρ同链只换λ",
        "nullB": "Pinnacle锚:pinClose去水三向·λsum仍体彩TTG(混合源)·此null赢才算赢市场",
        "segments": seg_stat, "anchor_val": anchor_stat, "verdict": verdict,
        "perMatch": rows}
    for seg, s in seg_stat.items():
        print(f"[U2|{seg}] n={s['n']} ΔA(V3W−nullA){s['meanDiffA']:+.5f} CI{s['ciA']} "
              f"null{s['signFlipNullA']} · top1 {s['top1_v3w']}vs{s['top1_nullA']} "
              f"top3 {s['top3_v3w']}vs{s['top3_nullA']} · λsum均值{s['lamSumMean']} "
              f"随机格LL{s['ll_randPick']}")
    if anchor_stat:
        print(f"[U2|锚] n={anchor_stat['n']} ΔB(V3W−nullB){anchor_stat['meanDiffB']:+.5f} "
              f"CI{anchor_stat['ciB']} · top3 {anchor_stat['top3_v3w_锚子样本']}vs{anchor_stat['top3_nullB']}")
    print(f"[U2] 终判: {verdict}")


# ---------- U5 假设层首账（描述性·无判据） ----------

def unit_u5(store):
    from collections import Counter
    found = Counter()
    by_verdict = defaultdict(lambda: {"n": 0, "dh_known": 0, "dirHit": 0})
    samples = []
    for f in sorted((ROOT / "data" / "02-results").glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:                  # noqa: BLE001
            continue
        for m in d.get("matches") or []:
            h = m.get("hypothesis")
            if not isinstance(h, dict) or not h.get("verdict"):
                continue
            v = str(h.get("verdict"))
            found[v] += 1
            dh = m.get("directionHit")
            rec = by_verdict[v]
            rec["n"] += 1
            if dh is not None:                       # 字段缺失≠未命中·分母分离
                rec["dh_known"] += 1
                if dh:
                    rec["dirHit"] += 1
            if len(samples) < 3:
                samples.append({"date": str(m.get("matchDate") or m.get("date"))[:10],
                                "match": f"{m.get('home')} v {m.get('away')}",
                                "verdict": v, "directionHit": dh,
                                "assumption": str(h.get("assumption") or h.get("text") or "")[:80]})
    stat = {v: {"n": s["n"], "dh_known": s["dh_known"],
                "方向命中": round(s["dirHit"] / s["dh_known"], 4) if s["dh_known"] else None}
            for v, s in by_verdict.items()}
    store["U5_hypothesis"] = {"judge": "描述性·verdict×directionHit（无显著性判据·预注册明说只建账）",
                              "counts": dict(found), "byVerdict": stat, "samples": samples}
    print(f"[U5] verdict 计数: {dict(found)}")
    for v, s in stat.items():
        print(f"  {v:10s} n={s['n']:3d} 方向命中{s['方向命中']}")
    print("[U5] 终判: 账本建立·前瞻积累至 n≥100 再立判据批次（预注册口径）")


# ---------- 审计 ----------

def audit():
    r = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    fails = []
    for unit, key in (("U1_fusion", "perMatch"), ("U2_lambda", "perMatch"),
                      ("U3_devig", "perMatch"), ("U4_rho", "perMatch")):
        rows = r.get(unit, {}).get(key) or []
        if not rows:
            continue
        if unit == "U1_fusion":
            rs = [x for x in rows if x["seg"] == "val" and x["lg"] != "?"]
            m_ = float(np.mean([x["rps_fused"] - x["rps_null"] for x in rs])) if rs else None
            if rs and abs(m_ - np.mean([x["rps_fused"] - x["rps_null"] for x in rs])) > 1e-9:
                fails.append("U1 复算异常")
        elif unit == "U2_lambda":
            rs = [x for x in rows if x["seg"] == "val"]
            m_ = round(float(np.mean([x["ll_v3w"] - x["ll_nullA"] for x in rs])), 5)
            stored = r["U2_lambda"]["segments"]["val"]["meanDiffA"]
            if abs(m_ - stored) > 1e-4:
                fails.append(f"U2 val 复算 {m_} ≠ 落盘 {stored}")
        elif unit == "U3_devig":
            rs = [x for x in rows if x["seg"] == "val"]
            m_ = round(float(np.mean([x["ll_k2"] - x["ll_k1"] for x in rs])), 5)
            stored = r["U3_devig"]["segments"]["val"]["meanDiff"]
            if abs(m_ - stored) > 1e-4:
                fails.append(f"U3 val 复算 {m_} ≠ 落盘 {stored}")
        elif unit == "U4_rho":
            rs = [x for x in rows if x["seg"] == "val"]
            m_ = round(float(np.mean([x["ll_band_rho"] - x["ll_band_zero"] for x in rs])), 5)
            stored = r["U4_rho"]["segments"]["val"]["meanDiff"]
            if abs(m_ - stored) > 1e-4:
                fails.append(f"U4 val 复算 {m_} ≠ 落盘 {stored}")
    print(f"[审计] {'PASS · ' + str(sum(len(r.get(u, {}).get('perMatch', [])) for u in ('U1_fusion', 'U2_lambda', 'U3_devig', 'U4_rho'))) + ' 场复算一致' if not fails else 'FAIL——' + str(fails)}")


def main():
    cmd = [a for a in sys.argv[1:] if not a.startswith("--")]
    store = json.loads(OUT_JSON.read_text(encoding="utf-8")) if OUT_JSON.exists() else {}
    store.setdefault("preReg", "fade-strategy-prereg v35Foundation.P3（判据跑前写死）")
    if "cache" in cmd:
        build_enrich()
        return
    if "--audit" in sys.argv:
        audit()
        return
    if "u5" in cmd:
        unit_u5(store)
    elif "u3" in cmd:
        unit_u3(store)
    elif "u1" in cmd or "u4" in cmd or "u2" in cmd:
        enrich = build_enrich()
        if "u1" in cmd:
            unit_u1(store, enrich)
        if "u4" in cmd:
            unit_u4(store, enrich)
        if "u2" in cmd:
            unit_u2(store, enrich)
    else:
        print(__doc__)
        return
    OUT_JSON.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"落盘: {OUT_JSON.name}")


if __name__ == "__main__":
    main()
