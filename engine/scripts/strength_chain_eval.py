# engine/scripts/strength_chain_eval.py
# -*- coding: utf-8 -*-
"""门1评估器：hist_odds 盲测场 walk-forward（as-of=赛日−2天）→ 1a校准/1b命中/1c log-loss + V4三基线强制。
只读预注册舱：判据数字全部从 engine/cache/strength_chain/preregistration.json 解析（改舱=新实验）。
DC泄漏修复（2026-10-05 Task12 裁定②）：评估器 dc_rolling=True——dc_att/dc_def 用 as-of 可见近10场
滚动代理，不读当前全历史 DC 缓存；rho/homeAdv 仍取当前联赛标量（残余泄漏·notes 如实标注）。
产出 data/04-summaries/strength-chain-gate1.json。开发者 sszhang"""
from __future__ import annotations
import json, math, os, random, re, sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import strength_loaders as sl
import paper_strength as ps
import lambda_bridge as lb
import score_matrix as sm
from strength_chain_run import LEAGUES, ENV_GOALS
from common import ROOT

PREREG_DEFAULT = ROOT / "engine" / "cache" / "strength_chain" / "preregistration.json"
SUMMARY_OUT = ROOT / "data" / "04-summaries" / "strength-chain-gate1.json"
HIST_DIR = ROOT / "engine" / "cache" / "hist_odds"
AS_OF_LAG_DAYS = 2                    # 裁定①：as-of = 赛日 − 2 天（再叠 team_state_on lag=2 = cutoff 赛日−4天）
SEED = 7                              # V5 确定性：bootstrap/随机基线全部定种
N_PERM = 200                          # 裁定④：randomShuffle 200 次置换
N_BOOT = 1000                         # 1c CI bootstrap 次数

POOL_OTHER = {"other_h": "s1sh", "other_d": "s1sd", "other_a": "s1sa"}   # 体彩池其他键 → 矩阵39键


class MissingBaselineError(RuntimeError):
    """V4：三基线（randomShuffle/market/oldChain）缺一即拒报。"""


# ---- 预注册判据解析（只读舱·数字不硬编码） ----

def _pp_margins(txt: str) -> list[float]:
    """'… - 1.5pp 且 … - 2pp' → [0.015, 0.02]（全部 Npp 数字·按出现序）。"""
    return [float(x) / 100 for x in re.findall(r"([0-9.]+)\s*pp", str(txt))]

def _parse_1b_margin(prereg_path: Path) -> float:
    """1b CRS 门槛：prereg gate1.criteria.1b_hitFloor 首个 pp 数（'market - 1.5pp' → 0.015）。"""
    txt = json.loads(Path(prereg_path).read_text(encoding="utf-8"))["gate1"]["criteria"]["1b_hitFloor"]
    ms = _pp_margins(txt)
    if not ms:
        raise ValueError(f"prereg 1b_hitFloor 解析不出 pp 门槛: {txt!r}")
    return ms[0]

def _parse_criteria(prereg: dict) -> dict:
    """三判据门槛全量解析（evaluate 用；解析失败=舱损坏=拒绝出报告）。"""
    crit = prereg["gate1"]["criteria"]
    m1a, min_n = _pp_margins(crit["1a_calibration"])[0], None
    mn = re.search(r"样本\s*>=\s*([0-9]+)", crit["1a_calibration"])
    min_n = int(mn.group(1)) if mn else 50
    ms1b = _pp_margins(crit["1b_hitFloor"])
    if len(ms1b) < 2:
        raise ValueError(f"prereg 1b_hitFloor 须含 CRS 与 TTG 两个 pp 门槛: {crit['1b_hitFloor']!r}")
    mc = re.search(r"\+\s*([0-9.]+)", crit["1c_logloss"])
    if mc is None:
        raise ValueError(f"prereg 1c_logloss 解析不出 '+ N' 门槛: {crit['1c_logloss']!r}")
    return {"1a_margin": m1a, "1a_min_bucket_n": min_n,
            "1b_margin": ms1b[0], "1b_ttg_margin": ms1b[1], "1c_margin": float(mc.group(1))}


# ---- V4 三基线强制 ----

def _gate1_verdict(baselines: dict) -> dict:
    for k in ("randomShuffle", "market", "oldChain"):
        if baselines.get(k) is None:
            raise MissingBaselineError(f"V4疫苗：基线 {k} 缺失，拒绝生成报告")
    return {"baselines": baselines, "rule": "优势须三杀+bootstrap CI 不含 0"}


# ---- 键映射：体彩池键/赛果 ↔ 矩阵39键 ----

