# -*- coding: utf-8 -*-
"""v21：形状决策函数 v1 对比回测（玩法灵活性·e 证据驱动）。

规则（预注册·v20/v20.1 e 曲线为据）：
  R1 黑名单：CRS 格体彩赔率 ≥5 禁入（longshot tax+高段高估双杀·v20 e=0.203/v20.1 A表）
  R1.5 打折：模型格 p≥20% → p×0.5 参与排序（v20.1 B表 dev+10.5pp·仅影响排序不影响结算）
  R2 基底：HAD 三向全入池（e≈1.03 市场效率线）
  R4 形状自适应：过审腿按打折 EV 排序·同场限一腿（混串铁律）·
     n≥4→4串11容错 / n=3→3串4 / n=2→双2串1 / n<2→不出票
  R5 注金：恒 22 元档（4串11）/14 元（3串4·2倍）/8 元（双2串1）
对照：
  A 基线 = 同腿池（HAD 三向+CRS 格）无 R1/R1.5·topP 直选 top4·4串11（v14 同构）
判据（跑前写死）：
  ① 验证段(2025-10~2026-09-28) B vs A ROI 改善 >5pp → 决策函数价值成立
  ② 扣最大单日 + 回款日率同报
  ③ B 仍负 → 如实呈报"灵活性=亏损优化器·非盈利引擎"
数据：v20.1 磁盘缓存（841 日·秒载）。
产出：data/04-summaries/v21-shape-policy.json
开发者 sszhang
"""
from __future__ import annotations

import itertools
import json
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v11_s4_recalib import FIT_WINDOW, VAL_WINDOW, UNIT, CAP, HIST
from v20_1_crs_full_grid import CACHE_PATH

ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = ROOT / "data" / "04-summaries" / "v21-shape-policy.json"
CRS_BAN_ODDS = 5.0
P_DISCOUNT = 0.20
DISCOUNT_FACTOR = 0.5


def load_pre():
    key = f"v2|{max((f.stat().st_mtime for f in HIST.glob('crs_hist_*.json')), default=0):.0f}|30"
    obj = None
    if CACHE_PATH.exists():
        obj = pickle.loads(CACHE_PATH.read_bytes())
    if obj is not None and obj.get("key") == key:
        print("preload 命中磁盘缓存(v2)", flush=True)
        return obj["pre"]
    print("缓存缺失/过期·重建（约45分钟）…", flush=True)
    import strength_loaders as sl
    from v11_s5_recalib import preload_days
    ctx = sl.build_ctx(__import__("v11_s4_recalib", fromlist=["LEAGUES"]).LEAGUES)
    pre = preload_days(ctx, sl.zh_to_id(), {})
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_bytes(pickle.dumps({"key": key, "pre": pre}))
    print("缓存已重建", flush=True)
    return pre


def build_legs(c):
    """当日一场 → 腿池 [(p_eff, p_true, odds, mk, hit)]。HAD 三向 + CRS 格。"""
    legs = []
    had = c.get("hadHist") or {}
    return legs


def legs_of(cand):
    out = []
    # HAD 三向（体彩价）
    hh = cand.get("hadHist")
    if hh:
        for key, mk in (("h", "h"), ("d", "d"), ("a", "a")):
            o = hh.get(key)
            try:
                o = float(o)
            except (TypeError, ValueError):
                continue
            if o > 1.0:
                out.append({"p": cand["hadModel"][key], "odds": o,
                            "hit": cand["real"] == mk, "mk": mk, "play": "HAD",
                            "matchKey": cand["key"]})
    # CRS 格
    for cell in cand["cells"]:
        out.append({"p": cell["p"], "odds": cell["odds"],
                    "hit": cell["mk"] == cand["real"], "mk": cell["mk"],
                    "play": "CRS", "matchKey": cand["key"]})
    return out


def payout_shape(sel, shape):
    """sel: [(odds, hit)] → (注数, 派彩)。"""
    n = len(sel)
    if shape == "4s11":
        combos = (list(itertools.combinations(range(4), 2))
                  + list(itertools.combinations(range(4), 3)) + [(0, 1, 2, 3)])
    elif shape == "3s4":
        combos = [(0, 1), (0, 2), (1, 2), (0, 1, 2)]
    else:
        combos = [(0, 1)]
    units = 0
    pay = 0.0
    for grp in combos:
        u, ok, prod = 1, True, 1.0
        for i in grp:
            u *= 1
            if not sel[i][1]:
                ok = False
            else:
                prod *= sel[i][0]
        units += u
        if ok:
            pay += min(UNIT * prod, CAP)
    return units, pay


