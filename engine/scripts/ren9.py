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

# ④ 平局暴露参数（26132 教训）：E_draw≥2.0 且 argmax 0 平 → 报警；
# 平局双选增益 ×1.5（pari-mutuel 稀注杠杆系数，先验值——积累 3+ 期后校准）
DRAW_EXPOSE_THRESHOLD = 1.5  # 26132实测E_draw=1.72开2平校准(26131/11无赔率不可回放)
DRAW_LEVERAGE = 1.5

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
    # 平局双选增益 × DRAW_LEVERAGE（pari-mutuel 稀注杠杆：大众追热门避平局，平局
    # 中的分钱注数少——argmax 单式结构性 0 平局 vs 历史开奖期望 ~3.25 平/期，
    # 26132 任9 7/9 死于澳巴+波兰双平局的直接教训。④三梯队一期）
    best_double = None
    for r in core:
        pf = r['pFused']
        p2 = sorted(pf)[-2]
        gain = (pf[r['pFused'].index(max(pf))] and (max(pf) + p2) / max(pf))
        second_dir = [3, 1, 0][sorted(range(3), key=lambda k: -pf[k])[1]]
        if second_dir == 1:
            gain *= DRAW_LEVERAGE
        if not best_double or gain > best_double['gain']:
            best_double = {'no': r['no'], 'match': f"{r['home']} vs {r['away']}",
                           'dirs': [r['dir'], second_dir],
                           'gain': round(gain, 3),
                           'leverageApplied': second_dir == 1}

    # drawGuard：平局暴露检查（26132 教训代码化）
    e_draw = sum(r['pFused'][1] for r in core if r.get('pFused'))
    n_draw = sum(1 for r in core if r.get('dir') == 1)
    guard = {'eDraw': round(e_draw, 2), 'nDrawLegs': n_draw, 'warn': False, 'suggest': []}
    if n_draw == 0 and e_draw >= DRAW_EXPOSE_THRESHOLD:
        guard['warn'] = True
        guard['note'] = (f"argmax 单式 0 平局 vs 期望 {e_draw:.1f} 平"
                         f"（26132 实测 E_draw=1.72 开 2 平 · pari-mutuel 稀注杠杆区遗漏）")
    # 名额函数 n_slots = min(4, ceil(E_draw×2))：暴露越多名额越多（26132 单期校准
    # ——E_draw=1.72→4 名额恰好覆盖双平局死因，3+ 期后复校防过拟合）。建议与报警
    # 解耦：始终输出 top n_slots（≥0.18）的平局双选候选
    import math as _math
    n_slots = min(4, _math.ceil(e_draw * 2)) if e_draw >= DRAW_EXPOSE_THRESHOLD else 2
    guard['nSlots'] = n_slots
    for r in sorted(core, key=lambda r: -(r['pFused'][1] if r.get('pFused') else 0))[:n_slots]:
        pf = r['pFused']
        if pf and pf[1] >= 0.18:
            guard['suggest'].append({'no': r['no'],
                                     'match': f"{r['home']} vs {r['away']}",
                                     'pDraw': round(pf[1], 3),
                                     'pick': {3: '主胜', 1: '平', 0: '客胜'}[r['dir']],
                                     'action': f"建议平局双选（{ {3:'主胜',1:'平',0:'客胜'}[r['dir']] }+平）"})
    return {'issue': issue, 'rows': rows, 'core': core, 'bench': bench,
            'doublePick': best_double,
            'drawGuard': guard,
            'variantSwap': {'out9': core[-1], 'in10': bench[0]} if len(bench) else None,
            'dcCoverage': sum(1 for r in rows if r['dcUsed'])}



def edge_rank(core: list, gamma: float = 1.5) -> list:
    """⑥ pari-mutuel 期望效用选场（近似版·三梯队二期）。

    spike 结论（2026-09-27）：体彩官方开奖公告仅公布销量/中奖注数/奖池，**不公布
    各场投注比例**；第三方无稳定归档 → p_crowd 无直接数据。降级为先验近似：
    p_crowd_i ∝ (1/o_i)^γ（γ>1 = 大众比理性更追热门），edge = p_fused − p_crowd。
    edge 高的方向 = 大众低估区 = pari-mutuel 稀注价值区（argmax 的数学修正）。
    γ=1.5 行为金融 FLB 系数量级（先验），3+ 期后校准。
    开发者 sszhang
    """
    out = []
    for r in core:
        pf = r.get("pFused")
        if not pf or not r.get("odds"):
            continue
        try:
            inv = [(1.0 / float(o)) ** gamma for o in r["odds"]]
        except (TypeError, ValueError):
            continue
        s = sum(inv)
        crowd = [x / s for x in inv]
        edges = [pf[k] - crowd[k] for k in range(3)]
        best = max(range(3), key=lambda k: edges[k])
        out.append({"no": r["no"], "match": f"{r['home']} vs {r['away']}",
                    "edgeDir": {0: "主胜", 1: "平", 2: "客胜"}[best],
                    "edge": round(edges[best], 3),
                    "pFusedDir": round(pf[best], 3), "pCrowdDir": round(crowd[best], 3)})
    return sorted(out, key=lambda x: -x["edge"])


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
                     f"（增益比×{d['gain']}{'·平局杠杆1.5已乘' if d.get('leverageApplied') else ''}）")
    g = res.get('drawGuard')
    if g:
        tag = '⚠️报警' if g['warn'] else '✅正常'
        lines.append(f"── 平局暴露 ── {tag} E_draw={g['eDraw']} 票内平局腿={g['nDrawLegs']}")
        if g.get('note'):
            lines.append(f"   {g['note']}")
        for s in g.get('suggest', []):
            lines.append(f"   #{s['no']} {s['match']} p平={s['pDraw']} 当前选{s['pick']} → {s['action']}")
    return '\n'.join(lines)


if __name__ == '__main__':
    issue = int(sys.argv[1]) if len(sys.argv) > 1 else 26132
    manual = {1: {'p': 0.85, 'dir': 3, 'note': '无盘人工假设：中国 vs 马尔代夫绝对实力差史级（FIFA排名差+历史净胜球级差），主胜把握历史级；友谊赛/正式赛性质待核 ❓低置信'}}
    res = ren9(issue, manual)
    (SFC_DIR / f'{issue}-ren9-plan.json').write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    print(render(res))