def _hist_key_to_matrix(k: str) -> str | None:
    if k in POOL_OTHER:
        return POOL_OTHER[k]
    h, _, a = str(k).partition(":")
    if h.isdigit() and a.isdigit() and len(h) <= 1 and len(a) <= 1:
        return f"s{int(h):02d}s{int(a):02d}"
    return None

def _score_facts(score: str, priced_keys: set) -> tuple[str, str, str] | None:
    """赛果 'h:a' → (矩阵键·未挂牌比分落三其他, tg档 s{min(h+a,8)}, HAD向 'h'/'d'/'a')；解析失败 None。"""
    try:
        h_s, a_s = str(score).split(":")
        h, a = int(h_s), int(a_s)
    except (ValueError, AttributeError):
        return None
    side = "h" if h > a else ("d" if h == a else "a")
    key = f"s{h:02d}s{a:02d}" if (h <= 5 and a <= 5 and f"{h}:{a}" in priced_keys) else POOL_OTHER[f"other_{side}"]
    return key, f"s{min(h + a, 8)}", side

def _market_crs_top2(crs: dict) -> tuple[str | None, str | None]:
    """crs 池按赔率升序 → (最低赔=市场top1, 次低赔=oldChain代理)。确定性排序 (odds, key)。"""
    valid = [(k, v) for k, v in (crs or {}).items() if isinstance(v, (int, float)) and v > 0]
    if not valid:
        return None, None
    ranked = sorted(valid, key=lambda kv: (kv[1], kv[0]))
    top1 = _hist_key_to_matrix(ranked[0][0])
    top2 = _hist_key_to_matrix(ranked[1][0]) if len(ranked) > 1 else None
    return top1, top2

def _market_ttg_pick(crs: dict) -> str | None:
    """crs 池比例去水 → 聚合 TTG 档 argmax（市场TTG top1·其他三键归 s8 开桶）。"""
    valid = [(k, v) for k, v in (crs or {}).items() if isinstance(v, (int, float)) and v > 0]
    if not valid:
        return None
    z = sum(1.0 / v for _, v in valid)
    ttg = {f"s{i}": 0.0 for i in range(9)}
    for k, v in valid:
        mk = _hist_key_to_matrix(k)
        if mk is None:
            continue
        if mk in POOL_OTHER.values():
            ttg["s8"] += (1.0 / v) / z
        else:
            ttg[f"s{min(int(mk[1:3]) + int(mk[4:6]), 8)}"] += (1.0 / v) / z
    return max(ttg, key=lambda k: ttg[k])

def _devig_had(had: dict) -> dict | None:
    """HAD 三向比例去水 p_i=(1/o_i)/Σ(1/o_j)（1c 市场基线·裁定④）。"""
    if not had or not all(isinstance(had.get(k), (int, float)) and had[k] > 0 for k in ("h", "d", "a")):
        return None
    inv = {k: 1.0 / had[k] for k in ("h", "d", "a")}
    z = sum(inv.values())
    return {k: v / z for k, v in inv.items()}


# ---- 链逐场预测（与 strength_chain_run.run_day 同构·as-of 语义） ----

def _predict_match(hid: str, aid: str, as_of: date, ctx: dict, memo: dict, beta: float) -> dict | None:
    """裁定①全链：team_state_on(dc_rolling) → devig_fame → hfa → compare → λ → dc_matrix → HAD/TTG。
    memo=(team, as_of) 级快照缓存（裁定③：同日多场共享·免全联赛重扫）。任队 no_league → None。"""
    states = {}
    for tid in (hid, aid):
        k = (tid, as_of)
        if k not in memo:
            st = sl.team_state_on(tid, as_of, ctx, dc_rolling=True)
            att, df = ps.devig_fame(st)
            memo[k] = {"state": st, "att": att, "def": df}
        states[tid] = memo[k]
    lg = states[hid]["state"].get("league")
    if lg is None or states[aid]["state"].get("league") is None:
        return None
    env = ENV_GOALS.get(lg, ENV_GOALS["default"])                       # 均匀T=2.7（notes 缺口声明）
    home_adv = float((ctx["dc"].get(lg) or {}).get("homeAdv", 0.30))
    hfa_v = ps.hfa_value(home_adv, states[hid]["state"]["n_xg"], None)
    cmp_out = ps.compare(states[hid]["att"], states[hid]["def"],
                         states[aid]["att"], states[aid]["def"], hfa_v, env)
    lam = lb.lambdas(cmp_out, inj_h=None, inj_a=None, beta=beta)        # 伤停轨 forward 未积累=中性
    rho = float((ctx["dc"].get(lg) or {}).get("rho", -0.05))
    matrix = sm.dc_matrix(lam["lam_h"], lam["lam_a"], rho)
    flags = sorted(set(states[hid]["state"]["flags"]) | set(states[aid]["state"]["flags"]))
    return {"league": lg, "matrix": matrix, "had": sm.had_from(matrix), "ttg": sm.ttg_from(matrix),
            "lam": (lam["lam_h"], lam["lam_a"]), "flags": flags}


