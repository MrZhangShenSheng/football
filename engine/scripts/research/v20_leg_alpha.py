# -*- coding: utf-8 -*-
"""v20：T002/T003 鉴定——腿质量 α 检验（体彩真实价 realized 口径）。

背景：T002/T003 双票 +228 系 6.5×10.5 双中右尾实现（~1.4% 概率·与 v14 +620%/
v15 +120% 同构）。对冲不创造期望——结构正收益的唯一可能来源是"腿质量 α"：
方案挑选的高赔率腿 realized 回报率是否系统性 >1。

预注册判据（跑前写死·git 时间戳凭证）：
  ① 按 玩法×赔率带（<3 / 3-5 / ≥5）分桶·每腿 e = 命中率×平均赔率
  ② 任一"高赔带(≥5)×玩法"格 e>1 且 n≥100 且 bootstrap95%CI 下限>1
     → 腿 α 成立·4串11 容错结构值得生产化评估
  ③ 全部高赔带 e≤1 或样本不足 → T002/T003 定性=右尾运气·结构关灯
  ④ 全玩法总 e 与分带 e 同报（样本不足格如实标注不可判）
数据：corpus records 方案腿（pick+odds+optionHit+result·各玩法）·体彩出票价。
产出：data/04-summaries/v20-leg-alpha.json
开发者 sszhang
"""
from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT_PATH = ROOT / "data" / "04-summaries" / "v20-leg-alpha.json"
BANDS = ((0.0, 3.0, "<3"), (3.0, 5.0, "3-5"), (5.0, 1e9, ">=5"))
BOOT_N = 2000
SEED = 20261008
ALPHA_LINE = 1.0


PLAY_ALIAS = {"CRS": "CRS", "比分": "CRS", "HAD": "HAD", "胜平负": "HAD", "方向": "HAD",
              "HHAD": "HHAD", "让球": "HHAD", "TTG": "TTG", "总进球": "TTG",
              "HAFU": "HAFU", "半全场": "HAFU"}


def hit_of(r: dict) -> bool | None:
    if r.get("optionHit") is not None:
        return bool(r["optionHit"])
    return None


def main():
    print("══ v20 腿质量 α 鉴定（T002/T003 鉴定·realized 口径）══\n")
    c = json.loads((ROOT / "data/04-summaries/corpus.json").read_text(encoding="utf-8"))
    legs = []
    skipped_multi = 0
    for r in c.get("records", []):
        odds = r.get("odds")
        hit = hit_of(r)
        play = PLAY_ALIAS.get(str(r.get("play", "")).upper(), str(r.get("play", "")).upper())
        if isinstance(odds, (dict, list)):
            skipped_multi += 1
            continue
        try:
            odds = float(odds)
        except (TypeError, ValueError):
            skipped_multi += 1
            continue
        if not odds or odds <= 1.0 or hit is None or play in ("", "NONE"):
            continue
        legs.append({"play": play, "odds": odds, "hit": hit,
                     "date": str(r.get("date", ""))[:10]})
    print(f"可判腿: {len(legs)}（corpus 798 条中 optionHit+标量odds 齐备者·跳过多选腿 {skipped_multi}）\n")

    def e_of(sub):
        if not sub:
            return None, 0
        return sum((1.0 if x["hit"] else 0.0) * x["odds"] for x in sub) / len(sub), len(sub)

    def boot_ci(sub):
        if len(sub) < 30:
            return None
        rng = random.Random(SEED)
        n = len(sub)
        es = sorted(
            sum((1.0 if sub[(k := rng.randrange(n))]["hit"] else 0.0) * sub[k]["odds"]
                for _ in range(n)) / n
            for _ in range(BOOT_N))
        return es[int(BOOT_N * 0.025)], es[int(BOOT_N * 0.975)]

    print("── 玩法×赔率带 e 曲线 ──")
    print(f"{'玩法':<6}{'带':<6}{'n':<6}{'命中率':<9}{'均赔':<8}{'e':<8}{'95%CI':<16}{'判定'}")
    results = {}
    alpha_found = []
    for play in sorted({x["play"] for x in legs}):
        sub_p = [x for x in legs if x["play"] == play]
        for lo, hi, name in BANDS:
            sub = [x for x in sub_p if lo <= x["odds"] < hi]
            e, n = e_of(sub)
            if not n:
                continue
            hr = sum(1 for x in sub if x["hit"]) / n
            ci = boot_ci(sub)
            ci_s = f"[{ci[0]:.2f},{ci[1]:.2f}]" if ci else "n<30不可判"
            is_high = (name == ">=5")
            verdict = ""
            if is_high and e is not None:
                if e > ALPHA_LINE and n >= 100 and ci and ci[0] > ALPHA_LINE:
                    verdict = "★腿α成立"
                    alpha_found.append((play, name, e, n, ci))
                elif e > ALPHA_LINE:
                    verdict = "e>1但CI下限≤1或n不足"
                else:
                    verdict = "e≤1·无α"
            results[f"{play}|{name}"] = {"n": n, "hitRate": round(hr, 4),
                                         "avgOdds": round(sum(x['odds'] for x in sub)/n, 2) if n else None,
                                         "e": round(e, 4) if e is not None else None,
                                         "ci": ci, "verdict": verdict}
            print(f"{play:<6}{name:<6}{n:<6}{hr*100:<9.1f}{(sum(x['odds'] for x in sub)/n):<8.2f}"
                  f"{e:<8.3f}{ci_s:<16}{verdict}")

    e_all, n_all = e_of(legs)
    ci_all = boot_ci(legs)
    print(f"\n全腿合计: e={e_all:.3f} (n={n_all}) CI{ci_all}")

    verdict = (f"腿α成立（{len(alpha_found)}格过线·结构生产化评估立项）" if alpha_found else
               "T002/T003定性=右尾运气·4串11容错结构关灯（高赔带无腿α）")
    print(f"\n══ 判定: {verdict} ══")
    out = {"ranAt": "2026-10-08", "preReg": "脚本头跑前写死(git e6f2ccf后时间戳凭证)",
           "nLegs": len(legs), "bands": BANDS, "bootN": BOOT_N,
           "curve": results, "allE": round(e_all, 4) if e_all else None,
           "allCI": ci_all, "alphaFound": alpha_found, "verdict": verdict}
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"归档 {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
