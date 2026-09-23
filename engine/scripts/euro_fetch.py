#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""okooo 99家平均欧指采集器（欧指市场锚·路径1第一步：只采不判）。

数据端点（2026-09-23 五轮探测实测，免登录）：
  1. 竞彩日期页 https://www.okooo.com/jingcai/{date}/（服务端渲染，gb18030）
     场次行属性直给：<tr data-ordercn="周三001" data-mid="1346979"
       data-hname="中国" data-aname="朝鲜" data-rq="-1">
     联赛 <a class="saiming" title="亚运男足"> · 时间 <div class="shijian" title="比赛时间:...">
  2. 欧指接口（竞彩页 init99 同源，一个请求拿全天全部场次）：
     https://www.okooo.com/I/?method=lottery.match.custom&lotteryType=SportteryWDL
       &lotteryNo={date}&typeId=1&pid=24&typeid=1&format=json
     → {"match_custom_response": {matchId: {"home":"1.56","draw":"4.12","away":"5.24"}}}
     pid=24 = 99家平均（zucai 期次页 data-pid="24" 印证）

设计：docs/2026-09-23-euro-anchor-design.html（方案C第一步：纯存档不做概率计算，
fd 覆盖与无锚场次一视同仁全采——前者对拍样本，后者接入受益人）

用法：
  python euro_fetch.py day 2026-09-23     # 指定销售日（幂等覆盖刷新）
  python euro_fetch.py today              # 今天（run.py update 自动调用）
  python euro_fetch.py week               # 近7天补洞（仅缺文件）

落盘：engine/cache/euro_odds/{date}.json
纪律：失败重试1次→降级返回 None 不抛异常，绝不阻塞预测流程。
开发者 sszhang
"""
import gzip
import json
import re
import sys
import time
import urllib.request
from datetime import date, timedelta

from common import log, ROOT

OUT_DIR = ROOT / "engine" / "cache" / "euro_odds"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Referer": "https://www.okooo.com/jingcai/"}
DAY_URL = "https://www.okooo.com/jingcai/{date}/"
CUSTOM_URL = ("https://www.okooo.com/I/?method=lottery.match.custom"
              "&lotteryType=SportteryWDL&lotteryNo={date}&typeId=1&pid=24"
              "&typeid=1&format=json")


def _get(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers=dict(UA, **{"Accept-Encoding": "gzip"}))
    resp = urllib.request.urlopen(req, timeout=timeout)
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def fetch_text(url: str, retries: int = 1):
    """带重试 GET；两次失败返回 None（降级，不抛异常）。"""
    for attempt in range(retries + 1):
        try:
            return _get(url)
        except Exception:
            if attempt < retries:
                time.sleep(2)
    return None


def parse_day_page(html: str) -> list[dict]:
    """日期页 HTML → 场次五元组（宽松解析：联赛/时间缺失容忍置 None）。

    场次行 = <div class="touzhu_1" data-...>（页面无 table；联赛/时间在紧随的
    liansai 子块里，用开标签后固定窗口提取，不受 div 嵌套闭合影响）。
    """
    out = []
    for m in re.finditer(r'<div class="touzhu_1"[^>]*>', html):
        head = m.group(0)

        def attr(name: str) -> str | None:
            a = re.search(rf'data-{name}="([^"]*)"', head)
            return a.group(1) if a else None

        ordercn = attr("ordercn")
        if not ordercn:
            continue
        window = html[m.end():m.end() + 1500]
        league = re.search(r'class="saiming[^"]*"[^>]*title="([^"]+)"', window)
        league_id = re.search(r'league/(\d+)/', window)
        kick = re.search(r'title="比赛时间:([\d\- :]+)"', window)
        rq_raw = attr("rq")
        out.append({
            "orderCn": ordercn,
            "matchId": attr("mid"),
            "home": attr("hname"),
            "away": attr("aname"),
            "rq": int(rq_raw) if rq_raw and rq_raw.lstrip("-").isdigit() else None,
            "league": league.group(1) if league else None,
            "leagueId": league_id.group(1) if league_id else None,
            "kickoff": kick.group(1).strip() if kick else None,
        })
    return out


def parse_custom(body: str) -> dict:
    """custom 接口返回 → {matchId: {"home":f,"draw":f,"away":f}}；坏数据整条丢弃。"""
    try:
        raw = json.loads(body).get("match_custom_response") or {}
    except (json.JSONDecodeError, AttributeError):
        return {}
    out = {}
    for mid, o in raw.items():
        try:
            out[mid] = {"home": float(o["home"]), "draw": float(o["draw"]), "away": float(o["away"])}
        except (KeyError, TypeError, ValueError):
            continue
    return out


def merge_odds(matches: list[dict], odds_map: dict) -> list[dict]:
    """五元组 × 欧指表 → 每场挂 euroAvg（无欧指置 None，不删场次）。"""
    for m in matches:
        m["euroAvg"] = odds_map.get(m["matchId"])
    return matches


def collect_day(day: str) -> dict | None:
    """单日采集落盘（幂等覆盖）；页面与接口双失败返回 None。"""
    html = fetch_text(DAY_URL.format(date=day))
    if html is None:
        log("euro", f"{day} 日期页不可达，跳过")
        return None
    matches = parse_day_page(html)
    if not matches:
        log("euro", f"{day} 无在售场次，跳过")
        return None
    odds_map = {}
    body = fetch_text(CUSTOM_URL.format(date=day))
    if body:
        odds_map = parse_custom(body)
    matches = merge_odds(matches, odds_map)
    payload = {
        "date": day, "source": "okooo", "pid": 24,
        "fetchedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "matches": matches,
        "coverage": {"matches": len(matches),
                     "withEuro": sum(1 for m in matches if m["euroAvg"])},
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{day}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    cov = payload["coverage"]
    log("euro", f"{day}: {cov['matches']} 场（欧指 {cov['withEuro']}）→ euro_odds/{day}.json")
    return payload


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    if cmd == "day" and len(args) >= 2:
        collect_day(args[1])
    elif cmd == "today":
        collect_day(date.today().isoformat())
    elif cmd == "week":
        for i in range(7):
            d = (date.today() - timedelta(days=i)).isoformat()
            if not (OUT_DIR / f"{d}.json").exists():
                collect_day(d)
                time.sleep(1.0)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
