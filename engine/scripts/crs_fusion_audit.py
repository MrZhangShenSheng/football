# engine/scripts/crs_fusion_audit.py
"""CRS 融合链路回测验收器（spec docs/2026-09-26-crs-fusion-redesign.html §7·SDD task-7）。

三节审计 → engine/cache/crs_fusion_audit.json：
  1. 187 唯一场 walk-forward 三方对照：纯模板(平滑) / 纯市场(power去水·兜底折叠
     公平口径) / 融合(对数意见池·生产同源链)，逐场 logloss + 配对差 bootstrap CI95
     （r/w=冻结值不做样本内拟合——walk-forward 语义=冻结参数前向评估，spec §7 弱点10
     纪律：CI 非均值比较）。join 样本 date+code 唯一化（I-2：207 行含 20 跨日重扫双记）。
  2. 族 top1 验收：top1 族族内实际命中率 vs 融合期望（Σ族内P），负 alpha 消除判定
     =实际≥期望×0.9；闸门放行场分层。
  3. 818 场 power 去水自检：存档场市场分布总进球分桶（0/1/2/3/4+）预测 vs 实际频率，
     尾部 4+ 球档专项。

计分口径（I-1 修复）：市场臂把 胜/平/负其他 兜底赔率折叠进代表键后 31 键 power 去水
——池外 actual（6:0/5:3 等）用代表键概率计分（市场明码报价过，不再记 ε=6.91 假惩罚）；
融合臂保持生产同源链（extract_mkt_dist 不折叠、融合代数 ε 兜底）——审计的是生产行为，
比较的是公平市场。
数据资产：engine/cache/crs_audit/v3_rows.json（207 join 行+612 赛果行，
与 data/02-results 主文件 612/612 一致性已核）+ engine/cache/score_odds/*.json。
输出 JSON 键卫生：(h,a) 元组键落盘前一律转 "h:a" 串（T6 移交约定）。
开发者 sszhang"""
import json
import math
import random
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # 直接 `python engine/scripts/crs_fusion_audit.py` 可跑

from common import ROOT
from crs_fusion import (ALPHA_LIDSTONE, CRS_POOL, EPS_MARKET, FAMILIES,
                        FAMILY_GATE_THRESHOLD, R_LOG_POOL, W_LAMBDA_SHRINK,
                        extract_mkt_dist, family_gate, family_scores, fuse_crs,
                        shrink_lambda)
from freq_band import (CRS_FUSION_MIN_ITEMS, T_AXIS_GUARD, _norm, _smooth_shifted,
                       _template_counts, _ttg_market_expect, global_pool,
                       league_base_rates, map_league, shifted_q, team_strength)

AUDIT_ROWS_PATH = ROOT / "engine" / "cache" / "crs_audit" / "v3_rows.json"
AUDIT_OUT_PATH = ROOT / "engine" / "cache" / "crs_fusion_audit.json"
ODDS_DIR = ROOT / "engine" / "cache" / "score_odds"

FAMILY_RATIO_GATE = 0.9        # 验收线2：族 top1 实际命中 ≥ 期望×0.9（spec §7 负 alpha 消除）
SMALL_SCORE_CAPTURE_MIN = 0.45  # 验收线3：放行场实际≤2球占比 ≥45%（spec §7，V3 in-sample 59% 打折）
SMALL_SCORE_TG_MAX = 2          # 小比分口径：总进球 ≤2
N_BOOT = 1000                   # 配对差 bootstrap 次数（brief 指定）
BOOT_SEED = 20260926            # 固定种子：审计结果可复现
CI_LO, CI_HI = 2.5, 97.5        # percentile CI95
TG_BUCKETS = ("0", "1", "2", "3", "4+")
TAIL_BUCKET = "4+"
REP_HOME, REP_DRAW, REP_AWAY = (4, 3), (4, 4), (3, 4)   # 胜其他/平其他/负其他 兜底代表键
CRS_FALLBACK_REPS = {"胜其他": REP_HOME, "平其他": REP_DRAW, "负其他": REP_AWAY}   # zh 兜底键 → 代表键（I-1 市场臂折叠）
_POOL_KEYSET = frozenset(CRS_POOL) - {REP_HOME, REP_DRAW, REP_AWAY}   # 28 数值挂牌比分（单一事实源 crs_fusion.CRS_POOL；代表键不在集合也无碍——4:3 等折叠目标即自身）
FROZEN_CFG = {"r": R_LOG_POOL, "w": W_LAMBDA_SHRINK, "alphaLidstone": ALPHA_LIDSTONE,
              "familyGateThreshold": FAMILY_GATE_THRESHOLD}   # 冻结值直评（不读 fusion_crs.json，防后续调参污染审计）


