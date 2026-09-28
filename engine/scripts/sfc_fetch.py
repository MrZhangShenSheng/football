"""传足（14场胜负平/任9）数据采集：期次解析 + 历史回填。

数据源：澳客 okooo.com/zucai/{issue}/（服务端渲染 GB2312，spike 已验证 26131 全字段解析，
赛果串与 500 彩票网交叉核对一致）。设计=docs/2026-09-22-sfc-ren9-design.html。

用法：
  python sfc_fetch.py issue 26131            # 单期抓取落盘
  python sfc_fetch.py backfill 26072 26131   # 区间回填（含端点）
  python sfc_fetch.py current                # 当期在售赛程（无赛果）

落盘：data/07-sfc/{issue}.json + _index.json。开发者 sszhang"""

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
OUT_DIR = BASE / 'data' / '07-sfc'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'


def fetch_html(issue: int) -> str:
    """单期抓取，含 405 限流退避。

    2026-09-28 实测：backfill 连抓 58 期全数 HTTP 405（澳客按频率限流，非封禁）——
    单期间隔 20 秒重试即成功。故 405 单独退避重试（5s→15s→40s），其余异常沿用原两次。
    """
    url = f'https://www.okooo.com/zucai/{issue}/'
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    backoff = (5, 15, 40)
    for attempt in range(1, len(backoff) + 2):
        try:
            return urllib.request.urlopen(req, timeout=20).read().decode('gb18030', errors='ignore')
        except urllib.error.HTTPError as e:
            if e.code != 405 or attempt > len(backoff):
                raise
            time.sleep(backoff[attempt - 1])
        except Exception:
            if attempt >= 2:
                raise
            time.sleep(2)


def parse_issue(issue: int, html: str) -> dict:
    """单期解析：14 场对阵/联赛/时间/比分/赛果 + 奖金（宽松匹配，缺失容忍）。"""
    table = None
    for t in re.findall(r'<table[^>]*>(.*?)</table>', html, re.S):
        if len(re.findall(r'<tr[^>]*>(.*?)</tr>', t, re.S)) >= 14:
            table = t
            break
    if table is None:
        raise ValueError(f'{issue}: 未找到 14 场表格（页面结构变更或期次不存在）')

    matches = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.S):
        no = re.search(r'class="xh td1"[^>]*>(\d+)', tr)
        league = re.search(r'jsLeagueName[^>]*>([^<]+)', tr)
        kick = re.search(r'比赛时间:([\d\- :]+)', tr)
        home = re.search(r'homenameobj[^>]*>([^<]+)', tr)
        away = re.search(r'awaynameobj[^>]*>([^<]+)', tr)
        ranks = re.findall(r'\[(\d+)\]', tr)
        score = re.search(r'class="tdfx td7"[^>]*>\s*(\d+)\s*:\s*(\d+)', tr)
        result = re.search(r'class="tdfx font_red td8"[^>]*>\s*([013])', tr)
        mid = re.search(r'/soccer/match/(\d+)/', tr)
        odds = [float(x) for x in re.findall(r'<em class="pltxt"[^>]*>\s*([\d.]+)\s*</em>', tr)]
        if not (no and league):
            continue
        matches.append({
            'no': int(no.group(1)),
            'league': league.group(1).strip(),
            'kickoff': kick.group(1).strip() if kick else None,
            'home': home.group(1).strip() if home else None,
            'away': away.group(1).strip() if away else None,
            'rankHome': int(ranks[0]) if ranks else None,
            'rankAway': int(ranks[1]) if len(ranks) > 1 else None,
            'score': f'{score.group(1)}-{score.group(2)}' if score else None,
            'result': int(result.group(1)) if result else None,   # 3主胜/1平/0客胜
            'odds': odds if len(odds) == 3 else None,             # 99家平均欧指 [主,平,客]
            'okoooMatchId': mid.group(1) if mid else None,
        })
    if len(matches) != 14:
        raise ValueError(f'{issue}: 解析出 {len(matches)} 场（应 14 场）')

    def money(pattern):
        m = re.search(pattern, html)
        return m.group(1) if m else None

    return {
        'issue': issue,
        'matches': sorted(matches, key=lambda x: x['no']),
        'resultSeq': ''.join(str(m['result']) if m['result'] is not None else '?' for m in matches),
        'prize': {
            'firstAmount': money(r'一等奖[^0-9]{0,60}?([\d,]+)\s*元'),
            'firstCount': money(r'一等奖[^0-9]{0,60}?([\d,]+)\s*元[^0-9]{0,40}?([\d,]+)\s*注'),
            'secondAmount': money(r'二等奖[^0-9]{0,60}?([\d,]+)\s*元'),
            'ren9Amount': money(r'任九[^0-9]{0,60}?([\d,]+)\s*元'),
            'sales': money(r'销售额[^0-9]{0,60}?([\d,]+)\s*元'),
            'note': '奖金字段为宽松正则抽取，个别期缺失属正常（回测非必需）',
        },
        'fetchedAt': time.strftime('%Y-%m-%d %H:%M:%S'),
    }


