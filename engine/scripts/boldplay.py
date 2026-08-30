"""Bold Play 阶梯出票卡生成器：三档组装/限额反算/月封顶/settle 回填/比分选法双链。开发者 sszhang
密度口径（体彩真实池水，skill v4.9 实测）：HAD 0.871^串 / CRS 0.661^串；4串单注限额50万。
freq-band（★ 2026-08-27 重设计默认，docs/2026-08-27-freq-band-design.html）：联赛频率模板+球队平移
+形状带+q排序，DC 退出比分链路；合格腿<4 关档不硬凑。--method=amix 过渡保留一个月（DC×体彩EV
三池全量扫描取最优，回测定去留），字节级不变。"""
import glob, json, math, sys
from datetime import date, datetime, timedelta
from pathlib import Path
from backfill import expand_combos   # 4串11=大小≥2全组合(结算引擎同源)
from band_calibration import devid
from common import ROOT, load_aliases
from dc_predict import (score_matrix, ttg_dist, hafu_approx, devig as devig_n,
                        reweight_matrix, reweight_hafu, temper, load_half_params, load_temperature)
from score_ev import build_freq_table, ev_scan, map_league
from freq_band import (build_team_form, freq_legs, pools_card, shifted_q, league_base_rates,
                       lambdas, team_strength, _norm)

SHAPES = {"guilin": {"band": (10.0, 17.0), "multiplier": 4, "cost": 8},
          "meizhou": {"band": (18.0, 28.0), "multiplier": 5, "cost": 10}}
CACHE_DIR = Path("engine/cache")
PRED_DIR = ROOT / "data" / "03-predictions"
DIVERGENCE_LIMIT = 0.05   # |p_model - p市场| 合规线（skill 铁律 8 / 8-25 会话口径）
ODDS_RANGE = (2.0, 40.0)  # A-MIX 单腿赔率合理域：排除 550 级长尾（经验频率/DC 尾部噪声 × 绝对pp分歧=假阳性，2026-08-25 探针实测 4:0@550 EV+845% 被放行）
POOL_KEEP = {"had": 0.871, "hhad": 0.871, "ttg": 0.796, "hafu": 0.796, "crs": 0.661}  # 体彩池水期望返还（skill v4.9 实测）
SINGLE_LIMIT = 500_000.0        # 4-5 串单注奖金限额（官方规则）
MONTHLY_CAP = 240.0
ROUND_COST = 20.0
MONTHLY_UPSET_CAP = 40.0        # 翻身月度彩票预算（spec §4.1 note·preference 同步）

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

def upset_month_spend(month: str) -> float:
    """当月翻身档累计投入：扫 data/03-predictions/{month}-*-boldplay*.json 的 tiers.upset.cost。开发者 sszhang"""
    total = 0.0
    for p in PRED_DIR.glob(f"{month}-*-boldplay*.json"):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            total += ((doc.get("tiers") or {}).get("upset") or {}).get("cost") or 0
        except (OSError, json.JSONDecodeError):
            continue
    return total


def _is_ab(m: dict) -> bool:
    """A/B级入池口径=现 build_ticket HAD 池过滤（had 齐全且最小赔率≥1.55 的非强胆场；
    spec D7：C/D 级数据等级不够，不出三池卡）。开发者 sszhang"""
    return bool(m.get("had")) and 1.55 <= min(m["had"].values())


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


def build_two_tier(odds_day: dict, freq_table: dict, seq: int, zh: dict, form: dict,
                   hafu_map: dict | None = None) -> dict:
    """新两档结构（spec §4.1）：保底 HAD 4串11(22元) + 翻身多池引擎(seq轮换)。
    选腿: 保底=现HAD选腿(_pick_had_legs); 翻身=各场 pools_card rec_upset 候选(同场≤1腿, 按EV降序)。开发者 sszhang"""
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
    # 翻身: seq 奇=跨池2串1×3 / 偶=跨池4串1×N; 腿=各场 rec_upset(同场≤1)
    cand = sorted((c["rec_upset"] for c in cards if c.get("candidates")), key=lambda c: -c["ev"])
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
    return {"structure": "new", "date": str(date.today()), "seq": seq,
            "tiers": {"base": base, "upset": upset},
            "totalCost": base["cost"] + upset["cost"],
            "cards": cards, "ranAt": str(date.today())}


