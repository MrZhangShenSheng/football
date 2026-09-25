#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性迁移：修正历史 scoreHit 口径污染（2026-09-25 审计）。开发者 sszhang

背景：backfill 此前把 option_hit（全玩法选项命中）结果直写 scoreHit，致方向/总进球/
半全场腿的 scoreHit 恒等于其选项命中——"比分命中率"分母被非比分腿灌水（实测 381 条
非空 scoreHit 中 331 条不属比分腿），与铁律 11「方向命中与比分命中分开统计」冲突。

迁移规则（无信息损失，铁律 7 只改字段不重写文件结构）：
- 非比分腿：原 scoreHit 值挪到 optionHit（保留事实），scoreHit 置 null
- 比分腿：按 score_hit_of 重判（修复复合 pick '1-1 + HAFU dd' 此前静默 None 的空缺）
用法：python engine/scripts/migrate_score_hit.py [--apply]   # 缺 --apply 为 dry-run
"""
import json
import re
import sys
from pathlib import Path

from backfill import option_hit, score_hit_of, parse_score
from common import log, ROOT

RESULTS_DIR = ROOT / "data" / "02-results"


def migrate(apply: bool) -> dict:
    n_file = n_moved = n_rescored = n_filled = 0
    for p in sorted(RESULTS_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log("migrate", f"跳过坏文件 {p.name}")
            continue
        dirty = False
        for rec in (data.get("matches") or data.get("records") or []):
            sc = parse_score(rec.get("result"))
            if not sc:
                continue
            hg, ag = sc
            half = parse_score(rec.get("half")) or (None, None)
            old = rec.get("scoreHit")
            new = score_hit_of(rec, hg, ag)
            if new is None and old is not None:      # 非比分腿：值挪 optionHit
                rec.setdefault("optionHit", old)
                rec["scoreHit"] = None
                n_moved += 1
                dirty = True
            elif new is not None and old != new:     # 比分腿：重判（含补空缺）
                rec["scoreHit"] = new
                n_rescored += 1 if old is not None else 0
                n_filled += 1 if old is None else 0
                dirty = True
            if "optionHit" not in rec:               # 补齐全玩法选项命中
                oh = option_hit(rec, hg, ag, half[0], half[1])
                if oh is not None:
                    rec["optionHit"] = oh
                    dirty = True
        if dirty:
            n_file += 1
            if apply:
                p.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"files": n_file, "moved": n_moved, "rescored": n_rescored, "filled": n_filled}


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    r = migrate(apply)
    mode = "已写入" if apply else "dry-run（加 --apply 落盘）"
    log("migrate", f"{mode}：文件 {r['files']} · 非比分腿挪 optionHit {r['moved']} · "
                   f"比分腿重判 {r['rescored']} · 补空缺 {r['filled']}")
