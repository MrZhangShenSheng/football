"""redteam: 红队审查·事前举证卡(wargame设计§五·P0-w 先行版 R1-R4/R6).
每入串场次生成反证清单——全部消费已有字段零新采集:
  R1 伤停反向(强): |d|>=2 缺阵差反 pick 方向(口径同修正系数9)
  R2 基面反向(强): 己队近10无胜 / 对手近10不败(踩线降档规则泛化·不服务平pick)
  R3 市场异动(强): pick 方向赔率升>5% 或反向降>5%(05-trends diff 链喂数)
  R4 模型分歧(中): |模型-市场|>5pp 且市场最高向≠pick向
  R5 剧本树背离(中): wargame 转正后接入, 先行版自动跳过
  R6 H2H 反向(弱): 调用方判不利传 ctx(仅叙事弱参考)
>=2 强反证 → ⚠软警示(不拦截不降档·纪律层软拦截拍板 2026-09-07); 缺证据→跳过该条.
事后结算走 verify 链(说中/误报→data/04-summaries/redteam.json·n>=30才谈硬闸).
开发者 sszhang"""
import json
import re
import sys

from common import ROOT, load_aliases

TEAMS_DIR = ROOT / "data" / "01-teams"
STRONG_THRESHOLD = 2      # 强反证条数 → 警示
ODDS_MOVE_PCT = 0.05      # R3 赔率异动幅度门槛(5%)
DIVERGE_PCT = 0.05        # R4 模型-市场分歧门槛(5pp)
INJ_GAP = 2               # R1 缺阵差门槛(同修正系数9)
RULES = {"R1": "伤停反向", "R2": "基面反向", "R3": "市场异动",
         "R4": "模型分歧", "R6": "H2H反向"}
STRENGTH = {"R1": "strong", "R2": "strong", "R3": "strong",
            "R4": "mid", "R6": "weak"}


def pick_direction(pick: str) -> str | None:
    """pick 字符串 → 三向方向 h/d/a; 无方向玩法(TTG等)→None. HHAD/CRS/HAFU 取隐含方向."""
    p = pick or ""
    if p.startswith("排除"):
        return None
    if "主胜" in p:
        return "h"
    if "客胜" in p:
        return "a"
    if "平" in p:
        return "d"
    m = re.search(r"(\d+)\s*:\s*(\d+)", p)
    if m:   # CRS "2:0" 等
        h, a = int(m.group(1)), int(m.group(2))
        return "h" if h > a else ("d" if h == a else "a")
    if p.startswith("HAFU") and len(p.split()) > 1:
        return p.split()[1][1] if len(p.split()[1]) > 1 else None   # 全场位
    return None


def parse_form(form: str | None) -> tuple[int, int, int] | None:
    '"9胜0平1负" → (9,0,1); 解析失败→None.'
    if not form:
        return None
    m = re.search(r"(\d+)胜(\d+)平(\d+)负", str(form))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _ev(rid, against, detail):
    return {"type": rid, "name": RULES[rid], "strength": STRENGTH[rid],
            "against_pick": bool(against), "detail": detail}


