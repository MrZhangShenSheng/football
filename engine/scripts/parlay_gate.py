# -*- coding: utf-8 -*-
r"""闯关票生产脚本（2026-09-29 大哥拍板直接转正）。

方案定义（全部参数冻结，证据=engine/scripts/research/ 全链验证）：
  概率源   ：比分族特征模型（score_family_model 族 softmax，逐月滚动重训——
            当月预测用联赛库 < 当月 1 日全部数据，实盘无泄漏）
  选场     ：滚动 2 天窗口（跨日在售池）内 gap 断层最大的前 2 场
  选腿     ：每场押模型 top1+top2 双选（防次热翻转，P352 教训）
  结构     ：2串1 复式 = 4 注 × 2 元 = 8 元/票（每日一票）
  预期     ：全季模拟 134 注 ROI +20.1%；右尾结构——6~8 月连续 3 个月 0 回款
            是常态，非故障；评估纪律 = 满 100 注或 3 个月 0 回款再议，中途不停

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
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "research"))

import score_family_model as sfm

ROOT = sfm.ROOT
OUT_DIR = ROOT / "engine" / "cache" / "parlay_gate"
WINDOW_DAYS = 2
N_LEGS = 2
UNIT = 2.0


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
                      "code": m.get("code"), "league": m.get("league"),
                      "home_zh": m.get("home"), "away_zh": m.get("away"),
                      "hid": hid, "aid": aid,
                      "actual": None,
                      "odds": {}})   # 赔率从 crs 池现取
    # 赔率解析（sXXsYY → (h,a)）；按 (matchDate, code) 回查原场次
    by_key = {}
    for x in live:
        by_key[(x.get("matchDate") or today, x.get("code"))] = x
    for b in blind:
        m = by_key.get((b["date"], b["code"]))
        odds = {}
        if m:
            for kk, v in (m.get("crs") or {}).items():
                if str(kk).startswith("other") or not v:
                    continue
                try:
                    hh, aa = (int(x) for x in str(kk).split(":")[:2])
                    odds[(hh, aa)] = float(v)
                except (ValueError, TypeError):
                    continue
        b["odds"] = odds

    # 训练：联赛库 < 当月 1 日（逐月滚动口径）
    cut = f"{today[:7]}-01"
    merged = [("L", d, h, a, hg, ag) for d, h, a, hg, ag in tl if d < cut]
    merged.sort(key=lambda r: r[0])
    # 预过滤：映射不上的场次直接剔除（国家队/未收录队——族模型特征=俱乐部
    # 联赛历史，对其本质不适用，2026-09-29 转正时明确边界）
    live = [x for x in live
            if zh.get(x.get("home")) and zh.get(x.get("away"))]
    if not live:
        print("[parlay_gate] 当前轮次无可映射俱乐部场次（国家队/杯赛轮或新队）——"
              "族特征模型不适用，不出票。适用范围=俱乐部联赛轮。")
        return []
    merged.sort(key=lambda r: r[0])
    stats = defaultdict(sfm.TeamStats)
    X_tr, y_tr = [], []
    tot_g = tot_n = 0
    for date_, h, a, hg, ag in [(r[1], r[2], r[3], r[4], r[5]) for r in merged]:
        lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
        fv_h = stats[h].vector(0, lg_gf)
        fv_a = stats[a].vector(1, lg_gf)
        if fv_h[12] >= sfm.MIN_HIST and fv_a[12] >= sfm.MIN_HIST:
            X_tr.append(sfm.feature_row_v4((fv_h, fv_a),
                                           stats[h].recent, stats[a].recent))
            y_tr.append(sfm.CLASSES.index(sfm.family_of(hg, ag)))
        stats[h].add(hg, ag, True, opp=a)
        stats[a].add(ag, hg, False, opp=h)
        tot_g += hg + ag
        tot_n += 1
    model = sfm.train_softmax(X_tr, y_tr, len(sfm.CLASSES))
    X_bl = [sfm.feature_row_v4((stats[b["hid"]].vector(0, tot_g / max(tot_n, 1)),
                                stats[b["aid"]].vector(1, tot_g / max(tot_n, 1))),
                               stats[b["hid"]].recent, stats[b["aid"]].recent)
            for b in blind]
    P = sfm.predict_proba(model, X_bl)
    fam_dist = defaultdict(Counter)
    for date_, h, a, hg, ag in [(r[1], r[2], r[3], r[4], r[5]) for r in merged]:
        fam_dist[sfm.family_of(hg, ag)][(hg, ag)] += 1
    packs = []
    for i, b in enumerate(blind):
        d = {}
        for ci, cname in enumerate(sfm.CLASSES):
            tot = sum(fam_dist.get(cname, {}).values()) or 1
            for s, c in fam_dist.get(cname, {}).items():
                d[s] = d.get(s, 0.0) + P[i][ci] * (c / tot)
        ranked = sorted(d.items(), key=lambda kv: -kv[1])
        t1, t2 = ranked[0], ranked[1]
        packs.append({
            "date": b["date"], "code": b["code"], "league": b["league"],
            "match": f'{b["home_zh"]} vs {b["away_zh"]}',
            "top1": t1[0], "p1": round(t1[1], 4),
            "o1": b["odds"].get(t1[0]),
            "top2": t2[0], "p2": round(t2[1], 4),
            "o2": b["odds"].get(t2[0]),
            "gap": round(t1[1] - t2[1], 4),
        })
    return packs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if a.status:
        files = sorted(OUT_DIR.glob("*-ticket.json"))
        tc = tp = 0.0
        for p in files:
            t = json.loads(p.read_text(encoding="utf-8"))
            st = t.get("settle") or {}
            print(f"  {t['date']} gap前2=[{t['legs'][0]['code']},{t['legs'][1]['code']}] "
                  f"成本{t['cost']}元 派彩={st.get('payout', '未结')}")
            tc += t["cost"]
            tp += st.get("payout") or 0.0
        print(f"累计 {len(files)} 票 · 投入 {tc:.0f} 元 · 已结回款 {tp:.0f} 元")
        return

    today = date.today().isoformat()
    out_path = OUT_DIR / f"{today}-ticket.json"
    if out_path.exists() and not a.force:
        print(f"[parlay_gate] 今日票已存在 {out_path.name}（--force 重出）")
        t = json.loads(out_path.read_text(encoding="utf-8"))
        _print_card(t)
        return

    packs = build_live_packs()
    if not packs:
        return   # build_live_packs 已打印原因（国家队轮/映射不足）
    # 滚动 2 天窗口：今天 + 明天在售场次
    w_end = (date.fromisoformat(today) + timedelta(days=WINDOW_DAYS - 1)).isoformat()
    pool = sorted((p for p in packs if today <= p["date"] <= w_end),
                  key=lambda p: -p["gap"])[:N_LEGS]
    if len(pool) < N_LEGS:
        raise SystemExit(f"[parlay_gate] 窗口内可映射场 {len(pool)} < {N_LEGS}，不出票")

    ticket = {
        "date": today, "shape": "2串1复式（4注×2元=8元）",
        "window": [today, w_end],
        "legs": [{"code": p["code"], "league": p["league"], "match": p["match"],
                  "date": p["date"], "gap": p["gap"],
                  "top1": f'{p["top1"][0]}:{p["top1"][1]}', "p1": p["p1"], "o1": p["o1"],
                  "top2": f'{p["top2"][0]}:{p["top2"][1]}', "p2": p["p2"], "o2": p["o2"]}
                 for p in pool],
        "cost": 8.0, "unitStake": 2.0, "multiplier": 1,
        "bets": 4,
        "model": "score_family v4（逐月滚动重训+族频率比分体质特征·2026-09-29 双段验证通过）· W=2 · gap前2 · 双选",
        "evidence": "2025-10~2026-09 全季模拟 134注 ROI+20.1%（右尾·6-8月连亏3月为常态）",
        "expected": "以小博大：多数注归零，靠双选全中(合赔数十倍级)回本翻正",
        "discipline": "满100注或连续3个月0回款再评估；中途不停",
        "settle": {"status": "pending"},
    }
    out_path.write_text(json.dumps(ticket, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"[parlay_gate] 票面已落 {out_path.relative_to(ROOT)}")
    _print_card(ticket)


def _print_card(t):
    print("\n┌ 闯关票 · 2串1复式 · 4注×2元=8元 " + "─" * 30)
    for i, l in enumerate(t["legs"], 1):
        print(f"│ 腿{i} {l['code']} {l['league']:5} {l['match'][:22]:24} "
              f"双选 {l['top1']}@{l['o1']} / {l['top2']}@{l['o2']}  "
              f"(gap {l['gap']:.3f})")
    print("│ 规则：两腿各命中任一双选比分 → 全中派彩（赔率相乘×2元）")
    print("└ 出票时逐条对照终端，赔率以终端为准 ──────────────────")


if __name__ == "__main__":
    main()