def render_ticket(t: dict) -> str:
    """出票卡文本渲染——可读性硬规范（大哥 2026-08-30 要求）：
    ①顶部摘要行(结构/seq/总成本/两档成本)；②每档一节、逐腿一行
      `编号 │ 对阵 │ 玩法 pick @赔率 │ EV`（列宽对齐，│分隔）；
    ③三池卡候选区每场两行(保底视角/翻身视角)；④旗标用 emoji 前缀(⚠分歧/🟡低置信)；
    ⑤结尾预算行(月翻身累计x/40·红线提示)；⑥与 v5.4.2 出票核对单同款式(编号│对阵)。
    开发者 sszhang"""
    lines = [f"┌ 阶梯出票卡 v2 · seq{t['seq']} · 总成本 {t['totalCost']}元 "
             f"(保底{t['tiers']['base']['cost']} + 翻身{t['tiers']['upset']['cost']}) ────────"]
    for name, tier in (("保底", t["tiers"]["base"]), ("翻身", t["tiers"]["upset"])):
        lines.append(f"│ {name}档 {tier['play'] if 'play' in tier else tier['shape']}"
                     f" · {tier['cost']}元 · {tier.get('note', '')}")
        for l in tier.get("legs") or []:
            lines.append(f"│   {l['matchNumStr']} │ {l.get('match', '')[:14]:14s} │ "
                         f"{l['play'].upper()} {l['pick']} @{l['odds']}"
                         + (f" │ EV{l['ev']:+.0%}" if "ev" in l else ""))
    lines.append("│ 三池推荐(A/B级场):")
    for c in t.get("cards") or []:
        if not c.get("candidates"):
            continue
        fl = ("⚠" if "divergence" in c["flags"] else "") + ("🟡" if "low_conf" in c["flags"] else "")
        rb, ru = c["rec_base"], c["rec_upset"]
        lines.append(f"│   {c['code']} {fl} 保底→{rb['pool'].upper()} {rb['pick']}(q{rb['q']:.0%})"
                     f" · 翻身→{ru['pool'].upper()} {ru['pick']}@{ru['odds']}")
    spend = upset_month_spend(str(date.today())[:7])
    warn = " ⚠月预算红线!" if spend >= MONTHLY_UPSET_CAP else ""
    lines.append(f"└ 翻身月预算: {spend:.0f}/{MONTHLY_UPSET_CAP:.0f}元{warn} · 出票核对单见 v5.4.2 格式")
    return "\n".join(lines)


def _direction(score: str) -> str:
    h, a = (int(x) for x in score.split(":"))
    return "主胜" if h > a else ("平" if h == a else "客胜")


HAFU_DIR = {"h": "主胜", "d": "平", "a": "客胜"}   # HAFU 两字母键 → 方向中文（首=半场 次=全场）

def settle(ticket: dict, results: dict) -> dict:
    """逐 leg 判定（phase2 任务6）。results: matchNumStr → 'h:a' 或 {'score':'h:a','half':'h:a'}。

    HAD pick 由比分方向推导；CRS 精确比对（选项兼容 pick/score 两键——
    计划口径用 pick，真实出票 JSON 的 upset 腿用 score）；HAFU 需 half
    （backfill 体彩链路落盘，ESPN 链路无半场 → None 待人工）。payout 只算
    upset 档全中（合赔×2×倍数，推演库口径；实票结算走 tickets.json 账本）。
    """
    leg_hits, payout = {}, 0.0
    for tier, blob in ticket.get("tiers", {}).items():
        leg_hits[tier] = []
        raw_legs = blob.get("legs") or []
        notes = raw_legs if raw_legs and isinstance(raw_legs[0], list) else [raw_legs]
        for note_legs in notes:
            hits = []
            for leg in note_legs:
                ent = results.get(leg["matchNumStr"])
                if ent is None:
                    hits.append(None); continue      # 赛果缺失
                sc = ent.get("score") if isinstance(ent, dict) else ent
                half = ent.get("half") if isinstance(ent, dict) else None
                if sc is None:
                    hits.append(None); continue
                play = leg.get("play") or ("crs" if tier == "upset" else "had")  # 08-24 A-MIX 老卡 upset 腿无 play 键
                if play == "crs":
                    ok = (leg.get("pick") or leg.get("score")) == sc
                elif play == "ttg":
                    h_, a_ = (int(x) for x in sc.split(":"))
                    want = str(leg.get("pick") or leg.get("score")).replace("球", "").replace("+", "")
                    ok = (h_ + a_ >= 7 and want == "7") if "7" in want and int(want) == 7 else (h_ + a_ == int(want))
                elif play == "hafu":
                    if not half:
                        hits.append(None); continue  # 无半场比分（ESPN 链路/旧档），人工判
                    pick = str(leg.get("pick") or leg.get("score"))
                    ok = (pick[:1] in HAFU_DIR and pick[1:] in HAFU_DIR
                          and _direction(half) == HAFU_DIR[pick[:1]]
                          and _direction(sc) == HAFU_DIR[pick[1:]])
                else:
                    ok = leg["pick"] == _direction(sc)
                hits.append(ok)
            leg_hits[tier].append(hits)
    u = ticket["tiers"].get("upset", {})
    upset_hit = bool(u) and all(h is True for h in leg_hits.get("upset", [[]])[0])
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
    paths = sorted((ROOT / "data" / "03-predictions").glob("*-boldplay.json"))
    if not paths:
        print("[boldplay] 无出票 JSON"); return
    for p in paths:
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


