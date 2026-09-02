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