# ---- 聚合工具 ----

def _mean(xs: list) -> float:
    return sum(xs) / len(xs) if xs else 0.0

def _calibration(pairs: list[tuple[float, int]], min_bucket_n: int) -> dict:
    """十分位固定桶（0.0-0.1 … 0.9-1.0）：predicted vs actual 偏差；n>=min_bucket_n 的桶才判（prereg）。"""
    buckets = []
    for i in range(10):
        lo, hi = i / 10, (i + 1) / 10
        sel = [p for p in pairs if (lo <= p[0] < hi) or (i == 9 and p[0] == 1.0)]
        if not sel:
            continue
        pred, act = _mean([p[0] for p in sel]), _mean([p[1] for p in sel])
        buckets.append({"bucket": f"{lo:.1f}-{hi:.1f}", "n": len(sel), "pred": round(pred, 4),
                        "act": round(act, 4), "dev": round(pred - act, 4), "judged": len(sel) >= min_bucket_n})
    judged = [abs(b["dev"]) for b in buckets if b["judged"]]
    return {"buckets": buckets, "worstDev": round(max(judged), 4) if judged else None,
            "judgedBuckets": len(judged), "minBucketN": min_bucket_n}

def _boot_ci_diff(diffs: list[float], seed: int = SEED, n_boot: int = N_BOOT) -> list[float]:
    """逐场配对差 (model−market) 的 bootstrap 均值 CI（定种·V5确定性）。"""
    rng, n = random.Random(seed), len(diffs)
    if n == 0:
        return [0.0, 0.0]
    means = sorted(_mean([diffs[rng.randrange(n)] for _ in range(n)]) for _ in range(n_boot))
    return [round(means[int(0.025 * len(means))], 6), round(means[min(int(0.975 * len(means)), len(means) - 1)], 6)]

def _random_shuffle_baseline(picks: list[str], actuals: list[str], seed: int = SEED,
                             n_perm: int = N_PERM) -> dict:
    """裁定④：模型自己的 pick × 他场随机赛果（置换分布均值=纯运气底线）。"""
    rng, n = random.Random(seed), len(actuals)
    if n == 0:
        return {"hit": 0.0, "n_perm": n_perm, "seed": seed}
    vals = []
    for _ in range(n_perm):
        sh = actuals[:]
        rng.shuffle(sh)
        vals.append(sum(1 for p, a in zip(picks, sh) if p == a) / n)
    return {"hit": round(sum(vals) / len(vals), 6), "n_perm": n_perm, "seed": seed}


# ---- 回测主循环（完整实现·非骨架） ----

