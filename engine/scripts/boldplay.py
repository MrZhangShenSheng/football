"""Bold Play 阶梯出票卡生成器：三档组装/限额反算/月封顶/settle 回填/A-MIX 跨池选腿。开发者 sszhang
密度口径（体彩真实池水，skill v4.9 实测）：HAD 0.871^串 / CRS 0.661^串；4串单注限额50万。
A-MIX（★ v5.1 混串合法后新默认）：每场 CRS/TTG/HAFU 三池全量 EV 扫描（铁律 8）取 EV 最优合规腿
（EV>0 且 |p_model-p市场|<5pp），跨池组串 2~4 关；纯 CRS 形状带版退居 fallback。"""
import glob, json, math, sys
from datetime import date, datetime, timedelta
from pathlib import Path
from band_calibration import devid
from common import load_aliases
from dc_predict import score_matrix, ttg_dist, hafu_approx, devig as devig_n
from score_ev import build_freq_table, ev_scan, map_league

SHAPES = {"guilin": {"band": (10.0, 17.0), "multiplier": 4, "cost": 8},
          "meizhou": {"band": (18.0, 28.0), "multiplier": 5, "cost": 10}}
CACHE_DIR = Path("engine/cache")
DIVERGENCE_LIMIT = 0.05   # |p_model - p市场| 合规线（skill 铁律 8 / 8-25 会话口径）
ODDS_RANGE = (2.0, 40.0)  # A-MIX 单腿赔率合理域：排除 550 级长尾（经验频率/DC 尾部噪声 × 绝对pp分歧=假阳性，2026-08-25 探针实测 4:0@550 EV+845% 被放行）
POOL_KEEP = {"had": 0.871, "hhad": 0.871, "ttg": 0.796, "hafu": 0.796, "crs": 0.661}  # 体彩池水期望返还（skill v4.9 实测）
SINGLE_LIMIT = 500_000.0        # 4-5 串单注奖金限额（官方规则）
MONTHLY_CAP = 240.0
ROUND_COST = 20.0

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
    """形状赔率带 + n>0（先验噪声永不入选）+ 每场最多 1 比分，按 ev 降序取 4。"""
    lo, hi = SHAPES[shape]["band"]
    picked, seen = [], set()
    for r in rows:
        mid = r["matchNumStr"]
        if r.get("n", 0) <= 0 or mid in seen or not (lo <= r["odds"] <= hi):
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


def mix_candidates(odds_day: dict, freq_table: dict, zh: dict, hafu: dict, dc_params_fn=_dc_params) -> list:
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

        matrix = score_matrix(lh, la, rho)
        crs = {k: float(v) for k, v in (m.get("crs") or {}).items() if ":" in k}
        if crs:
            inv = sum(1.0 / o for o in crs.values())
            for k, o in crs.items():
                if not (ODDS_RANGE[0] <= o <= ODDS_RANGE[1]):
                    continue
                x, y = (int(t) for t in k.split(":"))
                p_mkt = (1.0 / o) / inv
                if abs(float(matrix[x, y]) - p_mkt) < DIVERGENCE_LIMIT:
                    offer(float(matrix[x, y]) * o - 1, {"play": "crs", "pick": k, "odds": o, "source": "dc"})
        ttg_o = m.get("ttg") or {}
        if len(ttg_o) == 8:
            p_mkt = devig_n([float(ttg_o[f"s{i}"]) for i in range(8)])
            p_dc = ttg_dist(matrix)
            for i in range(8):
                o = float(ttg_o[f"s{i}"])
                if ODDS_RANGE[0] <= o <= ODDS_RANGE[1] and abs(p_dc[i] - p_mkt[i]) < DIVERGENCE_LIMIT:
                    offer(p_dc[i] * o - 1,
                          {"play": "ttg", "pick": f"{i}球" if i < 7 else "7+球", "odds": o, "source": "dc"})
        hf = hafu.get(mid) or {}
        if len(hf) == 9:
            keys = [a + b for a in "hda" for b in "hda"]
            p_mkt = devig_n([hf[k] for k in keys])
            p_dc = hafu_approx(lh, la)
            for k in keys:
                if ODDS_RANGE[0] <= hf[k] <= ODDS_RANGE[1] and abs(p_dc[k] - p_mkt[keys.index(k)]) < DIVERGENCE_LIMIT:
                    offer(p_dc[k] * hf[k] - 1, {"play": "hafu", "pick": k, "odds": hf[k], "source": "dc"})
        if best:
            ev, leg = best
            legs.append({**leg, "matchNumStr": mid, "match": f'{m.get("home")}-{m.get("away")}',
                         "ev": round(ev, 4)})
    legs.sort(key=lambda l: -l["ev"])
    return legs


