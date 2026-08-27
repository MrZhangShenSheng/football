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
                    league_glob="data/02-results/league/*_matches.json") -> dict:
    """fd CSV + 本地联赛库 → {norm队名: [(进球, 失球), ...]}（源内按时间序，取尾即最近）。
    fd 队名(HomeTeam/AwayTeam) 与本地 tid 统一 norm 化做键；测试以 fetch_rows_fn/league_glob 注入隔离网络。"""
    form = defaultdict(list)
    for season in SEASONS:
        for div in DIVS:
            for r in fetch_rows_fn(season, div):
                try:
                    hg, ag = int(r["FTHG"]), int(r["FTAG"])
                    form[_norm(r["HomeTeam"])].append((hg, ag))
                    form[_norm(r["AwayTeam"])].append((ag, hg))
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
