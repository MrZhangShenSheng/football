"""Bold Play 阶梯出票卡生成器：三档组装/限额反算/月封顶/settle 回填/比分选法双链。开发者 sszhang
密度口径（体彩真实池水，skill v4.9 实测）：HAD 0.871^串 / CRS 0.661^串；4串单注限额50万。
freq-band（★ 2026-08-27 重设计默认，docs/2026-08-27-freq-band-design.html）：联赛频率模板+球队平移
+形状带+q排序，DC 退出比分链路；合格腿<4 关档不硬凑。--method=amix 过渡保留一个月（DC×体彩EV
三池全量扫描取最优，回测定去留），字节级不变。"""
import glob, json, math, re, sys
from datetime import date, datetime, timedelta
from pathlib import Path
from backfill import expand_combos   # 4串11=大小≥2全组合(结算引擎同源)
from band_calibration import devid
from common import ROOT, load_aliases
from dc_predict import (score_matrix, ttg_dist, hafu_approx, devig as devig_n,
                        reweight_matrix, reweight_hafu, temper, load_half_params,
                        load_temperature, fuse)
from score_ev import build_freq_table, ev_scan, map_league
from freq_band import (build_team_form, freq_legs, pools_card, shifted_q, league_base_rates,
                       lambdas, team_strength, _norm)

SHAPES = {"guilin": {"band": (10.0, 17.0), "multiplier": 4, "cost": 8},
          "meizhou": {"band": (18.0, 28.0), "multiplier": 5, "cost": 10}}
CACHE_DIR = ROOT / "engine/cache"   # 绝对定位（2026-09-02：相对路径在 cwd=engine/scripts 的 run.py 调用链下落空——彩票档 _dc_params 首次踩中，_hafu_odds/_hhad_odds/_load_fusion 同修）
PRED_DIR = ROOT / "data" / "03-predictions"
DIVERGENCE_LIMIT = 0.05   # |p_model - p市场| 合规线（skill 铁律 8 / 8-25 会话口径）
ODDS_RANGE = (2.0, 40.0)  # A-MIX 单腿赔率合理域：排除 550 级长尾（经验频率/DC 尾部噪声 × 绝对pp分歧=假阳性，2026-08-25 探针实测 4:0@550 EV+845% 被放行）
POOL_KEEP = {"had": 0.871, "hhad": 0.871, "ttg": 0.796, "hafu": 0.796, "crs": 0.661}  # 体彩池水期望返还（skill v4.9 实测）
SINGLE_LIMIT = 500_000.0        # 4-5 串单注奖金限额（官方规则）
MONTHLY_CAP = 240.0
ROUND_COST = 20.0
ROUND_REDLINE = 30.0              # 轮次总预算红线（preference.json roundRedline 同步；含彩票档）
MONTHLY_UPSET_CAP = 40.0        # 翻身月度彩票预算（spec §4.1 note·preference 同步）
# 彩票档（docs/2026-09-02-lottery-tier-design.html）：HAD/HHAD N串1×1倍=2元，右尾优先，
# 预算归属：彩票 2 元计入轮次总红线 ROUND_REDLINE（保底+翻身+彩票 ≤ 30 元）
LOTTERY_MIN_P = 0.55            # 三星干净腿门槛（skill 星级口径）
LOTTERY_LOW_ODDS = 1.25         # 超低赔通道：赔率≤1.25 且 p≥0.50 视同合格（合赔稳定器）
LOTTERY_LOW_ODDS_MIN_P = 0.50
LOTTERY_MIN_LEGS, LOTTERY_MAX_LEGS = 4, 8   # 出档腿数窗：合格腿全上 N∈[4,8]，池>8 取 EV 前 8

def band_ok(had: dict) -> str:
    """体彩 had 自去水方向带：max>=0.60 偏好，否则中性。"""
    p_max = max(devid(had["h"], had["d"], had["a"]))
    return "偏好" if p_max >= 0.60 else "中性"