def save(issue: int, data: dict):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f'{issue}.json'
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')


def update_index():
    # 仅纯数字期次文件入索引（26132-prediction/anchors 等伴生文件不入）
    files = sorted(f for f in OUT_DIR.glob('*.json') if re.fullmatch(r'\d{5}', f.stem))
    index = [{'issue': int(f.stem),
              'complete': '?' not in json.loads(f.read_text(encoding='utf-8'))['resultSeq']}
             for f in files]
    (OUT_DIR / '_index.json').write_text(
        json.dumps({'count': len(index), 'issues': index}, ensure_ascii=False, indent=1),
        encoding='utf-8')
    print(f'[sfc] _index.json 更新：{len(index)} 期（完整 {sum(1 for x in index if x["complete"])}）')


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'issue':
        issue = int(sys.argv[2])
        data = parse_issue(issue, fetch_html(issue))
        save(issue, data)
        print(f'[sfc] {issue}: 赛果 {data["resultSeq"]} · 奖金 {data["prize"]}')
    elif cmd == 'backfill':
        # --force：重抓已存在期次（早期版本无 odds 字段，outcomes 校准表需赔率，须重抓补全）
        force = '--force' in sys.argv
        args = [a for a in sys.argv[2:] if not a.startswith('--')]
        lo, hi = int(args[0]), int(args[1])
        ok = fail = skip = 0
        for issue in range(lo, hi + 1):
            p = OUT_DIR / f'{issue}.json'
            if p.exists() and not force:
                skip += 1
                continue
            if p.exists() and force:
                # 已有赛果+赔率齐备的不重抓（省请求、避限流）
                try:
                    old = json.loads(p.read_text(encoding='utf-8'))
                    ms = old.get('matches') or []
                    if ms and all(m.get('odds') for m in ms) and all(m.get('result') is not None for m in ms):
                        skip += 1
                        continue
                except Exception:
                    pass
            try:
                data = parse_issue(issue, fetch_html(issue))
                save(issue, data)
                ok += 1
                n_odds = sum(1 for m in data['matches'] if m.get('odds'))
                print(f'[sfc] {issue}: {data["resultSeq"]} · odds {n_odds}/14')
            except Exception as e:
                fail += 1
                print(f'[sfc] {issue} FAIL: {e}')
            # 6s 间隔：0.8s 实测触发 405 全批失败（2026-09-28），澳客按频率限流
            time.sleep(6)
        print(f'[sfc] 回填完成 成功{ok} 跳过{skip} 失败{fail}')
    elif cmd == 'current':
        recent = None
        m = re.search(r'recentPrizeNo\s*=\s*(\d+)', fetch_html(26131 + 1))
        if m:
            recent = int(m.group(1))
        cur = recent + 1 if recent else None
        if not cur:
            print('[sfc] 当期号探测失败')
            return
        try:
            data = parse_issue(cur, fetch_html(cur))
        except ValueError:
            data = {'issue': cur, 'matches': [], 'resultSeq': '', 'note': '未开售或解析失败'}
            data = parse_soft(cur, fetch_html(cur))
        save(cur, data)
        print(f'[sfc] 当期 {cur}: {len(data.get("matches", []))} 场')
    else:
        print(__doc__)


def parse_soft(issue: int, html: str) -> dict:
    """当期在售：部分字段可能缺失（无赛果），容忍解析。"""
    try:
        return parse_issue(issue, html)
    except ValueError:
        return {'issue': issue, 'matches': [], 'resultSeq': '', 'note': '结构不完整'}


if __name__ == '__main__':
    main()
    update_index()
