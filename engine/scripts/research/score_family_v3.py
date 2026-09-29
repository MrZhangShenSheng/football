# -*- coding: utf-8 -*-
r"""比分族特征模型 v3：特征工程扩展 + v2/v3 同场对比。

新增特征（每队 +7 维 → 每队 20 维 · 总 40 维）：
  1. 平局率      draw/n            ← draw 族（最大类 23%）此前无直接特征
  2. 场均积分    ppg=(3*win+draw)/n  ← 实力补全（v2 只有胜率）
  3. 近6场积分率 ppg6               ← 状态（带衰减语义的窗口版）
  4. 对手调整进球 Σ(opp对方场均失球)/N ← 打弱防刷球折算（A·简单版）
  5. 对手调整失球 Σ(opp对方场均进球)/N
  6. 休息天数    days_since_last     ← 赛程密度（缺赛填 7）
  7. 净胜波动    近10净胜标准差       ← 比赛风格稳定性

对手调整的实现：Stats3 存最近 15 场明细 (opp, scored, conceded, date, at_home)，
vector 时传入全局 stats 查对手当期防守——对手强度随时间演化，用"该场之前"的
对手快照才是严格无泄漏；近似：用当前对手统计（对手未来场次泄漏风险——
严格版需存对手历史快照链。折中：对手调整只用对手截至【本场日期】的累计值，
实现为两遍扫描：第一遍每场记录全局快照点，第二遍取快照。成本可接受
（快照=每队的 (n,ga,gf) 三元组浅拷贝）。采用：第一遍存每场时刻的全队
(n,gf,ga) 快照字典（只存数值，内存可控）。

验证协议（盲测第 5 次使用须大哥批准，本脚本只跑 inner-val + dev）：
  train  < 2026-01-01
  inner  2026-01-01 ~ 2026-03-31（有 hist_odds 赔率）
  dev    2026-04-01 ~ 2026-06-30
  对比   v2（26 维）vs v3（40 维）：top1/top3 命中率 + k=1 回收率
  判定   v3 须两段同时 ≥ v2（命中率与回收率任一主判）

开发者 sszhang
"""
from __future__ import annotations

import argparse
import io
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import score_family_model as sfm

UNIT = 2.0
V3_CUTS = {"train": None, "inner": ("2026-01-01", "2026-03-31"),
           "dev": ("2026-04-01", "2026-06-30")}


class Stats3:
    """v3 滚动统计：v2 全部字段 + 平局/积分/对手链/休息天/波动。"""

    __slots__ = ("n", "gf", "ga", "win", "gd", "draw", "cs", "becs", "btts",
                 "over25", "gf_side", "ga_side", "recent_gd", "recent",
                 "last_date", "pts")

    def __init__(self):
        self.n = self.gf = self.ga = self.win = self.gd = 0
        self.draw = self.cs = self.becs = self.btts = self.over25 = 0
        self.pts = 0
        self.gf_side = [[0, 0], [0, 0]]
        self.ga_side = [[0, 0], [0, 0]]
        self.recent_gd = []
        self.recent = []          # (opp, scored, conceded, date, at_home) 近15
        self.last_date = None

    def add(self, opp, scored, conceded, d, at_home):
        i = 0 if at_home else 1
        self.n += 1
        self.gf += scored
        self.ga += conceded
        self.gd += scored - conceded
        self.win += int(scored > conceded)
        self.draw += int(scored == conceded)
        self.pts += 3 if scored > conceded else (1 if scored == conceded else 0)
        self.cs += int(conceded == 0)
        self.becs += int(scored == 0)
        self.btts += int(scored > 0 and conceded > 0)
        self.over25 += int(scored + conceded > 2)
        self.gf_side[i][0] += scored
        self.gf_side[i][1] += 1
        self.ga_side[i][0] += conceded
        self.ga_side[i][1] += 1
        self.recent_gd.append(scored - conceded)
        if len(self.recent_gd) > 3:
            self.recent_gd.pop(0)
        self.recent.append((opp, scored, conceded, d, at_home))
        if len(self.recent) > 15:
            self.recent.pop(0)
        self.last_date = d

    def v2_vector(self, side, lg_gf):
        """与 score_family_model.TeamStats.vector 完全同构（13 维）。"""
        n = max(self.n, 1)
        ni = max(self.gf_side[side][1], 1)
        momentum = (sum(self.recent_gd) / len(self.recent_gd)
                    if self.recent_gd else 0.0) - self.gd / n
        return [self.gf_side[side][0] / ni,
                self.ga_side[side][0] / max(self.ga_side[side][1], 1),
                self.gf / n, self.ga / n,
                self.win / n, self.gd / n,
                self.cs / n, self.becs / n, self.btts / n, self.over25 / n,
                lg_gf, self.gf_side[side][0] / ni - lg_gf / 2,
                self.n, momentum][:13]

    def v3_extra(self, side, opp_stats, today, lg_gf):
        """v3 增补 7 维（需对手快照查表）。"""
        from datetime import date as _d
        n = max(self.n, 1)
        # 对手调整：近10个对手在"本场之前"的场均失/进球
        adj_gf = adj_ga = 0.0
        cnt = 0
        for opp, _s, _c, d, _h in self.recent[-10:]:
            o = opp_stats.get(opp)
            if not o or o["n"] < 3:
                continue
            adj_gf += o["ga"] / max(o["n"], 1)
            adj_ga += o["gf"] / max(o["n"], 1)
            cnt += 1
        if cnt:
            adj_gf /= cnt
            adj_ga /= cnt
        # 休息天数
        rest = 7.0
        if self.last_date:
            try:
                rest = (_d.fromisoformat(today) - _d.fromisoformat(self.last_date)).days
                rest = min(max(rest, 1), 21)
            except ValueError:
                pass
        # 近10净胜波动
        gds = [s - c for _o, s, c, _d2, _h in self.recent[-10:]]
        var = st.pstdev(gds) if len(gds) >= 3 else 0.0
        ppg6_n = min(len(self.recent), 6)
        ppg6 = 0.0
        for _o, s, c, _d2, _h in self.recent[-6:]:
            ppg6 += 3 if s > c else (1 if s == c else 0)
        ppg6 = ppg6 / ppg6_n if ppg6_n else 0.0
        return [self.draw / n,
                self.pts / n,
                ppg6,
                adj_gf if cnt else self.gf / n,
                adj_ga if cnt else self.ga / n,
                rest,
                var]


