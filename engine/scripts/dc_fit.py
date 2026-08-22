#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""Dixon-Coles 模型拟合：从历史赛果拟合各队 attack/defense + 主场优势 + rho。

模型（Dixon & Coles 1997）：
  lambda_home = exp(attack_home + defense_away + home_adv)
  lambda_away = exp(attack_away + defense_home)
  P(x,y) = tau(x,y) * Poisson(x; lambda_home) * Poisson(y; lambda_away)
  tau 仅在 x,y<=1 生效：DC(0,0)=1-lh*la*rho, DC(0,1)=1-lh*rho, DC(1,0)=1-la*rho, DC(1,1)=1-rho
时间衰减：w(t) = exp(-xi * days_ago)，xi 默认 0.005（学术区间 0.001~0.007，v4.1 校准）
可辨识性：sum(attack)=0, sum(defense)=0（软约束惩罚实现）

数据源：
  fd CSV：engine/cache/odds_{league}_{season}.json（含 date/home/away/fthg/ftag）
  本地赛果：data/02-results/league/{league}_matches.json（espn_fetch.py history 回填，
           队名=规范ID，--source local 模式，供日职/沙特/瑞超等 fd 不覆盖联赛）

用法：
  python dc_fit.py spain-laliga 2526            # 上季西甲拟合（约380场，fd 源）
  python dc_fit.py spain-laliga 2526 --scan     # 扫描 xi 按留出 log-loss 选优
  python dc_fit.py japan --source local         # 本地赛果拟合（日职，espn 回填源）
  python dc_fit.py japan --source local --publish  # 拟合+版本化发布（models/ 存档）