def assess(match: dict, ctx: dict | None = None) -> dict:
    """match=预测JSON单场记录(pick/league/match等) · ctx=补充证据(form_*/injuries_*/
    odds_prev/odds_now/market/h2h_unfavorable), 传入优先于画像best-effort."""
    ctx = ctx or {}
    pick = match.get("pick") or ""
    direction = pick_direction(pick)
    evidences = []

    # R1 伤停反向(先主后客, d=主-客, 正值=主伤重)
    inj_h = ctx.get("injuries_home")
    inj_a = ctx.get("injuries_away")
    if inj_h is None or inj_a is None:
        inj_h, inj_a = _profile_injuries(match)
    if direction and (inj_h is None or inj_a is None):
        evidences.append(_ev("R1", False, "缺伤停证据, 跳过"))
    elif direction:
        d = inj_h - inj_a
        if direction == "h" and d >= INJ_GAP:
            evidences.append(_ev("R1", True, f"主伤重: 缺阵差 d=+{d}(主{inj_h}/客{inj_a})"))
        elif direction == "a" and d <= -INJ_GAP:
            evidences.append(_ev("R1", True, f"客伤重: 缺阵差 d={d}(主{inj_h}/客{inj_a})"))
        else:
            evidences.append(_ev("R1", False, f"d={d:+d} 未及±{INJ_GAP}门槛"))

    # R2 基面反向(不服务平pick)
    if direction in ("h", "a"):
        fh = parse_form(ctx.get("form_home")) or parse_form(_profile_field(match, "home"))
        fa = parse_form(ctx.get("form_away")) or parse_form(_profile_field(match, "away"))
        side, opp = (fh, fa) if direction == "h" else (fa, fh)
        side_name, opp_name = ("主队", "客队") if direction == "h" else ("客队", "主队")
        if side is not None and side[0] == 0:
            evidences.append(_ev("R2", True, f"{side_name}近10无胜({side[0]}胜{side[1]}平{side[2]}负)"))
        elif opp is not None and opp[2] == 0:
            evidences.append(_ev("R2", True, f"{opp_name}近10不败({opp[0]}胜{opp[1]}平{opp[2]}负)"))
        elif side is None and opp is None:
            evidences.append(_ev("R2", False, "缺近况证据, 跳过"))
        else:
            evidences.append(_ev("R2", False, f"基面:{side_name}{side} {opp_name}{opp}"))

    # R3 市场异动(pick向赔率升>5% 或 反向向降>5%)
    prev, now = ctx.get("odds_prev"), ctx.get("odds_now")
    if prev and now and len(prev) == 3 and len(now) == 3 and direction:
        idx = "hda".index(direction)
        up = now[idx] / prev[idx] - 1 if prev[idx] else 0.0
        down = min((now[j] / prev[j] - 1) for j in range(3) if j != idx and prev[j]) \
            if any(prev[j] for j in range(3) if j != idx) else 0.0
        if up > ODDS_MOVE_PCT:
            evidences.append(_ev("R3", True, f"pick向赔率升{up * 100:.1f}%({prev[idx]}→{now[idx]})"))
        elif down < -ODDS_MOVE_PCT:
            evidences.append(_ev("R3", True, f"反向赔率降{down * 100:.1f}%"))
        else:
            evidences.append(_ev("R3", False, f"异动幅度内(pick向{up * 100:+.1f}%)"))

    # R4 模型分歧(模型=match.fused|dc, 市场=ctx.market三向概率)
    model = match.get("fused") or match.get("dc")
    market = ctx.get("market")
    if model and market and len(model) == 3 and len(market) == 3 and direction:
        idx = "hda".index(direction)
        mkt_top = max(range(3), key=lambda j: market[j])
        gap = model[idx] - market[idx]
        if abs(gap) > DIVERGE_PCT and mkt_top != idx:
            evidences.append(_ev("R4", True,
                f"模型-{market[idx] * 100:.0f}%={gap * 100:+.1f}pp且市场最高向≠pick"))
        else:
            evidences.append(_ev("R4", False, f"分歧{gap * 100:+.1f}pp"))

    # R5 剧本树背离: wargame 转正后接入, 先行版跳过
    evidences.append({"type": "R5", "name": "剧本树背离", "strength": "mid",
                      "against_pick": False, "detail": "wargame 未转正, 跳过"})

    # R6 H2H 反向(调用方判不利)
    if ctx.get("h2h_unfavorable"):
        evidences.append(_ev("R6", True, ctx.get("h2h_text") or "交锋劣势(弱参考)"))

    strong = [e for e in evidences if e["against_pick"] and e["strength"] == "strong"]
    alert = len(strong) >= STRONG_THRESHOLD
    return {"code": match.get("code"), "pick": pick, "pick_dir": direction,
            "evidences": evidences, "alert": alert,
            "summary": f"⚠红队警示:{len(strong)}条强反证" if alert
                       else f"通过({len(strong)}强反证)"}


# ---- 画像 best-effort 接线(ctx 缺时兜底; 命中不了返回 None 由调用规则跳过) ----

def _team_ids(match: dict) -> tuple[str | None, str | None]:
    names = [x.strip() for x in (match.get("match") or "").split("vs")]
    if len(names) != 2:
        return None, None
    zh2id = {v.get("zh"): tid for tid, v in load_aliases().items()}
    return zh2id.get(names[0]), zh2id.get(names[1])


def _profile(match: dict, which: str):
    """aliases.league 字段即画像目录名(如 italy, 与 fd slug 不同名, 勿走 map_league 链)."""
    hid, aid = _team_ids(match)
    tid = hid if which == "home" else aid
    lg = load_aliases().get(tid, {}).get("league") if tid else None
    if not lg:
        return None
    try:
        return json.loads((TEAMS_DIR / lg / f"{tid}.json").read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _profile_field(match: dict, which: str):
    p = _profile(match, which)
    return p.get("last10") if p else None


def _n_injuries(p: dict) -> int | None:
    """伤停计数兼容双格式: insight 刷新画像=int 计数, 冷启动画像=名单数组."""
    v = p.get("starterInjuries")
    if v is None:
        v = p.get("injuries")
    if v is None:
        return None
    return v if isinstance(v, int) else len(v)


def _profile_injuries(match: dict):
    ph, pa = _profile(match, "home"), _profile(match, "away")
    if not ph or not pa:
        return None, None
    return _n_injuries(ph), _n_injuries(pa)


def main() -> None:
    """python redteam.py <预测JSON路径> <场次编号>  → 举证卡 JSON(stdout)."""
    if len(sys.argv) < 3:
        print("用法: python redteam.py <预测json> <场次编号>")
        return
    data = json.loads(open(sys.argv[1], encoding="utf-8-sig").read())
    code = sys.argv[2]
    m = next((x for x in data.get("matches", []) if x.get("code") == code), None)
    if m is None:
        print(json.dumps({"error": f"场次 {code} 未找到"}, ensure_ascii=False))
        return
    print(json.dumps(assess(m), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