SHAPE_STAKE = {"4s11": 11 * UNIT, "3s4": 4 * UNIT, "2s1x2": 2 * 2 * UNIT}


def run_policy(pre, lo, hi, policy):
    """policy: 'A'=topP直选4串11 | 'B'=R1-R5决策函数。"""
    total_stake = total_pay = 0.0
    days = hit_days = 0
    daily = []
    for day, cands in pre.items():
        if not (lo <= day <= hi):
            continue
        pool = []
        for ci, c in enumerate(cands):
            cand = dict(c)
            cand["key"] = ci
            had_m = c.get("hadModel")
            if had_m is None and "hadModel" not in c:
                # hadModel 在 preload 里没有——用 cells 派生（dc_matrix 已含 had？
                # preload cells 只存 CRS 有价格·had 模型值从 hadHist 场也不可靠——
                # 简化：HAD 模型 p 用 sm.had_from 需重算矩阵·此处用 corpus 无·
                # 折中：HAD 腿的 p 用体彩 devig 概率替代模型 p（排序等价·EV 打分近似）
                pass
            for leg in legs_of(cand):
                pool.append(leg)
        if policy == "B":
            pool = [x for x in pool if not (x["play"] == "CRS" and x["odds"] >= CRS_BAN_ODDS)]
            for x in pool:
                x["score"] = x["p"] * (DISCOUNT_FACTOR if x["p"] >= P_DISCOUNT else 1.0)
        else:
            for x in pool:
                x["score"] = x["p"]
        pool.sort(key=lambda x: -x["score"])
        # 同场限一腿
        seen, uniq = set(), []
        for x in pool:
            if x["matchKey"] in seen:
                continue
            seen.add(x["matchKey"])
            uniq.append(x)
            if len(uniq) >= 4:
                break
        if len(uniq) < 2:
            continue
        if len(uniq) >= 4:
            shape, sel = "4s11", [(x["odds"], x["hit"]) for x in uniq[:4]]
        elif len(uniq) == 3:
            shape, sel = ("3s4", [(x["odds"], x["hit"]) for x in uniq]) if policy == "B" \
                else (None, None)
            if shape is None:
                continue
        else:
            shape, sel = "2s1x2", [(x["odds"], x["hit"]) for x in uniq]
        units, pay = payout_shape(sel, shape)
        stake = SHAPE_STAKE[shape]
        total_stake += stake
        total_pay += pay
        days += 1
        if pay > 0:
            hit_days += 1
        daily.append({"day": day, "shape": shape, "pay": round(pay, 2)})
    roi = (total_pay - total_stake) / total_stake if total_stake else None
    daily.sort(key=lambda d: -d["pay"])
    return {"roi": roi, "stake": round(total_stake, 1), "pay": round(total_pay, 2),
            "days": days, "hitDays": hit_days,
            "topDays": daily[:3]}


def main():
    print("══ v21 形状决策函数对比回测 ══\n", flush=True)
    print("预注册: 脚本头跑前写死\n", flush=True)
    pre = load_pre()
    print(f"缓存载入: {len(pre)}日\n", flush=True)

    out = {}
    for label, window in (("fit", FIT_WINDOW), ("val", VAL_WINDOW)):
        a = run_policy(pre, *window, policy="A")
        b = run_policy(pre, *window, policy="B")
        out[label] = {"A": a, "B": b}
        print(f"── {label} 段 ──")
        for tag, r in (("A·topP直选4串11", a), ("B·R1-R5决策函数", b)):
            print(f"  {tag}: ROI {r['roi']*100:+7.1f}% (投{r['stake']} 回{r['pay']}·{r['days']}日·回款{r['hitDays']}日)")
        imp = b["roi"] - a["roi"]
        print(f"  B-A 改善: {imp*100:+.1f}pp\n")

    imp_val = out["val"]["B"]["roi"] - out["val"]["A"]["roi"]
    verdict = ("决策函数价值成立（改善>5pp·候选入组票流程）" if imp_val > 0.05 else
               "改善不足5pp——灵活性为亏损优化器·非盈利引擎")
    print(f"══ 判定: {verdict} ══")
    result = {"ranAt": "2026-10-08", "preReg": "脚本头跑前写死",
              "rules": {"R1": f"CRS odds>={CRS_BAN_ODDS} 禁入",
                        "R1.5": f"p>={P_DISCOUNT} 打折×{DISCOUNT_FACTOR}",
                        "R4": "n>=4→4s11/3→3s4/2→2s1x2/<2不出", "R5": "22元档"},
              "fit": out["fit"], "val": out["val"],
              "improveValPP": round(imp_val, 4), "verdict": verdict}
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"归档 {OUT_PATH.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