def _eval_rows(rows: list, ctx: dict | None, tmp_dir: Path | None = None, *, beta: float = 0.05,
               min_bucket_n: int = 50, zh2id: dict | None = None, memo: dict | None = None) -> dict:
    """盲测行 → 逐场 as-of 链预测 → {calibration, hit, logloss, baselines, perLeague, skipped}。
    ctx=None（冒烟）：链不可算 → 行按原因跳过，四节结构仍完整产出。"""
    memo = memo if memo is not None else {}
    zh2id = zh2id if zh2id is not None else sl.zh_to_id()
    recs, skipped = [], {}
    for m in sorted(rows, key=lambda r: (str(r.get("date", "")), str(r.get("matchId", "")))):
        def _skip(reason: str):
            skipped[reason] = skipped.get(reason, 0) + 1
        try:
            as_of = date.fromisoformat(str(m.get("date", ""))[:10]) - timedelta(days=AS_OF_LAG_DAYS)
        except ValueError:
            _skip("bad_date"); continue
        crs = m.get("crs") or {}
        priced = {k for k, v in crs.items() if isinstance(v, (int, float)) and v > 0}
        facts = _score_facts(m.get("score", ""), priced)
        if ctx is None:
            _skip("no_ctx"); continue
        hid, aid = zh2id.get(m.get("home")), zh2id.get(m.get("away"))
        if not hid or not aid:
            _skip("no_zh_mapping"); continue
        if facts is None:
            _skip("bad_score"); continue
        pred = _predict_match(hid, aid, as_of, ctx, memo, beta)
        if pred is None:
            _skip("no_league_team"); continue
        actual_key, actual_tg, side = facts
        mkt_top1, mkt_top2 = _market_crs_top2(crs)
        model_pick = max(pred["matrix"], key=lambda k: pred["matrix"][k])       # 矩阵固定键序·V5确定
        model_ttg_pick = max(pred["ttg"], key=lambda k: pred["ttg"][k])
        zs = sum(pred["had"].values()) or 1.0
        recs.append({"league": pred["league"], "model_pick": model_pick, "model_ttg_pick": model_ttg_pick,
                     "actual_key": actual_key, "actual_tg": actual_tg, "side": side,
                     "mkt_top1": mkt_top1, "mkt_top2": mkt_top2, "mkt_ttg": _market_ttg_pick(crs),
                     "had_model": {k: v / zs for k, v in pred["had"].items()},
                     "had_mkt": _devig_had(m.get("had") or {}),
                     "p_had_top": max(v / zs for v in pred["had"].values()),
                     "p_ttg_top": max(pred["ttg"].values()),
                     "had_hit": 1 if side == max(pred["had"], key=lambda k: pred["had"][k]) else 0,
                     "ttg_hit": 1 if model_ttg_pick == actual_tg else 0,
                     "flags": pred["flags"]})
    # ---- hit：模型/市场 CRS+TTG 四命中率（共同子集=模型可算且市场 crs 池在·同场对比才公平） ----
    hit_set = [r for r in recs if r["mkt_top1"]]
    n_hit = len(hit_set)
    hit = {"n": n_hit,
           "modelTop1": round(_mean([1 if r["model_pick"] == r["actual_key"] else 0 for r in hit_set]), 4),
           "marketTop1": round(_mean([1 if r["mkt_top1"] == r["actual_key"] else 0 for r in hit_set]), 4),
           "ttgModel": round(_mean([r["ttg_hit"] for r in hit_set if r["mkt_ttg"]]), 4),
           "ttgMarket": round(_mean([1 if r["mkt_ttg"] == r["actual_tg"] else 0
                                     for r in hit_set if r["mkt_ttg"]]), 4)}
    # ---- logloss：HAD 三向（市场=体彩 had 比例去水·配对差 bootstrap CI） ----
    ll_set = [r for r in recs if r["had_mkt"]]
    ll_m = [-math.log(max(r["had_model"][r["side"]], 1e-12)) for r in ll_set]
    ll_k = [-math.log(max(r["had_mkt"][r["side"]], 1e-12)) for r in ll_set]
    diffs = [a - b for a, b in zip(ll_m, ll_k)]
    logloss = {"n": len(ll_set), "model": round(_mean(ll_m), 6), "market": round(_mean(ll_k), 6),
               "diff": round(_mean(diffs), 6), "ci": _boot_ci_diff(diffs)}
    # ---- 1a 校准：HAD 三向 max-prob 十分位（主口径）+ TTG top1（次口径·桶样本不足如实标注） ----
    calibration = _calibration([(r["p_had_top"], r["had_hit"]) for r in recs], min_bucket_n)
    calibration["ttg"] = _calibration([(r["p_ttg_top"], r["ttg_hit"]) for r in recs], min_bucket_n)
    # ---- 三基线（V4强制·缺一 _gate1_verdict 拒报） ----
    oc_set = [r for r in hit_set if r["mkt_top2"]]
    baselines = {
        "randomShuffle": _random_shuffle_baseline([r["model_pick"] for r in hit_set],
                                                  [r["actual_key"] for r in hit_set]),
        "market": {"hit": hit["marketTop1"], "note": "crs池最低赔率选项"},
        "oldChain": {"hit": round(_mean([1 if r["mkt_top2"] == r["actual_key"] else 0 for r in oc_set]), 4),
                     "n": len(oc_set),
                     "proxy": "crs池次低赔率选项（开盘）——旧链crs_fusion逐场历史票不可得，见notes"}}
    # ---- perLeague 分解（均匀T=2.7 缺口的分解可见性·裁定⑥） ----
    per_league = {}
    for r in hit_set:
        e = per_league.setdefault(r["league"], {"n": 0, "modelHit": 0, "marketHit": 0})
        e["n"] += 1
        e["modelHit"] += 1 if r["model_pick"] == r["actual_key"] else 0
        e["marketHit"] += 1 if r["mkt_top1"] == r["actual_key"] else 0
    per_league = {lg: {"n": e["n"], "modelTop1": round(e["modelHit"] / e["n"], 4),
                       "marketTop1": round(e["marketHit"] / e["n"], 4)}
                  for lg, e in sorted(per_league.items(), key=lambda kv: -kv[1]["n"])}
    return {"calibration": calibration, "hit": hit, "logloss": logloss, "baselines": baselines,
            "perLeague": per_league, "n": len(recs), "nFull": len(rows), "skipped": skipped,
            "flagsDist": {f: sum(1 for r in recs if f in r["flags"])
                          for f in sorted({fl for r in recs for fl in r["flags"]})}}


