"""任9 选场模型（阶段2 v0）：14场 → p_mkt(devig) → [DC融合钩子] → grasp 排序
→ top9 选场 + 方向 + 变体注 + 复式贪心。

grasp = max(p_fused)。联赛期次走 DC 融合（LEAGUE_MAP→fd slug→_dc_params）；
国家队/杯赛期 dc=null 纯市场锚（闸门标记，26132 国家队窗口为首例）。
复式贪心 = 核心场中 (p主+p次)/p主 增益比最大者双选。
设计=docs/2026-09-22-sfc-ren9-design.html。开发者 sszhang"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from boldplay import _dc_params, _zh_map, _load_fusion   # 复用：DC参数/队名映射/融合系数
from dc_predict import devig as devig_n

BASE = Path(__file__).resolve().parents[2]
SFC_DIR = BASE / 'data' / '07-sfc'

# okooo 联赛中文名 → fd slug（联赛期次才用；国家队/杯赛不在表内=无DC）
LEAGUE_MAP = {
    '英超': 'england-premier', '西甲': 'spain-laliga', '德甲': 'germany-bundesliga',
    '意甲': 'italy-serie-a', '法甲': 'france-ligue1', '荷甲': 'netherlands-eredivisie',
    '葡超': 'portugal-liga', '英冠': 'england-championship', '德乙': 'germany-bundesliga2',
    '法乙': 'france-ligue2', '西乙': 'spain-liga2', '意乙': 'italy-serie-b',
    '比甲': 'belgium-first-a', '苏超': 'SC0', '土超': 'turkey-super-lig',
    '希超': 'greece-super', '葡甲': 'portugal-primeira',
}


def ren9(issue: int, manual: dict | None = None) -> dict:
    """manual: {场次号: {'p': float, 'dir': 3/1/0, 'note': str}} 人工无盘场补充（26132#1 中国场）。"""
    data = json.loads((SFC_DIR / f'{issue}.json').read_text(encoding='utf-8'))
    zh = _zh_map()
    rows = []
    for m in data['matches']:
        row = dict(m)
        p_mkt = devig_n([float(o) for o in m['odds']]) if m.get('odds') else None
        # DC 钩子：联赛在 fd 表内且队名可映射才有 λ（国家队期全部 None）
        params = None
        if m['league'] in LEAGUE_MAP and p_mkt is not None:
            params = _dc_params({'home': m['home'], 'away': m['away'],
                                 'league': LEAGUE_MAP[m['league']]}, zh)
        row['pMkt'] = [round(p, 4) for p in p_mkt] if p_mkt else None
        row['dcUsed'] = params is not None
        if params:
            from dc_predict import score_matrix
            from common import load_fusion_ab
            lh, la, rho = params
            matrix = score_matrix(lh, la, rho)
            p_dc = [0.0, 0.0, 0.0]
            for i in range(7):
                for j in range(7):
                    p_dc[0 if i > j else (1 if i == j else 2)] += float(matrix[i, j])
            a, b = load_fusion_ab(LEAGUE_MAP[m['league']])
            # 与 dc_predict.fuse 同式（log 意见池·a=0 联赛纯市场）
            num = [(p_dc[k] ** a) * ((p_mkt[k] or 1e-9) ** b) for k in range(3)]
            s = sum(num)
            p_f = [x / s for x in num]
            row['pFused'] = [round(x, 4) for x in p_f]
        else:
            row['pFused'] = row['pMkt']
        # 人工无盘场（假设驱动补充）
        man = (manual or {}).get(m['no'])
        if man and row['pFused'] is None:
            p = [0.0, 0.0, 0.0]
            p[{3: 0, 1: 1, 0: 2}[man['dir']]] = man['p']
            row['pFused'] = p
            row['manualNote'] = man['note']
        rows.append(row)

    # grasp 排序（pFused 缺失=无法评估，排末尾标记）
    for r in rows:
        pf = r['pFused']
        if pf:
            mx = max(pf)
            r['grasp'] = round(mx, 4)
            r['dir'] = [3, 1, 0][pf.index(mx)]
            r['spread'] = round((mx - min(pf)) * 100, 1)   # 极差 pp
        else:
            r['grasp'] = r['dir'] = None
            r['spread'] = None
    ranked = sorted((r for r in rows if r['grasp']), key=lambda r: -r['grasp'])
    core, bench = ranked[:9], ranked[9:]

    # 复式贪心：核心场中增益比 (p主+p次)/p主 最大者双选
    best_double = None
    for r in core:
        pf = r['pFused']
        p2 = sorted(pf)[-2]
        gain = (pf[r['pFused'].index(max(pf))] and (max(pf) + p2) / max(pf))
        if not best_double or gain > best_double['gain']:
            best_double = {'no': r['no'], 'match': f"{r['home']} vs {r['away']}",
                           'dirs': [r['dir'], [3, 1, 0][sorted(range(3), key=lambda k: -pf[k])[1]]],
                           'gain': round(gain, 3)}
    return {'issue': issue, 'rows': rows, 'core': core, 'bench': bench,
            'doublePick': best_double,
            'variantSwap': {'out9': core[-1], 'in10': bench[0]} if len(bench) else None,
            'dcCoverage': sum(1 for r in rows if r['dcUsed'])}


def render(res: dict) -> str:
    lines = [f"任9 第{res['issue']}期 · DC覆盖 {res['dcCoverage']}/14 场（国家队期=纯市场锚）", '']
    lines.append('── 核心九场（grasp 降序）──')
    for r in res['core']:
        tag = '🤚人工' if r.get('manualNote') else ('📊DC' if r['dcUsed'] else '⚓市场')
        lines.append(f"  #{r['no']:>2} {r['league']} {r['home']} vs {r['away']}"
                     f" → { {3:'主胜',1:'平',0:'客胜'}[r['dir']] } @{r['grasp']:.0%} {tag}"
                     f" 极差{r['spread']}pp")
    lines.append('── 落选替补 ──')
    for r in res['bench']:
        lines.append(f"  #{r['no']:>2} {r['league']} {r['home']} vs {r['away']} @{r['grasp']:.0%}（胶着 极差{r['spread']}pp）")
    v = res['variantSwap']
    if v:
        lines.append(f"── 变体注 ── 第9名 #{v['out9']['no']} {v['out9']['home']} ↔ 第10名 #{v['in10']['no']} {v['in10']['home']}")
    d = res['doublePick']
    if d:
        lines.append(f"── 复式建议 ── #{d['no']} {d['match']} 双选 "
                     f"{ {3:'主胜',1:'平',0:'客胜'}[d['dirs'][0]] }+{ {3:'主胜',1:'平',0:'客胜'}[d['dirs'][1]] }"
                     f"（增益比×{d['gain']}）")
    return '\n'.join(lines)


if __name__ == '__main__':
    issue = int(sys.argv[1]) if len(sys.argv) > 1 else 26132
    manual = {1: {'p': 0.85, 'dir': 3, 'note': '无盘人工假设：中国 vs 马尔代夫绝对实力差史级（FIFA排名差+历史净胜球级差），主胜把握历史级；友谊赛/正式赛性质待核 ❓低置信'}}
    res = ren9(issue, manual)
    (SFC_DIR / f'{issue}-ren9-plan.json').write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(render(res))
