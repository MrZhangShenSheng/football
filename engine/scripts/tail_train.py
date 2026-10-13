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
    return mu, np.where(sd < 1e-12, 1.0, sd)


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
    ap.add_argument("cmd", choices=["run"])
    args = ap.parse_args()
    check_prereg()
    rows, meta = load_dataset()
    result = run_folds(rows, meta)
    result["datasetMeta"] = {k: meta[k] for k in ("version", "count", "fingerprint")}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps({e: result[e] for e in ("E1", "E2", "E3")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
