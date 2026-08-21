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

数据源：engine/cache/odds_{league}_{season}.json（fd CSV 转档，含 date/home/away/fthg/ftag）

用法：
  python dc_fit.py spain-laliga 2526            # 上季西甲拟合（约380场）
  python dc_fit.py spain-laliga 2526 --scan     # 扫描 xi 按留出 log-loss 选优
  python dc_fit.py spain-laliga 2526,2627       # 多赛季合并拟合
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
DEFAULT_XI = 0.005
SCAN_GRID = [0.001, 0.003, 0.005, 0.007, 0.01, 0.02]


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


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    scan = "--scan" in sys.argv
    auto = "--auto" in sys.argv
    if not args:
        log("dc_fit", "用法: python dc_fit.py <league> <season[,season2...]> [--scan] [--auto]")
        return
    league = args[0]
    seasons = args[1].split(",") if len(args) > 1 else ["2627"]

    # --auto：缓存新鲜则跳过（新增场次 <5）
    dest = CACHE_DIR / f"{league}_dc.json"
    if auto and dest.exists():
        cached = json.loads(dest.read_text(encoding="utf-8"))
        n_now = len(load_matches(league, seasons))
        n_used = cached.get("matchesUsed", 0)
        if cached.get("seasons") == seasons and n_now - n_used < 5:
            log("dc_fit", f"缓存新鲜（{n_used}→{n_now} 场，新增 <5），跳过拟合 → {dest.name}")
            return

    matches = load_matches(league, seasons)
    if len(matches) < 30:
        log("dc_fit", f"仅 {len(matches)} 场（<30），不足以拟合")
        return
    log("dc_fit", f"{league} {seasons}: {len(matches)} 场（{matches[0]['date']} ~ {matches[-1]['date']}）")

    xi = DEFAULT_XI
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
    dest = CACHE_DIR / f"{league}_dc.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    top = sorted(out["teams"].items(), key=lambda kv: -kv[1]["attack"])[:5]
    log("dc_fit", f"完成 → {dest.name}（home_adv={home_adv:.3f}, rho={rho:.3f}）")
    log("dc_fit", "攻击力TOP5: " + ", ".join(f"{t} {v['attack']:+.2f}" for t, v in top))


def idx_map(teams):
    return {t: i for i, t in enumerate(teams)}


if __name__ == "__main__":
    main()