def build_ticket(odds_day: dict, freq_table: dict, seq: int) -> dict:
    shape = "guilin" if seq % 2 == 1 else "meizhou"
    rows = ev_scan(odds_day, freq_table)
    # upset 腿三链：A-MIX（v5.1 新默认）→ CRS 形状带 → 经验频率退路
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
        play = "crs-4串1"
        upset_note = f"{shape}形状 带宽{SHAPES[shape]['band']}"
    total_odds = 1.0
    for l in upset:
        total_odds *= l["odds"]
    mult = cap_multiplier(total_odds, SHAPES[shape]["multiplier"]) if upset else 1
    cost = min(SHAPES[shape]["cost"], 2 * mult) if upset else 0
    had_pool = [m for m in odds_day.get("matches", [])
                if m.get("had") and 1.55 <= min(m["had"].values())]
    def had_legs(n_matches, n_notes):
        legs = []
        for m in had_pool[:n_matches]:
            h = m["had"]
            pick = min(h, key=h.get)
            legs.append({"matchNumStr": m["matchNumStr"], "match": f'{m.get("home")}-{m.get("away")}',
                         "play": "had", "pick": {"h": "主胜", "d": "平", "a": "客胜"}[pick], "odds": h[pick]})
        if n_notes == 1 or len(legs) < 2:
            return [legs]
        half = (len(legs) + 1) // 2
        return [legs[:half], legs[half:]]
    return {
        "date": str(date.today()), "seq": seq, "shape": shape,
        "tiers": {
            "base": {"cost": 4, "legs": had_legs(4, 2), "play": "had-4串1", "note": "2注互补"},
            "mid": {"cost": 6, "legs": had_legs(5, 1), "play": "had-5串1", "note": "默认HAD"},
            "upset": {"cost": cost, "multiplier": mult, "legs": upset, "play": play,
                      "expOdds": round(total_odds, 1) if upset else 0,
                      "winIfHit": round(2 * total_odds * mult, 0) if upset else 0,
                      "note": upset_note
                              + (" · 频率退路" if upset and upset[0].get("fallback") else "")},
        },
        "totalCost": 4 + 6 + cost,
        "densityNote": "期望返还 ≈ " + (f"{math.prod(POOL_KEEP.get(l.get('play', 'crs'), 0.8) for l in upset):.1%}"
                                        + "（各腿池水连乘" + "/".join(sorted({l.get('play', 'crs') for l in upset})) + "）" if upset
                                        else "无腿"),
        "postTaxNote": "单注奖金超1万部分税20%;4串单注限额50万已反算倍数",
    }

def _direction(score: str) -> str:
    h, a = (int(x) for x in score.split(":"))
    return "主胜" if h > a else ("平" if h == a else "客胜")


