# engine/scripts/tail_train.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tail_train.py — 右尾肥尾场次模型训练评估链（spec docs/2026-10-13-tail-model-design.html §3）。

判据预注册 engine/cache/tail-model-prereg.json（跑前冻结·check_prereg 指纹闸）。
主判据 E1=总进球≥5：logistic 三头 vs 市场 ttg devig 聚合，配对 log-loss 差 CI 下限>0。
防泄漏：特征已由 tail_dataset 保证（as_of/lag/crsOpen）；本脚本 folds 内 scaler/模型
只在训练窗 fit。开发者 sszhang"""
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import market_lambda as mkl
from strength_chain_eval import _boot_ci_diff

PREREG_PATH = ROOT / "engine" / "cache" / "tail-model-prereg.json"
DATASET_DIR = ROOT / "engine" / "cache" / "tail_dataset"
OUT_DIR = ROOT / "data" / "04-summaries" / "tail-model"

C_LR = 1.0
MAX_ITER_LR = 2000
BOOT_B = 2000
SEED = 20261014
LOGLLOSS_CLIP = 1e-6
CS_NO_INFO_TG = 2                       # tgBin<2 的零封由结构决定，cs 头不训（tg=1 主胜=必 1:0）

E2_CELLS = [(3, 0), (4, 0), (4, 1), (5, 0), (5, 1), (5, 2),
            (0, 3), (0, 4), (1, 4), (0, 5), (1, 5), (2, 5)]
E3_CELLS = [(0, 3), (0, 4), (0, 5)]
TTG_KEYS = tuple(str(i) for i in range(8))


def date_str_month_end(y: int, m: int) -> str:
    """月末字符串（段 testTo）。"""
    import calendar
    return f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]}"


def time_folds() -> list[dict]:
    """expanding walk-forward：2025-01 起每 2 月一段共 11 段，末段截 2026-09-28。

    两月一段=奇数月开段并下月；2026-09 单月尾巴。开发者 sszhang"""
    monthly = []
    y, m = 2025, 1
    while (y, m) <= (2026, 9):
        monthly.append({"testFrom": f"{y}-{m:02d}-01", "testTo": date_str_month_end(y, m)})
        m += 1
        if m > 12:
            y, m = y + 1, 1
    merged = []
    i = 0
    while i + 1 < len(monthly):
        merged.append({"testFrom": monthly[i]["testFrom"], "testTo": monthly[i + 1]["testTo"]})
        i += 2
    if i < len(monthly):                                   # 2026-09 单月尾巴
        merged.append(monthly[i])
    merged[-1]["testTo"] = "2026-09-28"
    return merged


def load_dataset(ds_dir: Path = DATASET_DIR) -> tuple[list[dict], dict]:
    meta = json.loads((Path(ds_dir) / "meta.json").read_text(encoding="utf-8"))
    rows = [json.loads(x) for x in
            (Path(ds_dir) / "dataset.jsonl").read_text(encoding="utf-8").strip().split("\n")]
    return rows, meta


def feature_matrix(rows: list[dict], names: list[str]) -> np.ndarray:
    """行→矩阵（None→nan；scaler 前置）。"""
    return np.array([[float(r["features"].get(k)) if r["features"].get(k) is not None else np.nan
                      for k in names] for r in rows], dtype=float)


def fit_scaler(X_train: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """nan-safe 列均值/标准差（std=0→1 防除零）。train-only fit（防泄漏）。"""
    with np.errstate(invalid="ignore"):
        mu = np.nanmean(X_train, axis=0)
        sd = np.nanstd(X_train, axis=0)
    mu = np.where(np.isnan(mu), 0.0, mu)
    return mu, np.where(np.isnan(sd) | (sd < 1e-12), 1.0, sd)   # 全None列 nanstd→nan 须一并归 1（未映射分层暴露）


def apply_scaler(X: np.ndarray, mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    """nan→mu（scale 后为 0）；再标准化。"""
    Xf = np.where(np.isnan(X), mu, X)
    return (Xf - mu) / sd


def _onehot(vals: np.ndarray, n_slots: int) -> np.ndarray:
    z = np.zeros((len(vals), n_slots), dtype=float)
    z[np.arange(len(vals)), vals] = 1.0
    return z


def train_heads(X, y_tg, Xd, y_dir, Xc, y_cs) -> dict:
    """logistic 三头：tg 8类 / 方向|tg档one-hot / 零封|tg+方向（仅 tgBin≥CS_NO_INFO_TG 样本）。"""
    kw = dict(C=C_LR, max_iter=MAX_ITER_LR, random_state=SEED)
    h_tg = LogisticRegression(**kw).fit(X, y_tg)
    h_dir = LogisticRegression(**kw).fit(Xd, y_dir)
    mask = np.array([t >= CS_NO_INFO_TG for t in y_tg])
    h_cs = LogisticRegression(**kw).fit(Xc[mask], y_cs[mask]) if mask.sum() >= 30 else None
    return {"tg": h_tg, "dir": h_dir, "cs": h_cs}


def predict_event_probs(heads: dict, X: np.ndarray) -> np.ndarray:
    """骨架三轴连乘 → 每场 (E1, E2, E3)。

    条件化向量化：dir/cs 头输入 = X + 候选 tg 档 one-hot（+方向 one-hot），
    一次批量喂 8N / 24N 行避免逐格调用。开发者 sszhang"""
    N = len(X)
    p_tg = heads["tg"].predict_proba(X)                     # N×8（列序=classes_）
    tg_classes = list(heads["tg"].classes_)
    Xd_block = np.hstack([np.repeat(X, 8, axis=0), np.tile(np.eye(8), (N, 1))])
    p_dir_all = heads["dir"].predict_proba(Xd_block)        # 8N×3
    dir_classes = list(heads["dir"].classes_)
    tri = np.array([(t, d) for t in range(8) for d in range(3)])
    Xc_block = np.hstack([np.repeat(X, 24, axis=0),
                          np.tile(np.eye(8)[tri[:, 0]], (N, 1)),
                          np.tile(np.eye(3)[tri[:, 1]], (N, 1))])
    p_cs_all = heads["cs"].predict_proba(Xc_block) if heads["cs"] else None
    cs_classes = list(heads["cs"].classes_) if heads["cs"] else []
    idx_tg = {c: i for i, c in enumerate(tg_classes)}
    idx_dir = {c: i for i, c in enumerate(dir_classes)}
    idx_cs = {c: i for i, c in enumerate(cs_classes)}

    def cell_p(i_row: int, h: int, a: int) -> float:
        tg = min(h + a, 7)
        d = 0 if h > a else (1 if h == a else 2)
        cs = 2
        if h > a and a == 0:
            cs = 1
        elif h < a and h == 0:
            cs = 0
        pt = p_tg[i_row, idx_tg[tg]] if tg in idx_tg else 0.0
        pd = p_dir_all[i_row * 8 + tg, idx_dir[d]] if d in idx_dir else 0.0
        if p_cs_all is None or tg < CS_NO_INFO_TG:
            pc = 1.0 if cs == 2 else 0.0                    # 无 cs 头：非零封=1、零封=0（保守）
        else:
            pc = p_cs_all[i_row * 24 + tg * 3 + d, idx_cs[cs]] if cs in idx_cs else 0.0
        return float(pt * pd * pc)

    out = np.zeros((N, 3))
    for i in range(N):
        out[i, 0] = sum(p_tg[i, idx_tg[t]] for t in (5, 6, 7) if t in idx_tg)
        out[i, 1] = sum(cell_p(i, h, a) for h, a in E2_CELLS)
        out[i, 2] = sum(cell_p(i, h, a) for h, a in E3_CELLS)
    return out


def market_event_probs(row: dict) -> tuple[float, float, float] | None:
    """判据对手盘（封盘价·市场最终定价）：E1=ttg power devig s5+s6+s7；
    E2/E3=crs power devig 封盘同格聚合。任一池不足→None（双侧同跳）。"""
    ttg = row.get("ttg") or {}
    ks = [k for k in TTG_KEYS if ttg.get(k) and float(ttg[k]) > 1.0]
    if len(ks) < 6:
        return None
    p_t = mkl._power_devig_probs(np.array([1.0 / float(ttg[k]) for k in ks]))
    pm = dict(zip(ks, p_t))
    e1 = float(sum(pm.get(k, 0.0) for k in ("5", "6", "7")))
    crs = mkl.crs_power_devig(row.get("crs") or {})
    if not crs:
        return None
    e2 = float(sum(crs.get(f"s{h:02d}s{a:02d}", 0.0) for h, a in E2_CELLS))
    e3 = float(sum(crs.get(f"s{h:02d}s{a:02d}", 0.0) for h, a in E3_CELLS))
    return e1, e2, e3


def event_outcomes(score: str) -> tuple[int, int, int]:
    """赛果→三事件 0/1（E2/E3 同 0..5 网格口径·prereg secondary）。"""
    h, a = (int(x) for x in score.split(":"))
    return (int(h + a >= 5), int((h, a) in set(E2_CELLS)), int((h, a) in set(E3_CELLS)))


def _logloss(p: float, y: int) -> float:
    p = min(max(p, LOGLLOSS_CLIP), 1 - LOGLLOSS_CLIP)
    return -np.log(p) if y else -np.log(1 - p)


def run_folds(rows: list[dict], meta: dict, *, model: str = "logistic") -> dict:
    """expanding 逐段：train 窗 fit scaler+三头 → test 段逐场 (p_model, p_market, y)
    → 全段汇总配对 log-loss 差（market − model，正=模型更准）+ bootstrap CI。"""
    names = meta["featureNames"]
    diffs = {e: [] for e in ("E1", "E2", "E3")}
    seg_detail = []
    for k, fold in enumerate(time_folds()):
        train = [r for r in rows if r["date"] < fold["testFrom"]]
        test = [r for r in rows if fold["testFrom"] <= r["date"] <= fold["testTo"]]
        if len(train) < 500 or not test:
            continue
        Xtr = feature_matrix(train, names)
        mu, sd = fit_scaler(Xtr)
        Ztr = apply_scaler(Xtr, mu, sd)
        y_tg = np.array([r["label"]["tgBin"] for r in train])
        y_dir = np.array([r["label"]["direction"] for r in train])
        y_cs = np.array([r["label"]["cleanSheet"] for r in train])
        tg1h = _onehot(y_tg, 8)
        heads = train_heads(Ztr, y_tg,
                            np.hstack([Ztr, tg1h]), y_dir,
                            np.hstack([Ztr, tg1h, _onehot(y_dir, 3)]), y_cs)
        Zte = apply_scaler(feature_matrix(test, names), mu, sd)
        P = predict_event_probs(heads, Zte)
        n_seg = 0
        for i, r in enumerate(test):
            mk = market_event_probs(r)
            if mk is None:
                continue
            y = event_outcomes(r["score"])
            for j, e in enumerate(("E1", "E2", "E3")):
                diffs[e].append(_logloss(mk[j], y[j]) - _logloss(P[i, j], y[j]))
            n_seg += 1
        seg_detail.append({"fold": k, "testFrom": fold["testFrom"], "n": n_seg})
    out = {"model": model, "segments": seg_detail}
    for e in ("E1", "E2", "E3"):
        d = diffs[e]
        if len(d) >= 30:
            ci = _boot_ci_diff(d, seed=SEED, n_boot=BOOT_B)
            out[e] = {"n": len(d), "diffMean": round(float(np.mean(d)), 4),
                      "ci95": [round(float(x), 4) for x in ci],
                      "verdict": "PASS" if ci[0] > 0 else "FAIL"}
        else:
            out[e] = {"n": len(d), "verdict": "INSUFFICIENT_N"}
    return out


def check_prereg() -> dict:
    """指纹闸：prereg 存在且 frozenParams 与模块常量一致（铁律15 语义·跑前校验）。"""
    if not PREREG_PATH.exists():
        raise SystemExit(f"[prereg] 缺 {PREREG_PATH}——判据未冻结禁跑（铁律14②）")
    pr = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    fp = pr["frozenParams"]
    expect = {"C": C_LR, "max_iter": MAX_ITER_LR, "boot_B": BOOT_B, "seed": SEED,
              "logloss_clip": LOGLLOSS_CLIP}
    for k, v in expect.items():
        if fp.get(k) != v:
            raise SystemExit(f"[prereg] frozenParams.{k}={fp.get(k)} ≠ 模块常量 {v}")
    return pr


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="右尾肥尾模型训练评估链")
    ap.add_argument("cmd", choices=["run", "run-all"])
    args = ap.parse_args()
    check_prereg()
    rows, meta = load_dataset()
    result = {"logistic": run_folds(rows, meta)}
    if args.cmd == "run-all":
        result["histgbE1"] = run_histgb_e1(rows, meta)
        mapped, unmapped = split_by_mapping(rows)
        result["mappedLayer"] = {"E1": run_folds(mapped, meta)["E1"]} if len(mapped) > 500 else {"E1": {"n": 0, "verdict": "SKIP"}}
        result["unmappedLayer"] = {"E1": run_folds(unmapped, meta)["E1"]} if len(unmapped) > 500 else {"E1": {"n": 0, "verdict": "SKIP"}}
    result["datasetMeta"] = {k: meta[k] for k in ("version", "count", "fingerprint")}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    (OUT_DIR / "tail-model-report.html").write_text(render_report(result), encoding="utf-8")
    print(json.dumps({e: result["logistic"][e] for e in ("E1", "E2", "E3")}, ensure_ascii=False))
    return 0



# ── HistGB exploratory 对照 + featMask 分层 + 报告（Task 7）────────────────
from sklearn.ensemble import HistGradientBoostingClassifier

HISTGB_PARAMS = {"max_iter": 300, "learning_rate": 0.06, "random_state": SEED}


def run_histgb_e1(rows: list[dict], meta: dict) -> dict:
    """HistGB tg 头 E1 对照（exploratory·不进判据）。同 folds，逐段累计配对 log-loss 差。
    HistGB 原生吃 nan（不填均值），impute 口径差异在报告标注。开发者 sszhang"""
    names = meta["featureNames"]
    diffs = []
    for fold in time_folds():
        train = [r for r in rows if r["date"] < fold["testFrom"]]
        test = [r for r in rows if fold["testFrom"] <= r["date"] <= fold["testTo"]]
        if len(train) < 500 or not test:
            continue
        Xtr, Xte = feature_matrix(train, names), feature_matrix(test, names)
        y_tg = np.array([r["label"]["tgBin"] for r in train])
        clf = HistGradientBoostingClassifier(**HISTGB_PARAMS).fit(Xtr, y_tg)
        p_tg = clf.predict_proba(Xte)
        cols = {c: i for i, c in enumerate(clf.classes_)}
        for i, r in enumerate(test):
            mk = market_event_probs(r)
            if mk is None:
                continue
            y1 = event_outcomes(r["score"])[0]
            p_m = sum(p_tg[i, cols[t]] for t in (5, 6, 7) if t in cols)
            diffs.append(_logloss(mk[0], y1) - _logloss(p_m, y1))
    if len(diffs) < 30:
        return {"n": len(diffs), "verdict": "INSUFFICIENT_N", "exploratory": True}
    ci = _boot_ci_diff(diffs, seed=SEED, n_boot=BOOT_B)
    return {"n": len(diffs), "diffMean": round(float(np.mean(diffs)), 4),
            "ci95": [round(float(x), 4) for x in ci],
            "verdict": "PASS" if ci[0] > 0 else "FAIL", "exploratory": True}


def split_by_mapping(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """featMask 分层（红线②）：映射成功（两队均有 ID）vs 降级（任一 None）。"""
    mapped = [r for r in rows if r.get("homeId") and r.get("awayId")]
    unmapped = [r for r in rows if not (r.get("homeId") and r.get("awayId"))]
    return mapped, unmapped


def _ci_svg_block(result_all: dict) -> str:
    """三事件族+对照+分层 CI 误差条（内嵌 SVG）。"""
    items = [("E1 主判据·logistic", result_all.get("logistic", {}).get("E1")),
             ("E2 次判据", result_all.get("logistic", {}).get("E2")),
             ("E3 次判据", result_all.get("logistic", {}).get("E3")),
             ("E1·HistGB(exploratory)", result_all.get("histgbE1")),
             ("E1·映射层", (result_all.get("mappedLayer") or {}).get("E1")),
             ("E1·未映射层", (result_all.get("unmappedLayer") or {}).get("E1"))]
    rows_svg = []
    scale = 200.0   # 每侧最大渲染幅度
    for i, (label, d) in enumerate(items):
        y = 30 + i * 34
        if not d or "ci95" not in d:
            rows_svg.append(f'<text x="10" y="{y + 4}" font-size="12" fill="currentColor">{label}: n={d.get("n", 0) if d else 0} 样本不足</text>')
            continue
        lo, hi = d["ci95"]
        x0 = 430 + max(-scale, min(scale, lo * 1000))
        x1 = 430 + max(-scale, min(scale, hi * 1000))
        color = "#16a085" if d["verdict"] == "PASS" else "#c0392b"
        rows_svg.append(f'<line x1="{x0:.0f}" y1="{y}" x2="{x1:.0f}" y2="{y}" stroke="{color}" stroke-width="4"/>'
                        f'<circle cx="{430 + d["diffMean"] * 1000:.0f}" cy="{y}" r="4" fill="{color}"/>'
                        f'<text x="10" y="{y + 4}" font-size="12" fill="currentColor">{label}: Δ={d["diffMean"]:+.4f} CI[{lo:+.4f},{hi:+.4f}] {d["verdict"]}</text>')
    body = "".join(rows_svg)
    return (f'<svg viewBox="0 0 880 240" width="100%" xmlns="http://www.w3.org/2000/svg">'
            f'<line x1="430" y1="10" x2="430" y2="230" stroke="#888" stroke-dasharray="4,4"/>'
            f'<text x="434" y="18" font-size="10" fill="#888">0（平手线·右侧=模型更准）</text>{body}</svg>')


def render_report(result_all: dict) -> str:
    """tail-model-report.html（charset utf-8·主判据置顶·exploratory 全标注·限制节）。"""
    e1 = result_all.get("logistic", {}).get("E1", {})
    meta = result_all.get("datasetMeta", {})
    lines = []
    lines.append("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">")
    lines.append("<title>右尾肥尾场次模型报告</title></head><body>")
    lines.append("<h1>右尾肥尾场次模型 · 判据裁决报告</h1>")
    lines.append(f"<p>主判据 E1（总进球≥5·logistic vs 市场ttg去水·配对log-loss差 CI 下限&gt;0）：<b>{e1.get('verdict')}</b>"
                 f"（Δ={e1.get('diffMean')}, CI={e1.get('ci95')}, n={e1.get('n')}）</p>")
    lines.append(f"<p>数据集 {meta.get('version')} · {meta.get('count')} 场 · 指纹 {meta.get('fingerprint')}</p>")
    lines.append("<h2>CI 总览</h2>")
    lines.append(_ci_svg_block(result_all))
    lines.append("<h2>口径与限制</h2><ul>"
                 "<li>E2/E3 为 0..5 精确格口径（双侧同网格·outcome 同网格）；other 全口径未进判据（exploratory）</li>"
                 "<li>HistGB 原生吃 nan 与 logistic 的 train-mean 填充 impute 口径不同，仅 exploratory</li>"
                 "<li>特征市场价只用 crsOpen；ttg/had/crs 封盘只作对手盘（防泄漏口径）</li>"
                "<li>信息不对等残留：模型=开盘+历史特征 vs 对手盘=封盘价——输的部分含开盘→封盘市场吸收的增量（无法完全分解为『模型无信息』；未映射层输更狠佐证特征越多差距越小）</li>"
                 "<li>featMask 分层为红线②要求（防『只在易映射联赛赢』假象）</li>"
                 "<li>主判据 FAIL → tail_pick/影子层不激活（prereg 红线）</li></ul>")
    lines.append("<p style=\"color:#888;font-size:12px\">开发者 sszhang · 判据预注册 engine/cache/tail-model-prereg.json · 铁律 14②/15</p>")
    lines.append("</body></html>")
    return "".join(lines)

if __name__ == "__main__":
    raise SystemExit(main())
