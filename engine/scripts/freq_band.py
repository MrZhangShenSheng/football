"""freq-band 比分选法（2026-08-27 重设计）：联赛频率模板+球队实力平移+形状带+q排序。开发者 sszhang
铁律（docs/2026-08-27-freq-band-design.html）：分布形状来自真实赛果频率（不造分布）；
赔率只做形状带门槛、不做排序；DC 不参与比分选择；数据缺失零破坏降级（纯联赛模板）。"""
import glob, json, math
from collections import Counter, defaultdict
from pathlib import Path
from band_calibration import DIVS, SEASONS, fetch_rows
from score_ev import map_league

RECENT_WINDOW = 10          # 球队近况窗口（场）
FORM_MIN_MATCHES = 5        # 近况最少场数：不足 → 不平移（纯联赛模板降级）
SURVIVE_Q = 0.01            # 生存阈值：平移后 q < 1% 的比分视为噪声
LAMBDA_CLAMP = (0.2, 4.5)   # λ 合理域（防小样本狂胜拉爆）
SIGMA_FLOOR = 0.5           # 核带宽下限（防极端单点分布 σ→0）
LEAGUE_STORE = ("japan", "korea", "sweden", "saudi")   # 本地赛果库联赛（与 score_ev 口径一致）


def _norm(name: str) -> str:
    """队名宽松匹配键（与 boldplay._dc_params 同口径）：小写去连字符去空格。"""
    return str(name).lower().replace("-", "").replace(" ", "")


def league_base_rates(blob: Counter) -> tuple:
    """频率 Counter → (主场场均进球, 客场场均进球)：Σh·c/n 与 Σa·c/n。"""
    n = blob.get("__n", 0)
    if not n:
        return 0.0, 0.0
    gh = sum(int(s.split(":")[0]) * c for s, c in blob.items() if s != "__n")
    ga = sum(int(s.split(":")[1]) * c for s, c in blob.items() if s != "__n")
    return gh / n, ga / n


def global_pool(freq_table: dict) -> Counter:
    """全联赛汇总 Counter（欧冠/巴甲等无映射联赛的模板池）。"""
    g = Counter()
    for blob in freq_table.values():
        g.update(blob)
    return g


def build_team_form(fetch_rows_fn=fetch_rows,
                    league_glob="data/02-results/league/*_matches.json",
                    aliases: dict | None = None) -> dict:
    """fd CSV + 本地联赛库 → {norm队名: [(进球, 失球), ...]}（源内按时间序，取尾即最近）。
    fd 队名(HomeTeam/AwayTeam) 与本地 tid 统一 norm 化做键；fd 行经 aliases.fd 对照双写 tid 键
    （tid≠fd 名的队：bayern vs Bayern Munich——体彩中文→zh_map→tid 才能命中 fd 数据）。
    测试以 fetch_rows_fn/league_glob/aliases 注入隔离网络。"""
    if aliases is None:
        from common import load_aliases
        aliases = load_aliases()
    fd_to_tid = {}
    for tid, srcs in aliases.items():
        if isinstance(srcs, dict) and srcs.get("fd"):
            fd_to_tid[_norm(srcs["fd"])] = _norm(tid)

    def add(name: str, row: tuple):
        key = _norm(name)
        form[key].append(row)
        tid_key = fd_to_tid.get(key)
        if tid_key and tid_key != key:
            form[tid_key].append(row)     # fd 名 → tid 双键（同一行两键各存一份）

    form = defaultdict(list)
    for season in SEASONS:
        for div in DIVS:
            for r in fetch_rows_fn(season, div):
                try:
                    hg, ag = int(r["FTHG"]), int(r["FTAG"])
                    add(r["HomeTeam"], (hg, ag))
                    add(r["AwayTeam"], (ag, hg))
                except (KeyError, ValueError, TypeError):
                    continue
    for path in glob.glob(league_glob):
        key = path.replace("\\", "/").split("/")[-1].replace("_matches.json", "")
        if key not in LEAGUE_STORE:
            continue
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = data.get("matches", []) if isinstance(data, dict) else (data or [])
        for m in rows:
            try:
                form[_norm(m["home"])].append((int(m["hg"]), int(m["ag"])))
                form[_norm(m["away"])].append((int(m["ag"]), int(m["hg"])))
            except (KeyError, ValueError, TypeError):
                continue
    return dict(form)


def team_strength(form: dict, norm_name: str):
    """norm队名 → 近 RECENT_WINDOW 场 (场均进, 场均失)；不足 FORM_MIN_MATCHES → None。"""
    rows = form.get(norm_name) or []
    if len(rows) < FORM_MIN_MATCHES:
        return None
    recent = rows[-RECENT_WINDOW:]
    return (sum(r[0] for r in recent) / len(recent),
            sum(r[1] for r in recent) / len(recent))


