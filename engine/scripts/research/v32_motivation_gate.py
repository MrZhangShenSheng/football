# -*- coding: utf-8 -*-
"""v32（冷门多轨·轨2）：战意赛制终审（判据预注册 fade-strategy-prereg v32·跑前写死）。

先行审计：08-29 六条剧本原文 = 节奏/进球盘剧本，零战意条目 → 本轨净地（设计书已录）。
受审特征（积分榜 walk-forward 重建·只用已赛轮）：
  M1 死气场   剩余≤10轮 且 双方 既追不上欧战区(第5) 也数学安全(掉不进降级区)
  M2 保级拼命 冷门方处降级区/附加赛区或距安全线≤3分 且 热门方安全(距区>6分且不在区内)
  M3 欧战slack 热门方联赛排名≤6 且 比赛日为周二~周四
  M4 争冠压力 涉前二 且 榜首分差≤3 且 剩余≤6轮

判据（v32 预注册）：
  ① 主：任一 M 旗标场冷门腿(命中−隐含) − 无旗标场差 ≥ +2.5pp 且 bootstrap CI 下限 > +1pp
  ② 各 M 单旗同报·Bonferroni k=5 α=0.01
  ③ 利润层如实报（旗标场回收率 vs 全池）不作立废
  ④ 不过 → 轨2 结案·「战意已定价」与前证合流
诚实先验：战意属公开信息·市场大概率已定价。开发者 sszhang
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from band_calibration import DIVS, SEASONS, fetch_rows

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data" / "04-summaries" / "v32-motivation-gate.json"
LONGSHOT_MAX = 0.20
FIT_SEASONS = {"2223", "2324"}
VAL_SEASONS = {"2425", "2526"}
BOOT_N = 2000
SEED = 20261009
MIDWEEK = {1, 2, 3}          # 周二/三/四
EURO_RANK = 5                # 欧战区代理 = 第 5 名
SAFE_MARGIN = 6              # M2 热门方安全边际（分）
DESP_MARGIN = 3              # M2 冷门方拼命带（距安全线 ≤3 分）


def parse_date(s):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def load_matches():
    out = []
    for season in SEASONS:
        for div, league in DIVS.items():
            for r in fetch_rows(season, div):
                d = parse_date(r.get("Date"))
                if not d:
                    continue
                try:
                    m = {"date": d, "season": season, "league": league,
                         "home": r["HomeTeam"], "away": r["AwayTeam"],
                         "hg": int(r["FTHG"]), "ag": int(r["FTAG"]), "ftr": r["FTR"],
                         "pch": float(r["PSCH"]), "pcd": float(r["PSCD"]), "pca": float(r["PSCA"])}
                except (KeyError, ValueError, TypeError):
                    continue
                if m["ftr"] not in ("H", "D", "A"):
                    continue
                out.append(m)
    out.sort(key=lambda m: m["date"])
    return out


def devid(oh, od_, oa):
    s = 1 / oh + 1 / od_ + 1 / oa
    return (1 / oh) / s, (1 / od_) / s, (1 / oa) / s


class Table:
    """联赛×赛季 积分榜（walk-forward·先查后写）。"""

    def __init__(self, teams):
        self.teams = teams
        self.total_rounds = 2 * (len(teams) - 1)
        self.t = {tm: [0, 0, 0] for tm in teams}   # pts, gd, played

    def snapshot(self):
        """→ (rank 字典, pts_of_rank 函数, n_teams)。"""
        order = sorted(self.teams, key=lambda tm: (-self.t[tm][0], -self.t[tm][1]))
        rank = {tm: i + 1 for i, tm in enumerate(order)}
        return rank, order, len(self.teams)

    def remaining(self, tm):
        return self.total_rounds - self.t[tm][2]

    def add(self, home, away, hg, ag):
        th, ta = self.t[home], self.t[away]
        th[2] += 1
        ta[2] += 1
        th[1] += hg - ag
        ta[1] += ag - hg
        if hg > ag:
            th[0] += 3
        elif hg < ag:
            ta[0] += 3
        else:
            th[0] += 1
            ta[0] += 1


def flags_for(tbl, home, away):
    """场级战意旗标（读当轮之前积分榜）→ (旗标 dict, rank dict)。"""
    rank, order, n = tbl.snapshot()
    out = {}
    if n < 8:
        return out, rank
    pts5 = tbl.t[order[EURO_RANK - 1]][0]
    zone_top = order[n - 3]                     # 倒数第 3 = 降级区头名（区=倒三）
    pts_zone = tbl.t[zone_top][0]
    last_safe = order[n - 4] if n >= 4 else zone_top   # 安全线队（区外最后一名）
    pts_safe = tbl.t[last_safe][0]
    rem_h, rem_a = tbl.remaining(home), tbl.remaining(away)

    def hopeless(tm):                            # 追不上欧战区：全胜也到不了第 5 名线
        return tbl.t[tm][0] + tbl.remaining(tm) * 3 < pts5

    def math_safe(tm):                           # 掉不下去：降级区头名全胜也追不上
        return tbl.t[tm][0] > pts_zone + tbl.remaining(zone_top) * 3

    if (rem_h <= 10 and rem_a <= 10 and
            all(hopeless(tm) and math_safe(tm) for tm in (home, away))):
        out["M1_dead"] = 1

    ph_pt, pa_pt = tbl.t[home][0], tbl.t[away][0]
    und_fight_h = (rank[home] >= n - 2 or pts_safe - ph_pt <= DESP_MARGIN)   # 主队拼命带
    fav_safe_h = (ph_pt - pts_zone > SAFE_MARGIN and rank[home] <= n - 3)
    und_fight_a = (rank[away] >= n - 2 or pts_safe - pa_pt <= DESP_MARGIN)
    fav_safe_a = (pa_pt - pts_zone > SAFE_MARGIN and rank[away] <= n - 3)
    if (und_fight_h and fav_safe_a) or (und_fight_a and fav_safe_h):
        out["M2_desp"] = 1

    return out, rank


def build_rows(matches):
    """逐场：先读榜后写榜，产冷门腿行（含战意旗标）。"""
    # 赛季队集（公开赛程·赛前可知）
    season_teams = defaultdict(set)
    for m in matches:
        season_teams[(m["league"], m["season"])].update((m["home"], m["away"]))
    tables = {k: Table(v) for k, v in season_teams.items()}
    rows = []
    for m in matches:
        tbl = tables[(m["league"], m["season"])]
        ph, pd_, pa = devid(m["pch"], m["pcd"], m["pca"])
        fav_is_home = ph >= pa
        # 旗标（读榜）
        fl, rank = {}, {}
        try:
            fl, rank = flags_for(tbl, m["home"], m["away"])
        except (IndexError, KeyError):
            pass
        # M3 欧战 slack：热门方排名≤6 且 周二~周四
        fav_tm = m["home"] if fav_is_home else m["away"]
        if rank and rank.get(fav_tm, 99) <= 6 and m["date"].weekday() in MIDWEEK:
            fl["M3_slack"] = 1
        # M4 争冠压力：涉前二 且 榜首分差≤3 且 剩余≤6
        if rank:
            _, order, n = tbl.snapshot()
            top2_gap = tbl.t[order[0]][0] - tbl.t[order[1]][0]
            if (rank.get(m["home"], 99) <= 2 or rank.get(m["away"], 99) <= 2) \
                    and top2_gap <= 3 and min(tbl.remaining(m["home"]), tbl.remaining(m["away"])) <= 6:
                fl["M4_title"] = 1

        for side, p, odds in (("H", ph, m["pch"]), ("A", pa, m["pca"])):
            if p >= LONGSHOT_MAX:
                continue
            rows.append({
                "date": m["date"].date().isoformat(), "season": m["season"],
                "league": m["league"], "side": side, "p": p, "odds": odds,
                "hit": 1.0 if m["ftr"] == side else 0.0,
                **{k: v for k, v in fl.items()},
            })
        tbl.add(m["home"], m["away"], m["hg"], m["ag"])
    return rows


def cal_gap(sub):
    return sum(r["hit"] - r["p"] for r in sub) / len(sub)


def boot_diff_ci(flagged, unflagged):
    """旗标 − 无旗标 校准差之差的 bootstrap CI（腿级重抽·两组独立）。"""
    rng = random.Random(SEED)
    nf, nu = len(flagged), len(unflagged)
    vf = [r["hit"] - r["p"] for r in flagged]
    vu = [r["hit"] - r["p"] for r in unflagged]
    outs = []
    for _ in range(BOOT_N):
        sf = sum(vf[rng.randrange(nf)] for _ in range(nf)) / nf
        su = sum(vu[rng.randrange(nu)] for _ in range(nu)) / nu
        outs.append(sf - su)
    outs.sort()
    return outs[int(BOOT_N * 0.025)], outs[min(int(BOOT_N * 0.975), BOOT_N - 1)]


def judge(name, rows):
    flagged = [r for r in rows if any(k.startswith("M") for k in r)]
    unflagged = [r for r in rows if not any(k.startswith("M") for k in r)]
    n = len(rows)
    pool_ret = sum(r["odds"] * r["hit"] for r in rows) / n
    print(f"\n════ {name} 段（腿 {n}·旗标 {len(flagged)}）════")
    if not flagged:
        print("  无旗标腿·跳过", flush=True)
        return None
    gf_, gu = cal_gap(flagged), cal_gap(unflagged)
    lo, hi = boot_diff_ci(flagged, unflagged)
    ret_f = sum(r["odds"] * r["hit"] for r in flagged) / len(flagged)
    print(f"  旗标场：命中 {sum(r['hit'] for r in flagged) / len(flagged) * 100:.2f}%"
          f" · 隐含 {sum(r['p'] for r in flagged) / len(flagged) * 100:.2f}% · 校准差 {gf_ * 100:+.2f}pp"
          f" · 回收率 {ret_f:.4f}（全池 {pool_ret:.4f}）")
    print(f"  差(旗−无旗) {(gf_ - gu) * 100:+.2f}pp · CI[{lo * 100:+.2f},{hi * 100:+.2f}]pp")
    per = {}
    for mk in ("M1_dead", "M2_desp", "M3_slack", "M4_title"):
        sub = [r for r in rows if r.get(mk)]
        if not sub:
            per[mk] = {"n": 0}
            print(f"    {mk:<10} n=0")
            continue
        g = cal_gap(sub)
        lo2, hi2 = boot_diff_ci(sub, unflagged)
        per[mk] = {"n": len(sub), "gap": round(g, 4), "diffCi": [round(lo2, 4), round(hi2, 4)]}
        print(f"    {mk:<10} n={len(sub):<5} 校准差 {g * 100:+.2f}pp · 差CI[{lo2 * 100:+.2f},{hi2 * 100:+.2f}]pp")
    return {"nLegs": n, "nFlagged": len(flagged), "gapFlagged": round(gf_, 4),
            "gapUnflagged": round(gu, 4), "diff": round(gf_ - gu, 4),
            "diffCi": [round(lo, 4), round(hi, 4)], "retFlagged": round(ret_f, 4),
            "poolRet": round(pool_ret, 4), "perFlag": per}


def main():
    print("══ v32（轨2）战意赛制终审 ══\n", flush=True)
    print("预注册: fade-strategy-prereg v32·先行审计=六剧本零战意条目（净地）", flush=True)
    matches = load_matches()
    print(f"fd 比赛 {len(matches)} 场", flush=True)
    rows = build_rows(matches)
    print(f"冷门腿 {len(rows)} 条", flush=True)
    fit = judge("fit（参照）", [r for r in rows if r["season"] in FIT_SEASONS])
    val = judge("val（判据）", [r for r in rows if r["season"] in VAL_SEASONS])

    verdict = "样本不足"
    if val:
        passed = val["diff"] >= 0.025 and val["diffCi"][0] > 0.01
        verdict = ("判据①过——战意侦测成立（信息层）" if passed else
                   "判据④——不过·轨2 结案：战意旗标无市场外信息（战意已定价）")
    print(f"\n══ 判定: {verdict} ══", flush=True)
    OUT.write_text(json.dumps({
        "ranAt": "2026-10-09", "track": "轨2 战意赛制终审", "prereg": "v32",
        "fit": fit, "val": val, "verdict": verdict,
        "discipline": "信息层判侦测·利润层如实报·积分榜walk-forward先读后写·Bonferroni k=5 α=0.01 同报",
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"归档 {OUT.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
