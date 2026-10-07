# -*- coding: utf-8 -*-
r"""v18 方案每日执行器（大哥方案·2026-10-07 指令·影子级执行）

方案（大哥口径·纯 N串1·无容错票）:
  第一信号 = 市场置信分带（体彩 HAD 去水·踩线护栏 + p≥0.60·胆级≥0.75 优先）
  第二信号 = V3W-v2 比分模型 39 格矩阵方向同向确认
  腿数 N ∈ [3,8]（>8 取 p 前 8）· <3 当天关档不出方案 · N串1 × 1 倍 = 2 元
  ROI 四件套: 中奖概率=∏分带校准命中率(0.830/0.694·v16.1) · 中奖金额=2×∏体彩赔率 · 投入=2元 · ROI=P×∏o−1

背景注记: v18 历史回测判据③SEALED（DUAL−S3J=-0.1pp·CI下限<0）——大哥拍板影子级照常执行
（零成本前向积累·realized 样本自证）；回测证据随卡标注不隐藏。
V3W 预测不到的场（联赛库缺失·如芬超）自动出局双确认——关档/腿数不足时打印原因非静默。

用法: python engine/scripts/v18_scheme_daily.py            # 滚动池模式（默认·在售全量跨日选腿）
      python engine/scripts/v18_scheme_daily.py --byday    # 按销售日切分模式（对照）
幂等: 同 spec+同结算日已登记跳过。开发者 sszhang
2026-10-07 拍板: 跨日滚动池=默认（大哥选路径2·在售全量选腿·票挂最晚比赛日结算）。
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine" / "scripts"))
sys.path.insert(0, str(ROOT / "engine" / "shadow"))

import strength_loaders as sl          # noqa: E402
import strength_chain_eval as sce      # noqa: E402
from paper import register, load_tickets  # noqa: E402

CACHE = ROOT / "engine" / "cache"
PRED_DIR = ROOT / "data" / "03-predictions"
SPEC_NAME = "v18双信号N串1"
LEAGUES = ["england-premier", "spain-laliga", "germany-bundesliga", "italy-serie-a",
           "france-ligue1", "netherlands-eredivisie", "brazil", "portugal-liga",
           "england-championship", "spain-liga2", "germany-bundesliga2", "italy-serie-b",
           "france-ligue2", "belgium-first-a", "turkey-super-lig", "greece-super", "SC0"]

# 方案常量（与 v18 预注册同源）
TIER_DAN_P, TIER_STD_P = 0.75, 0.60
TREADLINE_ODDS, TREADLINE_P = 1.35, 0.68
MAX_LEGS, MIN_LEGS = 8, 3
DAN_CAL, STD_CAL = 0.830, 0.694       # 分带校准命中率（v16.1 收盘基）
STAKE, MULT = 2.0, 1
SIDES = ("H", "D", "A")
LABEL = {0: "主胜", 1: "平", 2: "客胜"}


def devig(h, d, a):
    inv = [1 / h, 1 / d, 1 / a]
    s = sum(inv)
    return [i / s for i in inv]


def _cell_ha(k):
    if k == "s1sh": return 6, 0
    if k == "s1sa": return 0, 6
    if k == "s1sd": return 3, 3
    try: return int(k[1:3]), int(k[4:6])
    except (ValueError, IndexError): return None


def main():
    today = date.today().isoformat()
    byday_mode = "--byday" in sys.argv
    raw = json.loads((CACHE / "sporttery_matches.json").read_text(encoding="utf-8"))
    ms = raw.get("matchList") or raw.get("matches") or []
    all_ms = [m for m in ms if all((m.get("had") or {}).get(k) for k in ("h", "d", "a"))]
    groups = {}
    if byday_mode:
        for m in all_ms:
            groups.setdefault(m["matchDate"][:10], []).append(m)
    else:
        groups["滚动池(在售全量)"] = all_ms        # 跨日合并·票挂最晚比赛日结算

    z2i = sl.zh_to_id()
    ctx = sl.build_ctx(LEAGUES)
    memo = {}
    existing = {(t["spec_name"], t["date"]) for t in load_tickets()}

    for day, pool in groups.items():
        print(f"\n◆ {day} · {len(pool)} 场")
        cands = []
        for m in pool:
            had = {k: float(m["had"][k]) for k in ("h", "d", "a")}
            probs = dict(zip(SIDES, devig(had["h"], had["d"], had["a"])))
            pick = max(probs, key=probs.get)
            p = probs[pick]
            o = had[{"H": "h", "D": "d", "A": "a"}[pick]]
            # 第一信号：踩线护栏 + p≥0.60
            if p < TIER_STD_P:
                continue
            if o < TREADLINE_ODDS and p < TREADLINE_P:
                continue
            hid, aid = z2i.get(m["home"]), z2i.get(m["away"])
            v3 = None
            if hid and aid:
                t = sce._predict_match(hid, aid, date.fromisoformat(m["matchDate"][:10]), ctx, memo, beta=0.05)
                if t and t.get("matrix"):
                    ph = pd_ = pa = 0.0
                    for k, v in t["matrix"].items():
                        ha = _cell_ha(k)
                        if ha is None: continue
                        if ha[0] > ha[1]: ph += v
                        elif ha[0] == ha[1]: pd_ += v
                        else: pa += v
                    s = ph + pd_ + pa
                    if s > 0: v3 = max(zip((ph, pd_, pa), SIDES))[1]
            agree = (v3 == pick) if v3 else False
            mark = "✓同向" if agree else ("✗分歧" if v3 else "—不可测")
            print(f"  {m['code']} {m['home']} vs {m['away']} [{m['league']}] "
                  f"选{LABEL[SIDES.index(pick)]} @{o:.2f} p={p:.2f} · V3W{mark}")
            if agree:
                cands.append({"m": m, "had": had, "pick": SIDES.index(pick), "p": p, "o": o})
        if len(cands) < MIN_LEGS:
            print(f"  [关档] 双确认腿 {len(cands)} < {MIN_LEGS} —— 当天不出方案")
            continue
        cands.sort(key=lambda c: (-(c["p"] >= TIER_DAN_P), -c["p"]))
        sel = cands[:MAX_LEGS]
        n = len(sel)
        p_all, prod = 1.0, 1.0
        legs, bet_legs = [], []
        for i, c in enumerate(sel):
            p_all *= DAN_CAL if c["p"] >= TIER_DAN_P else STD_CAL
            prod *= c["o"]
            odds3 = [None, None, None]
            odds3[c["pick"]] = c["o"]
            legs.append({"code": c["m"]["code"], "match": f"{c['m']['home']} vs {c['m']['away']}",
                         "market": "had", "pick": [c["pick"]], "odds": odds3})
            bet_legs.append([i, c["pick"]])
        roi_claimed = p_all * prod - 1
        print(f"  ── 方案: {n}串1 × 1倍 = 2元 ──")
        for i, c in enumerate(sel, 1):
            band = "胆级" if c["p"] >= TIER_DAN_P else "标准"
            print(f"    腿{i} {c['m']['code']} {c['m']['home']} vs {c['m']['away']} "
                  f"{LABEL[c['pick']]}@{c['o']:.2f} p={c['p']:.2f}[{band}]")
        print(f"    中奖概率(校准口径)={p_all:.1%} · 中奖金额=2×{prod:.2f}={STAKE*prod:.2f}元 · "
              f"投入=2元 · ROI(claimed)={roi_claimed:+.1%}")
        print(f"    回测注记: v18判据③SEALED(DUAL−S3J=-0.1pp)·影子级前向积累")
        if (SPEC_NAME, day) in existing:
            print(f"    [幂等] {SPEC_NAME} {day} 已登记，跳过")
            continue
        settle_day = max(c["m"]["matchDate"][:10] for c in sel) if not byday_mode else day
        tk = register(SPEC_NAME, settle_day, legs, [{"legs": bet_legs}], MULT, STAKE)
        print(f"    [影子登记] {tk['id']} · 赔率冻结 · 结算日挂 {settle_day}")
        card = {"salesDay": day, "settleDay": settle_day, "spec": SPEC_NAME, "generatedAt": today,
                "legs": [{"code": c['m']['code'], "match": f"{c['m']['home']} vs {c['m']['away']}",
                          "league": c['m']['league'], "pick": LABEL[c['pick']],
                          "odds": c['o'], "p": round(c['p'], 4),
                          "band": "dan" if c['p'] >= TIER_DAN_P else "std"} for c in sel],
                "roi4": {"pAll": round(p_all, 4), "payout": round(STAKE * prod, 2),
                         "stake": STAKE, "roiClaimed": round(roi_claimed, 4)},
                "note": "v18回测SEALED·影子级执行·大哥2026-10-07指令"}
        out = PRED_DIR / (f"{today}-v18scheme-rolling.json" if not byday_mode else f"{day}-v18scheme.json")
        if not out.exists():                      # 幂等：卡面不覆盖已存在文件
            out.write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"    [归档] {out.name}")


if __name__ == "__main__":
    main()
