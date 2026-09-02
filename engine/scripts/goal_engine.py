"""goal_engine 进球引擎（双轨分化设计 P0）——T3 特征层 + T4 矩阵层。
开发者 sszhang

设计出处：docs/2026-09-01-dual-track-prediction-design.html 第五节
（λ_final = λ_DC × exp(Σ βᵢ·xᵢ)，本模块产出特征 xᵢ、开季权重与修正后比分矩阵；
出口层见 T5）。

阶段0 语义：特征全部从场次序列自身滚动构造——每场只用该场之前的数据
（球队画像/standings 是当前时点快照，禁止用于历史逐场回测）。
超参标注：BETA / PRIOR_K 为阶段0 手写值，阶段1 学习器（残差回归学 β）替换，
替换前须过消融验证（消融铁律：无正增益即置零除名）。
"""
import math

import numpy as np

from dc_fit import dc_tau

BETA = 0.3            # 阶段0 手写乘子强度（学习器替换对象）
PRIOR_K = 2.0         # 小样本向联赛均值收缩的先验场次
WINDOW = 5            # 滚动窗口
EARLY_GAMES = 2       # 此前场次 <2 视为开季
EARLY_WEIGHT = 0.3


def pad_rate(team_sum, n, league_rate):
    """小样本收缩：(队累计 + 联赛均值×k) / (n+k)。"""
    return (team_sum + league_rate * PRIOR_K) / (n + PRIOR_K)


def league_sides(prior_matches):
    """联赛两侧进球基线 {"home_rate","away_rate"}（用传入的该场前场次算；空时全局常识垫值）。"""
    n = len(prior_matches)
    if n == 0:
        return {"home_rate": 1.4, "away_rate": 1.1}
    hg = sum(m["fthg"] for m in prior_matches)
    ag = sum(m["ftag"] for m in prior_matches)
    return {"home_rate": hg / n, "away_rate": ag / n}


def build_history(prior_matches):
    """队级滚动账本：home_gf/home_ga/away_gf/away_ga 四簿（进失球序列，进序追加）。
    返回 (账本, 联赛两侧基线)——基线用同一批该场前场次算，口径自洽。"""
    hist = {"home_gf": {}, "home_ga": {}, "away_gf": {}, "away_ga": {}}
    for m in prior_matches:
        hist["home_gf"].setdefault(m["home"], []).append(m["fthg"])
        hist["home_ga"].setdefault(m["home"], []).append(m["ftag"])
        hist["away_gf"].setdefault(m["away"], []).append(m["ftag"])
        hist["away_ga"].setdefault(m["away"], []).append(m["fthg"])
    return hist, league_sides(prior_matches)


def early_weight(games_before):
    """开季降权：此前 <EARLY_GAMES 场 → EARLY_WEIGHT，否则 1.0。"""
    return EARLY_WEIGHT if games_before < EARLY_GAMES else 1.0


def match_features(hist, sides, home, away):
    """返回 {"home_att","home_def","away_att","away_def","w_home","w_away"}——四 ratio + 两开季权重。
    无历史的簿记 ratio=1.0（不修正）；窗口=近 WINDOW 场；pad 用窗口内累计。
    失球基准=对侧产出（跨侧对尺度）：home_def（主队失球）分母用客队侧均值 away_rate，
    away_def（客队失球）分母用主队侧均值 home_rate。"""
    def rate(book, team, side_rate):
        seq = hist[book].get(team, [])
        if not seq:
            return None        # 无历史由调用侧决定（返回 None 而非垫值——ratio=1.0 的语义在 match_features 内处理）
        return pad_rate(sum(seq[-WINDOW:]), min(len(seq), WINDOW), side_rate)
    def ratio(book, team, side_rate):
        if side_rate <= 0:
            return 1.0         # 基准不可信（如揭幕战仅 0 进球侧）→ 不修正，与空簿哲学同构
        r = rate(book, team, side_rate)
        return 1.0 if r is None else r / side_rate
    f = {
        "home_att": ratio("home_gf", home, sides["home_rate"]),
        "home_def": ratio("home_ga", home, sides["away_rate"]),
        "away_att": ratio("away_gf", away, sides["away_rate"]),
        "away_def": ratio("away_ga", away, sides["home_rate"]),
    }
    f["w_home"] = early_weight(len(hist["home_gf"].get(home, [])))
    f["w_away"] = early_weight(len(hist["away_gf"].get(away, [])))
    return f


