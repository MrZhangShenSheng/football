# -*- coding: utf-8 -*-
r"""比分族特征模型 v1（设计=docs/2026-09-29-score-family-model-design.html）。

结构：13 维时点滚动特征 → softmax 6 族分类 → 族内条件频率 → 31 项比分分布。
独立于赔率（铁律 13 的"独立概率源"路线）。

判定标准（预注册·盲测前锁定）：
  盲测集 = hist_odds 中 date >= 2026-07-01 且两队可映射规范 ID 的场次。
  主判据（双条件）：①模型 top-k ≠ 市场 top-k 占比 ②仅偏离场次上模型命中 > 市场。
  财务判据：逐场真实结算回收率 vs 市场天花板 84.0%/78.7%/77.2%/76.7%（k=1~4）。
  三方对照：市场封盘 / DC 缓存 / 经验频率模板。
  中奖次数 <50 的行只入脚注。

用法：
  python engine/scripts/research/score_family_model.py            # 训练+盲测一条龙
  python engine/scripts/research/score_family_model.py --skip-dc  # 不算 DC（快）

开发者 sszhang
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from common import load_aliases

ROOT = Path(__file__).resolve().parents[3]
CUT = "2026-07-01"
N_RECENT = 10
MIN_HIST = 5
FAMILIES = {
    "home_clean": [(1, 0), (2, 0), (3, 0)],
    "home_multi": [(2, 1), (3, 1), (3, 2)],
    "draw":       [(0, 0), (1, 1), (2, 2)],
    "away_clean": [(0, 1), (0, 2), (0, 3)],
    "away_multi": [(1, 2), (1, 3), (2, 3)],
}
FAM_OF = {s: f for f, mem in FAMILIES.items() for s in mem}
CLASSES = list(FAMILIES) + ["other"]
UNIT = 2.0


def family_of(h: int, a: int) -> str:
    return FAM_OF.get((h, a), "other")


# ---------------------------------------------------------------- 数据装载

def league_timeline():
    """联赛库全量 → 按日期升序的 (date, home, away, hg, ag)。"""
    rows = []
    for p in sorted((ROOT / "data/02-results/league").glob("*_matches.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        ms = d.get("matches", d) if isinstance(d, dict) else d
        for m in ms:
            if m.get("hg") is None or m.get("ag") is None:
                continue
            rows.append((m["date"], m["home"], m["away"], int(m["hg"]), int(m["ag"])))
    rows.sort(key=lambda r: r[0])
    return rows


def load_hist():
    out = []
    for p in sorted((ROOT / "engine/cache/hist_odds").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        for m in d.get("matches", []):
            sc = str(m.get("score") or "")
            crs = m.get("crs") or {}
            if ":" not in sc or not crs:
                continue
            try:
                h, a = (int(x) for x in sc.split(":")[:2])
            except ValueError:
                continue
            odds = {}
            for kk, v in crs.items():
                if str(kk).startswith("other") or not v:
                    continue
                try:
                    hh, aa = (int(x) for x in str(kk).split(":")[:2])
                    odds[(hh, aa)] = float(v)
                except (ValueError, TypeError):
                    continue
            if len(odds) < 20:
                continue
            out.append({"date": str(m.get("date") or "")[:10],
                        "code": m.get("code"), "league": m.get("league"),
                        "home_zh": m.get("home"), "away_zh": m.get("away"),
                        "actual": (h, a), "odds": odds})
    out.sort(key=lambda x: x["date"])
    return out


# ---------------------------------------------------------------- 特征（时点滚动）

class TeamStats:
    """按日期滚动累积的每队统计。update() 只喂严格早于当前预测场的比赛。

    v2 新增：动量（近3场 vs 全期的净胜差）——状态变化特征。"""

    __slots__ = ("n", "gf", "ga", "win", "gd", "cs", "becs", "btts", "over25",
                 "gf_side", "ga_side", "recent_gd", "recent")

    def __init__(self):
        self.n = 0
        self.gf = self.ga = self.win = self.gd = 0
        self.cs = self.becs = self.btts = self.over25 = 0
        self.gf_side = [[0, 0], [0, 0]]   # [主/客][进球和, 场次]
        self.ga_side = [[0, 0], [0, 0]]
        self.recent_gd = []               # 最近 3 场净胜（滑动窗口）
        self.recent = []                  # v4：(opp, scored, conceded) 近 10（族频率特征源）

    def add(self, scored: int, conceded: int, at_home: bool, opp=None):
        i = 0 if at_home else 1
        self.n += 1
        self.gf += scored
        self.ga += conceded
        self.gd += scored - conceded
        self.win += int(scored > conceded)
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
        if opp is not None:               # v4：传 opp 才记明细（向后兼容 v2 调用）
            self.recent.append((opp, scored, conceded))
            if len(self.recent) > 10:
                self.recent.pop(0)

    def vector(self, side: int, lg_gf: float):
        """side=0 主场视角 1 客场视角。14 维（v2 加动量 1 维）。"""
        n = max(self.n, 1)
        ni = max(self.gf_side[side][1], 1)
        momentum = (sum(self.recent_gd) / len(self.recent_gd)
                    if self.recent_gd else 0.0) - self.gd / n
        return [
            self.gf_side[side][0] / ni,                    # 对应侧场均进球
            self.ga_side[side][0] / max(self.ga_side[side][1], 1),   # 对应侧场均失球
            self.gf / n,                                    # 总场均进球
            self.ga / n,                                    # 总场均失球
            self.win / n,                                   # 胜率
            self.gd / n,                                    # 场均净胜
            self.cs / n,                                    # 零封率
            self.becs / n,                                  # 被零封率
            self.btts / n,                                  # BTTS 率
            self.over25 / n,                                # 大球率
            lg_gf,                                          # 联赛场均进球基准
            self.gf_side[side][0] / ni - lg_gf / 2,         # 侧向攻击偏离
            self.n,                                         # 样本量（供填充判断）
            momentum,                                       # v2 动量
        ]


def feature_row(fv):
    """(home_vec, away_vec) → 拼接特征（去样本量列后 13+13=26 维）。v2 基线。"""
    return fv[0][:13] + fv[1][:13]


FAMS_LIST = list(FAMILIES)


def fam_freq_vector(recent, n=10):
    """v4 增补：近 n 场 5 族频率（比分体质特征·2026-09-29 双段验证通过）。"""
    fc = Counter()
    tail = recent[-n:]
    for _opp, s_, c_ in tail:
        fc[family_of(s_, c_)] += 1
    tot = max(len(tail), 1)
    return [fc.get(f, 0) / tot for f in FAMS_LIST]


def feature_row_v4(fv, rec_h, rec_a):
    """v4 生产特征：v2 26 维 + 两队族频率 10 维 = 36 维。"""
    return fv[0][:13] + fv[1][:13] + fam_freq_vector(rec_h) + fam_freq_vector(rec_a)


# ---------------------------------------------------------------- softmax 回归

def train_softmax(X, y, n_class, l2=1.0, iters=400, balanced=False):
    """scipy 手写多项逻辑回归（L-BFGS）。balanced=类频率倒数加权（v2 优化项）。"""
    import numpy as np
    from scipy.optimize import minimize
    X = np.asarray(X, dtype=float)
    y = np.asarray(y)
    # 标准化（训练集统计量）
    mu, sd = X.mean(axis=0), X.std(axis=0) + 1e-9
    X = (X - mu) / sd
    n, d = X.shape
    Xb = np.hstack([X, np.ones((n, 1))])
    W0 = np.zeros((d + 1, n_class))
    if balanced:
        cnt = np.bincount(y, minlength=n_class).astype(float)
        w_cls = n / (n_class * np.maximum(cnt, 1.0))
        sw = w_cls[y]
    else:
        sw = np.ones(n)

    def loss(w):
        W = w.reshape(d + 1, n_class)
        z = Xb @ W
        z -= z.max(axis=1, keepdims=True)
        p = np.exp(z)
        p /= p.sum(axis=1, keepdims=True)
        ll = -(sw * np.log(p[np.arange(n), y] + 1e-12)).sum()
        reg = 0.5 * l2 * (W ** 2).sum()
        return ll + reg

    def grad(w):
        W = w.reshape(d + 1, n_class)
        z = Xb @ W
        z -= z.max(axis=1, keepdims=True)
        p = np.exp(z)
        p /= p.sum(axis=1, keepdims=True)
        p[np.arange(n), y] -= sw
        g = Xb.T @ p
        g += l2 * W
        return g.ravel()

    res = minimize(loss, W0.ravel(), jac=grad, method="L-BFGS-B",
                   options={"maxiter": iters})
    W = res.x.reshape(d + 1, n_class)
    return {"W": W, "mu": mu, "sd": sd}


def predict_proba(model, X):
    import numpy as np
    X = (np.asarray(X, dtype=float) - model["mu"]) / model["sd"]
    Xb = np.hstack([X, np.ones((len(X), 1))])
    z = Xb @ model["W"]
    z -= z.max(axis=1, keepdims=True)
    p = np.exp(z)
    return p / p.sum(axis=1, keepdims=True)


def bucket_of(fv_home, lg_gf_half):
    """攻击力分桶（v2 族内分布条件化）：对应侧场均进球 ≥ 联赛半场均 → 强攻桶。"""
    return 1 if fv_home[0] >= lg_gf_half else 0


def family_conditional(fam_dist, fam, bucket, lg_gf_half, min_n=50):
    """族内条件分布：按攻击力桶查，样本 <min_n 回退全局族分布（小样本纪律）。"""
    key = (fam, bucket)
    inside = fam_dist.get(key) or Counter()
    if sum(inside.values()) < min_n:
        inside = fam_dist.get((fam, None)) or Counter()
    return inside
    p = np.exp(z)
    return p / p.sum(axis=1, keepdims=True)


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-dc", action="store_true")
    a = ap.parse_args()

    print("=" * 78)
    print("比分族特征模型 v1 · 训练+盲测（判定标准预注册见设计文档 §〇）")
    print("=" * 78)

    zh = {}
    for tid, srcs in load_aliases().items():
        if srcs.get("zh"):
            zh[srcs["zh"]] = tid

    tl = league_timeline()

    # ---- 盲测集（映射）----
    hist = load_hist()
    blind, skipped_map = [], 0
    for m in hist:
        if m["date"] < CUT:
            continue
        hid, aid = zh.get(m["home_zh"]), zh.get(m["away_zh"])
        if not hid or not aid:
            skipped_map += 1
            continue
        blind.append({**m, "hid": hid, "aid": aid})
    print(f"盲测集：{len(blind)} 场（>={CUT}·两队可映射）· 映射失败跳过 {skipped_map} 场\n")

    # ---- 合并时间轴逐场滚动：联赛库(训练特征) + 盲测场(预测特征) ----
    # 无泄漏：每场特征只统计严格早于本场日期的比赛（同日场次按库序，训练集不混盲测周）
    CUT2 = "2026-04-01"   # dev 切分：优化迭代只在 train2/dev 内做，盲测仅最终确认
    merged = []
    for date, h, aw, hg, ag in tl:
        merged.append(("L", date, h, aw, hg, ag, None))
    for i, m in enumerate(blind):
        merged.append(("B", m["date"], m["hid"], m["aid"],
                       m["actual"][0], m["actual"][1], i))
    merged.sort(key=lambda r: (r[1], 0 if r[0] == "L" else 1))

    stats = defaultdict(TeamStats)
    seg = {"tr": ([], []), "dev": ([], []), "bl": ([], [])}
    meta_bl, meta_dev = [], []
    bucket_bl, bucket_dev = [], []
    tot_g = tot_n = 0
    for r in merged:
        kind, date, h, aw, hg, ag, bi = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
        lg_gf = (tot_g / tot_n) if tot_n >= 50 else 2.6
        fv_h = stats[h].vector(0, lg_gf)
        fv_a = stats[aw].vector(1, lg_gf)
        ready = fv_h[12] >= MIN_HIST and fv_a[12] >= MIN_HIST
        row = feature_row((fv_h, fv_a))
        if kind == "L" and ready:
            if date < CUT2:
                seg["tr"][0].append(row)
                seg["tr"][1].append(CLASSES.index(family_of(hg, ag)))
            elif date < CUT:
                seg["dev"][0].append(row)
                seg["dev"][1].append(CLASSES.index(family_of(hg, ag)))
        elif kind == "B":
            meta_bl.append(blind[bi])
            seg["bl"][0].append(row)
            bucket_bl.append(bucket_of(fv_h, lg_gf / 2))
        stats[h].add(hg, ag, True)
        stats[aw].add(ag, hg, False)
        tot_g += hg + ag
        tot_n += 1

    print(f"切分：train2(<{CUT2}) {len(seg['tr'][0])} 场 · dev {len(seg['dev'][0])} 场 · "
          f"盲测 {len(seg['bl'][0])} 场")

    # 族内条件频率：全局版（v1）与分桶版（v2），均只用 <CUT2（时点与 dev 对齐）
    fam_global = defaultdict(Counter)
    fam_bucket = defaultdict(Counter)
    stats2 = defaultdict(TeamStats)
    tot_g2 = tot_n2 = 0
    for r in merged:
        kind, date, h, aw, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
        lg_gf = (tot_g2 / tot_n2) if tot_n2 >= 50 else 2.6
        fv_h = stats2[h].vector(0, lg_gf)
        if kind == "L" and date < CUT2 and fv_h[12] >= MIN_HIST:
            f = family_of(hg, ag)
            fam_global[f][(hg, ag)] += 1
            fam_bucket[(f, bucket_of(fv_h, lg_gf / 2))][(hg, ag)] += 1
        stats2[h].add(hg, ag, True)
        stats2[aw].add(ag, hg, False)
        tot_g2 += hg + ag
        tot_n2 += 1

    # ---- dev 内部验证：v1 vs v2（配置选优·不碰盲测）----
    def dist_for(P_row, famtable, buckets_row, use_bucket):
        d = {}
        for ci, cname in enumerate(CLASSES):
            if use_bucket:
                inside = family_conditional(famtable, cname, buckets_row, 1.3)
            else:
                inside = famtable.get(cname) or Counter()
            tot = sum(inside.values()) or 1
            for s, c in inside.items():
                d[s] = d.get(s, 0.0) + P_row[ci] * (c / tot)
        return d

    def dev_eval(tag, balanced, use_bucket):
        mdl = train_softmax(seg["tr"][0], seg["tr"][1], len(CLASSES),
                            balanced=balanced)
        Pd = predict_proba(mdl, seg["dev"][0])
        picks, fam_hit = [], 0
        for bi, row in enumerate(Pd):
            d = dist_for(row, fam_bucket if use_bucket else fam_global,
                         bucket_dev[bi] if bi < len(bucket_dev) else 0, use_bucket)
            top = max(d.items(), key=lambda kv: kv[1])[0]
            picks.append(top)
        acts = [(r[4], r[5]) for r in merged
                if r[0] == "L" and CUT2 <= r[1] < CUT]
        n = min(len(picks), len(acts))
        h1 = sum(1 for p, act in zip(picks[:n], acts[:n]) if p == act)
        fh = sum(1 for p, act in zip(picks[:n], acts[:n])
                 if FAM_OF.get(p) == family_of(*act))
        print(f"  {tag:26} 比分top1 {h1}/{n} = {h1/n*100:.1f}% · "
              f"族命中 {fh}/{n} = {fh/n*100:.1f}%")
        return h1

    print("\nDev 内部验证（配置选优·不碰盲测）：")
    h_v1 = dev_eval("v1 基线(无权重·全局族内)", False, False)
    h_v2 = dev_eval("v2(类权重+族内分桶)", True, True)
    use_v2 = h_v2 >= h_v1
    print(f"  → 选用 {'v2（类权重+族内分桶）' if use_v2 else 'v1（基线）'}\n")

    # ---- 最终：full train(<CUT) 按选优配置重训 → 盲测四方对照 ----
    X_full = seg["tr"][0] + seg["dev"][0]
    y_full = seg["tr"][1] + seg["dev"][1]
    fam_global_full = defaultdict(Counter)
    fam_bucket_full = defaultdict(Counter)
    stats3 = defaultdict(TeamStats)
    tot_g3 = tot_n3 = 0
    for r in merged:
        kind, date, h, aw, hg, ag = r[0], r[1], r[2], r[3], r[4], r[5]
        lg_gf = (tot_g3 / tot_n3) if tot_n3 >= 50 else 2.6
        fv_h = stats3[h].vector(0, lg_gf)
        if kind == "L" and date < CUT and fv_h[12] >= MIN_HIST:
            f = family_of(hg, ag)
            fam_global_full[f][(hg, ag)] += 1
            fam_bucket_full[(f, bucket_of(fv_h, lg_gf / 2))][(hg, ag)] += 1
        stats3[h].add(hg, ag, True)
        stats3[aw].add(ag, hg, False)
        tot_g3 += hg + ag
        tot_n3 += 1

    model = train_softmax(X_full, y_full, len(CLASSES), balanced=use_v2)
    P = predict_proba(model, seg["bl"][0])
    model_dist = []
    for bi, row in enumerate(P):
        model_dist.append(dist_for(row,
                                   fam_bucket_full if use_v2 else fam_global_full,
                                   bucket_bl[bi] if bi < len(bucket_bl) else 0,
                                   use_v2))
    meta = meta_bl

    # ---- 四方对照 ----
    rank_tpl = Counter()
    for date, h, aw, hg, ag in tl:
        if date < CUT:
            rank_tpl[(hg, ag)] += 1
    tpl = [s for s, _ in rank_tpl.most_common()]

    dc_cache = {}
    if not a.skip_dc:
        for p in glob.glob(str(ROOT / "engine/cache/*_dc.json")):
            lg = Path(p).stem.replace("_dc", "")
            try:
                dc_cache[lg] = json.loads(Path(p).read_text(encoding="utf-8"))
            except Exception:
                pass

    def dc_rank(m):
        c = dc_cache.get(m["hid"]) and dc_cache[m["hid"]].get("teams") \
            and dc_cache[m["hid"]]
        return None

    # DC 排序：直接读联赛级缓存（规范 ID = 联赛库的 league 前缀不可知，改用队伍所在缓存）
    def dc_matrix_for(hid, aid):
        for lg, c in dc_cache.items():
            teams = c.get("teams") or {}
            if hid in teams and aid in teams:
                import math as _m
                lh = _m.exp(c["teams"][hid]["attack"] + c["teams"][aid]["defense"] + c["homeAdv"])
                la = _m.exp(c["teams"][aid]["attack"] + c["teams"][hid]["defense"])
                rho = c.get("rho", 0.0)
                from dc_predict import score_matrix
                return score_matrix(lh, la, rho)
        return None

    KS = (1, 2, 3, 4)
    n_all = Counter()
    hit = {"mkt": Counter(), "mdl": Counter(), "dc": Counter(), "tpl": Counter()}
    n_div = Counter(); div_mkt = Counter(); div_mdl = Counter()
    cost = Counter(); pay = {"mkt": Counter(), "mdl": Counter()}
    n_dc_ok = 0

    for i, m in enumerate(meta):
        act = m["actual"]
        close = m["odds"]
        mkt = [s for s, _ in sorted(close.items(), key=lambda kv: kv[1])]
        mdl = sorted(model_dist[i].items(), key=lambda kv: -kv[1])
        mdl = [s for s, _ in mdl]
        dm = dc_matrix_for(m["hid"], m["aid"])
        dcr = None
        if dm is not None:
            flat = sorted(((dm[x][y], (x, y)) for x in range(7) for y in range(7)),
                          reverse=True)
            dcr = [s for _, s in flat]
            n_dc_ok += 1
        n_all["n"] += 1
        for k in KS:
            hit["mkt"][k] += int(act in mkt[:k])
            hit["mdl"][k] += int(act in mdl[:k])
            hit["tpl"][k] += int(act in tpl[:k])
            if dcr:
                hit["dc"][k] += int(act in dcr[:k])
            if set(mkt[:k]) != set(mdl[:k]):
                n_div[k] += 1
                div_mkt[k] += int(act in mkt[:k])
                div_mdl[k] += int(act in mdl[:k])
            cost[k] += k * UNIT
            if act in mkt[:k]:
                pay["mkt"][k] += close[act] * UNIT
            if act in mdl[:k] and act in close:
                pay["mdl"][k] += close[act] * UNIT

    n = n_all["n"]
    print("=" * 78)
    print(f"四方命中率（盲测 {n} 场 · DC 可算 {n_dc_ok} 场）")
    print("=" * 78)
    print(f"  {'k':>3} {'市场':>8} {'DC':>8} {'模板':>8} {'本模型':>8}")
    for k in KS:
        dcv = f"{hit['dc'][k]/n_dc_ok*100:.1f}%" if n_dc_ok else "—"
        print(f"  {k:>3} {hit['mkt'][k]/n*100:>7.1f}% {dcv:>8} "
              f"{hit['tpl'][k]/n*100:>7.1f}% {hit['mdl'][k]/n*100:>7.1f}%")

    print("\n" + "=" * 78)
    print("主判据 · 偏离双条件（模型 vs 市场）")
    print("=" * 78)
    print(f"  {'k':>3} {'偏离场次':>7} {'市场命中':>9} {'模型命中':>9} {'差值':>9} {'判定':>8}")
    for k in KS:
        if not n_div[k]:
            continue
        pm = div_mkt[k] / n_div[k] * 100
        pd = div_mdl[k] / n_div[k] * 100
        print(f"  {k:>3} {n_div[k]:>7} {pm:>8.1f}% {pd:>8.1f}% {pd-pm:>+8.1f}pp "
              f"{'有alpha' if pd > pm else '无':>10}")

    print("\n" + "=" * 78)
    print("财务判据 · 真实结算回收率（k 选单关·逐场实际中奖项赔率）")
    print("=" * 78)
    print(f"  {'k':>3} {'市场':>9} {'本模型':>9}   (天花板线 84.0/78.7/77.2/76.7)")
    for k in KS:
        rm = pay["mkt"][k] / cost[k] * 100 if cost[k] else 0
        rd = pay["mdl"][k] / cost[k] * 100 if cost[k] else 0
        print(f"  {k:>3} {rm:>8.1f}% {rd:>8.1f}%")


if __name__ == "__main__":
    main()