# ================= 核心：池键映射 / logloss / 三方分布 =================

def actual_pool_key(h: int, a: int) -> tuple:
    """实际比分 → CRS 池键：池外比分（6:0/5:3 等）按胜负方向折叠进兜底代表键
    （与 crs_fusion.smooth_template 计数折叠同口径——体彩"胜其他"语义）。"""
    if (h, a) in _POOL_KEYSET:
        return (h, a)
    return REP_HOME if h > a else (REP_DRAW if h == a else REP_AWAY)


def fold_crs_fallback(crs_odds: dict) -> dict:
    """体彩 CRS 兜底键（胜/平/负其他）→ 代表键折叠（I-1：市场臂公平计分）。

    折叠=分组语义：隐含概率相加（1/o 调和合并成等效赔率）。池结构保证代表键无
    数值挂牌（28 数值+3 兜底=31），合并分支仅防御性保留。无效项静默跳过。"""
    out = {k: v for k, v in crs_odds.items() if k not in CRS_FALLBACK_REPS}
    for zh, rep in CRS_FALLBACK_REPS.items():
        if zh not in crs_odds:
            continue
        try:
            w = 1.0 / float(crs_odds[zh])
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        rk = f"{rep[0]}:{rep[1]}"
        if rk in out:
            try:
                w += 1.0 / float(out[rk])
            except (TypeError, ValueError, ZeroDivisionError):
                continue
        if w > 0:
            out[rk] = 1.0 / w
    return out


def logloss(dist: dict, h: int, a: int, eps: float = EPS_MARKET) -> float:
    """分布对实际比分的 -ln P。真缺键（分布支撑集不含该键）→ ε 兜底（与 fuse_crs 同源）。
    市场臂自 I-1 折叠兜底后代表键有价——池外 actual 不再落 ε。"""
    p = dist.get(actual_pool_key(h, a))
    if p is None or p <= 0:
        p = eps
    return -math.log(p)