# ---------- T4 矩阵层 ----------

def lambda_from_dc(dc, home, away):
    """DC 参数 → (λ_home, λ_away)。参数结构 {teams:{name:{attack,defense}}, homeAdv, rho}。"""
    th, ta = dc["teams"].get(home), dc["teams"].get(away)
    if not th or not ta:
        return None
    return (math.exp(th["attack"] + ta["defense"] + dc["homeAdv"]),
            math.exp(ta["attack"] + th["defense"]))


DISABLE_KEYS = ("att", "def", "shrink")   # lambda_mult 合法消融键（typo 即抛错，防静默假零增益）


def lambda_mult(f, side, disable=()):
    """side="home" → 用 home_att × away_def；side="away" 对称。
    逐项开季加权（09-01 审查修订）：att 项权重=攻方簿场次、def 项=守方（对侧）簿场次——
    防主队主战 1 场(w=0.3)把客队 10 场样本的防守信号也压到 0.3 的交叉污染。"""
    if set(disable) - set(DISABLE_KEYS):
        raise ValueError(f"unknown disable: {disable}")
    att = f["home_att"] if side == "home" else f["away_att"]
    dfn = f["away_def"] if side == "home" else f["home_def"]
    w_att = f["w_home"] if side == "home" else f["w_away"]
    w_def = f["w_away"] if side == "home" else f["w_home"]
    if "shrink" in disable:          # 消融：关开季降权 = 两权重恒 1
        w_att = w_def = 1.0
    z = 0.0
    if "att" not in disable and att > 0:
        z += BETA * w_att * math.log(att)
    if "def" not in disable and dfn > 0:
        z += BETA * w_def * math.log(dfn)
    return math.exp(z)


def score_matrix(lh, la, rho):
    """修正后 λ 重建 7×7 DC 比分矩阵（低分 tau 修正，构造同 backtest.dc_three 但独立实现不动 backtest）。"""
    p = np.zeros((7, 7))
    for x in range(7):
        for y in range(7):
            pm = math.exp(-lh) * lh ** x / math.factorial(x) * math.exp(-la) * la ** y / math.factorial(y)
            p[x, y] = max(pm * dc_tau(x, y, lh, la, rho), 1e-12)
    p /= p.sum()
    return p


def ttg_probs(matrix):
    """总进球 0..12 桶概率（7×7 矩阵 i+j 最大 12，13 桶无死桶，和为 1）。"""
    out = [0.0] * 13
    for i in range(7):
        for j in range(7):
            out[min(i + j, 12)] += matrix[i, j]
    return out


def crs_rank(matrix):
    """49 比分按概率降序 [(score, p), ...]。"""
    return sorted(((f"{i}-{j}", float(matrix[i, j])) for i in range(7) for j in range(7)),
                  key=lambda kv: -kv[1])


# ---------- T5 对照统计 + 消融 CLI ----------

import argparse
import json
from collections import Counter
from datetime import datetime

from common import ROOT

CACHE_DIR = ROOT / "engine" / "cache"
REPORT_PATH = ROOT / "data" / "04-summaries" / "goal-engine-report.json"

FD_LEAGUES = ["england-premier", "england-championship", "spain-laliga", "germany-bundesliga",
              "germany-bundesliga2", "italy-serie-a", "italy-serie-b", "france-ligue1",
              "france-ligue2", "netherlands-eredivisie", "portugal-primeira",
              "belgium-first-a", "turkey-super-lig", "greece-super"]

METRIC_KEYS = ("ttg3", "crs1", "crs3", "crs5")
LINES = ("corrected", "bareDc", "naiveRolling", "naiveStatic")
BASELINE_REFS = {"designTtg3": 0.624, "reviewNaiveStaticTtg3_2526": 0.648, "reviewBareDcTtg3_2526": 0.624}