def evaluate(prereg_path: Path = PREREG_DEFAULT) -> dict:
    """生产入口：真实 hist_odds 全量 → 门1报告落盘 data/04-summaries/strength-chain-gate1.json。"""
    prereg = json.loads(Path(prereg_path).read_text(encoding="utf-8"))
    crit = _parse_criteria(prereg)
    ctx = sl.build_ctx(LEAGUES)
    hist: list[dict] = []
    for f in sorted(HIST_DIR.glob("crs_hist_*.json")):
        hist += json.loads(f.read_text(encoding="utf-8"))["matches"]
    t0 = datetime.now()
    out = _eval_rows(hist, ctx, min_bucket_n=crit["1a_min_bucket_n"])
    out["generatedAt"] = datetime.now().isoformat(timespec="seconds")
    out["runtimeSec"] = round((datetime.now() - t0).total_seconds(), 1)
    out["gate1"] = _gate1_verdict(out["baselines"])
    out["gate1"]["1a"] = {"criterion": prereg["gate1"]["criteria"]["1a_calibration"],
                          "worstDev": out["calibration"]["worstDev"], "margin": crit["1a_margin"],
                          "judgedBuckets": out["calibration"]["judgedBuckets"],
                          "ttgWorstDev": out["calibration"]["ttg"]["worstDev"],
                          "ttgJudgedBuckets": out["calibration"]["ttg"]["judgedBuckets"],
                          "pass": out["calibration"]["worstDev"] is not None
                                  and out["calibration"]["worstDev"] <= crit["1a_margin"]}
    h = out["hit"]
    out["gate1"]["1b"] = {"criterion": prereg["gate1"]["criteria"]["1b_hitFloor"],
                          "crsMargin": crit["1b_margin"], "ttgMargin": crit["1b_ttg_margin"],
                          "crsDiff": round(h["modelTop1"] - h["marketTop1"], 4),
                          "ttgDiff": round(h["ttgModel"] - h["ttgMarket"], 4),
                          "pass": h["modelTop1"] >= h["marketTop1"] - crit["1b_margin"]
                                  and h["ttgModel"] >= h["ttgMarket"] - crit["1b_ttg_margin"]}
    ll = out["logloss"]
    out["gate1"]["1c"] = {"criterion": prereg["gate1"]["criteria"]["1c_logloss"],
                          "ciHigh": ll["ci"][1], "margin": crit["1c_margin"],
                          "pass": ll["ci"][1] <= crit["1c_margin"]}
    out["gate1"]["pass"] = all(out["gate1"][k]["pass"] for k in ("1a", "1b", "1c"))
    out["prereg"] = {"version": prereg["version"], "path": str(prereg_path)}
    out["notes"] = [
        "DC泄漏修复(裁定②): dc_att/dc_def=as-of可见近10场滚动代理(±1.35标准化·dc_rolling=True), 不读当前全历史DC缓存",
        "残余泄漏(如实标注): rho/homeAdv 取当前联赛 DC 标量(裁定②只覆盖队级参数); xG/elo 砖仅2526+档, 早期赛季降级见 flagsDist",
        "oldChain代理: 旧链crs_fusion逐场历史票不可得(engine/shadow/qcache 实票仅18销售日173场, 覆盖不足) → crs池次低赔率选项为代理, 非旧链本体",
        "均匀T缺口: ENV_GOALS 全联赛统一2.7(②c offset 未接入) → perLeague 键提供联赛分解直视",
        "hit/logloss 口径: hit=模型与市场crs共同子集(同场对比); logloss=had三向, 市场=体彩had比例去水, 配对差bootstrap CI(1000次·seed7)",
        f"覆盖率: nFull={out['nFull']} → 链可算 n={out['n']} (skipped={out['skipped']}); randomShuffle seed=7×200置换",
    ]
    SUMMARY_OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = SUMMARY_OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, SUMMARY_OUT)
    return out


if __name__ == "__main__":
    print(json.dumps(evaluate(), ensure_ascii=False)[:2000])