def lambdas(base: tuple, home_str, away_str) -> tuple:
    """乘法进球模型（Maher/Dixon-Coles 只取 λ 计算，不用 Poisson 造分布）：
    λ_h = 主队场均进 × 客队场均失 / 模板主场场均；λ_a 对称。clamp 防极端。"""
    if not home_str or not away_str or base[0] <= 0 or base[1] <= 0:
        return None
    lh = home_str[0] * away_str[1] / base[0]
    la = away_str[0] * home_str[1] / base[1]
    return (min(max(lh, LAMBDA_CLAMP[0]), LAMBDA_CLAMP[1]),
            min(max(la, LAMBDA_CLAMP[0]), LAMBDA_CLAMP[1]))


def shifted_q(blob: Counter, lam=None) -> dict:
    """联赛频率 Counter → {比分: q}。lam=(λh,λa) 时按 (t=h+a, d=h−a) 双轴高斯核平移：
    靠近本场目标的比分按距离比例抬升、靠近基准的回落——形状仍来自真实频率
    （c=0 的格子恒 0：真实数据从未出现的比分永不被选）。lam=None → 纯频率归一化。"""
    n = blob.get("__n", 0)
    if not n:
        return {}
    counts = {s: c for s, c in blob.items() if s != "__n"}
    coords = {}
    for s in counts:
        h, a = (int(x) for x in s.split(":"))
        coords[s] = (h + a, h - a)
    t_bar = sum(coords[s][0] * c for s, c in counts.items()) / n
    d_bar = sum(coords[s][1] * c for s, c in counts.items()) / n
    sig_t2 = max(sum((coords[s][0] - t_bar) ** 2 * c for s, c in counts.items()) / n, SIGMA_FLOOR ** 2)
    sig_d2 = max(sum((coords[s][1] - d_bar) ** 2 * c for s, c in counts.items()) / n, SIGMA_FLOOR ** 2)
    if lam is None:
        return {s: c / n for s, c in counts.items()}
    T, D = lam[0] + lam[1], lam[0] - lam[1]
    w = {}
    for s, c in counts.items():
        t, d = coords[s]
        g = (((t - t_bar) ** 2 - (t - T) ** 2) / (2 * sig_t2)
             + ((d - d_bar) ** 2 - (d - D) ** 2) / (2 * sig_d2))
        w[s] = c * math.exp(g)
    z = sum(w.values())
    return {s: v / z for s, v in w.items()} if z else {}


def freq_legs(odds_day: dict, freq_table: dict, form: dict, zh: dict, band: tuple) -> list:
    """三步选法（docs/2026-08-27-freq-band-design.html 图2）：
    ①生存阈：模板真实频率 q_pure≥1%（平移不救真实出现不足的比分——数据说话）
    ②赔率∈形状带 ③带内平移后 q 最高（每场 1 腿），跨场按 q 降序。
    模板池：联赛映射命中用联赛频率，否则全局池；近况缺失自动降级纯模板。"""
    lo, hi = band
    pool = global_pool(freq_table)
    legs = []
    for m in odds_day.get("matches", []):
        lg = map_league(m.get("league", ""))
        blob = freq_table.get(lg) if lg else None
        blob = blob if (blob and blob.get("__n", 0)) else pool
        if not blob.get("__n", 0):
            continue                                   # 全局池也空 → 无数据不选
        base = league_base_rates(blob)
        lam = lambdas(base,
                      team_strength(form, _norm(zh.get(m.get("home", ""), ""))),
                      team_strength(form, _norm(zh.get(m.get("away", ""), ""))))
        q_pure = shifted_q(blob, None)
        q_rank = shifted_q(blob, lam)
        best = None                                    # (q, leg)
        for s, o in (m.get("crs") or {}).items():
            if ":" not in s:                           # 胜其他/平其他/负其他
                continue
            o = float(o)
            if not (lo <= o <= hi):
                continue
            if q_pure.get(s, 0.0) < SURVIVE_Q:
                continue
            q = q_rank.get(s, 0.0)
            if best is None or q > best[0]:
                best = (q, {"matchNumStr": m["matchNumStr"],
                            "match": f'{m.get("home")}-{m.get("away")}',
                            "score": s, "odds": o, "q": round(q, 4),
                            "shifted": lam is not None})
        if best:
            legs.append(best[1])
    legs.sort(key=lambda l: -l["q"])
    return legs
