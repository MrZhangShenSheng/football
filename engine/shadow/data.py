#!/usr/bin/env python3
"""data.py — 影子层轮次装载器（2026-09-07 重建版）。

settle-all CLI 的赛果源：load_rounds() 扫 data/02-results/ 主文件 →
[{date, legs: [{code, match, outcome, score, half}]}]（仅已回填场）。
原版随 scratch/replay_v2 清理误删，本版按 paper.py settle-all 段消费面重建
（体彩开奖缓存的补充合并仍留在 paper.py CLI 段，职责不变）。
开发者 sszhang
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
RESULTS_DIR = os.path.join(REPO, 'data', '02-results')

_SCORE_RE = re.compile(r'^(\d+)\s*[-:]\s*(\d+)')


def _outcome_of(hg: int, ag: int) -> int:
    return 0 if hg > ag else (1 if hg == ag else 2)


def _parse_score(s):
    m = _SCORE_RE.match(str(s or '').strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def load_rounds():
    """→ [{date, legs: [...]}]（日期升序；仅含有赛果的场次）。"""
    rounds = []
    for fname in sorted(os.listdir(RESULTS_DIR)):
        if not fname.endswith('.json') or fname.startswith('_'):
            continue
        date = fname[:-5]
        try:
            with open(os.path.join(RESULTS_DIR, fname), encoding='utf-8') as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        legs = []
        for rec in data.get('matches') or []:
            score = _parse_score(rec.get('result'))
            if score is None or not rec.get('code'):
                continue
            legs.append({
                'code': rec['code'], 'match': rec.get('match') or '',
                'outcome': _outcome_of(*score), 'score': score,
                'half': _parse_score(rec.get('half')),
            })
        if legs:
            rounds.append({'date': date, 'legs': legs})
    return rounds