def _ttg3_score_dedup(matrix, actual_t):
    """设计 62.4% 同口径互验（had_crs_divergence.part_b）：49 比分按概率降序遍历，
    首次出现的总进球档去重收集，前 3 个不同档含 actual_t 为命中。
    与主口径（聚合概率 top3）的差异：受单比分概率位置影响，系统性偏低。"""
    ranked = crs_rank(matrix)
    tg_rank = []
    for s, _ in ranked:
        g = sum(map(int, s.split("-")))
        if g not in tg_rank:
            tg_rank.append(g)
    return actual_t in tg_rank[:3]


def load_odds_matches(league, seasons=("2526",)):
    """读 engine/cache/odds_{league}_{season}.json → [{"home","away","season","fthg","ftag"}]。
    fthg/ftag None（未赛）跳过；int() 失败跳过（脏行防御）；文件缺失静默跳过该季。"""
    out = []
    for season in seasons:
        p = CACHE_DIR / f"odds_{league}_{season}.json"
        if not p.exists():
            continue
        for m in json.loads(p.read_text(encoding="utf-8")).get("matches", []):
            try:
                hg, ag = int(m["fthg"]), int(m["ftag"])
            except (TypeError, ValueError):
                continue
            out.append({"home": m["home"], "away": m["away"], "season": season, "fthg": hg, "ftag": ag})
    return out


def _new_counter():
    """四指标命中账本 {key: [hit, n]}。"""
    return {k: [0, 0] for k in METRIC_KEYS}


def _top_keys(counter, k):
    """频序前 k 键，并列按键升序（确定性——Counter.most_common 并列时按插入序不稳定）。"""
    return [key for key, _ in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:k]]


def bump(counter, matrix, actual_t, actual_s):
    """matrix 命中统计：ttg3（概率前3档含 actual_t）/ crs1|crs3|crs5（crs_rank 前 k 含 actual_s）。"""
    tp = ttg_probs(matrix)
    top3 = set(sorted(range(len(tp)), key=lambda i: (-tp[i], i))[:3])
    ranked = [s for s, _ in crs_rank(matrix)]
    for key, hit in (("ttg3", actual_t in top3),
                     ("crs1", actual_s in ranked[:1]),
                     ("crs3", actual_s in ranked[:3]),
                     ("crs5", actual_s in ranked[:5])):
        counter[key][0] += 1 if hit else 0
        counter[key][1] += 1


def bump_naive(counter, ttg3_set, crs5_set, actual_t, actual_s):
    """固定档集合命中：ttg3 直接判 in；crs5_set 为按频序排好的 TOP5 比分列表，前 k 截取得 crs1/3/5。"""
    for key, hit in (("ttg3", actual_t in ttg3_set),
                     ("crs1", actual_s in crs5_set[:1]),
                     ("crs3", actual_s in crs5_set[:3]),
                     ("crs5", actual_s in crs5_set[:5])):
        counter[key][0] += 1 if hit else 0
        counter[key][1] += 1


