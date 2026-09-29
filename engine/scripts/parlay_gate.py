# -*- coding: utf-8 -*-
r"""闯关票生产脚本（2026-09-30 双方案并行）。

方案定义（全部参数冻结，证据=engine/scripts/research/ 全链验证）：

【方案A - 保守】
  选场条件：每日在售 >=2 场即可
  gap阈值 ：0.05
  预期ROI ：+15.77%（8/8 切分点正收益）
  票数    ：日均产出高

【方案B - 激进】
  选场条件：每日在售 >=10 场才下注
  gap阈值 ：0.06
  预期ROI ：+37.62%（8/8 切分点正收益）
  票数    ：日均产出低，但单票质量高

共同规则：
  概率源   ：比分族特征模型（score_family_model 族 softmax，v2 26维特征，
            逐月滚动重训——当月预测用联赛库 < 当月 1 日全部数据，实盘无泄漏）
  选场     ：滚动 2 天窗口（跨日在售池）内 gap 断层最大的前 2 场
  选腿     ：动态 1-2 选（gap > 阈值 选单选，否则选双选）
  结构     ：2串1 复式 = 1~4 注 × 2 元（每日一票）

纪律：
  · 幂等：同窗口已出票（cache 存在）则跳过，--force 才重出
  · 凭证：票面 JSON 落 engine/cache/parlay_gate/（纳管 git）
  · 实买：大哥终端实购后走"我买了"登记流程，本脚本只出票面建议
  · 赔率以体彩终端为准，本票面为出票时点参考

用法：
  python engine/scripts/parlay_gate.py            # 出今日票（幂等）
  python engine/scripts/parlay_gate.py --force    # 强制重出
  python engine/scripts/parlay_gate.py --status   # 查看历史票与累计账

开发者 sszhang
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from collections import defaultdict, Counter
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "research"))

import score_family_model as sfm

ROOT = sfm.ROOT
OUT_DIR = ROOT / "engine" / "cache" / "parlay_gate"
WINDOW_DAYS = 2
N_LEGS = 2
UNIT = 2.0

# 双方案配置
SCHEMES = {
    "A": {
        "name": "保守",
        "min_matches": 2,
        "gap_threshold": 0.05,
        "evidence": "8/8切分点正收益 ROI+15.77%",
    },
    "B": {
        "name": "激进",
        "min_matches": 10,
        "gap_threshold": 0.06,
        "evidence": "8/8切分点正收益 ROI+37.62%（日>=10场才出票）",
    },
}


def dynamic_k(gap, threshold):
    """根据 gap 决定选几个比分"""
    return 1 if gap > threshold else 2


def current_window_matches():
    """当轮在售场次（sporttery_matches.json）→ 窗口池。"""
    d = json.loads((ROOT / "engine/cache/sporttery_matches.json").read_text(encoding="utf-8"))
    ms = d.get("matches", d) if isinstance(d, dict) else d
    out = []
    for m in ms:
        crs = m.get("crs") or {}
        if len(crs) < 20:
            continue
        out.append(m)
    return out


def build_live_packs():
    """当轮场次 → 模型包（gap/top1/top2/赔率）。"""
    zh = {}
    for tid, srcs in sfm.load_aliases().items():
        if srcs.get("zh"):
            zh[srcs["zh"]] = tid
    tl = sfm.league_timeline()
    live = current_window_matches()
    today = date.today().isoformat()
    blind = []
    for m in live:
        hid, aid = zh.get(m.get("home")), zh.get(m.get("away"))
        if not (hid and aid):
            continue
        mon = today[:7]
        blind.append({"date": m.get("matchDate") or today,
                      "hid": hid, "aid": aid,
                      "home": m.get("home"), "away": m.get("away"),
                      "crs": m.get("crs"), "code": m.get("code"),
                      "league": m.get("league")})
    if not blind:
        return []

    merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
    merged += [("B", b["date"], b["hid"], b["aid"], None, None, i)
               for i, b in enumerate(blind)]
    merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

    stats = defaultdict(sfm.TeamStats)
    X_tr, y_tr, X_bl, meta = [], [], [], []
    tot_g = tot_n = 0

    for r in merged:
        kind, dt, h, a = r[0], r[1], r[2], r[3]
        lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
        fv_h = stats[h].vector(0, lg_gf)
        fv_a = stats[a].vector(1, lg_gf)

        if kind == "L":
            hg, ag = r[4], r[5]
            if dt < today and fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST:
                X_tr.append(sfm.feature_row((fv_h, fv_a)))
                y_tr.append(sfm.CLASSES.index(sfm.family_of(hg, ag)))
            stats[h].add(hg, ag, True)
            stats[a].add(ag, hg, False)
            tot_g += hg + ag
            tot_n += 1
        else:
            meta.append(blind[r[6]])
            X_bl.append(sfm.feature_row((fv_h, fv_a)))

    if len(X_tr) < 100 or len(X_bl) == 0:
        return []

    import numpy as np
    model = sfm.train_softmax(np.array(X_tr), np.array(y_tr), len(sfm.CLASSES))
    P = sfm.predict_proba(model, np.array(X_bl))

    fam_dist = defaultdict(Counter)
    for r in merged:
        if r[0] == "L" and r[1] < today:
            fam_dist[sfm.family_of(r[4], r[5])][(r[4], r[5])] += 1

    packs = []
    for i, m in enumerate(meta):
        model_probs = {}
        for ci, cname in enumerate(sfm.CLASSES):
            tot = sum(fam_dist.get(cname, {}).values()) or 1
            for s, c in fam_dist.get(cname, {}).items():
                model_probs[s] = model_probs.get(s, 0.0) + P[i][ci] * (c / tot)

        mdl_sorted = sorted(model_probs.items(), key=lambda kv: -kv[1])
        gap = mdl_sorted[0][1] - mdl_sorted[1][1] if len(mdl_sorted) >= 2 else 0

        packs.append({
            "date": m["date"],
            "home": m["home"], "away": m["away"],
            "code": m["code"], "league": m["league"],
            "crs": m["crs"],
            "model_sorted": mdl_sorted,
            "gap": gap,
        })

    return packs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="强制重出今日票")
    ap.add_argument("--status", action="store_true", help="查看历史累计账")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.status:
        show_status()
        return

    today = date.today().isoformat()
    w_end = (date.today() + timedelta(days=WINDOW_DAYS - 1)).isoformat()

    packs = build_live_packs()
    if not packs:
        print("[parlay_gate] 无有效在售场次")
        return

    # 按日期分组
    by_day = defaultdict(list)
    for p in packs:
        by_day[p["date"]].append(p)

    # 统计今日场次数
    today_matches = len(by_day.get(today, []))
    print(f"[parlay_gate] 今日在售 {today_matches} 场，窗口共 {len(packs)} 场")

    # 分别处理两个方案
    for scheme_id, cfg in SCHEMES.items():
        out_path = OUT_DIR / f"{today}_{scheme_id}.json"

        if out_path.exists() and not args.force:
            print(f"[方案{scheme_id}] 今日已出票，跳过（{out_path.name}）")
            continue

        # 检查场次数条件
        if today_matches < cfg["min_matches"]:
            print(f"[方案{scheme_id}-{cfg['name']}] 今日 {today_matches} 场 < {cfg['min_matches']} 场，不出票")
            continue

        # 选场：gap 最大的2场
        sorted_packs = sorted(packs, key=lambda p: -p["gap"])[:N_LEGS]
        if len(sorted_packs) < N_LEGS:
            print(f"[方案{scheme_id}] 场次不足")
            continue

        gap_threshold = cfg["gap_threshold"]

        legs = []
        for p in sorted_packs:
            top1, prob1 = p["model_sorted"][0]
            top2, prob2 = p["model_sorted"][1]
            o1 = p["crs"].get(f"{top1[0]}:{top1[1]}") or p["crs"].get(f"{top1[0]}{top1[1]}")
            o2 = p["crs"].get(f"{top2[0]}:{top2[1]}") or p["crs"].get(f"{top2[0]}{top2[1]}")
            k = dynamic_k(p["gap"], gap_threshold)
            legs.append({
                "code": p["code"], "league": p["league"],
                "match": f"{p['home']} vs {p['away']}",
                "top1": f"{top1[0]}:{top1[1]}", "o1": o1 or "?",
                "top2": f"{top2[0]}:{top2[1]}", "o2": o2 or "?",
                "gap": round(p["gap"], 4),
                "k": k,
            })

        k1, k2 = legs[0]["k"], legs[1]["k"]
        n_bets = k1 * k2
        cost = n_bets * UNIT

        shape_desc = f"2串1复式（{n_bets}注×{UNIT:.0f}元={cost:.0f}元）"
        pick_desc = f"{k1}选×{k2}选"

        ticket = {
            "date": today,
            "scheme": scheme_id,
            "scheme_name": cfg["name"],
            "shape": shape_desc,
            "pick": pick_desc,
            "window": [today, w_end],
            "legs": legs,
            "cost": cost,
            "unitStake": UNIT,
            "multiplier": 1,
            "bets": n_bets,
            "min_matches": cfg["min_matches"],
            "gap_threshold": gap_threshold,
            "model": f"score_family v2（26维特征·逐月滚动重训）· 方案{scheme_id} · gap>{gap_threshold}选1",
            "evidence": cfg["evidence"],
            "expected": "以小博大：多数注归零，靠全中(合赔数十倍级)回本翻正",
            "discipline": "满100注或连续3个月0回款再评估；中途不停",
            "settle": {"status": "pending"},
        }
        out_path.write_text(json.dumps(ticket, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        print(f"[方案{scheme_id}-{cfg['name']}] 票面已落 {out_path.relative_to(ROOT)}")
        _print_card(ticket)


def show_status():
    """显示历史票与累计账"""
    files = sorted(OUT_DIR.glob("*.json"))
    if not files:
        print("[parlay_gate] 暂无历史票")
        return

    print(f"\n{'='*80}")
    print("闯关票历史记录")
    print(f"{'='*80}")

    # 按方案分组统计
    stats = {"A": {"tickets": 0, "cost": 0, "payout": 0, "pending": 0},
             "B": {"tickets": 0, "cost": 0, "payout": 0, "pending": 0}}

    for f in files:
        t = json.loads(f.read_text(encoding="utf-8"))
        scheme = t.get("scheme", "A")  # 兼容旧票
        if scheme not in stats:
            scheme = "A"

        stats[scheme]["tickets"] += 1
        stats[scheme]["cost"] += t.get("cost", 0)

        settle = t.get("settle", {})
        if settle.get("status") == "pending":
            stats[scheme]["pending"] += 1
        else:
            stats[scheme]["payout"] += settle.get("payout", 0)

    print(f"\n{'方案':<10} {'票数':>8} {'成本':>10} {'派彩':>10} {'盈亏':>10} {'待结':>8}")
    print("-" * 60)

    total_cost = 0
    total_payout = 0

    for scheme_id in ["A", "B"]:
        s = stats[scheme_id]
        if s["tickets"] == 0:
            continue
        cfg = SCHEMES.get(scheme_id, {"name": "未知"})
        profit = s["payout"] - s["cost"]
        total_cost += s["cost"]
        total_payout += s["payout"]
        print(f"方案{scheme_id}({cfg['name']}) {s['tickets']:>6} {s['cost']:>10.0f} "
              f"{s['payout']:>10.0f} {profit:>+10.0f} {s['pending']:>8}")

    print("-" * 60)
    total_profit = total_payout - total_cost
    print(f"{'合计':<10} {stats['A']['tickets']+stats['B']['tickets']:>8} "
          f"{total_cost:>10.0f} {total_payout:>10.0f} {total_profit:>+10.0f}")


def _print_card(t):
    scheme = t.get("scheme", "?")
    scheme_name = t.get("scheme_name", "")
    print(f"\n┌ 闯关票·方案{scheme}({scheme_name}) · {t['shape']} " + "─" * 20)
    for i, l in enumerate(t["legs"], 1):
        k = l.get("k", 2)
        if k == 1:
            pick_str = f"单选 {l['top1']}@{l['o1']}"
        else:
            pick_str = f"双选 {l['top1']}@{l['o1']} / {l.get('top2', '?')}@{l.get('o2', '?')}"
        print(f"│ 腿{i} {l['code']} {l['league']:5} {l['match'][:22]:24} "
              f"{pick_str}  (gap {l['gap']:.3f})")
    print(f"│ 条件：日>=  {t.get('min_matches', 2)} 场，gap阈值 {t.get('gap_threshold', 0.05)}")
    print("│ 规则：两腿各命中所选比分 → 全中派彩（赔率相乘×2元）")
    print("└ 出票时逐条对照终端，赔率以终端为准 ──────────────────")


if __name__ == "__main__":
    main()
