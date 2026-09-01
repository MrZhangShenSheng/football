"""goal_engine 进球引擎（双轨分化设计 P0）——T3 特征层：滚动无泄漏的队级攻防率。
开发者 sszhang

设计出处：docs/2026-09-01-dual-track-prediction-design.html 第五节
（λ_final = λ_DC × exp(Σ βᵢ·xᵢ)，本模块产出特征 xᵢ 与开季权重；矩阵层/出口层见 T4/T5）。

阶段0 语义：特征全部从场次序列自身滚动构造——每场只用该场之前的数据
（球队画像/standings 是当前时点快照，禁止用于历史逐场回测）。
超参标注：BETA / PRIOR_K 为阶段0 手写值，阶段1 学习器（残差回归学 β）替换，
替换前须过消融验证（消融铁律：无正增益即置零除名）。
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT

CACHE = ROOT / "engine" / "cache"
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
    home_def 分母用主队侧均值（失球基准=对侧产出，同侧同尺度），away_def 同理用客队侧均值。"""
    def rate(book, team, side_rate):
        seq = hist[book].get(team, [])
        if not seq:
            return None        # 无历史由调用侧决定（返回 None 而非垫值——ratio=1.0 的语义在 match_features 内处理）
        return pad_rate(sum(seq[-WINDOW:]), min(len(seq), WINDOW), side_rate)
    def ratio(book, team, side_rate):
        r = rate(book, team, side_rate)
        return 1.0 if r is None else r / side_rate
    f = {
        "home_att": ratio("home_gf", home, sides["home_rate"]),
        "home_def": ratio("home_ga", home, sides["home_rate"]),
        "away_att": ratio("away_gf", away, sides["away_rate"]),
        "away_def": ratio("away_ga", away, sides["away_rate"]),
    }
    f["w_home"] = early_weight(len(hist["home_gf"].get(home, [])))
    f["w_away"] = early_weight(len(hist["away_gf"].get(away, [])))
    return f