def settle(ticket: dict, results: dict) -> dict:
    """逐 leg 判定（phase2 任务6）。results: matchNumStr → 实际比分 'h:a'。

    HAD pick 由比分方向推导；CRS 精确比对（选项兼容 pick/score 两键——
    计划口径用 pick，真实出票 JSON 的 upset 腿用 score）。payout 只算
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
                sc = results.get(leg["matchNumStr"])
                if sc is None:
                    hits.append(None); continue      # 赛果缺失
                if leg["play"] == "crs":
                    ok = (leg.get("pick") or leg.get("score")) == sc
                elif leg["play"] == "ttg":
                    h_, a_ = (int(x) for x in sc.split(":"))
                    want = str(leg.get("pick") or leg.get("score")).replace("球", "").replace("+", "")
                    ok = (h_ + a_ >= 7 and want == "7") if "7" in want and int(want) == 7 else (h_ + a_ == int(want))
                elif leg["play"] == "hafu":
                    hits.append(None); continue      # HAFU 需半场比分（02-results 无半场字段），人工判
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
    """出票日 d~d+2 三天赛果 → {场次编号: 'h:a'}（02-results 的 result 为 'h-a' 需转冒号）。"""
    out = {}
    try:
        base = datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError:
        return out
    for delta in (0, 1, 2):
        p = Path(f"data/02-results/{base + timedelta(days=delta)}.json")
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        for rec in data.get("matches") or []:
            if rec.get("code") and rec.get("result") and rec["result"] != "不可得":
                out[rec["code"]] = str(rec["result"]).replace("-", ":")
    return out


def cmd_settle() -> None:
    paths = sorted(glob.glob("data/03-predictions/*-boldplay.json"))
    if not paths:
        print("[boldplay] 无出票 JSON"); return
    p = Path(paths[-1])
    ticket = json.loads(p.read_text(encoding="utf-8"))
    if ticket.get("settle"):
        print(f"[boldplay] {p.name} 已结算(payout={ticket['settle']['payout']:.0f})，跳过"); return
    results = _load_results(ticket["date"])
    codes = set()
    for tier in ticket["tiers"].values():
        legs = tier.get("legs") or []
        for note in (legs if legs and isinstance(legs[0], list) else [legs]):
            codes.update(l["matchNumStr"] for l in note)
    missing = sorted(c for c in codes if c not in results)
    if missing:
        print(f"[boldplay] 赛果未回填: {', '.join(missing)}（完赛后重跑）"); return
    res = settle(ticket, results)
    ticket["settle"] = {**res, "settledAt": str(date.today())}
    p.write_text(json.dumps(ticket, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    u = res["legHits"].get("upset", [[]])[0]
    print(f"[boldplay] settle {p.name}: upset {sum(1 for h in u if h)}/{len(u)}关 "
          f"全中={res['upsetHit']} payout={res['payout']:.0f} 密度回收={res['densityRecovered']} → 已写回 settle 字段")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "settle":
        return cmd_settle()
    latest = sorted(glob.glob("engine/cache/score_odds/*.json"))[-1]
    odds = json.load(open(latest, encoding="utf-8"))
    table = build_freq_table()
    hist = [json.load(open(p, encoding="utf-8")) for p in glob.glob("data/03-predictions/*-boldplay.json")]
    seq = len(hist) + 1
    spend = monthly_spend(hist, str(date.today())[:7])
    if not budget_gate(spend):
        print(f"[boldplay] 月封顶触及: 本月已花 {spend:.0f}/{MONTHLY_CAP:.0f} 元, 本轮停")
        return
    # 当轮=全部在售比赛日合并（2026-08-25 修复：原 matchDays[-1] 漏掉当晚场次）
    all_days = {"matches": [m for d in odds.get("matchDays", []) for m in d.get("matches", [])]}
    out = build_ticket(all_days, table, seq)
    out["ranAt"] = str(date.today())
    path = f"data/03-predictions/{date.today()}-boldplay.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    u = out["tiers"]["upset"]
    print(f"[boldplay] seq={out['seq']} {out['shape']} | 翻身档 {u['cost']}元 ×{u['multiplier']}倍 "
          f"合赔{u['expOdds']} 中即≈{u['winIfHit']:.0f}元 | 总投入 {out['totalCost']}元 → {path}")

if __name__ == "__main__":
    main()