def evaluate_league(league, disable=(), seasons=("2526",)):
    """逐场切片对照：corrected（乘子修正矩阵）/ bareDc（裸 DC）/ naiveRolling（该场前滚动频次）/
    naiveStatic（全季频次，含泄漏=复审 64.8% 同口径）。
    DC 缺队场次整场跳过（四线同场次切片才可对照）；无 DC 缓存返回 None。"""
    dcp = CACHE_DIR / f"{league}_dc.json"
    if not dcp.exists():
        print(f"[goal-engine] {league}: 无 DC 缓存 {dcp.name}，跳过（先跑 dc_fit）")
        return None
    dc = json.loads(dcp.read_text(encoding="utf-8"))
    ms = load_odds_matches(league, seasons)
    rho = dc["rho"]
    corrected, bareDc = _new_counter(), _new_counter()
    naiveRolling, naiveStatic = _new_counter(), _new_counter()
    static_ttg = Counter(m["fthg"] + m["ftag"] for m in ms)
    static_crs = Counter(f"{m['fthg']}-{m['ftag']}" for m in ms)
    st_t3, st_c5 = _top_keys(static_ttg, 3), _top_keys(static_crs, 5)
    roll_ttg, roll_crs = Counter(), Counter()
    sd_ttg3 = [0, 0]   # bareDc 矩阵的设计同口径（比分降序去重档）ttg3 计数——62.4% 互验锚
    for idx, m in enumerate(ms):
        lam = lambda_from_dc(dc, m["home"], m["away"])
        if lam is None:
            continue
        lh, la = lam
        actual_t, actual_s = m["fthg"] + m["ftag"], f"{m['fthg']}-{m['ftag']}"
        hist, sides = build_history(ms[:idx])
        f = match_features(hist, sides, m["home"], m["away"])
        bump(corrected,
             score_matrix(lh * lambda_mult(f, "home", disable), la * lambda_mult(f, "away", disable), rho),
             actual_t, actual_s)
        bare_m = score_matrix(lh, la, rho)
        bump(bareDc, bare_m, actual_t, actual_s)
        sd_ttg3[0] += 1 if _ttg3_score_dedup(bare_m, actual_t) else 0
        sd_ttg3[1] += 1
        bump_naive(naiveStatic, st_t3, st_c5, actual_t, actual_s)
        if idx:  # prior 非空才有滚动频次可言（首场无历史不计 naiveRolling）
            bump_naive(naiveRolling, _top_keys(roll_ttg, 3), _top_keys(roll_crs, 5), actual_t, actual_s)
        roll_ttg[actual_t] += 1
        roll_crs[actual_s] += 1
    return {"corrected": corrected, "bareDc": bareDc,
            "naiveRolling": naiveRolling, "naiveStatic": naiveStatic, "sdTtg3": sd_ttg3}


def _aggregate(reports):
    """league 级 [hit, n] 计数求和 → total 同构计数。"""
    out = {line: _new_counter() for line in LINES}
    for r in reports:
        for line in LINES:
            for key in METRIC_KEYS:
                out[line][key][0] += r[line][key][0]
                out[line][key][1] += r[line][key][1]
    return out


def _rates(counter):
    """[hit, n] → {key: rate}（n=0 → None 防除零）。"""
    return {key: (round(hit / n, 4) if n else None) for key, (hit, n) in counter.items()}


def _section(counter_block):
    """计数块 → 落盘节：主口径 n + naiveRolling 独立 n + 四线 rate。"""
    n = counter_block["corrected"]["ttg3"][1]
    n_rolling = counter_block["naiveRolling"]["ttg3"][1]
    sec = {"n": n, "naiveRollingN": n_rolling}
    sec.update({line: _rates(counter_block[line]) for line in LINES})
    return sec