def cap_multiplier(total_odds: float, budget_mult: int, limit: float = SINGLE_LIMIT) -> int:
    """倍数限额反算：单注奖金 = 2*total_odds*倍数 ≤ limit；上限 50。"""
    m = int(limit // (2 * total_odds))
    return max(1, min(budget_mult, m, 50))

def monthly_spend(records: list, month: str) -> float:
    return sum(r.get("cost", r.get("totalCost", 0)) for r in records
               if str(r.get("date", "")).startswith(month))

def budget_gate(spend: float, cap: float = MONTHLY_CAP, round_cost: float = ROUND_COST) -> bool:
    return spend + round_cost <= cap

def pick_upset_legs(rows: list, shape: str) -> list:
    """形状赔率带 + n>0（先验噪声永不入选）+ 正 EV（负期望永不入选）+ 每场最多 1 比分，按 ev 降序取 4。"""
    lo, hi = SHAPES[shape]["band"]
    picked, seen = [], set()
    for r in rows:
        mid = r["matchNumStr"]
        if (r.get("n", 0) <= 0 or mid in seen or not (lo <= r["odds"] <= hi)
                or r.get("ev") is None or r["ev"] <= 0):
            continue
        seen.add(mid); picked.append(r)
        if len(picked) == 4:
            break
    return picked

def _fallback_upset(odds_day: dict) -> list:
    """经验频率退路：每场取 1-1/1-0/2-1 中体彩赔率最高者（标注 fallback）。"""
    picked = []
    for m in odds_day.get("matches", []):
        crs = m.get("crs") or {}
        best = max((s for s in ("1:1", "1:0", "2:1") if s in crs), key=lambda s: crs[s], default=None)
        if best:
            picked.append({"matchNumStr": m["matchNumStr"], "match": f'{m.get("home")}-{m.get("away")}',
                           "score": best, "odds": crs[best], "n": -1, "ev": None, "fallback": True})
        if len(picked) == 4:
            break
    return picked

def _zh_map() -> dict:
    """_aliases.json → {中文队名: 规范tid}（common.load_aliases 平铺口径）。
    variants 一并展开（2026-08-25 修复：体彩票面译名'利雅胜利'在 variants 里但主名映射不命中）。"""
    out = {}
    for tid, srcs in load_aliases().items():
        if not isinstance(srcs, dict):
            continue
        for v in srcs.get("variants") or []:
            out[v] = tid
        if srcs.get("zh"):
            out[srcs["zh"]] = tid   # 主名后写，冲突时优先
    return out


def _hafu_odds() -> dict:
    """sporttery_matches.json → {场次编号: {hh..aa: 赔率float}}（score_odds 存档无 hafu）。"""
    out = {}
    try:
        d = json.loads((CACHE_DIR / "sporttery_matches.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    for m in d.get("matches") or []:
        hf = m.get("hafu")
        mid = m.get("matchNumStr") or m.get("code")  # 存档键 matchNumStr / 实时文件键 code
        if hf and mid:
            out[mid] = {k: float(v) for k, v in hf.items()}
    return out


def _hhad_odds() -> dict:
    """sporttery_matches.json → {场次编号: {goalLine, h, d, a}}（score_odds 存档无 hhad，
    与 _hafu_odds 同补链）。goalLine 体彩口径：'-1'=主让1球（让球结果=(主队得分+goalLine) vs 客队）。
    开发者 sszhang"""
    out = {}
    try:
        d = json.loads((CACHE_DIR / "sporttery_matches.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    for m in d.get("matches") or []:
        hh, mid = m.get("hhad"), m.get("matchNumStr") or m.get("code")
        if not (hh and mid and hh.get("goalLine") is not None):
            continue
        try:
            out[mid] = {"goalLine": float(hh["goalLine"]),
                        "h": float(hh["h"]), "d": float(hh["d"]), "a": float(hh["a"])}
        except (TypeError, ValueError):
            continue
    return out


def _load_fusion() -> tuple[float, float]:
    """fusion.json → (a, b) 融合系数，缺失/损坏回退检索铁律4 默认 (0.4, 1.0)。开发者 sszhang"""
    try:
        f = json.loads((CACHE_DIR / "fusion.json").read_text(encoding="utf-8"))
        return float(f["a"]), float(f["b"])
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return 0.4, 1.0


def _dc_params(m: dict, zh: dict, cache_dir: Path = CACHE_DIR):
    """体彩场次 → (lh, la, rho) | None。中文→tid→DC缓存宽松匹配（去连字符/空格小写比较，
    兼容本地库规范ID键'al-ahli'与fd原始名键'Aston Villa'两种缓存）。"""
    lg = map_league(m.get("league", ""))
    if not lg:
        return None
    p = cache_dir / f"{lg}_dc.json"
    if not p.exists():
        return None
    dc = json.loads(p.read_text(encoding="utf-8"))
    norm = lambda s: str(s).lower().replace("-", "").replace(" ", "")

    def find(zh_name):
        tid = zh.get(zh_name)
        if not tid:
            return None
        nt = norm(tid)
        for t in dc["teams"]:
            if norm(t) == nt:
                return t
        return None

    h, a = find(m.get("home")), find(m.get("away"))
    if not h or not a:
        return None
    th, ta = dc["teams"][h], dc["teams"][a]
    lh = math.exp(th["attack"] + ta["defense"] + dc["homeAdv"])
    la = math.exp(ta["attack"] + th["defense"])
    return lh, la, dc["rho"]


def mix_candidates(odds_day: dict, freq_table: dict, zh: dict, hafu: dict,
                   dc_params_fn=_dc_params, adjust_map: dict | None = None) -> list:
    """A-MIX 候选腿：每场 CRS/TTG/HAFU 三池 EV 最优合规项，按 EV 降序。

    概率源统一 DC 模型（score_matrix 49 比分 + ttg_dist/hafu_approx 聚合）——8-25 会话
    验证口径；合规三门槛：EV>0 且 |p_dc−p市场(去水)|<5pp 且单腿赔率∈ODDS_RANGE。
    经验频率只服务 fallback 链（pick_upset_legs），不进 A-MIX（尾部噪声假阳性）。
    freq_table 仅保留签名兼容。
    """
    legs = []
    for m in odds_day.get("matches", []):
        mid = m["matchNumStr"]
        params = dc_params_fn(m, zh)
        if not params:
            continue  # 无 DC 缓存/队名未入库 → 该场不入 A-MIX
        lh, la, rho = params
        best = None  # (ev, leg)

        def offer(ev, leg):
            nonlocal best
            if ev > 0 and (best is None or ev > best[0]):
                best = (ev, leg)

        tpool = load_temperature()
        adj = adjust_map.get(mid) if adjust_map else None
        s_lg, rho_h = load_half_params(map_league(m.get("league", "")))
        matrix = score_matrix(lh, la, rho)
        if adj:
            matrix = reweight_matrix(matrix, adj)
        p_crs = temper([float(matrix[i, j]) for i in range(7) for j in range(7)], tpool["crs"])
        crs_p = {(i, j): p_crs[i * 7 + j] for i in range(7) for j in range(7)}
        crs = {k: float(v) for k, v in (m.get("crs") or {}).items() if ":" in k}
        if crs:
            inv = sum(1.0 / o for o in crs.values())
            for k, o in crs.items():
                if not (ODDS_RANGE[0] <= o <= ODDS_RANGE[1]):
                    continue
                x, y = (int(t) for t in k.split(":"))
                p_mkt = (1.0 / o) / inv
                if abs(crs_p[(x, y)] - p_mkt) < DIVERGENCE_LIMIT:
                    offer(crs_p[(x, y)] * o - 1,
                          {"play": "crs", "pick": k, "odds": o, "source": "dc-reweighted" if adj else "dc"})
        ttg_o = m.get("ttg") or {}
        if len(ttg_o) == 8:
            p_mkt = devig_n([float(ttg_o[f"s{i}"]) for i in range(8)])
            p_dc = ttg_dist(matrix)
            p_dc = temper(p_dc, tpool["ttg"])
            for i in range(8):
                o = float(ttg_o[f"s{i}"])
                if ODDS_RANGE[0] <= o <= ODDS_RANGE[1] and abs(p_dc[i] - p_mkt[i]) < DIVERGENCE_LIMIT:
                    offer(p_dc[i] * o - 1,
                          {"play": "ttg", "pick": f"{i}球" if i < 7 else "7+球", "odds": o,
                           "source": "dc-reweighted" if adj else "dc"})
        hf = hafu.get(mid) or {}
        if len(hf) == 9:
            keys = [a + b for a in "hda" for b in "hda"]
            p_mkt = devig_n([hf[k] for k in keys])
            p_dc0 = hafu_approx(lh, la, s_lg, rho_h)
            if adj:
                p_dc0 = reweight_hafu(p_dc0, adj)
            p_dc9 = temper([p_dc0[k] for k in keys], tpool["hafu"])
            p_dc = dict(zip(keys, p_dc9))
            for k in keys:
                if ODDS_RANGE[0] <= hf[k] <= ODDS_RANGE[1] and abs(p_dc[k] - p_mkt[keys.index(k)]) < DIVERGENCE_LIMIT:
                    offer(p_dc[k] * hf[k] - 1,
                          {"play": "hafu", "pick": k, "odds": hf[k], "source": "dc-reweighted" if adj else "dc"})
        if best:
            ev, leg = best
            legs.append({**leg, "matchNumStr": mid, "match": f'{m.get("home")}-{m.get("away")}',
                         "ev": round(ev, 4)})
    legs.sort(key=lambda l: -l["ev"])
    return legs


def build_ticket(odds_day: dict, freq_table: dict, seq: int,
                 method: str = "freq", form: dict | None = None) -> dict:
    shape = "guilin" if seq % 2 == 1 else "meizhou"
    if method == "amix":
        # ---- amix 过渡路径（一个月回测定去留，字节级不变；Tier3 退路随本路径共存亡）----
        rows = ev_scan(odds_day, freq_table)
        # upset 腿三链：A-MIX（v5.1 至 8-27 默认）→ CRS 形状带 → 经验频率退路
        mix = mix_candidates(odds_day, freq_table, _zh_map(), _hafu_odds())
        if len(mix) >= 2:
            upset = mix[:4]
            play = f"mix-{len(upset)}串1"
            upset_note = f"A-MIX 跨池EV最优{len(upset)}腿（{','.join(l['play'] for l in upset)}）"
            low_cnt = sum(1 for l in upset
                          if (l["play"] == "crs" and l["pick"] in ("0:0", "0:1", "1:0", "1:1", "0:2", "2:0", "1:2", "2:1", "0:3", "3:0", "1:3", "3:1", "2:2") and sum(int(t) for t in l["pick"].split(":")) <= 2)
                          or (l["play"] == "ttg" and l["pick"] in ("0球", "1球")))
            if low_cnt >= 3:
                upset_note += f" ⚠️{low_cnt}/{len(upset)}腿低分同向,警惕ρ低分修正系统性偏差(8-25 TTG0球拒收教训,回填验证前慎跟)"
        else:
            upset = pick_upset_legs(rows, shape) or _fallback_upset(odds_day)
            upset = [dict(l, play="crs", pick=l.get("pick", l["score"])) for l in upset]  # settle() schema
            play = "crs-4串1"
            upset_note = f"{shape}形状 带宽{SHAPES[shape]['band']}"
    else:
        # ---- freq-band 默认路径（2026-08-27 重设计）：三步选法；<4 腿关档不硬凑 ----
        legs = freq_legs(odds_day, freq_table, form if form is not None else build_team_form(),
                         _zh_map(), SHAPES[shape]["band"])
        upset = ([dict(l, play="crs", pick=l["score"]) for l in legs[:4]]   # settle() schema
                 if len(legs) >= 4 else [])
        play = "crs-4串1"
        shifted_cnt = sum(1 for l in legs[:4] if l["shifted"])
        upset_note = (f"{shape}形状 freq-band 带宽{SHAPES[shape]['band']} · 球队平移{shifted_cnt}/{len(upset)}腿"
                      if upset else f"{shape}形状 freq-band 关档（合格腿{len(legs)}<4，不硬凑）")
    total_odds = 1.0
    for l in upset:
        total_odds *= l["odds"]
    mult = cap_multiplier(total_odds, SHAPES[shape]["multiplier"]) if upset else 1
    cost = min(SHAPES[shape]["cost"], 2 * mult) if upset else 0
    had_pool = [m for m in odds_day.get("matches", [])
                if m.get("had") and 1.55 <= min(m["had"].values())]
    def had_leg(m):
        h = m["had"]
        pick = min(h, key=h.get)
        return {"matchNumStr": m["matchNumStr"], "match": f'{m.get("home")}-{m.get("away")}',
                "play": "had", "pick": {"h": "主胜", "d": "平", "a": "客胜"}[pick], "odds": h[pick]}
    legs_pool = [had_leg(m) for m in had_pool]
    # base：两条 4 串注，共享 pool[2:4] 共 2 场（池≥6 满配；池≥4 单注降级；空池 0 注）
    if len(legs_pool) >= 6:
        base_notes = [legs_pool[0:4], legs_pool[2:6]]
    elif legs_pool:
        base_notes = [legs_pool[:4]]
    else:
        base_notes = []
    base_cost = 2 * len(base_notes)
    mid_legs = [legs_pool[:5]]
    MID_MULT = 3
    mid_cost = 2 * MID_MULT if mid_legs[0] else 0
    tiers = {
        "base": {"cost": base_cost, "legs": base_notes, "play": "had-4串1", "note": "×2注互补(共享≤2场)"},
        "mid": {"cost": mid_cost, "legs": mid_legs, "multiplier": MID_MULT, "play": "had-5串1×3倍", "note": "默认HAD"},
        "upset": {"cost": cost, "multiplier": mult, "legs": upset, "play": play,
                  "expOdds": round(total_odds, 1) if upset else 0,
                  "winIfHit": round(2 * total_odds * mult, 0) if upset else 0,
                  "note": upset_note
                          + (" · 频率退路" if upset and upset[0].get("fallback") else "")},
    }
    if len(base_notes) < 2:                                 # 设计 2 非空注组
        tiers["base"]["degraded"] = True
    if len(mid_legs[0]) < 5:                                # 设计 5 串
        tiers["mid"]["degraded"] = True
    if len(upset) < 4:                                      # 设计 4 串
        tiers["upset"]["degraded"] = True
    return {
        "date": str(date.today()), "seq": seq, "shape": shape, "method": method,
        "tiers": tiers,
        "totalCost": min(20, base_cost + mid_cost + cost),
        "densityNote": "期望返还 ≈ " + (f"{math.prod(POOL_KEEP.get(l.get('play', 'crs'), 0.8) for l in upset):.1%}"
                                        + "（各腿池水连乘" + "/".join(sorted({l.get('play', 'crs') for l in upset})) + "）" if upset
                                        else "无腿"),
        "postTaxNote": "单注奖金超1万部分税20%;4串单注限额50万已反算倍数",
    }

SNAPSHOT_STEM_RE = re.compile(r"-r\d+-boldplay")   # -rN 过程快照（{date}-rN-boldplay.json，铁律7：同日主文件=真相）


def is_process_snapshot(p: Path) -> bool:
    """-rN 过程快照判定：gate 证据（月投入/连败）与历史回放只认主文件，防同轮双计。
    与 freq_band._half_ft_rows 的 -rN 排除同口径；boldplay 的 -rN 在 -boldplay 前缀前，
    故锚 `-r\\d+-boldplay` 不锚行尾（02-results 的 -rN 是文件名后缀才锚 .json$）。开发者 sszhang"""
    return bool(SNAPSHOT_STEM_RE.search(p.stem))


def upset_month_spend(month: str, pred_dir: Path = PRED_DIR) -> float:
    """当月翻身档累计投入：扫 data/03-predictions/{month}-*-boldplay*.json 的 tiers.upset.cost
    （-rN 过程快照排除，铁律7 同日主文件=真相）。开发者 sszhang"""
    total = 0.0
    for p in pred_dir.glob(f"{month}-*-boldplay*.json"):
        if is_process_snapshot(p):
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            total += ((doc.get("tiers") or {}).get("upset") or {}).get("cost") or 0
        except (OSError, json.JSONDecodeError):
            continue
    return total


def upset_dry_streak(month: str, pred_dir: Path = PRED_DIR) -> int:
    """当月连续翻身档 0 回款轮数（SKILL v5.5 降半仓 gate 依据）：扫与 upset_month_spend
    同口径的 {month}-*-boldplay*.json（-rN 过程快照排除），按文件序从最新**已结算**票
    往回数 settle.upsetHit ==False 的连续张数；未结算票跳过（未开彩≠已见亏损），
    遇回款轮即断。开发者 sszhang"""
    streak = 0
    for p in sorted(pred_dir.glob(f"{month}-*-boldplay*.json"), reverse=True):
        if is_process_snapshot(p):
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not doc.get("settle"):                 # 未结算：跳过不断 streak
            continue
        if doc["settle"].get("upsetHit") is False:
            streak += 1
        else:
            break                                  # 回款轮：连断
    return streak


def halve_upset(t: dict) -> bool:
    """翻身档降半仓（连续≥4轮 0 回款触发）：pool-2x1x3→砍到 1 注 2串1(2元)；
    pool-4x1→倍数砍到 1；closed/已在最低仓不动。返回是否实际降仓。开发者 sszhang"""
    up = t.get("tiers", {}).get("upset") or {}
    shape = up.get("shape")
    if shape == "pool-2x1x3":
        if len(up.get("bets") or []) <= 1:
            return False
        up["legs"], up["bets"], up["cost"] = up["legs"][:2], up["bets"][:1], 2
    elif shape == "pool-4x1" and up.get("multiplier", 0) > 1:
        up["multiplier"] = 1
        up["bets"] = [{**b, "multiplier": 1} for b in up.get("bets") or []]
        up["cost"] = 2 * len(up["bets"])
    else:
        return False
    up["note"] = f'{up.get("note", "")} · 连续0回款降半仓'
    t["totalCost"] = t["tiers"]["base"]["cost"] + up["cost"]
    return True


def _is_ab(m: dict) -> bool:
    """A/B级入池口径=现 build_ticket HAD 池过滤（had 齐全且最小赔率≥1.55 的非强胆场；
    spec D7：C/D 级数据等级不够，不出三池卡）。开发者 sszhang"""
    return bool(m.get("had")) and 1.55 <= min(m["had"].values())


def _filter_onsale(all_days: dict) -> dict:
    """2026-08-31 修复：跨日存档合并把已完赛场拉进选腿池（周一出卡时周日场已踢完，
    实测保底档选出四条已完赛周日腿=废票）——按在售缓存白名单过滤，并在售
    当前 HAD 价覆盖存档旧价（出票赔率以终端实价为准）；在售缓存缺失/空时退回旧行为。
    开发者 sszhang"""
    try:
        onsale = json.loads((CACHE_DIR / "sporttery_matches.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return all_days
    cur = {m.get("code"): m for m in onsale.get("matches") or [] if m.get("code")}
    if not cur:
        return all_days
    out = dict(all_days)
    out["matches"] = [m for m in all_days.get("matches", []) if m.get("matchNumStr") in cur]
    for m in out["matches"]:
        live_had = (cur.get(m.get("matchNumStr")) or {}).get("had") or {}
        if all(live_had.get(k) for k in ("h", "d", "a")):
            m["had"] = {k: float(live_had[k]) for k in ("h", "d", "a")}
    return out


def _pick_had_legs(odds_day: dict) -> list:
    """现 build_ticket base 档 HAD 选腿复用（行为不变）：入池=_is_ab，逐场取最低赔方向，
    取前 4 腿（=现 base 第一注组 legs_pool[0:4]）；matchNumStr 缺时落 code 口径。开发者 sszhang"""
    legs = []
    for m in odds_day.get("matches", []):
        if not _is_ab(m):
            continue
        h = m["had"]
        pick = min(h, key=h.get)
        legs.append({"matchNumStr": m.get("matchNumStr") or m.get("code"),
                     "match": f'{m.get("home")}-{m.get("away")}',
                     "play": "had", "pick": {"h": "主胜", "d": "平", "a": "客胜"}[pick], "odds": h[pick]})
        if len(legs) == 4:
            break
    return legs


def _q_map_for(m: dict, freq_table: dict, form: dict, zh: dict) -> dict:
    """freq_legs 内部平移链薄封装：map_league→联赛模板→base rates→λ平移→shifted_q。
    无联赛模板 → {}（pools_card low_conf 路径自兜底全局池）。开发者 sszhang"""
    lg = map_league(m.get("league", ""))
    blob = freq_table.get(lg) if lg else None
    if not (blob and blob.get("__n", 0)):
        return {}
    lam = lambdas(league_base_rates(blob),
                  team_strength(form, _norm(zh.get(m.get("home", ""), ""))),
                  team_strength(form, _norm(zh.get(m.get("away", ""), ""))))
    return shifted_q(blob, lam)


def _card_view(m: dict, hafu_map: dict) -> dict:
    """体彩场次 → pools_card 入参视图：补 code（score_odds 存档键=matchNumStr）与 hafu 赔率
    （存档无 hafu，sporttery_matches.json 有——与 mix_candidates 同源；自带 hafu 的场次不覆盖，测试可注入）。开发者 sszhang"""
    mid = m.get("matchNumStr") or m.get("code")
    return {**m, "code": mid, "hafu": m.get("hafu") or hafu_map.get(mid) or {}}


def _lottery_legs(odds_day: dict, zh: dict, hhad_map: dict | None = None,
                  dc_params_fn=_dc_params, fusion: tuple[float, float] | None = None) -> list:
    """彩票档选腿（docs/2026-09-02-lottery-tier-design.html §03）：逐场逐选项（HAD+HHAD 三向）
    合格判定 + 场内 EV 最优 + 同场去重（铁律9），EV 降序返回。

    合格 = p_fused≥0.55 或 超低赔≤1.25 且 p_fused≥0.50；p_fused = fuse(p_dc, p_mkt体彩去水, a, b)
    （出票时点无 Pinnacle 收盘，市场腿=体彩即时价去水，与 mix_candidates 同口径）。
    无 DC 缓存/队名未入库/无 had → 该场不入池。开发者 sszhang"""
    if hhad_map is None:
        hhad_map = _hhad_odds()
    a, b = fusion if fusion else _load_fusion()
    legs = []
    for m in odds_day.get("matches", []):
        mid = m.get("matchNumStr") or m.get("code")
        had = m.get("had") or {}
        params = dc_params_fn(m, zh)
        if not (mid and had and params):
            continue
        lh, la, rho = params
        matrix = score_matrix(lh, la, rho)
        pools = [("had", {"h": had.get("h"), "d": had.get("d"), "a": had.get("a")}, 0.0)]
        hh = hhad_map.get(mid)
        if hh:
            pools.append(("hhad", {k: hh[k] for k in ("h", "d", "a")}, hh["goalLine"]))
        best = None                                              # (ev, leg) 场内 EV 最优合格项
        for play, odds3, gl in pools:
            if not all(odds3.get(k) for k in ("h", "d", "a")):
                continue
            o3 = [float(odds3["h"]), float(odds3["d"]), float(odds3["a"])]
            p_mkt = devig_n(o3)
            if play == "had":                                    # 三向聚合：净胜差 i-j
                def bucket(i, j, gl=0.0):
                    d = i - j + gl
                    return 0 if d > 0 else (1 if d == 0 else 2)
            else:                                                # 让球三向：i + goalLine vs j
                def bucket(i, j, gl=gl):
                    d = i + gl - j
                    return 0 if d > 0 else (1 if d == 0 else 2)
            p_dc = [0.0, 0.0, 0.0]
            for i in range(7):
                for j in range(7):
                    p_dc[bucket(i, j)] += float(matrix[i, j])
            p_f = fuse(p_dc, p_mkt, a, b)
            names = {"had": ("主胜", "平", "客胜"),
                     "hhad": ("让球主胜", "让球平", "让球客胜")}[play]
            for k in range(3):
                p, o = p_f[k], o3[k]
                if not (p >= LOTTERY_MIN_P
                        or (o <= LOTTERY_LOW_ODDS and p >= LOTTERY_LOW_ODDS_MIN_P)):
                    continue
                ev = p * o - 1
                leg = {"matchNumStr": mid, "match": f'{m.get("home")}-{m.get("away")}',
                       "play": play, "pick": names[k], "odds": o,
                       "p": round(p, 4), "ev": round(ev, 4)}
                if play == "hhad":
                    leg["goalLine"] = gl
                if best is None or ev > best[0]:
                    best = (ev, leg)
        if best:
            legs.append(best[1])
    legs.sort(key=lambda l: -l["ev"])
    return legs


def _lottery_tier(legs: list) -> dict:
    """彩票档组装：池≥4 出 N串1×1倍=2元（bets 全索引单注——派彩走 settle._tier_bets
    同源链路：全中才回款）；池<4 关档不硬凑。无预算管理（设计拍板C）。开发者 sszhang"""
    legs = legs[:LOTTERY_MAX_LEGS]
    if len(legs) < LOTTERY_MIN_LEGS:
        return {"shape": "closed", "cost": 0, "legs": legs,
                "note": f"彩票档关档（合格腿{len(legs)}<{LOTTERY_MIN_LEGS}，不硬凑）"}
    total = math.prod(l["odds"] for l in legs)
    return {"shape": f"lottery-{len(legs)}x1", "cost": 2, "legs": legs,
            "bets": [{"legs": list(range(len(legs))), "multiplier": 1}],
            "expOdds": round(total, 1), "winIfHit": round(2 * total, 0),
            "note": f"{len(legs)}串1×1倍=2元 · 全中≈{2 * total:.0f}元 · 计入轮次红线{ROUND_REDLINE}元"}


def build_three_tier(odds_day: dict, freq_table: dict, seq: int, zh: dict, form: dict,
                     hafu_map: dict | None = None) -> dict:
    """三档结构（spec §4.1 两档 + docs/2026-09-02 彩票档）：保底 HAD 4串11(22元) +
    翻身多池引擎(seq轮换) + 彩票 N串1×1倍(2元,合格腿全上4~8,HAD/HHAD)。
    选腿: 保底=现HAD选腿(_pick_had_legs); 翻身=各场 pools_card rec_upset 候选(同场≤1腿,
    按EV降序); 彩票=_lottery_legs(p_fused≥0.55/超低赔通道)。开发者 sszhang"""
    had_legs = _pick_had_legs(odds_day)
    bets = [{"legs": list(c), "multiplier": 1} for c in expand_combos(len(had_legs))]
    base = {"cost": 2 * len(bets), "legs": had_legs, "play": "had-4串11", "bets": bets,
            "note": "6×2串1+4×3串1+1×4串1 · 中2关回1注2串1"}
    if len(had_legs) < 4:
        base["degraded"] = True
    hafu_map = hafu_map if hafu_map is not None else _hafu_odds()
    cards = [pools_card(_card_view(m, hafu_map), _q_map_for(m, freq_table, form, zh),
                        form, zh, freq_table)
             for m in odds_day.get("matches", []) if _is_ab(m)]
    # 翻身: seq 奇=跨池2串1×3 / 偶=跨池4串1×N; 腿=各场 rec_upset(同场≤1; 全分歧场
    # rec_upset=None → 不出翻身腿, freq_band pools_card I1 裁定)
    cand = sorted((c["rec_upset"] for c in cards if c.get("rec_upset")), key=lambda c: -c["ev"])
    seen, legs = set(), []
    for c in cand:
        code = c.get("code")
        if code in seen:
            continue
        seen.add(code)
        legs.append({"matchNumStr": code, "match": c.get("match"), "play": c["pool"],
                     "pick": c["pick"], "odds": c["odds"], "q": c["q"], "ev": c["ev"]})
    if seq % 2 == 1:                                        # 容错引擎: 3注独立2串1
        n = min(3, len(legs) // 2)
        upset = {"shape": "pool-2x1x3", "cost": n * 2, "legs": legs[:n * 2],
                 "bets": [{"legs": [i * 2, i * 2 + 1], "multiplier": 1} for i in range(n)],
                 "note": f"{n}注跨池2串1(同场≤1腿)"}
    else:                                                   # 彩票: 跨池4串1×倍数
        m4 = legs[:4]
        mult = cap_multiplier(math.prod(l["odds"] for l in m4), 4, 50.0) if len(m4) == 4 else 0
        upset = {"shape": "pool-4x1", "cost": 2 * mult if mult else 0, "legs": m4,
                 "multiplier": mult,
                 "bets": ([{"legs": [0, 1, 2, 3], "multiplier": mult}] if mult else []),
                 "note": "跨池4串1(木桶≤4关)"}
    if upset["cost"] < 2:                                   # 腿不足关档(铁律8不硬凑)
        upset = {"shape": "closed", "cost": 0, "legs": legs, "note": "翻身候选腿不足·关档"}
    lottery = _lottery_tier(_lottery_legs(odds_day, zh))
    total_cost = base["cost"] + upset["cost"] + lottery["cost"]
    out = {"structure": "new", "date": str(date.today()), "seq": seq,
           "tiers": {"base": base, "upset": upset, "lottery": lottery},
           "totalCost": total_cost,
           "cards": cards, "ranAt": str(date.today())}
    # 轮次预算红线（含彩票档，preference.json roundRedline 同步）
    if total_cost > ROUND_REDLINE:
        out["budgetWarning"] = f"totalCost {total_cost} > roundRedline {ROUND_REDLINE}"
    return out


def render_ticket(t: dict) -> str:
    """出票卡文本渲染——可读性硬规范（大哥 2026-08-30 要求）：
    ①顶部摘要行(结构/seq/总成本/两档成本)；②每档一节、逐腿一行
      `编号 │ 对阵 │ 玩法 pick @赔率 │ EV`（列宽对齐，│分隔）；
    ③三池卡候选区每场两行(保底视角/翻身视角)；④旗标用 emoji 前缀(⚠分歧/🟡低置信)；
    ⑤结尾预算行(月翻身累计x/40·红线提示)；⑥与 v5.4.2 出票核对单同款式(编号│对阵)。
    开发者 sszhang"""
    lot = t["tiers"].get("lottery") or {}
    lines = [f"┌ 阶梯出票卡 v2 · seq{t['seq']} · 总成本 {t['totalCost']}元 "
             f"(保底{t['tiers']['base']['cost']} + 翻身{t['tiers']['upset']['cost']}"
             + (f" + 彩票{lot['cost']}" if lot else "") + ") ────────"]
    rows = [("保底", t["tiers"]["base"]), ("翻身", t["tiers"]["upset"])]
    if lot:
        rows.append(("彩票", lot))
    for name, tier in rows:
        lines.append(f"│ {name}档 {tier['play'] if 'play' in tier else tier['shape']}"
                     f" · {tier['cost']}元 · {tier.get('note', '')}")
        for l in tier.get("legs") or []:
            gl_txt = f"(让{l['goalLine']:+g})" if l.get("goalLine") is not None else ""
            lines.append(f"│   {l['matchNumStr']} │ {l.get('match', '')[:14]:14s} │ "
                         f"{l['play'].upper()} {l['pick']}{gl_txt} @{l['odds']}"
                         + (f" │ EV{l['ev']:+.0%}" if "ev" in l else ""))
    if t.get("upsetHalved"):
        lines.append(f"│ ⚠连续{t['upsetHalved']}轮翻身0回款·仓位减半")
    lines.append("│ 三池推荐(A/B级场):")
    for c in t.get("cards") or []:
        if not c.get("candidates"):
            continue
        fl = ("⚠" if "divergence" in c["flags"] else "") + ("🟡" if "low_conf" in c["flags"] else "")
        rb, ru = c["rec_base"], c["rec_upset"]
        ru_txt = f"{ru['pool'].upper()} {ru['pick']}@{ru['odds']}" if ru else "—(分歧排除)"
        lines.append(f"│   {c['code']} {fl} 保底→{rb['pool'].upper()} {rb['pick']}(q{rb['q']:.0%})"
                     f" · 翻身→{ru_txt}")
    spend = upset_month_spend(str(date.today())[:7])
    warn = " ⚠月预算红线!" if spend >= MONTHLY_UPSET_CAP else ""
    lines.append(f"└ 翻身月预算: {spend:.0f}/{MONTHLY_UPSET_CAP:.0f}元{warn} · 出票核对单见 v5.4.2 格式")
    return "\n".join(lines)


def _direction(score: str) -> str:
    h, a = (int(x) for x in score.split(":"))
    return "主胜" if h > a else ("平" if h == a else "客胜")


HAFU_DIR = {"h": "主胜", "d": "平", "a": "客胜"}   # HAFU 两字母键 → 方向中文（首=半场 次=全场）

def _leg_hit(leg: dict, ent, default_play: str):
    """单腿命中判定 → True/False/None（赛果缺失/HAFU无半场=None 待人工）。开发者 sszhang

    HAD pick 由比分方向推导；CRS 精确比对（选项兼容 pick/score 两键——计划口径
    用 pick，真实出票 JSON 的 upset 腿用 score）；TTG 总进球双词汇统一（pools_card
    体彩池键 s0..s7 与 legacy "2球"/"2"/"7+"——s 前缀剥离后同判，含 7+ 档）；HAFU
    需 half（backfill 体彩链路落盘，ESPN 链路无半场 → None 待人工）。"""
    if ent is None:
        return None                                    # 赛果缺失
    sc = ent.get("score") if isinstance(ent, dict) else ent
    half = ent.get("half") if isinstance(ent, dict) else None
    if sc is None:
        return None
    play = leg.get("play") or default_play             # 08-24 A-MIX 老卡 upset 腿无 play 键
    if play == "crs":
        return (leg.get("pick") or leg.get("score")) == sc
    if play == "ttg":
        h_, a_ = (int(x) for x in sc.split(":"))
        want = str(leg.get("pick") or leg.get("score")).replace("球", "").replace("+", "").lstrip("s")
        return (h_ + a_ >= 7 and want == "7") if "7" in want and int(want) == 7 else (h_ + a_ == int(want))
    if play == "hafu":
        if not half:
            return None                                # 无半场比分（ESPN 链路/旧档），人工判
        pick = str(leg.get("pick") or leg.get("score"))
        return (pick[:1] in HAFU_DIR and pick[1:] in HAFU_DIR
                and _direction(half) == HAFU_DIR[pick[:1]]
                and _direction(sc) == HAFU_DIR[pick[1:]])
    if play == "hhad":
        gl = leg.get("goalLine")
        if gl is None:
            return None                                # 让球线缺失（旧档），人工判
        h_, a_ = (int(x) for x in sc.split(":"))
        d_ = h_ + gl - a_
        return leg["pick"] == ("让球主胜" if d_ > 0 else ("让球平" if d_ == 0 else "让球客胜"))
    return leg["pick"] == _direction(sc)


def _tier_bets(blob: dict, flat_hits: list) -> tuple:
    """两档 bets 结构单档结算 → (派彩, 全部注全中)。开发者 sszhang

    派彩=Σ全中注 2×倍数×Π腿赔率（推演对照口径，无税无上限，与 legacy 无税
    payout 一致）；空注/腿索引越界判不中（防 vacuous-True 假阳）；无 bets
    （closed 关档）→ (0.0, False)。"""
    legs, bets = blob.get("legs") or [], blob.get("bets") or []
    payout, flags = 0.0, []
    for bet in bets:
        idxs = bet.get("legs") or []
        hit = bool(idxs) and all(0 <= i < len(legs) and flat_hits[i] is True for i in idxs)
        flags.append(hit)
        if hit:
            payout += 2 * bet.get("multiplier", 1) * math.prod(legs[i]["odds"] for i in idxs)
    return payout, (bool(bets) and all(flags))


def settle(ticket: dict, results: dict) -> dict:
    """逐 leg 判定（phase2 任务6；v2 任务5 双形状）。results: matchNumStr → 'h:a' 或 {'score':'h:a','half':'h:a'}。

    双形状分派：①两档 bets 结构（structure=new 或任一档带 bets）：payout=base+
    upset 各档 Σ全中注 2×倍数×Π腿赔率，另记 tierPayout 分档回款（对照报告用）；
    upsetHit=翻身档有 bets 且每注腿全中（pool-4x1=倍数注全中；closed 关档=False）。
    ②legacy 票（无 bets）原口径字节不变：payout 只算 upset 档全中（合赔×2×倍数，
    推演库口径；实票结算走 tickets.json 账本）。upset 空腿 legacy 票判 False
    （vacuous-True 防守：all([])==True 会让关档空轮污染 dry-streak 回款记录）。
    开发者 sszhang"""
    is_new = ticket.get("structure") == "new" or any(
        bool(t.get("bets")) for t in ticket.get("tiers", {}).values())
    leg_hits, tier_payout, upset_hit = {}, {}, False
    for tier, blob in ticket.get("tiers", {}).items():
        leg_hits[tier] = []
        raw_legs = blob.get("legs") or []
        notes = raw_legs if raw_legs and isinstance(raw_legs[0], list) else [raw_legs]
        default_play = "crs" if tier == "upset" else "had"
        flat_hits = []
        for note_legs in notes:
            hits = [_leg_hit(leg, results.get(leg["matchNumStr"]), default_play) for leg in note_legs]
            leg_hits[tier].append(hits)
            flat_hits.extend(hits)
        if is_new:
            tier_payout[tier], hit_all = _tier_bets(blob, flat_hits)
            if tier == "upset":
                upset_hit = hit_all
    if is_new:
        payout = sum(tier_payout.values())
        return {"legHits": leg_hits, "upsetHit": upset_hit, "payout": payout,
                "tierPayout": tier_payout,
                "densityRecovered": round(payout / ticket.get("totalCost", 1), 4)}
    u = ticket["tiers"].get("upset", {})
    upset_hit = bool(u) and bool(u.get("legs")) and all(h is True for h in leg_hits.get("upset", [[]])[0])
    payout = 0.0
    if upset_hit:
        raw = 2 * u["multiplier"]
        for leg in u["legs"]:
            raw *= leg["odds"]
        payout = raw
    return {"legHits": leg_hits, "upsetHit": upset_hit, "payout": payout,
            "densityRecovered": round(payout / ticket.get("totalCost", 1), 4)}


def _load_results(d: str) -> dict:
    """出票日 d~d+2 三天赛果 → {场次编号: {'score':'h:a','half':'h:a'|None}}（result/half 为 'h-a' 需转冒号）。"""
    out = {}
    try:
        base = datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError:
        return out
    for delta in (-1, 0, 1, 2):   # -1：体彩票"周X编号"晚场跨自然日（周一001 实际周日晚开赛，出卡日=次日）
        p = ROOT / "data" / "02-results" / f"{base + timedelta(days=delta)}.json"
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        for rec in data.get("matches") or []:
            if rec.get("code") and rec.get("result") and rec["result"] != "不可得":
                out[rec["code"]] = {"score": str(rec["result"]).replace("-", ":"),
                                    "half": str(rec["half"]).replace("-", ":") if rec.get("half") else None}
    return out


def cmd_settle() -> None:
    # 旧→新循环结算全部卡：只取最新一张时，漏跑一轮的旧卡永远轮不到结算（2026-08-28
    # 08-27 卡被 08-28 卡顶住教训）；路径走 ROOT 绝对定位，run.py sh() 的 cwd=engine/scripts
    # 下裸相对 glob 落空 → verify 链路静默"无出票 JSON"同日实证
    paths = sorted((ROOT / "data" / "03-predictions").glob("*-boldplay*.json"))  # v2: 含 -legacy 双轨对照卡
    if not paths:
        print("[boldplay] 无出票 JSON"); return
    for p in paths:
        try:
            ticket = json.loads(p.read_text(encoding="utf-8"))
            if ticket.get("settle"):
                print(f"[boldplay] {p.name} 已结算(payout={ticket['settle']['payout']:.0f})，跳过"); continue
            results = _load_results(ticket["date"])
            codes = set()
            for tier in ticket["tiers"].values():
                legs = tier.get("legs") or []
                for note in (legs if legs and isinstance(legs[0], list) else [legs]):
                    codes.update(l["matchNumStr"] for l in note)
            missing = sorted(c for c in codes if c not in results)
            if missing:
                print(f"[boldplay] {p.name} 赛果未回填: {', '.join(missing)}（完赛后重跑）"); continue
            res = settle(ticket, results)
            ticket["settle"] = {**res, "settledAt": str(date.today())}
            p.write_text(json.dumps(ticket, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            u = res["legHits"].get("upset", [[]])[0]
            print(f"[boldplay] settle {p.name}: upset {sum(1 for h in u if h)}/{len(u)}关 "
                  f"全中={res['upsetHit']} payout={res['payout']:.0f} 密度回收={res['densityRecovered']} → 已写回 settle 字段")
            lot_pay = (res.get("tierPayout") or {}).get("lottery")
            if lot_pay is not None:
                lot_hits = [h for note in res["legHits"].get("lottery", []) for h in note]
                print(f"[boldplay]   彩票档: {sum(1 for h in lot_hits if h)}/{len(lot_hits)}关 "
                      f"派彩{lot_pay:.0f}元（全中才回款，断=0）")
        except Exception as e:   # 单卡损坏不中断整轮结算（v2 任务5：新旧/两档结构混扫兼容）
            print(f"[boldplay] settle {p.name} 失败(跳过): {type(e).__name__}: {e}")


def _selftest_three_tier():
    from collections import Counter
    # 6 场裁定（brief 2 场拿不出保底 4 腿 HAD）：周日004 ttg 赔率工程化 12.0 →
    # TTG EV6.2 独占相近带（hafu dd EV≈2.1），保底/翻身双行稳定落 TTG（渲染锚点）。
    fake_day = {"matches": [
        {"code": "周日004", "league": "德乙", "home": "圣保利", "away": "凯泽",
         "had": {"h": 1.72, "d": 3.6, "a": 3.7},
         "crs": {"0:2": 24.0, "1:1": 7.5}, "ttg": {"s2": 12.0}, "hafu": {"dd": 6.25}},
        {"code": "周一002", "league": "芬超", "home": "赫尔火花", "away": "TPS",
         "had": {"h": 1.9, "d": 3.5, "a": 3.15},
         "crs": {"0:2": 20.0}, "ttg": {"s3": 3.55}, "hafu": {"aa": 5.2}},
        {"code": "周六007", "league": "日职", "home": "东京绿茵", "away": "冈山绿雉",
         "had": {"h": 1.62, "d": 3.7, "a": 4.4},
         "crs": {"1:1": 8.0}, "ttg": {"s3": 3.55}, "hafu": {"dd": 6.5, "hd": 9.0}},
        {"code": "周六012", "league": "瑞超", "home": "哥德堡", "away": "天狼星",
         "had": {"h": 2.05, "d": 3.3, "a": 3.2},
         "crs": {"1:1": 8.0}, "ttg": {"s3": 3.55}, "hafu": {"dd": 7.0}},
        {"code": "周日009", "league": "挪超", "home": "维京", "away": "奥勒松",
         "had": {"h": 1.68, "d": 3.8, "a": 3.9},
         "crs": {"1:1": 8.0}, "ttg": {"s3": 3.55}, "hafu": {"dd": 6.8}},
        {"code": "周日015", "league": "韩职", "home": "全北现代", "away": "大田市民",
         "had": {"h": 1.58, "d": 3.9, "a": 4.2},
         "crs": {"1:1": 8.0}, "ttg": {"s3": 3.55}, "hafu": {"dd": 7.2}},
    ]}
    ft = {"germany-2-bundesliga": Counter({"1:1": 120, "2:2": 40, "__n": 200})}  # 德乙模板 n=200 免 low_conf
    t = build_three_tier(fake_day, ft, seq=9, zh={}, form={})
    assert t["structure"] == "new"
    base = t["tiers"]["base"]
    assert base["cost"] == 22 and len(base["legs"]) == 4            # 4串11=22元
    assert base["play"] == "had-4串11"
    up = t["tiers"]["upset"]
    assert up["shape"] in ("pool-2x1x3", "pool-4x1")               # seq奇偶轮换
    codes = [l["matchNumStr"] for l in up["legs"]]
    assert len(codes) == len(set(codes))                            # 同场最多1腿(硬约束)
    assert 2 <= up["cost"] <= 8
    assert 24 <= t["totalCost"] <= 30                               # zh={} 无DC参数→彩票档关档cost=0
    lot = t["tiers"]["lottery"]
    assert lot["shape"] == "closed" and lot["cost"] == 0            # 合格腿0<4 关档不硬凑
    txt = render_ticket(t)
    for kw in ("出票核对单", "周日004", "圣保利", "TTG", "彩票档关档", "│"):  # 可读性规范锚点
        assert kw in txt, kw
    print("[selftest] build_three_tier + render_ticket OK")


def _selftest_lottery():
    """彩票档自检（docs/2026-09-02-lottery-tier-design.html）：合格腿全上/同场去重留EV最高/
    池<4关档/池>8截前8/HHAD让球判定（goalLine 三向+_leg_hit）。开发者 sszhang"""
    fake_dc = lambda m, zh: (2.0, 0.85, -0.05)         # 主强λ：p_dc 主胜~0.7+
    mk = lambda i, h, d, a: {"matchNumStr": f"周六00{i}", "match": f"m{i}",
                             "league": "英超", "home": f"H{i}", "away": f"A{i}",
                             "had": {"h": h, "d": d, "a": a}}
    day = {"matches": [mk(1, 1.45, 4.00, 6.50), mk(2, 1.42, 4.20, 6.80),
                       mk(3, 1.48, 3.90, 6.20), mk(4, 1.44, 4.10, 6.60),
                       mk(5, 1.50, 3.80, 5.90)]}        # 5场低赔主胜 → 池=5 全上
    legs = _lottery_legs(day, zh={}, dc_params_fn=fake_dc, fusion=(0.4, 1.0))
    assert len(legs) == 5 and all(l["pick"] == "主胜" for l in legs)   # 全主胜入池
    assert all(l["p"] >= LOTTERY_LOW_ODDS_MIN_P for l in legs)
    # 同场去重：hhad 让球主胜与 had 主胜同场 → 只留 EV 最高一条
    hhad_map = {"周六001": {"goalLine": -1.0, "h": 2.10, "d": 3.30, "a": 3.05}}
    legs2 = _lottery_legs(day, zh={}, hhad_map=hhad_map, dc_params_fn=fake_dc,
                          fusion=(0.4, 1.0))
    assert len(legs2) == 5
    assert sum(1 for l in legs2 if l["matchNumStr"] == "周六001") == 1
    # 池<4 关档 / 池>8 截前 8
    t3 = _lottery_tier(legs[:3])
    assert t3["shape"] == "closed" and t3["cost"] == 0
    day9 = {"matches": [mk(i, 1.40 + 0.015 * i, 3.80, 5.50) for i in range(1, 10)]}
    legs9 = _lottery_legs(day9, zh={}, dc_params_fn=fake_dc, fusion=(0.4, 1.0))
    t9 = _lottery_tier(legs9)
    assert t9["shape"] == "lottery-8x1" and len(t9["legs"]) == 8 and t9["cost"] == 2
    assert t9["bets"] == [{"legs": [0, 1, 2, 3, 4, 5, 6, 7], "multiplier": 1}]
    # HHAD 判定：goalLine=-1，1:0→让球平 / 2:0→让球主胜 / 0:1→让球客胜；无线=None
    gl_leg = {"play": "hhad", "pick": "让球平", "goalLine": -1.0}
    assert _leg_hit(gl_leg, "1:0", "hhad") is True
    assert _leg_hit({**gl_leg, "pick": "让球主胜"}, "2:0", "hhad") is True
    assert _leg_hit({**gl_leg, "pick": "让球客胜"}, "0:1", "hhad") is True
    assert _leg_hit({**gl_leg, "pick": "让球主胜"}, "1:0", "hhad") is False
    assert _leg_hit({"play": "hhad", "pick": "让球主胜"}, "1:0", "hhad") is None  # 无goalLine人工判
    # 全中派彩：bets 结构走 _tier_bets 同源（全中=2×Π赔率，断1场=0）
    tk = {"structure": "new", "totalCost": 24,
          "tiers": {"lottery": t9, "base": {"cost": 22, "legs": [], "bets": []},
                    "upset": {"shape": "closed", "cost": 0, "legs": []}}}
    res = settle(tk, {l["matchNumStr"]: "2:0" for l in t9["legs"]})
    assert res["tierPayout"]["lottery"] == 2 * math.prod(l["odds"] for l in t9["legs"])
    res1 = settle(tk, {l["matchNumStr"]: ("2:0" if i else "0:2")   # 第0腿断
                       for i, l in enumerate(t9["legs"])})
    assert res1["tierPayout"]["lottery"] == 0.0
    print("[selftest] lottery tier(选腿/去重/关档/截断/HHAD判定/派彩) OK")


def _selftest_settle():
    """两档 bets 结构结算自检（v2 任务5）：4串11中2关回1注2串1 / 跨池2串1逐注独立 /
    pool-4x1 倍数注 / closed 关档防崩（无 bets 不假阳）/ legacy 票走原口径不变。开发者 sszhang"""
    had = lambda mid, pick, o: {"matchNumStr": mid, "match": mid, "play": "had", "pick": pick, "odds": o}
    base_legs = [had("周六001", "主胜", 1.8), had("周六002", "主胜", 2.0),
                 had("周六003", "主胜", 2.5), had("周六004", "平", 3.2)]
    base = {"cost": 22, "legs": base_legs,
            "bets": [{"legs": list(c), "multiplier": 1} for c in expand_combos(4)]}
    up_legs = [{"matchNumStr": "周六005", "match": "m", "play": "ttg", "pick": "2球", "odds": 3.5},
               {"matchNumStr": "周六006", "match": "m", "play": "crs", "pick": "1:1", "odds": 7.0},
               {"matchNumStr": "周六007", "match": "m", "play": "had", "pick": "主胜", "odds": 1.7},
               {"matchNumStr": "周六008", "match": "m", "play": "hafu", "pick": "hh", "odds": 5.0},
               {"matchNumStr": "周六009", "match": "m", "play": "hafu", "pick": "hh", "odds": 4.0},
               {"matchNumStr": "周六010", "match": "m", "play": "crs", "pick": "0:0", "odds": 9.0}]
    results = {"周六001": "2:0", "周六002": "3:1", "周六003": "0:1", "周六004": "1:2",  # base 中2/4
               "周六005": "1:1", "周六006": "1:1",                                      # pair1 ttg2球+crs1:1 全中
               "周六007": "2:1",                                                        # had 主胜 ✓
               "周六008": {"score": "0:1", "half": "0:1"},                              # hafu hh ✗ → pair2 挂
               "周六009": {"score": "2:0", "half": "1:0"},                              # hafu hh ✓
               "周六010": "2:0"}                                                        # crs 0:0 ✗ → pair3 挂
    # ① pool-2x1x3：3注独立2串1仅 pair1 全中；base 4串11 中2关回 1 注2串1
    t21 = {"structure": "new", "totalCost": 28, "tiers": {"base": base,
        "upset": {"shape": "pool-2x1x3", "cost": 6, "legs": up_legs,
                  "bets": [{"legs": [0, 1], "multiplier": 1},
                           {"legs": [2, 3], "multiplier": 1},
                           {"legs": [4, 5], "multiplier": 1}]}}}
    r21 = settle(t21, results)
    assert r21["legHits"]["base"] == [[True, True, False, False]]
    assert r21["tierPayout"] == {"base": 2 * 1.8 * 2.0, "upset": 2 * 3.5 * 7.0}   # 精确浮点
    assert r21["payout"] == 2 * 1.8 * 2.0 + 2 * 3.5 * 7.0
    assert r21["upsetHit"] is False                          # 3对只中1对 ≠ 翻身档全中
    # ② pool-4x1：单注倍数票全中 → upsetHit 真、2×倍数×Π腿赔率
    t41 = {"structure": "new", "totalCost": 28, "tiers": {"base": base,
        "upset": {"shape": "pool-4x1", "cost": 6, "legs": [up_legs[i] for i in (0, 1, 2, 4)],
                  "multiplier": 3, "bets": [{"legs": [0, 1, 2, 3], "multiplier": 3}]}}}
    r41 = settle(t41, results)
    assert r41["upsetHit"] is True
    assert abs(r41["tierPayout"]["upset"] - 2 * 3 * 3.5 * 7.0 * 1.7 * 4.0) < 1e-9
    assert abs(r41["payout"] - (2 * 1.8 * 2.0 + 2 * 3 * 3.5 * 7.0 * 1.7 * 4.0)) < 1e-9
    # ③ closed 关档：无 bets → upsetHit 假、0 回款、settle 不崩（无 KeyError）
    tc = {"structure": "new", "totalCost": 22, "tiers": {"base": base,
        "upset": {"shape": "closed", "cost": 0, "legs": up_legs[:1], "note": "翻身候选腿不足·关档"}}}
    rc = settle(tc, results)
    assert rc["upsetHit"] is False and rc["tierPayout"]["upset"] == 0.0
    assert rc["payout"] == rc["tierPayout"]["base"] == 2 * 1.8 * 2.0
    # ④ legacy 票（无 bets）：原口径不变——upset 全中=2×倍数×Π腿赔率，无 tierPayout 键
    lg = {"totalCost": 18, "tiers": {
        "base": {"cost": 4, "legs": [[base_legs[0], base_legs[1]], [base_legs[2], base_legs[3]]]},
        "upset": {"cost": 8, "multiplier": 4, "legs": [up_legs[i] for i in (0, 1, 2, 4)]}}}
    rl = settle(lg, results)
    assert rl["upsetHit"] is True and "tierPayout" not in rl
    assert abs(rl["payout"] - 2 * 4 * 3.5 * 7.0 * 1.7 * 4.0) < 1e-9
    # ⑤ legacy 空档 upset 票：vacuous-True 防守——空腿不判全中（否则污染 dry-streak 回款记录）
    le = {"totalCost": 4, "tiers": {"base": {"cost": 4, "legs": []},
                                    "upset": {"cost": 0, "legs": []}}}
    rle = settle(le, results)
    assert rle["upsetHit"] is False and rle["payout"] == 0.0
    # ⑥ TTG sN 词汇（C1 回归）：pools_card 体彩池键 s2/s7 与 legacy 2球/7+ 同判
    assert _leg_hit({"play": "ttg", "pick": "s2"}, {"score": "1:1"}, "ttg") is True     # 总2球
    assert _leg_hit({"play": "ttg", "pick": "s7"}, {"score": "3:4"}, "ttg") is True    # 7球=7+档
    assert _leg_hit({"play": "ttg", "pick": "s3"}, {"score": "1:1"}, "ttg") is False   # 2≠3
    assert _leg_hit({"play": "ttg", "pick": "7+"}, {"score": "4:3"}, "ttg") is True    # legacy 7+ 仍通
    print("[selftest] settle(两档bets/legacy双形状) OK")


def _selftest_dry_streak():
    """连续翻身0回款降半仓 gate 自检（v2 T7 评审沉淀）：4张已结算全 False → streak=4 且
    两形状实降（2x1x3→1注2元 / 4x1→倍数1）/ 未结算票跳过 / 回款轮断连 / closed 不动 /
    最低仓 4x1 mult=1 不实降且不落 upsetHalved 存证。开发者 sszhang"""
    import tempfile
    had = lambda i: {"matchNumStr": f"周六00{i}", "match": "m", "play": "had",
                     "pick": "主胜", "odds": 2.0}
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        for i, day in enumerate((26, 27, 28, 29)):
            (d / f"2026-08-{day}-boldplay.json").write_text(json.dumps(
                {"date": f"2026-08-{day}", "settle": {"upsetHit": False}}), encoding="utf-8")
        assert upset_dry_streak("2026-08", d) == 4
        # -rN 过程快照不入证据（I2 回归·铁律7 同日主文件=真相）：-legacy 双轨卡仍入账
        (d / "2026-08-29-r2-boldplay.json").write_text(json.dumps(
            {"date": "2026-08-29", "settle": {"upsetHit": False},
             "tiers": {"upset": {"cost": 8}}}), encoding="utf-8")
        (d / "2026-08-27-boldplay-legacy.json").write_text(json.dumps(
            {"date": "2026-08-27", "tiers": {"upset": {"cost": 2}}}), encoding="utf-8")
        assert upset_dry_streak("2026-08", d) == 4                      # 快照已结算False不续败(否则5)
        assert upset_month_spend("2026-08", d) == 2.0                   # 快照8不入账·legacy真票2入账
        (d / "2026-08-30-boldplay.json").write_text(json.dumps(
            {"date": "2026-08-30"}), encoding="utf-8")                  # 未结算：跳过不断
        assert upset_dry_streak("2026-08", d) == 4
        (d / "2026-08-25-boldplay.json").write_text(json.dumps(
            {"date": "2026-08-25", "settle": {"upsetHit": True}}), encoding="utf-8")
        assert upset_dry_streak("2026-08", d) == 4                      # 更早回款轮不在连上
        (d / "2026-08-29-boldplay.json").write_text(json.dumps(
            {"date": "2026-08-29", "settle": {"upsetHit": True}}), encoding="utf-8")
        assert upset_dry_streak("2026-08", d) == 0                      # 最新已结算票回款 → 连断
    t21 = {"tiers": {"base": {"cost": 22, "play": "had-4串11", "legs": []},
                     "upset": {"shape": "pool-2x1x3", "cost": 6, "legs": [had(i) for i in (1, 2, 3, 4, 5, 6)],
                               "bets": [{"legs": [0, 1], "multiplier": 1},
                                        {"legs": [2, 3], "multiplier": 1},
                                        {"legs": [4, 5], "multiplier": 1}]}}}
    assert halve_upset(t21) is True
    assert t21["tiers"]["upset"]["cost"] == 2 and len(t21["tiers"]["upset"]["bets"]) == 1
    assert len(t21["tiers"]["upset"]["legs"]) == 2 and t21["totalCost"] == 24
    t41 = {"tiers": {"base": {"cost": 22, "play": "had-4串11", "legs": []},
                     "upset": {"shape": "pool-4x1", "cost": 8, "multiplier": 4,
                               "legs": [had(i) for i in (1, 2, 3, 4)],
                               "bets": [{"legs": [0, 1, 2, 3], "multiplier": 4}]}}}
    assert halve_upset(t41) is True
    assert t41["tiers"]["upset"]["multiplier"] == 1 and t41["tiers"]["upset"]["cost"] == 2
    tc = {"tiers": {"base": {"cost": 22, "play": "had-4串11", "legs": []},
                    "upset": {"shape": "closed", "cost": 0, "legs": [], "note": "关档"}}}
    assert halve_upset(tc) is False                                    # 关档无仓位不动
    t41f = {"tiers": {"base": {"cost": 22, "play": "had-4串11", "legs": []},
                      "upset": {"shape": "pool-4x1", "cost": 2, "multiplier": 1,
                                "legs": [had(i) for i in (1, 2, 3, 4)],
                                "bets": [{"legs": [0, 1, 2, 3], "multiplier": 1}]}}}
    assert halve_upset(t41f) is False and "totalCost" not in t41f      # 4x1 倍数已在1=最低仓：不实降不改票
    streak = 4
    assert not (streak >= 4 and t41f["tiers"]["upset"]["cost"] > 0 and halve_upset(t41f))
                                          # main 落键同式：False → upsetHalved 存证不落（防虚假减半）
    t21["seq"], t21["cards"] = 9, []
    assert "⚠连续4轮翻身0回款·仓位减半" in render_ticket({**t21, "upsetHalved": 4})
    print("[selftest] upset_dry_streak + halve_upset OK")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "settle":
        return cmd_settle()
    if "--selftest" in args:
        _selftest_three_tier()
        _selftest_settle()
        _selftest_dry_streak()
        _selftest_lottery()
        return
    method = "amix" if "--method=amix" in args else "freq"
    structure = "legacy" if "--structure=legacy" in args else "new"   # v2 两档默认，legacy 双轨对照一个月
    dry = "--dry" in args
    # ROOT 绝对定位（cmd_settle 同款：cwd=engine/scripts 下裸相对 glob 落空）
    latest = sorted(glob.glob(str(ROOT / "engine/cache/score_odds/*.json")))[-1]
    odds = json.load(open(latest, encoding="utf-8"))
    table = build_freq_table()
    # -rN 过程快照排除（2026-09-04 审计 P1）：seq 与 monthly_spend/upset_month_spend 口径归一。
    # 修前 13 文件 seq=14 → 修后 11 文件 seq=12，偶→偶不翻翻身档轮换；未来快照数为奇时
    # seq 奇偶翻转属预期行为变更，提交须注明。
    hist = [json.load(open(p, encoding="utf-8"))
            for p in glob.glob(str(ROOT / "data/03-predictions/*-boldplay.json"))
            if not is_process_snapshot(Path(p))]
    seq = len(hist) + 1
    spend = monthly_spend(hist, str(date.today())[:7])
    if not budget_gate(spend):
        print(f"[boldplay] 月封顶触及: 本月已花 {spend:.0f}/{MONTHLY_CAP:.0f} 元, 本轮停")
        return
    # 当轮=全部在售比赛日合并（2026-08-25 修复：原 matchDays[-1] 漏掉当晚场次）
    all_days = {"matches": [m for d in odds.get("matchDays", []) for m in d.get("matches", [])]}
    all_days = _filter_onsale(all_days)
    if structure == "new":
        out = build_three_tier(all_days, table, seq, zh=_zh_map(), form=build_team_form())
        u_spend = upset_month_spend(str(date.today())[:7])
        if u_spend >= MONTHLY_UPSET_CAP and out["tiers"]["upset"]["cost"] > 0:
            out["tiers"]["upset"] = {"shape": "closed", "cost": 0,
                                     "legs": out["tiers"]["upset"].get("legs") or [],
                                     "note": f"翻身月预算红线 {u_spend:.0f}/{MONTHLY_UPSET_CAP:.0f}元 · 关档"}
            out["totalCost"] = out["tiers"]["base"]["cost"]
        streak = upset_dry_streak(str(date.today())[:7])   # v5.5: 连续4轮0回款降半仓
        if streak >= 4 and out["tiers"]["upset"]["cost"] > 0 and halve_upset(out):
            out["upsetHalved"] = streak   # 仅实降落存证（最低仓/关档 False 不写·评审裁定）
        print(render_ticket(out))
    else:
        out = build_ticket(all_days, table, seq, method=method)
        u = out["tiers"]["upset"]
        print(f"[boldplay] legacy seq={out['seq']} {out['shape']} | 翻身档 {u['cost']}元 ×{u['multiplier']}倍 "
              f"合赔{u['expOdds']} 中即≈{u['winIfHit']:.0f}元 | 总投入 {out['totalCost']}元")
    out["ranAt"] = str(date.today())
    # 数据新鲜度标记（CI 校验 + 下游可读）
    _odds_stem = Path(latest).stem  # e.g. "2026-09-02"
    out["dataAsOf"] = _odds_stem
    _age_days = (date.today() - date.fromisoformat(_odds_stem)).days
    if _age_days > 7:
        out.setdefault("warnings", []).append(f"stale_cache: score_odds {(_age_days)}d old")
    suffix = "-legacy" if structure == "legacy" else ""
    path = PRED_DIR / f"{date.today()}-boldplay{suffix}.json"
    if dry:
        print(f"[boldplay] --dry 未落盘（seq={out['seq']} 总投入 {out['totalCost']}元）")
        return
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[boldplay] → {path}")

if __name__ == "__main__":
    main()
