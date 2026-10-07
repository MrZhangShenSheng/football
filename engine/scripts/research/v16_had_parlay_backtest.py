# -*- coding: utf-8 -*-
r"""每日 HAD 串关回测（v16）——方向1实证检验（2026-10-07）

预注册（跑前写死）: engine/cache/strength_chain/fade-strategy-prereg.json → v16HadParlay
设计档: docs/2026-10-07-had-parlay-backtest-design.html

数据: engine/cache/odds_{league}_{season}.json（Pinnacle 三向收盘+赛果·三季 428 日 8820 场·与 7216 sweep 同底子）

结构: 每日日池选腿 · 每腿单选 argmax 方向 · N串1 一注 2 元 · 成交按 Pinnacle 收盘（零 CLV 假设）
策略: S1 置信度(D=3..8) / S2 赔率甜点[1.40,1.90](D=3..8) / S3 置信分层(胆0.75+标准0.60+踩线护栏·变深) /
      S4 彩票档门槛(p>=0.55或赔率<=1.25·变深) / RND 随机基线×200 种子(固定D+逐日配对腿数)
判据: ①S1~S4 ROI 落 RND 95% 区间=无增量 ②任一策略 bootstrap CI 下限>0 且扣最大单日仍>0=唯一跟进信号
      ③全灭=方向1盖棺 ④胆级/标准级大样本命中率仅报告
审计三件套: ①逐日票面明细 stdout 打印 ②票级全量落盘 tickets[] ③--audit 复算对账+抽 10 天全链路重放

用法:
  python engine/scripts/research/v16_had_parlay_backtest.py          # 跑回测
  python engine/scripts/research/v16_had_parlay_backtest.py --audit  # 复算对账（结算审计）

开发者 sszhang
"""
import json
import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "engine" / "scripts"))

from dc_predict import devig  # noqa: E402  比例去水（与主链同源）

CACHE = ROOT / "engine" / "cache"
SUMM = ROOT / "data" / "04-summaries"
OUT_JSON = SUMM / "v16-had-parlay-backtest.json"
OUT_HTML = SUMM / "2026-10-07-v16-had-parlay-review.html"

# ---- 预注册常量（禁魔法值·判据跑前写死） ----
STAKE = 2.0                       # 每注 2 元
DEPTHS = (3, 4, 5, 6, 7, 8)       # 固定串深档
ODDS_SWEET_LO, ODDS_SWEET_HI = 1.40, 1.90   # S2 甜点赔率段
TIER_DAN_P = 0.75                 # S3 胆级线（09-06 结论）
TIER_STD_P = 0.60                 # S3 标准级线
TREADLINE_ODDS, TREADLINE_P = 1.35, 0.68    # S3 踩线护栏（朗斯案）
LOTTERY_P = 0.55                  # S4 彩票档概率门槛
LOTTERY_ODDS = 1.25               # S4 超低赔门槛
MAX_LEGS = 8                      # 变深策略上限
S3_MIN_LEGS = 3                   # S3 关档线
S4_MIN_LEGS = 4                   # S4 关档线（彩票档 4~8 串）
N_RND = 200                       # 随机基线种子数
N_BOOT = 2000                     # bootstrap 次数
BOOT_SEED = 20261007              # bootstrap 种子（预注册）
RND_SAMPLE_SEED = 1               # 明细打印/票级落盘的 RND 样本种子
AUDIT_SAMPLE_DAYS = 10            # --audit 全链路重放抽检天数
BAND_EDGES = (1.4, 1.9)           # 体彩敏感性赔率分带
DUP_ALIAS = {"portugal-primeira": "portugal-liga"}  # 同一葡超两份缓存去重（sweep 同口径）
PICK_LABEL = {"H": "主胜", "D": "平", "A": "客胜"}