def three_dists(m: dict, freq_table: dict, form: dict, zh: dict,
                cfg: dict | None = None) -> dict:
    """单场三分布装配（fused_legs 同款链接线，分布本体全部保留供 logloss）：
    模板(λ平移+市场TTG收缩+Lidstone平滑) / 市场(power去水·兜底折叠公平口径) /
    融合(对数意见池·生产同源链)。市场臂与融合臂刻意不对称（I-1）：市场臂把兜底赔率
    折叠进代表键后 31 键去水=市场真实报价的公平计分；融合臂走生产 extract_mkt_dist
    （不折叠、融合代数 ε 兜底）——审计对象=生产行为，比较基线=公平市场。
    附 family_scores(带 members) + 闸门 + λ 护栏诊断（T6 移交项：收缩前后 T 轴越界）。
    模板链接线各环节零重写：_template_counts → shrink_lambda → shifted_q →
    _smooth_shifted → extract_mkt_dist → fuse_crs。开发者 sszhang"""
    cfg = cfg or FROZEN_CFG
    crs = m.get("crs") or {}
    pool = global_pool(freq_table)
    blob, lam = _template_counts(freq_table, m, zh, form, pool)
    n_tpl = blob.get("__n", 0)
    base = league_base_rates(blob)
    t_mean = base[0] + base[1]
    # λ None 归因诊断（不重写 lambdas 公式：strengths 齐+base 有效而 lam=None ⇒ T 轴护栏拦截）
    lg = map_league(m.get("league", ""))
    h_str = team_strength(form, _norm(zh.get(m.get("home", ""), "")), lg)
    a_str = team_strength(form, _norm(zh.get(m.get("away", ""), "")), lg)
    strength_ok = h_str is not None and a_str is not None and base[0] > 0 and base[1] > 0
    lam_sum_pre = (lam[0] + lam[1]) if lam else None
    shrunk = False
    if lam is not None:
        e_mkt = _ttg_market_expect(m.get("ttg"))
        if e_mkt is not None:
            lam_sum, shrunk = shrink_lambda(lam[0] + lam[1], e_mkt, w=cfg["w"])
            scale = lam_sum / (lam[0] + lam[1])
            lam = (lam[0] * scale, lam[1] * scale)
    lam_sum_post = (lam[0] + lam[1]) if lam else None
    q = _smooth_shifted(shifted_q(blob, lam), n_tpl, cfg["alphaLidstone"]) if n_tpl else {}
    mkt_ok = len(crs) >= CRS_FUSION_MIN_ITEMS
    p_mkt_fuse = extract_mkt_dist(crs) if mkt_ok else {}          # 融合链输入（生产同源·不折叠）
    p_mkt = extract_mkt_dist(fold_crs_fallback(crs)) if mkt_ok else {}   # 市场臂（公平计分·I-1）
    fused = fuse_crs(q, p_mkt_fuse, r=cfg["r"]) if p_mkt_fuse else q
    fams = [{"family": f["family"], "prob": f["prob"], "members": FAMILIES[f["family"]],
             "top1": f["top1"], "top2": f["top2"]} for f in family_scores(fused)]
    ok, mx = family_gate(fused, cfg["familyGateThreshold"])
    guard = {
        "tMean": round(t_mean, 4),
        "lamSumPre": round(lam_sum_pre, 4) if lam_sum_pre else None,
        "lamSumPost": round(lam_sum_post, 4) if lam_sum_post else None,
        "strengthMissing": lam is None and not strength_ok,
        "guardBlockedPre": lam is None and strength_ok,       # lambdas 内 T 轴护栏拦截
        "postShrinkViolation": bool(
            shrunk and t_mean > 0
            and abs(lam_sum_post - t_mean) / t_mean > T_AXIS_GUARD),
    }
    return {"tpl": q, "mkt": p_mkt, "fused": fused, "families": fams,
            "gate": {"pass": ok, "maxProb": mx}, "lam": lam, "shrunk": shrunk,
            "guard": guard, "nTpl": n_tpl}


# ================= 核心：族命中统计 / 分桶校准 / bootstrap =================

def family_top1_stats(rows: list) -> dict:
    """rows=[{top1:{family,prob,members}, actual:(h,a)}] → top1 族实际命中 vs 期望。
    负 alpha 消除判定（spec §7）：hitRate ≥ expectedRate×0.9。"""
    n = len(rows)
    if not n:
        return {"n": 0, "hits": 0, "hitRate": 0.0, "expectedRate": 0.0,
                "ratio": 0.0, "pass": False}
    hits = sum(1 for r in rows if r["actual"] in set(r["top1"]["members"]))
    hit_rate = hits / n
    exp_rate = sum(r["top1"]["prob"] for r in rows) / n
    ratio = hit_rate / exp_rate if exp_rate > 0 else 0.0
    return {"n": n, "hits": hits, "hitRate": hit_rate,
            "expectedRate": exp_rate, "ratio": ratio,
            "pass": ratio >= FAMILY_RATIO_GATE}