def build_report(seasons, disable=()):
    """全联赛基线 + 三键消融 → 报告 dict（含落盘与打印前的全部数据）。"""
    reports = {}
    for lg in FD_LEAGUES:
        r = evaluate_league(lg, disable=disable, seasons=seasons)
        if r is not None:
            reports[lg] = r
    leagues_sec = {lg: _section(r) for lg, r in reports.items()}
    total_sec = _section(_aggregate(reports.values()))
    sd_hit = sum(r["sdTtg3"][0] for r in reports.values())
    sd_n = sum(r["sdTtg3"][1] for r in reports.values())
    total_sec["bareDcTtg3ScoreDedup"] = round(sd_hit / sd_n, 4) if sd_n else None
    ablation = {}
    for name, key in (("noAtt", ("att",)), ("noDef", ("def",)), ("noShrink", ("shrink",))):
        abl = {lg: evaluate_league(lg, disable=key, seasons=seasons) for lg in reports}
        ablation[name] = _section(_aggregate(abl.values()))
    mode = "in-sample corrected-vs-naive (ttg3=aggregated-prob top3, design 62.4%=score-dedup caliber)"
    if disable:
        mode += f" disable={list(disable)}"
    return {
        "ranAt": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "seasons": list(seasons),
        "hyper": {"beta": BETA, "priorK": PRIOR_K, "window": WINDOW, "earlyWeight": EARLY_WEIGHT,
                  "note": "阶段0手写，待阶段1学习器替换"},
        "leagues": leagues_sec,
        "total": total_sec,
        "ablation": ablation,
        "baselineRefs": BASELINE_REFS,
        "notes": ["in-sample：λ_DC 全季拟合含泄漏；干净结论看 walkForward 节（T6）",
                  "naiveStatic 全季聚合含泄漏，naiveRolling 干净（该场前场次滚动）",
                  "生死线：walk-forward 超不过 naiveRolling 则引擎不上线（复审 A3 拍板）",
                  "TTG 口径差异（T5 实测澄清）：设计 62.4% 出自 had_crs_divergence 的「比分降序去重档前3」",
                  f"（total.bareDcTtg3ScoreDedup={total_sec['bareDcTtg3ScoreDedup']} 为该口径复验值，应≈0.624）；",
                  "本报告主口径=总进球聚合概率 top3（与 naive 侧频次 top3 对称）——两口径同场次同矩阵，",
                  f"bareDc 主口径 {total_sec['bareDc']['ttg3']} 高于设计基线属口径差异非模型增益；",
                  "复审 A1「DC 输朴素 2.4pp」是聚合朴素 vs 去重 DC 的不对称对比，同聚合口径下",
                  f"bareDc {total_sec['bareDc']['ttg3']} > naiveStatic {total_sec['naiveStatic']['ttg3']}"
                  f" > naiveRolling {total_sec['naiveRolling']['ttg3']}（供 09-27 评审重估）"],
    }


def _print_total(sec, label):
    """终端四线对比表 + 基线互验行。"""
    print(f"\n== {label} 四线对照（率=hit/n） ==")
    print(f"{'line':<14}" + "".join(f"{k:>8}" for k in METRIC_KEYS) + f"{'n':>8}")
    for line in LINES:
        n = sec["naiveRollingN"] if line == "naiveRolling" else sec["n"]
        cells = "".join(f"{(sec[line][k] if sec[line][k] is not None else float('nan')):>8.4f}"
                        for k in METRIC_KEYS)
        print(f"{line:<14}{cells}{n:>8}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="goal_engine T5 对照统计+消融")
    ap.add_argument("--compare", action="store_true", help="四线对照+三键消融，落盘 goal-engine-report.json")
    ap.add_argument("--disable", default="", help="消融键逗号分隔(att/def/shrink)，作用于 corrected 线")
    ap.add_argument("--seasons", default="2526", help="赛季逗号分隔(默认 2526)")
    ap.add_argument("--walk-forward", action="store_true", help="占位：walk-forward 为 T6 交付，本版未实现")
    args = ap.parse_args(argv)
    if args.walk_forward:
        print("[goal-engine] --walk-forward 为 T6 交付，本版未实现（占位）")
        return
    if not args.compare:
        ap.print_help()
        return
    seasons = tuple(s for s in (x.strip() for x in args.seasons.split(",")) if s)
    disable = tuple(d for d in (x.strip() for x in args.disable.split(",")) if d)
    bad = set(disable) - set(DISABLE_KEYS)
    if bad:
        raise SystemExit(f"[goal-engine] 未知 disable 键 {sorted(bad)}，合法：{DISABLE_KEYS}")

    report = build_report(seasons, disable)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[goal-engine] 报告落盘 {REPORT_PATH}")
    _print_total(report["total"], "total")
    for name, sec in report["ablation"].items():
        _print_total(sec, f"ablation.{name}")
    t = report["total"]
    print(f"\n基线互验：bareDc.ttg3={t['bareDc']['ttg3']}（聚合概率口径，高于设计属口径差异）；"
          f"bareDcTtg3ScoreDedup={t['bareDcTtg3ScoreDedup']} vs 设计 {BASELINE_REFS['designTtg3']}(±0.02 同口径)；"
          f"naiveStatic.ttg3={t['naiveStatic']['ttg3']} vs 复审 {BASELINE_REFS['reviewNaiveStaticTtg3_2526']}"
          "（复审为常识固定档+Pinnacle过滤场次，本报告为全季频序档+DC可算场次）")


if __name__ == "__main__":
    main()