# ---------------------------------------------------------------- 数据装载
def load_day_pools():
    """→ {date: [match dict]} · match: league/season/home/away/odds(H,D,A)/probs/pick/pickP/pickOdds/res."""
    pools = defaultdict(list)
    seen_files = set()
    n_matches = 0
    for p in sorted(CACHE.glob("odds_*.json")):
        league, _, season = p.stem[len("odds_"):].rpartition("_")
        if not (league and season.isdigit()):
            continue
        key = (DUP_ALIAS.get(league, league), season)
        if key in seen_files:
            continue
        seen_files.add(key)
        raw = json.loads(p.read_text(encoding="utf-8"))
        for m in raw.get("matches", []):
            try:
                d = datetime.strptime(m["date"], "%d/%m/%Y").date().isoformat()
            except (ValueError, TypeError, KeyError):
                continue
            if not (m.get("pin_h") and m.get("pin_d") and m.get("pin_a") and m.get("fthg") is not None):
                continue
            odds = {"H": float(m["pin_h"]), "D": float(m["pin_d"]), "A": float(m["pin_a"])}
            probs = dict(zip(("H", "D", "A"), devig([odds["H"], odds["D"], odds["A"]])))
            pick = max(probs, key=probs.get)
            hg, ag = int(m["fthg"]), int(m["ftag"])
            pools[d].append({
                "date": d, "league": key[0], "season": season,
                "home": m["home"].strip().lower(), "away": m["away"].strip().lower(),
                "odds": odds, "probs": probs,
                "pick": pick, "pickP": round(probs[pick], 4), "pickOdds": odds[pick],
                "res": "H" if hg > ag else ("D" if hg == ag else "A"),
                "score": f"{hg}:{ag}",
            })
            n_matches += 1
    return dict(pools), n_matches


# ---------------------------------------------------------------- 选腿策略
def _sorted_by_p(pool):
    return sorted(pool, key=lambda l: (-l["pickP"], l["pickOdds"], l["home"], l["away"]))


def sel_s1(pool, depth):
    """S1 置信度：去水概率 argmax 最高前 D 场（日池<D 关档）。"""
    if len(pool) < depth:
        return None
    return _sorted_by_p(pool)[:depth]


def sel_s2(pool, depth):
    """S2 赔率甜点：argmax 赔率∈[1.40,1.90] 按概率取前 D·不足全池概率降序补齐。"""
    if len(pool) < depth:
        return None
    sweet = [l for l in pool if ODDS_SWEET_LO <= l["pickOdds"] <= ODDS_SWEET_HI]
    sweet = _sorted_by_p(sweet)
    if len(sweet) >= depth:
        return sweet[:depth]
    rest = [l for l in _sorted_by_p(pool) if l not in sweet]
    return (sweet + rest)[:depth]


def sel_s3(pool):
    """S3 置信分层（09-06 结论对照臂）：踩线护栏后 p>=0.60·胆级优先·N=min(8,池)·池<3 关档。"""
    qual = [l for l in pool if l["pickP"] >= TIER_STD_P
            and not (l["pickOdds"] < TREADLINE_ODDS and l["pickP"] < TREADLINE_P)]
    if len(qual) < S3_MIN_LEGS:
        return None, len(qual)
    dans = _sorted_by_p([l for l in qual if l["pickP"] >= TIER_DAN_P])
    stds = _sorted_by_p([l for l in qual if l["pickP"] < TIER_DAN_P])
    return (dans + stds)[:MAX_LEGS], len(qual)


def sel_s4(pool):
    """S4 彩票档门槛（boldplay 现行）：p>=0.55 或赔率<=1.25 全上·N=min(8,池)·池<4 关档。"""
    qual = _sorted_by_p([l for l in pool if l["pickP"] >= LOTTERY_P or l["pickOdds"] <= LOTTERY_ODDS])
    if len(qual) < S4_MIN_LEGS:
        return None, len(qual)
    return qual[:MAX_LEGS], len(qual)


# ---------------------------------------------------------------- 结算
def settle(legs):
    """全中 → 2×赔率积；任一腿错 → 0。→ (回款, 是否全中, 赔率积)"""
    prod = 1.0
    for l in legs:
        if l["res"] != l["pick"]:
            return 0.0, False, prod
        prod *= l["pickOdds"]
    return STAKE * prod, True, prod


def mk_ticket(date, strat, legs, depth_label):
    ret, hit, prod = settle(legs)
    return {
        "date": date, "season": legs[0]["season"], "strat": strat, "depth": depth_label,
        "legs": [{"home": l["home"], "away": l["away"], "league": l["league"],
                  "pick": l["pick"], "pickOdds": l["pickOdds"], "p": l["pickP"],
                  "score": l["score"], "res": l["res"], "hit": l["res"] == l["pick"]}
                 for l in legs],
        "oddsProd": round(prod, 3), "stake": STAKE, "ret": round(ret, 2), "hit": hit,
    }


