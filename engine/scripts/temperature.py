#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""池级温度拟合 + 8 组合消融（fd 8联赛×4季，赛季内 60/40 split 无前视）。

拟合：每联赛每赛季前 60% 场 fit() 拟 DC 参数 → 后 40% 场评估各池 logloss；
温度 T 网格 [0.60, 1.60] step 0.05 最小化评估集 NLL。bootstrap 1000 次 CI。
消融：2^3=8 组合（重标定[用 PPC 收盘 devig + fusion a0.4/b1.0 融合三向] × 温度 × hafu升级）。
产出 engine/cache/temperature.json + data/04-summaries/pool_ablation.json。
设计: docs/2026-08-26-pool-coverage-design.html §5 §7   开发者 sszhang
"""
import json
import math
import random
import sys
from datetime import date, datetime

import numpy as np

from band_calibration import DIVS, SEASONS, fetch_rows
from dc_fit import fit
from dc_predict import (score_matrix, ttg_dist, hafu_approx, devig, fuse,
                        reweight_matrix, reweight_hafu, temper, load_half_params)
from common import ROOT, log

CACHE = ROOT / "engine" / "cache" / "temperature.json"
ABLATION = ROOT / "data" / "04-summaries" / "pool_ablation.json"
SPLIT = 0.6
T_GRID = [round(0.60 + 0.05 * i, 2) for i in range(21)]   # 0.60~1.60
FUSION_DEFAULT = {"a": 0.4, "b": 1.0}
BOOTSTRAP_N = 1000
BOOTSTRAP_SEED = 42
MIN_EVAL_N = 50
DEFAULT_S = 0.45
DEFAULT_RHO_HALF = 0.0
XI = 0.005
# 消融组合键：(use_adj, use_T, use_hafu)
COMBO_KEYS = [a + b for a in "hda" for b in "hda"]  # HAFU 9 键（有序）


# ── 日期/行解析 ────────────────────────────────────────────

def parse_date(s: str) -> date:
    """fd Date 字段：DD/MM/YY 或 DD/MM/YYYY，容错两种格式。"""
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


def _pinnacle_odds(row: dict) -> tuple[float, float, float] | None:
    """取 Pinnacle 收盘三向：优先 PPCH/D/A（旧名），回退 PSCH/D/A（新名）。"""
    for h_key, d_key, a_key in (("PPCH", "PPCD", "PPCA"),
                                 ("PSCH", "PSCD", "PSCA")):
        try:
            return float(row[h_key]), float(row[d_key]), float(row[a_key])
        except (KeyError, ValueError, TypeError):
            continue
    return None


def parse_rows(season: str, div: str) -> tuple[list, int]:
    """fd CSV → [{date,home,away,hg,ag,hth,hta,odds}]；返回 (rows, skipped)。"""
    out = []
    skipped = 0
    for r in fetch_rows(season, div):
        try:
            d = parse_date(r["Date"])
            hg, ag = int(r["FTHG"]), int(r["FTAG"])
            hth, hta = int(r["HTHG"]), int(r["HTAG"])
            ppc = _pinnacle_odds(r)
            if ppc is None:
                skipped += 1
                continue
            out.append({
                "date": d, "home": r["HomeTeam"], "away": r["AwayTeam"],
                "hg": hg, "ag": ag, "hth": hth, "hta": hta,
                "odds": list(ppc),
            })
        except (KeyError, ValueError, TypeError):
            skipped += 1
    return sorted(out, key=lambda m: m["date"]), skipped


# ── 概率计算工具 ──────────────────────────────────────────

def three_way(p: np.ndarray) -> list[float]:
    """7×7 矩阵 → [p_home, p_draw, p_away]。"""
    ph = float(sum(p[i, j] for i in range(7) for j in range(7) if i > j))
    pd = float(np.trace(p))
    pa = 1.0 - ph - pd
    return [ph, pd, pa]


def make_target(matrix: np.ndarray, odds: list[float], fusion: dict) -> list[float]:
    """重标定 target = fuse(p_dc, devig(PPC), a, b)（设计 §4 护栏③）。"""
    p_dc = three_way(matrix)
    p_mkt = devig(odds)
    return fuse(p_dc, p_mkt, fusion["a"], fusion["b"])


def hafu_obs_key(hth: int, hta: int, hg: int, ag: int) -> str:
    """HAFU 观测键：sign(hth-hta) + sign(hg-ag) → 两字母键。"""
    ht = "h" if hth > hta else ("d" if hth == hta else "a")
    ft = "h" if hg > ag else ("d" if hg == ag else "a")
    return ht + ft


def crs_probs_50(matrix: np.ndarray) -> list[float]:
    """矩阵 → 50 维向量（49 格 + 其他档）。
    矩阵已归一→其他档=1-域内和≈0，用 floor 1e-12 避免 log(0)。"""
    flat = [max(float(matrix[i, j]), 1e-12) for i in range(7) for j in range(7)]
    domain_sum = sum(flat)
    other = max(1.0 - domain_sum, 1e-12)
    return flat + [other]


# ── 预计算（单场） ────────────────────────────────────────

def precompute(rec: dict, fusion: dict) -> dict:
    """单场预计算：矩阵/重标定/HAFU(2种参数×2种标定=4变体) → 各池基础概率。
    返回结构支持任意 (use_adj, use_hafu, use_T, T) 组合的快速评估。"""
    lh, la, rho = rec["lh"], rec["la"], rec["rho"]
    s_lg, rho_half = rec["s_lg"], rec["rho_half"]
    m = rec["match"]

    raw_mat = score_matrix(lh, la, rho)
    target = make_target(raw_mat, m["odds"], fusion)
    adj_mat = reweight_matrix(raw_mat, target)

    hg, ag = m["hg"], m["ag"]
    crs_obs = hg * 7 + ag if 0 <= hg < 7 and 0 <= ag < 7 else 49
    ttg_obs = min(hg + ag, 7)
    hafu_key = hafu_obs_key(m["hth"], m["hta"], hg, ag)
    hafu_obs = COMBO_KEYS.index(hafu_key) if hafu_key in COMBO_KEYS else -1

    # HAFU 4 变体：(adj on/off × hafu upgrade on/off)
    hafu_base = hafu_approx(lh, la, DEFAULT_S, DEFAULT_RHO_HALF)
    hafu_upg = hafu_approx(lh, la, s_lg, rho_half)
    hafu_base_adj = reweight_hafu(hafu_base, target)
    hafu_upg_adj = reweight_hafu(hafu_upg, target)

    return {
        "crs_base": crs_probs_50(raw_mat),
        "crs_adj": crs_probs_50(adj_mat),
        "crs_obs": crs_obs,
        "ttg_base": ttg_dist(raw_mat),
        "ttg_adj": ttg_dist(adj_mat),
        "ttg_obs": ttg_obs,
        "hafu_base_raw": list(hafu_base.values()),
        "hafu_base_adj": list(hafu_base_adj.values()),
        "hafu_upg_raw": list(hafu_upg.values()),
        "hafu_upg_adj": list(hafu_upg_adj.values()),
        "hafu_obs": hafu_obs,
    }


# ── 池级 log-loss ─────────────────────────────────────────

def pool_ll(probs: list[float], obs_idx: int, t: float) -> float:
    """单池 -log p(观测)：probs → temper(t) → 查 obs_idx。"""
    if obs_idx < 0 or obs_idx >= len(probs):
        return -math.log(1e-12)
    tempered = temper(probs, t) if abs(t - 1.0) > 1e-12 else list(probs)
    return -math.log(max(tempered[obs_idx], 1e-12))


def pick_probs(pre: dict, pool: str, use_adj: bool, use_hafu: bool) -> tuple[list[float], int]:
    """从预计算结构选取对应开关的池概率向量 + 观测索引。"""
    if pool == "crs":
        return (pre["crs_adj"] if use_adj else pre["crs_base"]), pre["crs_obs"]
    if pool == "ttg":
        return (pre["ttg_adj"] if use_adj else pre["ttg_base"]), pre["ttg_obs"]
    # hafu
    if use_adj and use_hafu:
        return pre["hafu_upg_adj"], pre["hafu_obs"]
    if use_adj:
        return pre["hafu_base_adj"], pre["hafu_obs"]
    if use_hafu:
        return pre["hafu_upg_raw"], pre["hafu_obs"]
    return pre["hafu_base_raw"], pre["hafu_obs"]


def eval_combo(pre: dict, use_adj: bool, use_t: bool, use_hafu: bool,
               t_crs: float, t_ttg: float, t_hafu: float) -> dict[str, float]:
    """8 组合之一：从预计算查概率 + 温度 → 各池 -log p。"""
    result = {}
    for pool, t_val in (("crs", t_crs), ("ttg", t_ttg), ("hafu", t_hafu)):
        probs, obs = pick_probs(pre, pool, use_adj, use_hafu)
        t_applied = t_val if use_t else 1.0
        result[pool] = pool_ll(probs, obs, t_applied)
    return result


# ── 温度拟合（单池） ──────────────────────────────────────

def fit_pool_t(all_pre: list[dict], pool: str) -> dict:
    """单池 T 网格搜索 + bootstrap CI。
    全关基线拟合（use_adj=F, use_hafu=F），确保消融 baseline ≡ ll_before。"""
    n = len(all_pre)
    per_match_t1 = []
    t_nll = {t: 0.0 for t in T_GRID}

    for pre in all_pre:
        probs, obs = pick_probs(pre, pool, use_adj=False, use_hafu=False)
        ll_t1 = pool_ll(probs, obs, 1.0)
        per_match_t1.append(ll_t1)
        for t in T_GRID:
            t_nll[t] += pool_ll(probs, obs, t)

    best_t = min(T_GRID, key=lambda t: t_nll[t])
    ll_before = sum(per_match_t1)
    ll_after = t_nll[best_t]

    # Per-match ll at best T（bootstrap 用）
    per_match_best = []
    for pre in all_pre:
        probs, obs = pick_probs(pre, pool, use_adj=False, use_hafu=False)
        per_match_best.append(pool_ll(probs, obs, best_t))

    # Bootstrap CI of improvement（ll_t1 - ll_best 的均值）
    improvements = [t1 - b for t1, b in zip(per_match_t1, per_match_best)]
    rng = random.Random(BOOTSTRAP_SEED)
    boot_means = sorted(
        sum(rng.choices(improvements, k=len(improvements))) / len(improvements)
        for _ in range(BOOTSTRAP_N)
    )
    ci_lo = boot_means[int(0.025 * BOOTSTRAP_N)]
    ci_hi = boot_means[int(0.975 * BOOTSTRAP_N)]

    return {
        "T": best_t, "n_eval": n,
        "ll_before": round(ll_before, 4), "ll_after": round(ll_after, 4),
        "ci": [round(ci_lo, 6), round(ci_hi, 6)],
        "improvement": round(ll_before - ll_after, 4),
    }


# ── --check 只读状态断言 ────────────────────────────────────

def _check_status() -> int:
    """只读 temperature.json，打印各池状态。exit 0=就绪/缺文件不阻断，1=结构损坏。"""
    if not CACHE.exists():
        print("[temperature] temperature.json 不存在（run.py learn 未跑过温度拟合，消费端按 T=1 零破坏）")
        return 0
    try:
        data = json.loads(CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[temperature] temperature.json 读取失败: {e}")
        return 1
    pools = data.get("pools")
    if not isinstance(pools, dict):
        print("[temperature] 结构损坏：缺 pools 字段")
        return 1
    missing = []
    for pool in ("crs", "ttg", "hafu"):
        p = pools.get(pool)
        if not isinstance(p, dict):
            missing.append(pool)
            continue
        for field in ("T", "enabled", "ci"):
            if field not in p:
                missing.append(f"{pool}.{field}")
    if missing:
        print(f"[temperature] 结构损坏：缺 {', '.join(missing)}")
        return 1
    print(f"[temperature] fittedAt: {data.get('fittedAt', '?')}")
    for pool in ("crs", "ttg", "hafu"):
        p = pools[pool]
        ci = p["ci"]
        print(f"  {pool}: T={p['T']:.2f} enabled={p['enabled']} CI=[{ci[0]:.4f}, {ci[1]:.4f}]")
    print("[temperature] OK")
    return 0


# ── 主函数 ────────────────────────────────────────────────

def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        sys.exit(_check_status())
    # Fusion 系数：优先读文件，缺则默认
    fusion = FUSION_DEFAULT.copy()
    fusion_path = ROOT / "engine" / "cache" / "fusion.json"
    if fusion_path.exists():
        fusion.update(json.loads(fusion_path.read_text(encoding="utf-8")))
    log("temperature", f"融合系数: a={fusion['a']}, b={fusion['b']}")

    # 遍历 8联赛×4季 → fit + 收集预计算
    all_pre: list[dict] = []
    total_skipped = 0
    combos_used = 0

    for season in SEASONS:
        for div, league in DIVS.items():
            rows, skipped = parse_rows(season, div)
            total_skipped += skipped
            if len(rows) < 80:
                log("temperature", f"{div}/{season}: 仅 {len(rows)} 场，跳过")
                continue

            cut = int(len(rows) * SPLIT)
            train_rows = rows[:cut]
            eval_rows = rows[cut:]

            if len(eval_rows) < MIN_EVAL_N:
                log("temperature",
                    f"{div}/{season}: eval {len(eval_rows)} 场 < {MIN_EVAL_N}，跳过")
                continue

            # fit 格式：{home, away, date(date对象), hg, ag}
            train_matches = [{"date": r["date"], "home": r["home"], "away": r["away"],
                              "hg": r["hg"], "ag": r["ag"]} for r in train_rows]

            try:
                teams, attack, defense, home_adv, rho, neg_ll = fit(
                    train_matches, XI)
            except Exception as e:
                log("temperature", f"{div}/{season} fit 失败: {e}")
                continue

            teams_idx = {t: i for i, t in enumerate(teams)}
            s_lg, rho_half = load_half_params(league)
            combos_used += 1

            n_eval_used = 0
            for r in eval_rows:
                h_idx = teams_idx.get(r["home"])
                a_idx = teams_idx.get(r["away"])
                if h_idx is None or a_idx is None:
                    continue
                lh = math.exp(attack[h_idx] + defense[a_idx] + home_adv)
                la = math.exp(attack[a_idx] + defense[h_idx])
                rec = {
                    "lh": lh, "la": la, "rho": rho,
                    "s_lg": s_lg, "rho_half": rho_half,
                    "match": r,
                }
                all_pre.append(precompute(rec, fusion))
                n_eval_used += 1

            log("temperature",
                f"{div}/{season}: train={len(train_rows)} eval={n_eval_used} "
                f"teams={len(teams)} rho={rho:.3f} home_adv={home_adv:.3f}")

    log("temperature",
        f"总评估集: {len(all_pre)} 场 "
        f"({combos_used} 联赛×赛季, 跳过 {total_skipped} 行缺列)")

    if len(all_pre) < MIN_EVAL_N:
        log("temperature",
            f"评估场 {len(all_pre)} < {MIN_EVAL_N}，不足以拟合温度")
        return

    # ── 拟合各池温度（全关基线：use_adj=F, use_hafu=F） ──
    pool_results = {}
    for pool in ("crs", "ttg", "hafu"):
        pool_results[pool] = fit_pool_t(all_pre, pool)
        r = pool_results[pool]
        log("temperature",
            f"{pool}: T={r['T']:.2f} n={r['n_eval']} "
            f"ll_before={r['ll_before']} ll_after={r['ll_after']} "
            f"improvement={r['improvement']} CI={r['ci']}")

    # ── enabled 判定 ──
    for pool in ("crs", "ttg", "hafu"):
        r = pool_results[pool]
        t_val = r["T"]
        ci_lo = r["ci"][0]

        if pool == "hafu":
            r["enabled"] = False
            r["reason"] = ("HAFU 暂不启用（设计 §5："
                           "线上 HAFU 无历史回溯，前瞻监控攒样本）")
        elif abs(t_val - 1.0) > 0.6:
            r["enabled"] = False
            r["reason"] = f"|T-1|={abs(t_val - 1):.2f}>0.6 超出安全范围"
        elif ci_lo <= 0:
            r["enabled"] = False
            r["reason"] = f"CI 下界={ci_lo:.6f}<=0 改善不确证"
        else:
            r["enabled"] = True
            r["reason"] = "CI 下界>0 且 |T-1|<=0.6"

    # ── 落盘 temperature.json ──
    temp_out = {
        "fittedAt": str(date.today()),
        "source": (f"fd {len(SEASONS)}季x{len(DIVS)}联赛 "
                   f"walk-forward {SPLIT:.0%}/{1 - SPLIT:.0%} split"),
        "tGrid": T_GRID,
        "bootstrapN": BOOTSTRAP_N,
        "pools": {
            pool: {
                "T": pool_results[pool]["T"],
                "enabled": pool_results[pool]["enabled"],
                "n_eval": pool_results[pool]["n_eval"],
                "ll_before": pool_results[pool]["ll_before"],
                "ll_after": pool_results[pool]["ll_after"],
                "ci": pool_results[pool]["ci"],
                "improvement": pool_results[pool]["improvement"],
                "reason": pool_results[pool]["reason"],
            }
            for pool in ("crs", "ttg", "hafu")
        },
    }
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(temp_out, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
    log("temperature", f"→ {CACHE}")

    # ── 消融 8 组合 × 3 池 ──
    ablation_combos = {}
    for use_adj in (False, True):
        for use_t in (False, True):
            for use_hafu in (False, True):
                key = f"adj{int(use_adj)}_T{int(use_t)}_hafu{int(use_hafu)}"
                t_crs = pool_results["crs"]["T"] if use_t else 1.0
                t_ttg = pool_results["ttg"]["T"] if use_t else 1.0
                t_hafu = pool_results["hafu"]["T"] if use_t else 1.0

                totals = {"crs": 0.0, "ttg": 0.0, "hafu": 0.0}
                for pre in all_pre:
                    ll = eval_combo(pre, use_adj, use_t, use_hafu,
                                    t_crs, t_ttg, t_hafu)
                    for pool in ("crs", "ttg", "hafu"):
                        totals[pool] += ll[pool]

                ablation_combos[key] = {
                    "use_adj": use_adj, "use_T": use_t, "use_hafu": use_hafu,
                    "n": len(all_pre),
                    **{p: round(totals[p], 4) for p in ("crs", "ttg", "hafu")},
                }

    # ── 基线一致性核对 ──
    baseline = ablation_combos["adj0_T0_hafu0"]
    all_consistent = True
    for pool in ("crs", "ttg", "hafu"):
        fit_ll = pool_results[pool]["ll_before"]
        abl_ll = baseline[pool]
        diff = abs(fit_ll - abl_ll)
        status = "OK" if diff < 0.5 else "WARN"
        if diff >= 0.5:
            all_consistent = False
        log("temperature",
            f"{pool} 基线{status}: fit_pool_T={fit_ll}, "
            f"ablation={abl_ll}, diff={diff:.4f}")

    ablation_out = {
        "fittedAt": str(date.today()),
        "n_eval": len(all_pre),
        "fittedT": {p: pool_results[p]["T"] for p in ("crs", "ttg", "hafu")},
        "combinations": ablation_combos,
    }
    ABLATION.parent.mkdir(parents=True, exist_ok=True)
    ABLATION.write_text(json.dumps(ablation_out, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    log("temperature", f"→ {ABLATION}")

    # ── 断言 ──
    for pool in ("crs", "ttg", "hafu"):
        t_val = pool_results[pool]["T"]
        assert abs(t_val - 1) <= 0.6 + 1e-9, (
            f"{pool} |T-1|={abs(t_val - 1):.2f} > 0.6")
    log("temperature", "断言通过: 各池 |T-1| <= 0.6")
    if not all_consistent:
        log("temperature", "WARN: 基线一致性有偏差（见上方 WARN 行）")

    # ── 控制台摘要 ──
    print("\n=== 池级温度拟合摘要 ===")
    print(f"评估集: {len(all_pre)} 场 ({combos_used} 联赛x赛季)")
    print(f"T 网格: [{T_GRID[0]}, {T_GRID[-1]}] step {T_GRID[1] - T_GRID[0]}")
    print()
    for pool in ("crs", "ttg", "hafu"):
        r = pool_results[pool]
        status = "ENABLED" if r["enabled"] else "DISABLED"
        print(f"  {pool:>4}: T={r['T']:.2f} [{status}] "
              f"n={r['n_eval']} Delta-ll={r['improvement']:.2f} "
              f"CI=[{r['ci'][0]:.6f}, {r['ci'][1]:.6f}]")
        print(f"         reason: {r['reason']}")
    print()

    # 消融矩阵
    print("=== 消融矩阵 (NLL) ===")
    header = f"{'combo':<20} {'CRS':>10} {'TTG':>10} {'HAFU':>10}"
    print(header)
    print("-" * len(header))
    for key, combo in sorted(ablation_combos.items()):
        print(f"{key:<20} {combo['crs']:>10.2f} {combo['ttg']:>10.2f} "
              f"{combo['hafu']:>10.2f}")


if __name__ == "__main__":
    main()