def _selftest_two_tier():
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
    t = build_two_tier(fake_day, ft, seq=9, zh={}, form={})
    assert t["structure"] == "new"
    base = t["tiers"]["base"]
    assert base["cost"] == 22 and len(base["legs"]) == 4            # 4串11=22元
    assert base["play"] == "had-4串11"
    up = t["tiers"]["upset"]
    assert up["shape"] in ("pool-2x1x3", "pool-4x1")               # seq奇偶轮换
    codes = [l["matchNumStr"] for l in up["legs"]]
    assert len(codes) == len(set(codes))                            # 同场最多1腿(硬约束)
    assert 2 <= up["cost"] <= 8
    assert 24 <= t["totalCost"] <= 30
    txt = render_ticket(t)
    for kw in ("出票核对单", "周日004", "圣保利", "TTG", "│"):     # 可读性规范锚点
        assert kw in txt, kw
    print("[selftest] build_two_tier + render_ticket OK")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "settle":
        return cmd_settle()
    if "--selftest" in args:
        _selftest_two_tier()
        return
    method = "amix" if "--method=amix" in args else "freq"
    structure = "legacy" if "--structure=legacy" in args else "new"   # v2 两档默认，legacy 双轨对照一个月
    dry = "--dry" in args
    # ROOT 绝对定位（cmd_settle 同款：cwd=engine/scripts 下裸相对 glob 落空）
    latest = sorted(glob.glob(str(ROOT / "engine/cache/score_odds/*.json")))[-1]
    odds = json.load(open(latest, encoding="utf-8"))
    table = build_freq_table()
    hist = [json.load(open(p, encoding="utf-8")) for p in glob.glob(str(ROOT / "data/03-predictions/*-boldplay.json"))]
    seq = len(hist) + 1
    spend = monthly_spend(hist, str(date.today())[:7])
    if not budget_gate(spend):
        print(f"[boldplay] 月封顶触及: 本月已花 {spend:.0f}/{MONTHLY_CAP:.0f} 元, 本轮停")
        return
    # 当轮=全部在售比赛日合并（2026-08-25 修复：原 matchDays[-1] 漏掉当晚场次）
    all_days = {"matches": [m for d in odds.get("matchDays", []) for m in d.get("matches", [])]}
    if structure == "new":
        out = build_two_tier(all_days, table, seq, zh=_zh_map(), form=build_team_form())
        u_spend = upset_month_spend(str(date.today())[:7])
        if u_spend >= MONTHLY_UPSET_CAP and out["tiers"]["upset"]["cost"] > 0:
            out["tiers"]["upset"] = {"shape": "closed", "cost": 0,
                                     "legs": out["tiers"]["upset"].get("legs") or [],
                                     "note": f"翻身月预算红线 {u_spend:.0f}/{MONTHLY_UPSET_CAP:.0f}元 · 关档"}
            out["totalCost"] = out["tiers"]["base"]["cost"]
        print(render_ticket(out))
    else:
        out = build_ticket(all_days, table, seq, method=method)
        u = out["tiers"]["upset"]
        print(f"[boldplay] legacy seq={out['seq']} {out['shape']} | 翻身档 {u['cost']}元 ×{u['multiplier']}倍 "
              f"合赔{u['expOdds']} 中即≈{u['winIfHit']:.0f}元 | 总投入 {out['totalCost']}元")
    out["ranAt"] = str(date.today())
    suffix = "-legacy" if structure == "legacy" else ""
    path = PRED_DIR / f"{date.today()}-boldplay{suffix}.json"
    if dry:
        print(f"[boldplay] --dry 未落盘（seq={out['seq']} 总投入 {out['totalCost']}元）")
        return
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[boldplay] → {path}")

if __name__ == "__main__":
    main()
