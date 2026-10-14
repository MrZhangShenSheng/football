#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""provenance.py — 影子层链籍（2026-10-12 大哥拍板「回溯机制四件套」之一）。

痛点回放：影子票腿出自哪条链哪一代，曾需 git log + qcache 快照 + import 溯源三层人肉
才定论（「这模型上周才跑出来的吧」之问）。本模块把链代变成账面事实：
  - engine/cache/chain_registry.json 登记每条链的 epoch（改链必登记）
  - 票登记时 stamp（paper.register chain 参数 → 票.chain 字段）
  - 回溯查询（paper.py why / 分链战绩汇总）按票自身 chain 读，不再溯源
开发者 sszhang
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY_PATH = os.path.join(HERE, '..', 'cache', 'chain_registry.json')

# spec pool → 链名（shapes.PLAN_FAMILIES 的 pool 字段口径；未知 pool 返回 None 不乱标）
POOL_CHAIN = {
    'strong': 'market_devig', 'jiao_zhuo': 'market_devig', 'ev_best': 'market_devig',
    'ttg': 'shadow_qcache', 'crs': 'shadow_qcache', 'tail_greedy': 'shadow_qcache',
    'v4b': 'shadow_v4b',
    'llm_crs': 'llm-31grid-v1',          # L 族（2026-10-13 spec·review fix MEDIUM 补登）
}

# 不在 PLAN_FAMILIES 的独立执行器 spec → 链名（spec_name 直查）
SPEC_NAME_CHAIN = {
    'v18双信号N串1': 'shadow_v18',
}


def load_registry(path=None):
    """→ registry dict；损坏/缺失返回空骨架（fail-open，不炸登记主流程）。"""
    p = path or REGISTRY_PATH
    try:
        with open(p, encoding='utf-8') as fh:
            return json.load(fh)
        # json.load 后不可达
    except (OSError, json.JSONDecodeError):
        return {'chains': {}}


def resolve_epoch(chain, day, path=None):
    """链名+销售日 → {'from','engine'[,'to']} or None（日期不在任何 epoch=无籍可标）。"""
    epochs = (load_registry(path).get('chains') or {}).get(chain, {}).get('epochs') or []
    for ep in epochs:
        if ep.get('from') and ep['from'] <= day and (not ep.get('to') or day <= ep['to']):
            return ep
    return None


def stamp_for_spec(spec, day, path=None):
    """spec dict + 销售日 → 票 stamp {'chain','engine','since'} or None。
    spec 可为 PLAN_FAMILIES 成员（按 pool）或独立执行器 spec（按 name 直查）。"""
    chain = POOL_CHAIN.get(spec.get('pool') or '') or SPEC_NAME_CHAIN.get(spec.get('name') or '')
    if not chain:
        return None
    ep = resolve_epoch(chain, day, path)
    if not ep:
        return None
    return {'chain': chain, 'engine': ep['engine'], 'since': ep['from']}


def engine_of_ticket(ticket):
    """票 → engine 名（无 stamp 的历史票按 chain 缺失返回 '?'）。"""
    ch = ticket.get('chain') or {}
    return ch.get('engine') or '?'