def tg_buckets(dist: dict) -> dict:
    """比分分布（元组键）→ 总进球五档 {0,1,2,3,4+}（4+=4球及以上并桶）。"""
    out = {b: 0.0 for b in TG_BUCKETS}
    for (h, a), p in dist.items():
        out[TAIL_BUCKET if h + a >= 4 else str(h + a)] += p
    return out


def _tg_bucket(tg: int) -> str:
    return TAIL_BUCKET if tg >= 4 else str(tg)


def bucket_calibration(entries: list) -> dict:
    """entries=[(五档预测dict, 实际tg)] → 逐档 predMean vs actualFreq + 4+ 尾部专项。"""
    out = {"n": len(entries), "buckets": {}}
    for b in TG_BUCKETS:
        out["buckets"][b] = {
            "predMean": round(sum(e[0][b] for e in entries) / len(entries), 4) if entries else 0.0,
            "actualFreq": round(sum(1.0 for e in entries if _tg_bucket(e[1]) == b) / len(entries), 4) if entries else 0.0,
        }
    tail = out["buckets"][TAIL_BUCKET]
    out["tail4plus"] = {"predMean": tail["predMean"], "actualShare": tail["actualFreq"]}
    return out


def paired_bootstrap_ci(diffs: list, n_boot: int = N_BOOT, seed: int = BOOT_SEED):
    """配对差 bootstrap CI95（percentile）。返回 (mean, lo95, hi95)，固定种子可复现。"""
    n = len(diffs)
    if not n:
        return (0.0, 0.0, 0.0)
    rng = random.Random(seed)
    means = sorted(
        sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot))
    lo = means[int(CI_LO / 100 * n_boot)]
    hi = means[min(int(CI_HI / 100 * n_boot), n_boot - 1)]
    return (sum(diffs) / n, lo, hi)


# ================= 数据装载 =================