"""
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

from common import log, ROOT

CACHE_DIR = ROOT / "engine" / "cache"
MODELS_DIR = CACHE_DIR / "models"
LOCAL_RESULTS_DIR = ROOT / "data" / "02-results" / "league"
DEFAULT_XI = 0.005
SCAN_GRID = [0.001, 0.003, 0.005, 0.007, 0.01, 0.02]
SOURCE_FD = "fd"
SOURCE_LOCAL = "local"


def load_matches(league: str, seasons: list[str]) -> list[dict]:
    matches = []
    for season in seasons:
        p = CACHE_DIR / f"odds_{league}_{season}.json"
        if not p.exists():
            log("dc_fit", f"缺 {p.name}（先跑 odds_fetch.py）")
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        for m in data.get("matches", []):
            if m.get("fthg") is None or m.get("date") is None:
                continue
            try:
                d = datetime.strptime(m["date"], "%d/%m/%Y").date()
                matches.append({"date": d, "home": m["home"], "away": m["away"],
                                "hg": int(m["fthg"]), "ag": int(m["ftag"])})
            except (ValueError, TypeError):
                continue
    matches.sort(key=lambda m: m["date"])
    return matches


def load_local_matches(league: str) -> list[dict]:
    """本地赛果（espn history 回档）：date=ISO，队名=规范ID。"""
    p = LOCAL_RESULTS_DIR / f"{league}_matches.json"
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    matches = []
    for m in data.get("matches", []):
        try:
            d = datetime.strptime(m["date"], "%Y-%m-%d").date()
            matches.append({"date": d, "home": m["home"], "away": m["away"],
                            "hg": int(m["hg"]), "ag": int(m["ag"])})
        except (ValueError, TypeError, KeyError):
            continue
    matches.sort(key=lambda m: m["date"])
    return matches


def dc_tau(x: int, y: int, lh: float, la: float, rho: float) -> float:
    """Dixon-Coles 1997 原始定义：rho<0 时上调 0-0/1-1、下调 1-0/0-1。"""
    if x == 0 and y == 0:
        return 1.0 - lh * la * rho
    if x == 0 and y == 1:
        return 1.0 + lh * rho
    if x == 1 and y == 0:
        return 1.0 + la * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def make_neg_ll(matches, teams, idx, weights, ref_date):
    n = len(teams)

    def neg_ll(params):
        attack = params[:n]
        defense = params[n:2 * n]
        home_adv, rho = params[2 * n], params[2 * n + 1]
        total = 0.0
        for m, w in zip(matches, weights):
            lh = math.exp(attack[idx[m["home"]]] + defense[idx[m["away"]]] + home_adv)
            la = math.exp(attack[idx[m["away"]]] + defense[idx[m["home"]]])
            x, y = m["hg"], m["ag"]
            tau = dc_tau(x, y, lh, la, rho)
            if tau <= 0:
                tau = 1e-10
            ll = (math.log(tau) + x * math.log(lh) - lh - math.lgamma(x + 1)
                  + y * math.log(la) - la - math.lgamma(y + 1))
            total += w * ll
        # 软约束：sum(attack)=sum(defense)=0
        total -= 10.0 * (attack.sum() ** 2 + defense.sum() ** 2)
        return -total

    return neg_ll


def fit(matches: list[dict], xi: float):
    teams = sorted({m["home"] for m in matches} | {m["away"] for m in matches})
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    ref = matches[-1]["date"]
    weights = [math.exp(-xi * (ref - m["date"]).days) for m in matches]
    params0 = np.zeros(2 * n + 2)
    params0[2 * n] = 0.25   # home_adv 初值
    params0[2 * n + 1] = -0.05  # rho 初值
    # 用加权均值初始化 attack/defense（加速收敛）
    gf = {t: 0.0 for t in teams}
    ga = {t: 0.0 for t in teams}
    wsum = {t: 0.0 for t in teams}
    for m, w in zip(matches, weights):
        gf[m["home"]] += w * m["hg"]
        gf[m["away"]] += w * m["ag"]
        ga[m["home"]] += w * m["ag"]
        ga[m["away"]] += w * m["hg"]
        wsum[m["home"]] += w
        wsum[m["away"]] += w
    lg = sum(w for _, w in zip(matches, weights)) and math.log(
        sum(gf.values()) / max(sum(wsum.values()), 1e-9))
    for t in teams:
        params0[idx[t]] = math.log((gf[t] + 0.5) / (wsum[t] + 1e-9)) - lg
        params0[n + idx[t]] = -0.3 * math.log((ga[t] + 0.5) / (gf[t] + 0.5))
    params0[:n] -= params0[:n].mean()
    params0[n:2 * n] -= params0[n:2 * n].mean()

    neg_ll = make_neg_ll(matches, teams, idx, weights, ref)
    bounds = [(None, None)] * (2 * n) + [(0.0, 0.8), (-0.2, 0.1)]
    res = minimize(neg_ll, params0, method="L-BFGS-B", bounds=bounds,
                   options={"maxiter": 500, "maxfun": 20000})
    attack = res.x[:n] - res.x[:n].mean()
    defense = res.x[n:2 * n] - res.x[n:2 * n].mean()
    return teams, attack, defense, float(res.x[2 * n]), float(res.x[2 * n + 1]), res.fun


def holdout_logloss(matches, xi, split=0.8):
    """留出验证 log-loss，用于 xi 扫描。"""
    cut = int(len(matches) * split)
    train, test = matches[:cut], matches[cut:]
    if len(test) < 10:
        return None
    teams, attack, defense, home_adv, rho, _ = fit(train, xi)
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    ll, cnt = 0.0, 0
    for m in test:
        if m["home"] not in idx or m["away"] not in idx:
            continue
        lh = math.exp(attack[idx[m["home"]]] + defense[idx[m["away"]]] + home_adv)
        la = math.exp(attack[idx[m["away"]]] + defense[idx[m["home"]]])
        p = np.zeros((7, 7))
        for x in range(7):
            for y in range(7):
                pm = math.exp(-lh) * lh ** x / math.factorial(x) * math.exp(-la) * la ** y / math.factorial(y)
                p[x, y] = max(pm * dc_tau(x, y, lh, la, rho), 1e-12)
        p /= p.sum()
        p1 = p.sum(axis=1)
        p2 = p.sum(axis=0)
        ph, pd_, pa = float(p[:3, :3].sum() - 0), 0.0, float(p2.sum())
        ph = float(sum(p[i, j] for i in range(7) for j in range(7) if i > j))
        pd_ = float(np.trace(p))
        pa = float(sum(p[i, j] for i in range(7) for j in range(7) if i < j))
        outcome = 0 if m["hg"] > m["ag"] else (1 if m["hg"] == m["ag"] else 2)
        probs = [ph, pd_, pa]
        ll += -math.log(max(probs[outcome], 1e-12))
        cnt += 1
    return ll / cnt if cnt else None


def publish_version(out: dict, holdout: float | None, source: str, reason: str) -> str:
    """版本化发布：models/{league}_dc_v{n}.json + .meta.json + latest.json 路由更新。

    发布门槛：新版本 holdout log-loss 不劣于当前生效版（差距 >2% 拒绝）；首版直接发布。
    返回发布说明（一行）。
    """
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    league = out["league"]
    latest_path = MODELS_DIR / "latest.json"
    latest = json.loads(latest_path.read_text(encoding="utf-8")) if latest_path.exists() else {}
    cur_ver = latest.get(league)
    cur_meta = None
    if cur_ver:
        mp = MODELS_DIR / f"{league}_dc_v{cur_ver}.meta.json"
        if mp.exists():
            cur_meta = json.loads(mp.read_text(encoding="utf-8"))
    if cur_meta and holdout is not None and cur_meta.get("holdoutLogloss") is not None:
        if holdout > cur_meta["holdoutLogloss"] * 1.02:
            return f"拒绝发布：holdout {holdout:.4f} 劣于当前 v{cur_ver} 的 {cur_meta['holdoutLogloss']:.4f}"
    if cur_meta and out.get("matchesUsed") == cur_meta.get("nTrain") and out.get("dateRange") == cur_meta.get("dateRange"):
        return f"跳过发布：训练集与 v{cur_ver} 完全相同（{out['matchesUsed']} 场），无新数据"
    new_ver = (cur_ver or 0) + 1
    out = {**out, "version": new_ver, "source": source}
    (MODELS_DIR / f"{league}_dc_v{new_ver}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    meta = {
        "league": league, "version": new_ver, "source": source,
        "nTrain": out["matchesUsed"], "dateRange": out["dateRange"], "xi": out["xi"],
        "holdoutLogloss": round(holdout, 4) if holdout is not None else None,
        "replacedVersion": cur_ver, "replacedBy": None, "reason": reason,
        "createdBy": "sszhang pipeline", "createdAt": date.today().isoformat(),
    }
    (MODELS_DIR / f"{league}_dc_v{new_ver}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if cur_ver and cur_meta:
        old_meta = {**cur_meta, "replacedBy": new_ver}
        (MODELS_DIR / f"{league}_dc_v{cur_ver}.meta.json").write_text(
            json.dumps(old_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    latest[league] = new_ver
    latest_path.write_text(json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return f"发布 v{new_ver}（holdout={meta['holdoutLogloss']}，替代 v{cur_ver}）"


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    scan = "--scan" in sys.argv
    auto = "--auto" in sys.argv
    source = SOURCE_LOCAL if "--source" in sys.argv and "local" in sys.argv else SOURCE_FD
    publish = "--publish" in sys.argv
    if not args:
        log("dc_fit", "用法: python dc_fit.py <league> [season[,s2]] [--scan] [--auto] [--source local] [--publish]")
        return
    league = args[0]
    seasons = args[1].split(",") if len(args) > 1 else ["2627"]

    # --auto：缓存新鲜则跳过（新增场次 <5）
    dest = CACHE_DIR / f"{league}_dc.json"
    if auto and dest.exists() and source == SOURCE_FD:
        cached = json.loads(dest.read_text(encoding="utf-8"))
        n_now = len(load_matches(league, seasons))
        n_used = cached.get("matchesUsed", 0)
        if cached.get("seasons") == seasons and n_now - n_used < 5:
            log("dc_fit", f"缓存新鲜（{n_used}→{n_now} 场，新增 <5），跳过拟合 → {dest.name}")
            return

    if source == SOURCE_LOCAL:
        matches = load_local_matches(league)
        seasons = ["local"]
        if not matches:
            log("dc_fit", f"无本地赛果 {LOCAL_RESULTS_DIR / f'{league}_matches.json'}（先 espn_fetch.py history）")
            return
    else:
        matches = load_matches(league, seasons)
    if len(matches) < 30:
        log("dc_fit", f"仅 {len(matches)} 场（<30），不足以拟合")
        return
    log("dc_fit", f"{league} [{source}] {seasons}: {len(matches)} 场（{matches[0]['date']} ~ {matches[-1]['date']}）")

    xi = DEFAULT_XI
    holdout = None
    if scan:
        log("dc_fit", "xi 扫描（80/20 留出 log-loss）：")
        best = (None, None)
        for cand in SCAN_GRID:
            ll = holdout_logloss(matches, cand)
            log("dc_fit", f"  xi={cand}: log-loss={ll if ll is None else round(ll, 4)}")
            if ll is not None and (best[1] is None or ll < best[1]):
                best = (cand, ll)
        if best[0] is not None:
            xi = best[0]
        log("dc_fit", f"选定 xi={xi}")

    teams, attack, defense, home_adv, rho, fun = fit(matches, xi)
    out = {
        "league": league, "seasons": seasons, "xi": xi, "homeAdv": round(home_adv, 4), "rho": round(rho, 4),
        "matchesUsed": len(matches),
        "dateRange": [str(matches[0]["date"]), str(matches[-1]["date"])],
        "lastFit": date.today().isoformat(),
        "teams": {t: {"attack": round(float(attack[i]), 4), "defense": round(float(defense[i]), 4)}
                  for t, i in idx_map(teams).items()},
    }
    holdout = holdout_logloss(matches, xi)
    # 服务路径不变：{league}_dc.json 仍是 dc_predict 的读取点
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    top = sorted(out["teams"].items(), key=lambda kv: -kv[1]["attack"])[:5]
    log("dc_fit", f"完成 → {dest.name}（home_adv={home_adv:.3f}, rho={rho:.3f}, holdout={holdout and round(holdout, 4)}）")
    log("dc_fit", "攻击力TOP5: " + ", ".join(f"{t} {v['attack']:+.2f}" for t, v in top))
    if publish:
        log("dc_fit", publish_version(out, holdout, source, reason=f"{len(matches)}场 {source} 源拟合"))


def idx_map(teams):
    return {t: i for i, t in enumerate(teams)}


if __name__ == "__main__":
    main()