def print_ticket(t, cum_stake, cum_ret):
    print(f"[{t['date']}] {t['strat']} {t['depth']} · {len(t['legs'])}腿")
    for i, l in enumerate(t["legs"], 1):
        mark = "✓" if l["hit"] else "✗"
        print(f"  腿{i} {l['home']} vs {l['away']} [{l['league']}] "
              f"选{PICK_LABEL[l['pick']]} @{l['pickOdds']:.2f} p={l['p']:.2f} "
              f"→ 赛果{l['score']} {mark}")
    roi = cum_ret / cum_stake - 1 if cum_stake else 0.0
    verdict = f"全中 +{t['ret']:.2f}元" if t["hit"] else "未全中 -2.00元"
    print(f"  票面: {t['depth']}串1×2元 · 赔率积{t['oddsProd']:.2f} · 结算: {verdict} "
          f"· 累计投入{cum_stake:.0f}元 回款{cum_ret:.2f}元 ROI{roi:+.1%}")


# ---------------------------------------------------------------- 指标
def summarize(tickets):
    """票级汇总 → ROI/全中率/回款日率/扣最大单日/分赛季/最大连黑/bootstrap CI。"""
    if not tickets:
        return None
    stake = sum(t["stake"] for t in tickets)
    ret = sum(t["ret"] for t in tickets)
    days = defaultdict(lambda: [0.0, 0.0])          # date → [stake, ret]
    for t in tickets:
        days[t["date"]][0] += t["stake"]
        days[t["date"]][1] += t["ret"]
    day_list = sorted(days.items())
    best_net, best_day = max(((r - s, d) for d, (s, r) in day_list))
    stake_ex = stake - days[best_day][0]
    ret_ex = ret - days[best_day][1]
    by_season = {}
    for sn in sorted({t["season"] for t in tickets}):
        ts = [t for t in tickets if t["season"] == sn]
        by_season[sn] = {"n": len(ts),
                         "roi": round(sum(t["ret"] for t in ts) / sum(t["stake"] for t in ts) - 1, 4)}
    ts_sorted = sorted(tickets, key=lambda t: t["date"])
    streak = max_streak = 0
    for t in ts_sorted:
        streak = 0 if t["ret"] > 0 else streak + 1
        max_streak = max(max_streak, streak)
    # bootstrap 按日重抽样
    import numpy as np
    rng = np.random.RandomState(BOOT_SEED)
    day_arr = [(s, r) for _, (s, r) in day_list]
    rois = []
    for _ in range(N_BOOT):
        idx = rng.randint(0, len(day_arr), len(day_arr))
        s = sum(day_arr[i][0] for i in idx)
        r = sum(day_arr[i][1] for i in idx)
        rois.append(r / s - 1 if s else 0.0)
    rois.sort()
    return {
        "nTickets": len(tickets), "nDays": len(days),
        "stake": round(stake, 2), "ret": round(ret, 2),
        "roi": round(ret / stake - 1, 4),
        "hitRate": round(sum(1 for t in tickets if t["hit"]) / len(tickets), 4),
        "payDayRate": round(sum(1 for _, r in day_list if r[1] > 0) / len(day_list), 4),
        "roiExMaxDay": round(ret_ex / stake_ex - 1, 4) if stake_ex else None,
        "maxDay": {"date": best_day, "net": round(best_net, 2)},
        "maxLoseStreak": max_streak,
        "bySeason": by_season,
        "boot": {"p2.5": round(rois[int(0.025 * N_BOOT)], 4),
                 "p50": round(rois[N_BOOT // 2], 4),
                 "p97.5": round(rois[int(0.975 * N_BOOT)], 4)},
    }


# ---------------------------------------------------------------- RND 基线
def run_rnd(pools, paired_counts):
    """×200 种子：固定 D 档 + S3/S4 逐日配对腿数。→ {结构: [roi_seed,...]}·seed1 票样例。"""
    dist = {f"D{d}": [] for d in DEPTHS}
    dist["pairedS3"], dist["pairedS4"] = [], []
    sample_tickets = []
    for seed in range(1, N_RND + 1):
        r = random.Random(seed)
        for d in DEPTHS:
            stake = ret = 0.0
            for date in sorted(pools):
                pool = pools[date]
                if len(pool) < d:
                    continue
                legs = r.sample(pool, d)
                s_ret, _, _ = settle(legs)
                stake += STAKE
                ret += s_ret
                if seed == RND_SAMPLE_SEED:
                    sample_tickets.append(mk_ticket(date, f"RND_D{d}", legs, f"{d}串1"))
            dist[f"D{d}"].append(ret / stake - 1 if stake else 0.0)
        for tag in ("S3", "S4"):
            stake = ret = 0.0
            for date, n in paired_counts[tag]:
                legs = r.sample(pools[date], n)
                s_ret, _, _ = settle(legs)
                stake += STAKE
                ret += s_ret
                if seed == RND_SAMPLE_SEED:
                    sample_tickets.append(mk_ticket(date, f"RND_paired{tag}", legs, f"{n}串1"))
            dist[f"paired{tag}"].append(ret / stake - 1 if stake else 0.0)
    return dist, sample_tickets


def rnd_dist_summary(rois):
    rs = sorted(rois)
    return {"n": len(rs), "p2.5": round(rs[int(0.025 * len(rs))], 4),
            "p50": round(rs[len(rs) // 2], 4),
            "p97.5": round(rs[int(0.975 * len(rs))], 4)}


def rnd_percentile(rois, roi):
    return round(100.0 * sum(1 for r in rois if r <= roi) / len(rois), 1)


# ---------------------------------------------------------------- 体彩价敏感性
def sporttery_sensitivity(pools):
    """34 天体彩档 vs Pinnacle 逐赔率带比价 → {band: (n, ratio_mean)}·匹配按 zh别名→id·日期±1。"""
    alias_path = ROOT / "data" / "01-teams" / "_aliases.json"
    zh2id = {}
    if alias_path.exists():
        for _, teams in json.loads(alias_path.read_text(encoding="utf-8")).items():
            if not isinstance(teams, dict):
                continue
            for tid, meta in teams.items():
                if isinstance(meta, dict) and meta.get("zh"):
                    zh2id.setdefault(meta["zh"], tid)
    lookup = {}
    for date, pool in pools.items():
        for l in pool:
            lookup[(date, l["home"], l["away"])] = l
    from datetime import date as _date, timedelta as _td
    matched = defaultdict(list)
    files = sorted((CACHE / "score_odds").glob("*.json"))
    n_files, n_matches = len(files), 0
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for day in d.get("matchDays", []):
            try:
                bd = datetime.strptime(day.get("businessDate", ""), "%Y-%m-%d").date()
            except ValueError:
                continue
            for m in day.get("matches", []):
                had = m.get("had") or {}
                if not (had.get("h") and had.get("d") and had.get("a")):
                    continue
                h_id, a_id = zh2id.get(m.get("home")), zh2id.get(m.get("away"))
                if not (h_id and a_id):
                    continue
                for delta in (0, -1, 1):
                    cand = (bd + _td(days=delta)).isoformat()
                    l = lookup.get((cand, h_id, a_id))
                    if l:
                        n_matches += 1
                        for side in ("H", "D", "A"):
                            po = l["odds"][side]
                            band = ("<1.4" if po < BAND_EDGES[0]
                                    else "1.4-1.9" if po <= BAND_EDGES[1] else ">1.9")
                            matched[band].append(had[{"H": "h", "D": "d", "A": "a"}[side]] / po)
                        break
    bands = {b: {"n": len(v), "ratioMean": round(sum(v) / len(v), 4)} for b, v in sorted(matched.items())}
    return {"nFiles": n_files, "nMatches": n_matches, "bands": bands}


# ---------------------------------------------------------------- 判据（预注册·机械应用）
def apply_criteria(summaries, rnd_dist):
    out = {}
    for key, s in summaries.items():
        dist_key = f"D{key.split('_D')[1]}" if "_D" in key else f"paired{key}"
        rois = rnd_dist.get(dist_key, [])
        if not rois or not s:
            continue
        ci_low = s["boot"]["p2.5"]
        out[key] = {
            "c1_inRnd95": s["roi"] >= rnd_dist_summary(rois)["p2.5"] and s["roi"] <= rnd_dist_summary(rois)["p97.5"],
            "c2_ciLowPos_exMaxPos": ci_low > 0 and s["roiExMaxDay"] is not None and s["roiExMaxDay"] > 0,
            "rndPercentile": rnd_percentile(rois, s["roi"]),
        }
    any_follow = any(v["c2_ciLowPos_exMaxPos"] for v in out.values())
    verdict = ("FOLLOW-UP(判据②触发→过体彩敏感性→启动丙+)" if any_follow
               else "SEALED(判据③·全灭→方向1盖棺·影子层亦不挂)" if all(s["roi"] < 0 for s in summaries.values())
               else "MIXED(存在正ROI但未过判据②→不跟进·细节见报告)")
    return out, verdict


# ---------------------------------------------------------------- HTML 报告
def gen_html(result):
    rows = "".join(
        f"<tr><td>{k}</td><td class='num'>{s['nTickets']}</td><td class='num'>{s['nDays']}</td>"
        f"<td class='num'>{s['stake']:.0f}</td><td class='num'>{s['ret']:.2f}</td>"
        f"<td class='num {'ok' if s['roi'] > 0 else 'bad'}'>{s['roi']:+.1%}</td>"
        f"<td class='num'>{s['hitRate']:.1%}</td><td class='num'>{s['payDayRate']:.1%}</td>"
        f"<td class='num'>{s['roiExMaxDay']:+.1%}</td>"
        f"<td class='num'>{s['boot']['p2.5']:+.1%}~{s['boot']['p97.5']:+.1%}</td>"
        f"<td class='num'>{result['criteria'][k]['rndPercentile']}</td></tr>"
        for k, s in result["strategies"].items())
    season_rows = "".join(
        f"<tr><td>{k}</td>" + "".join(f"<td class='num'>{s['bySeason'].get(sn, {}).get('roi', '—')}</td>"
                                      for sn in ("2425", "2526", "2627")) + "</tr>"
        for k, s in result["strategies"].items())
    tier_rows = "".join(
        f"<tr><td>{b['band']}</td><td class='num'>{b['n']}</td><td class='num'>{b['hitRate']:.1%}</td></tr>"
        for b in result["tierRecheck"]["bands"])
    sens = result["sensitivity"]
    sens_rows = "".join(
        f"<tr><td>{b}</td><td class='num'>{v['n']}</td><td class='num'>{v['ratioMean']}</td></tr>"
        for b, v in sens["bands"].items()) or "<tr><td colspan=3>匹配过稀·降级定性</td></tr>"
    def _leg_cell(t):
        return "; ".join(f"{l['home']} {PICK_LABEL[l['pick']]}@{l['pickOdds']:.2f}"
                         f"{'✓' if l['hit'] else '✗'}" for l in t["legs"])

    ledger_rows = "".join(
        f"<tr><td>{t['date']}</td><td>{t['strat']} {t['depth']}</td><td>{_leg_cell(t)}</td>"
        f"<td class='num'>{t['oddsProd']:.2f}</td><td class='num'>{t['ret']:.2f}</td></tr>"
        for t in result["tickets"][:30])
    v = result["verdict"]
    v_cls = "ok" if v.startswith("FOLLOW") else ("bad" if v.startswith("SEALED") else "warn")
    a = result["audit"]
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>v16 每日HAD串关回测 · 审计报告</title>
<style>
:root{{--ink:#1c2733;--sub:#5b6b7a;--line:#dde4ea;--bg:#f7f9fb;--card:#fff;--ok:#0f6b4f;--bad:#a33;--warn:#b3541e;--accent:#1a5fb4}}
*{{box-sizing:border-box}}
body{{font-family:"Microsoft YaHei","PingFang SC",sans-serif;color:var(--ink);background:var(--bg);margin:0;padding:24px 16px;max-width:1100px;margin-inline:auto}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:15px;margin:20px 0 8px;padding-bottom:5px;border-bottom:2px solid var(--accent)}}
.meta{{color:var(--sub);font-size:12.5px;margin-bottom:12px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px 16px;margin:8px 0}}
table{{border-collapse:collapse;width:100%;font-size:12px;margin:5px 0}}
th,td{{border:1px solid var(--line);padding:3px 6px;text-align:left}} th{{background:#f0f4f7}}
.num{{font-variant-numeric:tabular-nums}} .ok{{color:var(--ok);font-weight:600}} .bad{{color:var(--bad);font-weight:600}} .warn{{color:var(--warn);font-weight:600}}
.small{{font-size:11.5px;color:var(--sub)}} pre{{background:#f0f4f7;border-radius:8px;padding:10px;overflow-x:auto;font-size:11.5px}}
</style></head><body>
<h1>v16 每日 HAD 串关回测 · 审计报告</h1>
<div class="meta">跑时 {result['ranAt']} · 数据 {result['data']['days']} 日 {result['data']['matches']} 场（{result['data']['seasons']}）· 预注册 fade-strategy-prereg v16 · 成交按 Pinnacle 收盘</div>
<div class="card"><b>终局判定：</b><span class="{v_cls}">{v}</span></div>
<h2>一、策略 × 深度汇总（判据机械应用）</h2>
<div class="card"><table>
<tr><th>策略</th><th>票数</th><th>日数</th><th>投入</th><th>回款</th><th>ROI</th><th>全中率</th><th>回款日率</th><th>扣最大单日ROI</th><th>bootstrap95%CI</th><th>RND分位</th></tr>
{rows}</table>
<p class="small">c1_inRnd95=选场无增量 · c2=CI下限&gt;0且扣最大单日&gt;0（唯一跟进信号）· 明细见 JSON criteria 节</p></div>
<h2>二、分赛季 ROI</h2>
<div class="card"><table><tr><th>策略</th><th>2425</th><th>2526</th><th>2627</th></tr>{season_rows}</table></div>
<h2>三、胆级/标准级大样本复验（判据④·仅报告）</h2>
<div class="card"><table><tr><th>p 分带</th><th>n</th><th>腿命中率</th></tr>{tier_rows}</table>
<p class="small">{result['tierRecheck']['note']}</p></div>
<h2>四、体彩价敏感性（34 天档比价·非主口径）</h2>
<div class="card"><table><tr><th>赔率带</th><th>样本</th><th>体彩/Pinnacle 均值比</th></tr>{sens_rows}</table>
<p class="small">匹配 {sens['nMatches']} 对（{sens['nFiles']} 档案·zh别名→id·日期±1）·均值比&lt;1 即体彩端回报再打折</p></div>
<h2>五、对账行（审计③）</h2>
<div class="card"><pre>{a['reconcile']}</pre>
<p class="small">票级复算 {a['ticketRecompute']} · 抽 {a['replayDays']} 天全链路重放 {a['replayCheck']}</p></div>
<h2>六、票务总账样例（前 30 票·全量 {len(result['tickets'])} 票见 JSON tickets[]）</h2>
<div class="card"><table><tr><th>日期</th><th>策略</th><th>票面</th><th>赔率积</th><th>回款</th></tr>{ledger_rows}</table></div>
<div class="small">开发者 sszhang · 2026-10-07 · 明细流水 stdout 留档 · 审计口径=票面可独立重算一切汇总</div>
</body></html>"""


# ---------------------------------------------------------------- 主流程
def run():
    pools, n_matches = load_day_pools()
    dates = sorted(pools)
    print(f"底表装载: {len(dates)} 比赛日 · {n_matches} 场 · 赛季分布 "
          f"{dict(sorted(((sn, sum(1 for d in dates for l in pools[d] if l['season'] == sn)) for sn in {l['season'] for d in dates for l in pools[d]})))}")

    tickets = []
    summaries = {}
    s3_pairs, s4_pairs = [], []          # (date, n) 供 RND 配对
    cum = defaultdict(lambda: [0.0, 0.0])  # strat → [cumStake, cumRet]（打印累计用）

    def emit(t):
        tickets.append(t)
        c = cum[t["strat"]]
        c[0] += t["stake"]
        c[1] += t["ret"]
        print_ticket(t, c[0], c[1])

    print("=" * 72)
    for date in dates:
        pool = pools[date]
        print(f"\n◆ {date} · 日池 {len(pool)} 场")
        for d in DEPTHS:
            for tag, sel in (("S1置信度", lambda: sel_s1(pool, d)),
                             ("S2赔率甜点", lambda: sel_s2(pool, d))):
                legs = sel()
                if legs:
                    emit(mk_ticket(date, f"{tag[:2]}_D{d}", legs, f"{d}串1"))
        legs3, q3 = sel_s3(pool)
        if legs3:
            s3_pairs.append((date, len(legs3)))
            emit(mk_ticket(date, "S3", legs3, f"{len(legs3)}串1"))
        else:
            print(f"  [S3置信分层] 关档（合格腿 {q3}<{S3_MIN_LEGS}）")
        legs4, q4 = sel_s4(pool)
        if legs4:
            s4_pairs.append((date, len(legs4)))
            emit(mk_ticket(date, "S4", legs4, f"{len(legs4)}串1"))
        else:
            print(f"  [S4彩票档] 关档（合格腿 {q4}<{S4_MIN_LEGS}）")

    # RND 基线（不逐票打印·只落 seed=1 样例）
    print("\n" + "=" * 72 + "\nRND×200 基线计算中（seed=1 样例落盘）...")
    rnd_dist, rnd_sample = run_rnd(pools, {"S3": s3_pairs, "S4": s4_pairs})
    for t in rnd_sample:
        tickets.append(t)

    # 汇总
    groups = defaultdict(list)
    for t in tickets:
        if not t["strat"].startswith("RND"):
            groups[t["strat"]].append(t)
    for key in sorted(groups):
        s = summarize(groups[key])
        if s:
            summaries[key] = s

    # 判据④ 胆级/标准级大样本复验（全量场·腿级）
    all_legs = [l for d in dates for l in pools[d]]
    bands = []
    for name, lo, hi in (("p>=0.75(胆级)", TIER_DAN_P, 9.99), ("0.60<=p<0.75(标准级)", TIER_STD_P, TIER_DAN_P),
                         ("0.55<=p<0.60", LOTTERY_P, TIER_STD_P), ("p<0.55", -1.0, LOTTERY_P)):
        sel_legs = [l for l in all_legs if lo <= l["pickP"] < hi]
        bands.append({"band": name, "n": len(sel_legs),
                      "hitRate": round(sum(1 for l in sel_legs if l["res"] == l["pick"]) / len(sel_legs), 4)
                      if sel_legs else None})
    tier_recheck = {"bands": bands,
                    "note": "去水市场概率口径（非生产 p_fused）·对照 09-06 结论（胆级96% n=24/标准级74~77%）·仅报告不作为转正依据"}

    sensitivity = sporttery_sensitivity(pools)

    criteria, verdict = apply_criteria(summaries, rnd_dist)

    result = {
        "ranAt": "2026-10-07", "script": str(Path(__file__).relative_to(ROOT)),
        "preReg": "fade-strategy-prereg v16HadParlay（跑前写死·95b4a5f）",
        "data": {"days": len(dates), "matches": n_matches,
                 "seasons": {sn: sum(1 for d in dates for l in pools[d] if l["season"] == sn)
                             for sn in sorted({l["season"] for d in dates for l in pools[d]})},
                 "settle": "Pinnacle 收盘价·零 CLV·每注 2 元"},
        "strategies": summaries,
        "rnd": {k: rnd_dist_summary(v) for k, v in rnd_dist.items()},
        "criteria": criteria, "verdict": verdict,
        "tierRecheck": tier_recheck, "sensitivity": sensitivity,
        "audit": {"reconcile": "", "ticketRecompute": "", "replayDays": 0, "replayCheck": ""},
        "tickets": tickets,
    }

    # 自检对账（跑时即做一道·--audit 再独立复算）
    n_paid = sum(len(v) for v in groups.values())
    tot_stake = sum(t["stake"] for v in groups.values() for t in v)
    tot_ret = sum(t["ret"] for v in groups.values() for t in v)
    result["audit"]["reconcile"] = (
        f"S1~S4 票数 {n_paid} × 2元 = 投入 {tot_stake:.0f}元 · 票面回款和 = {tot_ret:.2f}元 · 总ROI {tot_ret / tot_stake - 1:+.2%}")
    result["audit"]["ticketRecompute"] = audit_recompute(result, verbose=False)
    result["audit"]["replayDays"] = AUDIT_SAMPLE_DAYS
    result["audit"]["replayCheck"] = audit_replay(result, verbose=False)

    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    OUT_HTML.write_text(gen_html(result), encoding="utf-8")
    print("\n" + "=" * 72)
    print(f"终局判定: {verdict}")
    for k, s in summaries.items():
        c = criteria.get(k, {})
        print(f"  {k:8s} ROI {s['roi']:+7.1%} · CI[{s['boot']['p2.5']:+.1%},{s['boot']['p97.5']:+.1%}] "
              f"· RND分位 {c.get('rndPercentile', '—')} · 扣最大单日 {s['roiExMaxDay']:+.1%}")
    print(f"产物: {OUT_JSON.name} + {OUT_HTML.name}")
    return result


# ---------------------------------------------------------------- 审计
def audit_recompute(result, verbose=True):
    """从 tickets[] 独立重算全部汇总·与 strategies 比对。→ PASS/FAIL（逐项）"""
    groups = defaultdict(list)
    for t in result["tickets"]:
        if not t["strat"].startswith("RND"):
            groups[t["strat"]].append(t)
    fails = []
    for key, ts in sorted(groups.items()):
        s = result["strategies"].get(key)
        if not s:
            fails.append(f"{key}: 汇总缺失")
            continue
        stake = sum(t["stake"] for t in ts)
        ret = sum(t["ret"] for t in ts)
        for name, got, want in (("nTickets", len(ts), s["nTickets"]), ("stake", round(stake, 2), s["stake"]),
                                ("ret", round(ret, 2), s["ret"]), ("roi", round(ret / stake - 1, 4), s["roi"]),
                                ("hitRate", round(sum(1 for t in ts if t["hit"]) / len(ts), 4), s["hitRate"])):
            if got != want:
                fails.append(f"{key}.{name}: 票面重算 {got} ≠ 落盘 {want}")
        # 单票结算复算（腿级）
        for t in ts:
            prod = 1.0
            ok = all(l["hit"] == (l["res"] == l["pick"]) for l in t["legs"])
            hit = all(l["hit"] for l in t["legs"])
            for l in t["legs"]:
                if l["hit"]:
                    prod *= l["pickOdds"]
            if not ok or t["hit"] != hit or (hit and abs(t["ret"] - round(STAKE * prod, 2)) > 0.01) or (not hit and t["ret"] != 0):
                fails.append(f"{t['date']} {key}: 结算腿级复算不符")
                break
    verdict = "PASS" if not fails else f"FAIL({len(fails)}项)——" + ";".join(fails[:5])
    if verbose:
        print(f"[审计②票级复算] {verdict}")
    return verdict


def audit_replay(result, verbose=True):
    """抽 AUDIT_SAMPLE_DAYS 天·从原始底表全链路重放·比对票面。→ PASS/FAIL"""
    dates = sorted({t["date"] for t in result["tickets"]})
    if not dates:
        return "SKIP(无票)"
    step = max(1, len(dates) // AUDIT_SAMPLE_DAYS)
    sample = dates[::step][:AUDIT_SAMPLE_DAYS]
    pools, _ = load_day_pools()
    fails = []
    for date in sample:
        pool = pools.get(date, [])
        expect = {
            **{f"S1_D{d}": (sel_s1(pool, d) or []) for d in DEPTHS},
            **{f"S2_D{d}": (sel_s2(pool, d) or []) for d in DEPTHS},
            "S3": (sel_s3(pool)[0] or []),
            "S4": (sel_s4(pool)[0] or []),
        }
        stored = defaultdict(list)
        for t in result["tickets"]:
            if t["date"] == date and not t["strat"].startswith("RND"):
                stored[t["strat"]].append(t)
        for strat, legs in expect.items():
            want = sorted((l["home"], l["pick"]) for l in legs)
            got = sorted((l["home"], l["pick"]) for t in stored.get(strat, []) for l in t["legs"])
            if want != got:
                fails.append(f"{date} {strat}: 重放选腿 {want} ≠ 落盘 {got}")
    verdict = "PASS" if not fails else f"FAIL({len(fails)}项)——" + ";".join(fails[:3])
    if verbose:
        print(f"[审计③全链路重放·{len(sample)}天] {verdict}")
        for date in sample:
            n = sum(1 for t in result["tickets"]
                    if t["date"] == date and not t["strat"].startswith("RND"))
            print(f"  {date}: 落盘 {n} 票重放核对完成")
    return verdict


def audit():
    """--audit 入口：重读产物·三件套复算。"""
    result = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    groups = defaultdict(list)
    for t in result["tickets"]:
        if not t["strat"].startswith("RND"):
            groups[t["strat"]].append(t)
    n, stake, ret = (sum(len(v) for v in groups.values()),
                     sum(t["stake"] for v in groups.values() for t in v),
                     sum(t["ret"] for v in groups.values() for t in v))
    print(f"[审计①对账行] 票数{n} × 2元 = 投入{stake:.0f}元 · 票面回款和 = {ret:.2f}元 · 总ROI {ret / stake - 1:+.2%}")
    audit_recompute(result)
    audit_replay(result)
    print("审计完成·三件套全过才可信")


if __name__ == "__main__":
    if "--audit" in sys.argv:
        audit()
    else:
        run()