def load_all():
    zh = {}
    for tid, srcs in sfm.load_aliases().items():
        if srcs.get("zh"):
            zh[srcs["zh"]] = tid
    tl = sfm.league_timeline()
    hist = sfm.load_hist()
    live = []
    for m in hist:
        hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
        if hid and aid:
            live.append({**m, "hid": hid, "aid": aid})
    merged = [("L", d, h, a, hg, ag, None) for d, h, a, hg, ag in tl]
    merged += [("B", m["date"], m["hid"], m["aid"], m["actual"][0], m["actual"][1], i)
               for i, m in enumerate(live)]
    merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))
    return merged, live


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-inner", action="store_true")
    args = ap.parse_args()

    merged, live = load_all()
    # 两遍扫描：第一遍产出每场的 (v2特征行, v3特征行) + 对手快照表
    stats = defaultdict(Stats3)
    snap = {}          # 快照链：date → {team: (n,gf,ga)}（浅数值）
    X2_tr, y_tr = [], []
    seg = {"inner": ([], [], []), "dev": ([], [], [])}   # (X2, X3, meta)
    tot_g = tot_n = 0
    day_seen = set()
    for r in merged:
        kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
        lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
        s_h, s_a = stats[h], stats[a]
        # 当日首场前固化快照（对手强度用"今天开始时"的状态=严格无当日泄漏）
        if date not in day_seen:
            day_seen.add(date)
            snap[date] = {t: (s.n, s.gf, s.ga) for t, s in stats.items()}
        today_snap = snap[date]
        opp_lookup = defaultdict(lambda: {"n": 5, "gf": lg_gf / 2, "ga": lg_gf / 2})
        for t, (nn, gf, ga) in today_snap.items():
            opp_lookup[t] = {"n": nn, "gf": gf, "ga": ga}
        row2 = sfm.feature_row((s_h.v2_vector(0, lg_gf), s_a.v2_vector(1, lg_gf)))
        x3 = (s_h.v2_vector(0, lg_gf) + s_h.v3_extra(0, opp_lookup, date, lg_gf)
              + s_a.v2_vector(1, lg_gf) + s_a.v3_extra(1, opp_lookup, date, lg_gf))
        is_eval = kind == "B"
        if not is_eval and date < "2026-01-01":
            if s_h.n >= sfm.MIN_HIST and s_a.n >= sfm.MIN_HIST:
                X2_tr.append(row2)
                y_tr.append(sfm.CLASSES.index(sfm.family_of(hg, ag)))
        elif is_eval:
            seg_key = "inner" if date < "2026-04-01" else "dev"
            if not (args.skip_inner and seg_key == "inner"):
                seg[seg_key][0].append(row2)
                seg[seg_key][1].append(x3)
                seg[seg_key][2].append(live[r[6]])
        s_h.add(a, hg, ag, date, True)
        s_a.add(h, ag, hg, date, False)
        tot_g += hg + ag
        tot_n += 1

    print(f"训练集 {len(X2_tr)} 场（<2026-01）· "
          + " · ".join(f"{k} {len(v[2])} 场" for k, v in seg.items() if v[2]))

    # v2 模型（26 维）
    m2 = sfm.train_softmax(X2_tr, y_tr, len(sfm.CLASSES))
    # v3 模型（40 维）：X3_tr 从训练场重算——为省扫描，训练 v3 用 X2 训练场上的
    # v3 行需要在第一遍同步收集：改在收集处同时 append X3_tr
    # （此处 v3 训练集来自同一次扫描的 train 段 v3 行）
    # ——见下方 X3_tr 收集修正
    seg_report = {}
    for key, (X2s, X3s, meta) in seg.items():
        # 用 v2 模型给 v2 特征打分；v3 需 v3 训练 → 收集段再训练
        seg_report[key] = (X2s, X3s, meta)

    # v3 训练集：重放收集（train 段的 v3 行在扫描里没存——补一遍轻量重放）
    # 为控制成本：train 段 v3 行已在扫描时丢弃，这里重放一遍只补 train v3
    stats_b = defaultdict(Stats3)
    X3_tr, y3_tr = [], []
    tot_g = tot_n = 0
    day_seen = set()
    for r in merged:
        kind, date, h, a, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
        if kind != "L" or date >= "2026-01-01":
            # 仍需推进统计
            if kind == "L":
                stats_b[h].add(a, hg, ag, date, True)
                stats_b[a].add(h, ag, hg, date, False)
            continue
        lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
        s_h, s_a = stats_b[h], stats_b[a]
        if date not in day_seen:
            day_seen.add(date)
        opp_lookup = defaultdict(lambda: {"n": 5, "gf": lg_gf / 2, "ga": lg_gf / 2})
        x3 = (s_h.v2_vector(0, lg_gf) + s_h.v3_extra(0, opp_lookup, date, lg_gf)
              + s_a.v2_vector(1, lg_gf) + s_a.v3_extra(1, opp_lookup, date, lg_gf))
        if s_h.n >= sfm.MIN_HIST and s_a.n >= sfm.MIN_HIST:
            X3_tr.append(x3)
            y3_tr.append(sfm.CLASSES.index(sfm.family_of(hg, ag)))
        s_h.add(a, hg, ag, date, True)
        s_a.add(h, ag, hg, date, False)
        tot_g += hg + ag
        tot_n += 1
    m3 = sfm.train_softmax(X3_tr, y3_tr, len(sfm.CLASSES))
    print(f"v3 训练集 {len(X3_tr)} 场（40 维）")

    # 评估
    for key in ("inner", "dev"):
        if args.skip_inner and key == "inner":
            continue
        X2s, X3s, meta = seg_report[key]
        if not meta:
            continue
        P2 = sfm.predict_proba(m2, X2s)
        P3 = sfm.predict_proba(m3, X3s)
        fam = defaultdict(Counter)
        for r in merged:
            if r[0] == "L" and r[1] < "2026-01-01":
                fam[sfm.family_of(r[4], r[5])][(r[4], r[5])] += 1

        def top1_hit(dist):
            d = {}
            for ci, cname in enumerate(sfm.CLASSES):
                tot = sum(fam.get(cname, {}).values()) or 1
                for s, c in fam.get(cname, {}).items():
                    d[s] = d.get(s, 0.0) + dist[ci] * (c / tot)
            return max(d.items(), key=lambda kv: kv[1])[0]

        h2 = h3 = 0
        pay2 = pay3 = 0.0
        n = len(meta)
        for i, m in enumerate(meta):
            act = m["actual"]
            close = m["odds"]
            t2 = top1_hit(P2[i])
            t3 = top1_hit(P3[i])
            h2 += int(act == t2)
            h3 += int(act == t3)
            if act == t2 and act in close:
                pay2 += close[act] * UNIT
            if act == t3 and act in close:
                pay3 += close[act] * UNIT
        cost = n * UNIT
        print(f"\n— {key} 段（{n} 场）—")
        print(f"  v2: top1 {h2/n*100:.1f}% · 回收率 {pay2/cost*100:.1f}%")
        print(f"  v3: top1 {h3/n*100:.1f}% · 回收率 {pay3/cost*100:.1f}%")
        print(f"  判定：v3 {'↑' if h3 > h2 and pay3 >= pay2 else '↗ 部分改善' if pay3 > pay2 else '✗ 无改善'}")


if __name__ == "__main__":
    main()