def load_result_rows(path: Path = AUDIT_ROWS_PATH) -> list:
    """crs_audit/v3_rows.json → 赛果行（612 行；与 02-results 主文件 612/612 一致已核）。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_archive_entries(odds_dir: Path = ODDS_DIR) -> tuple:
    """score_odds 存档 → (原始条目列表 818 口径[跨日重扫同场保留], 唯一场索引[末次扫档胜出])。"""
    entries, uniq = [], {}
    for p in sorted(Path(odds_dir).glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        for day in data.get("matchDays", []):
            bd = day.get("businessDate")
            for m in day.get("matches", []):
                key = (bd, m.get("matchNumStr"))
                entries.append({"businessDate": bd, "match": m})
                uniq[key] = m                     # 文件名序=时间序，末次=临场最近一次扫档
    return entries, uniq


def dedup_by_match(rows: list) -> tuple:
    """(date,code) 唯一化（I-2：v3_rows 跨日重扫双记，如 2026-09-06×18）。
    返回 (唯一行, 丢弃重复行数)；保留首现——重复对结果字段 rh/ra/tg 逐位相同，
    预测侧字段（fused/pick）随扫档日漂移但不进审计统计。"""
    seen, uniq, dupes = set(), [], 0
    for r in rows:
        key = (r["date"], r["code"])
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        uniq.append(r)
    return uniq, dupes


def build_fam_rows(per: list) -> list:
    """per-match 摘要 → 族统计行。top1=None（空模板场）不入族统计；gatePass 随行携带，
    分层按行自身字段过滤（M-1 修复：替代 zip(per)——滤后子列与 per 错位、闸门分层配错场）。"""
    return [{"top1": p["top1"],
             "actual": tuple(int(x) for x in p["actual"].split(":")),
             "gatePass": p["gatePass"]}
            for p in per if p["top1"]]


# ================= 三节审计管道 =================

def run_walk_forward(rows: list, mkt_idx: dict, freq_table: dict, form: dict,
                     zh: dict) -> dict:
    """节1+2+λ护栏：唯一 join 场（I-2：date+code 去重）三方 logloss 配对差 CI95 +
    族 top1 + 小比分捕获。市场臂=公平口径（I-1：兜底折叠计分）。"""
    raw_join = [r for r in rows if r.get("mktTg") is not None]
    join_rows, n_dupes = dedup_by_match(raw_join)
    per = []
    for r in sorted(join_rows, key=lambda x: (x["date"], x["code"])):
        d = three_dists(mkt_idx[(r["date"], r["code"])], freq_table, form, zh)
        per.append({
            "date": r["date"], "code": r["code"], "league": r.get("league", ""),
            "actual": f'{r["rh"]}:{r["ra"]}', "tg": r["tg"],
            "ll": {arm: round(logloss(d[arm], r["rh"], r["ra"]), 4)
                   for arm in ("tpl", "mkt", "fused")},
            "top1": d["families"][0] if d["families"] else None,
            "gatePass": d["gate"]["pass"], "shrunk": d["shrunk"], "guard": d["guard"],
        })
    ll_tpl = [p["ll"]["tpl"] for p in per]
    ll_mkt = [p["ll"]["mkt"] for p in per]
    ll_fused = [p["ll"]["fused"] for p in per]
    mean = lambda xs: round(sum(xs) / len(xs), 4)   # noqa: E731（审计内局部惯用）
    d_tpl = [t - f for t, f in zip(ll_tpl, ll_fused)]        # >0 = 融合更优
    d_mkt = [t - f for t, f in zip(ll_mkt, ll_fused)]        # 市场臂=公平口径（I-1）
    m_dt, lo_dt, hi_dt = paired_bootstrap_ci(d_tpl)
    m_dm, lo_dm, hi_dm = paired_bootstrap_ci(d_mkt)
    fam_rows = build_fam_rows(per)                 # top1=None 场不入族统计；gatePass 随行（M-1）
    gate_pass_rows = [fr for fr in fam_rows if fr["gatePass"]]
    gate_fail_rows = [fr for fr in fam_rows if not fr["gatePass"]]
    small_pass = [p for p in per if p["gatePass"]]
    capture = (sum(1 for p in small_pass if p["tg"] <= SMALL_SCORE_TG_MAX)
               / len(small_pass)) if small_pass else 0.0
    guards = [p["guard"] for p in per]
    return {
        "n": len(per),
        "n_unique": len(join_rows),
        "n_dupes": n_dupes,
        "n_raw_join": len(raw_join),
        "logloss": {"tpl": mean(ll_tpl), "mkt": mean(ll_mkt), "fused": mean(ll_fused)},
        "pairedDiff": {
            "fusedVsTpl": {"mean": round(m_dt, 4), "ci95": [round(lo_dt, 4), round(hi_dt, 4)],
                           "ciExcludesZero": lo_dt > 0},
            "fusedVsMkt": {"mean": round(m_dm, 4), "ci95": [round(lo_dm, 4), round(hi_dm, 4)],
                           "ciExcludesZero": lo_dm > 0,
                           "caliber": "公平口径：市场臂兜底折叠计分（I-1）。"
                                      "CI 跨 0=统计不可分，不作留产判据"},
        },
        "gate1": {  # 验收线1（留产决策线·spec §7 纯 CI 纪律）：融合 < 模板 CI95 不跨 0。
            # 融合 vs 市场（公平口径）CI 跨 0 = 统计不可分、点估计融合略劣——如实报数，
            # 不作判据（I-1 修复：删除"点估计更优"假象条款）
            "fusedBeatsTpl": m_dt > 0 and lo_dt > 0,
            "fusedVsMktFair": {"mean": round(m_dm, 4), "ci95": [round(lo_dm, 4), round(hi_dm, 4)],
                               "ciExcludesZero": lo_dm > 0,
                               "statisticallyInseparable": lo_dm <= 0,
                               "pointEstimateFavors": "mkt" if m_dm < 0 else "fused"},
            "pass": (m_dt > 0 and lo_dt > 0),
        },
        "familyTop1": {"all": family_top1_stats(fam_rows),
                       "gatePass": family_top1_stats(gate_pass_rows),
                       "gateFail": family_top1_stats(gate_fail_rows)},
        "smallScoreCapture": {"gatePassN": len(small_pass), "captureRate": round(capture, 4),
                              "pass": capture >= SMALL_SCORE_CAPTURE_MIN},
        "lambdaGuard": {
            "nLamActive": sum(1 for g in guards if g["lamSumPost"] is not None),
            "nStrengthMissing": sum(1 for g in guards if g["strengthMissing"]),
            "nGuardBlockedPre": sum(1 for g in guards if g["guardBlockedPre"]),
            "nPostShrinkViolation": sum(1 for g in guards if g["postShrinkViolation"]),
            "note": "pre 收缩越界=0（lambdas 内护栏构造性保证，guardBlockedPre 即拦截层）",
        },
        "perMatch": per,
    }


def run_power_selfcheck(entries: list, result_rows: list) -> dict:
    """节3：818 存档场 power 去水分布总进球分桶校准（有赛果才进校准；尾部 4+ 专项）。"""
    result_tg = {(r["date"], r["code"]): r["tg"] for r in result_rows}
    n_mkt = 0
    cal, cal_uniq, seen = [], [], set()
    for e in entries:
        dist = extract_mkt_dist(e["match"].get("crs") or {})
        if not dist:
            continue
        n_mkt += 1
        key = (e["businessDate"], e["match"].get("matchNumStr"))
        if key in result_tg:
            cal.append((tg_buckets(dist), result_tg[key]))
            if key not in seen:                 # 去重口径：唯一场（跨日重扫只计首现）
                seen.add(key)
                cal_uniq.append((tg_buckets(dist), result_tg[key]))
    return {"nMkt": n_mkt, "nCalibrated": len(cal), "nCalibratedUnique": len(cal_uniq),
            "rawEntries": bucket_calibration(cal), "uniqueMatches": bucket_calibration(cal_uniq)}


# ================= 主入口 =================

def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def main() -> int:
    from freq_band import _zh_alias_map, build_team_form
    from score_ev import build_freq_table

    rows = load_result_rows()
    entries, mkt_idx = load_archive_entries()
    print(f"[crs-fusion-audit] 存档原始条目 {len(entries)} · 唯一场 {len(mkt_idx)} · "
          f"赛果行 {len(rows)}（join 样本 {sum(1 for r in rows if r.get('mktTg') is not None)}）")
    print("[crs-fusion-audit] 构建模板/近况（生产同源：fd CSV + 本地联赛库）…")
    freq_table = build_freq_table()
    form = build_team_form()
    zh = _zh_alias_map()

    wf = run_walk_forward(rows, mkt_idx, freq_table, form, zh)
    power = run_power_selfcheck(entries, rows)

    out = {
        "generatedAt": str(date.today()),
        "params": {**FROZEN_CFG, "frozenNote": "r/w/α=冻结值前向评估（不做样本内拟合）",
                   "nBoot": N_BOOT, "bootSeed": BOOT_SEED,
                   "familyRatioGate": FAMILY_RATIO_GATE,
                   "smallScoreCaptureMin": SMALL_SCORE_CAPTURE_MIN,
                   "marketArmCaliber": "公平口径：胜/平/负其他兜底赔率折叠进代表键"
                                       "(4,3)/(4,4)/(3,4)后31键power去水（I-1修复，池外actual有价计分）；"
                                       "融合臂保持生产链（不折叠、融合代数ε兜底）",
                   "sampleDedup": "walk-forward join样本 date+code 唯一化（I-2修复，"
                                  "跨日重扫双记丢弃计数见 walkForward.n_dupes）"},
        "walkForward": {k: v for k, v in wf.items() if k != "perMatch"},
        "perMatch": wf["perMatch"],
        "powerSelfCheck": power,
        "acceptance": {
            "line1Logloss": wf["gate1"]["pass"],
            "line2FamilyTop1": wf["familyTop1"]["all"]["pass"],
            "line3SmallScore": wf["smallScoreCapture"]["pass"],
        },
    }
    AUDIT_OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n",
                              encoding="utf-8")

    ll, pd = wf["logloss"], wf["pairedDiff"]
    g1, g2, g3 = wf["gate1"], wf["familyTop1"]["all"], wf["smallScoreCapture"]
    lg = wf["lambdaGuard"]
    tail = power["rawEntries"]["tail4plus"]
    fair = g1["fusedVsMktFair"]
    fair_pt = "融合略劣" if fair["pointEstimateFavors"] == "mkt" else "融合略优"
    print(f"[crs-fusion-audit] → {AUDIT_OUT_PATH}")
    print(f"== {wf['n_unique']} 唯一场 walk-forward logloss（{wf['n_raw_join']} join 行去 "
          f"{wf['n_dupes']} 双记·配对差 CI95·bootstrap{n_boot_label()}）==")
    print(f"  纯模板 {ll['tpl']} · 纯市场(公平口径) {ll['mkt']} · 融合 {ll['fused']}")
    print(f"  融合−模板 Δmean {pd['fusedVsTpl']['mean']} CI95 {pd['fusedVsTpl']['ci95']}"
          f"（不跨0: {pd['fusedVsTpl']['ciExcludesZero']}）")
    print(f"  融合−市场(公平口径) Δmean {fair['mean']} CI95 {fair['ci95']}"
          f"→ {'统计不可分' if fair['statisticallyInseparable'] else 'CI不跨0'}"
          f"（点估计{fair_pt}·非留产判据）")
    print(f"  验收线1 融合胜模板(CI纪律·留产决策线): {'过' if g1['pass'] else '不过'}"
          f"（胜模板CI={g1['fusedBeatsTpl']}）")
    print(f"  验收线2 族top1: 实际 {_fmt_pct(g2['hitRate'])} vs 期望 {_fmt_pct(g2['expectedRate'])}"
          f"×0.9={_fmt_pct(g2['expectedRate'] * FAMILY_RATIO_GATE)} → {'过' if g2['pass'] else '不过'}"
          f"（放行层 n={wf['familyTop1']['gatePass']['n']}"
          f" 实际 {_fmt_pct(wf['familyTop1']['gatePass']['hitRate'])}"
          f"/期望 {_fmt_pct(wf['familyTop1']['gatePass']['expectedRate'])}）")
    print(f"  验收线3 小比分捕获(放行{_plural(g3['gatePassN'])}): "
          f"{_fmt_pct(g3['captureRate'])} ≥45% → {'过' if g3['pass'] else '不过'}")
    print(f"  λ护栏(T6移交): 活跃 {lg['nLamActive']} · 近况缺 {lg['nStrengthMissing']}"
          f" · 收缩前护栏拦截 {lg['nGuardBlockedPre']} · 收缩后越界 {lg['nPostShrinkViolation']}")
    print(f"== power 去水自检（{power['nMkt']} 存档场 · 校准 {power['nCalibrated']} 条/"
          f"{power['nCalibratedUnique']} 唯一场）==")
    for b in TG_BUCKETS:
        row = power["rawEntries"]["buckets"][b]
        print(f"  {b:>2}球: 预测均值 {_fmt_pct(row['predMean'])} vs 实际 {_fmt_pct(row['actualFreq'])}")
    print(f"  尾部4+专项: 预测均值 {_fmt_pct(tail['predMean'])} vs 实际占比 {_fmt_pct(tail['actualShare'])}")
    verdict = all(out["acceptance"].values())
    print(f"[crs-fusion-audit] 总判定: {'三线全过——融合链留在生产' if verdict else 'STOP——有验收线不过，带数据回报（不静默降标准）'}")
    return 0 if verdict else 1


def n_boot_label() -> str:
    return f"×{N_BOOT}"


def _plural(n: int) -> str:
    return f"{n}场"


if __name__ == "__main__":
    sys.exit(main())
