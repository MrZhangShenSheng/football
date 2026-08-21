#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取球队 Elo 评级，更新球队画像。双链路兜底（2026-08-21 实测）：

主链路  api.clubelo.com/{name}   官方 CSV API（最全，含 From/To 曲线）
兜底链  clubelo.com/{slug}       主域 HTML 页正则提 Elo: <b>1980</b>
                                 （api 子域本机不可达时自动切换；仅"近期有比赛"的
                                  活跃队有页面，休赛期联赛队会 MISS → 标注降级）

主域 slug 解析顺序：_aliases.json clubeloSlug（人工兜底映射，优先）
                  → clubelo.com/All 页面 slug 表（脚本自动拉取缓存）
                  → 显示名去空格/去重音变体

用法：
  python elo_fetch.py                    # 全部已映射球队
  python elo_fetch.py benfica lech-poznan # 指定球队
"""
import csv
import io
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path
from urllib.parse import quote

import requests

from common import load_aliases, load_team, save_team, log, ROOT

API = "https://api.clubelo.com/{}"
FALLBACK_BASE = "https://clubelo.com/{}"
ALL_URL = "https://clubelo.com/All"
CACHE_DIR = ROOT / "engine" / "cache"
SLUG_CACHE = CACHE_DIR / "clubelo_slugs.json"
SLUG_CACHE_DAYS = 7
UA = {"User-Agent": "Mozilla/5.0 (football-kb personal project)"}
# 无 UA 头主域也可达；带 UA 走 api 子域（若可达）
ELO_RE = re.compile(r"Elo:\s*<b>(\d+)</b>")
LINKED_SLUG_RE = re.compile(r'href="/([^"]+)"><span class="max640">[^<]*</span><span class="min641">([^<]+)</span>')


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def load_slug_table() -> dict[str, str]:
    """clubelo.com/All 显示名 → slug 映射（本地缓存 7 天）。"""
    if SLUG_CACHE.exists():
        import json
        data = json.loads(SLUG_CACHE.read_text(encoding="utf-8"))
        age = (date.today() - date.fromisoformat(data["fetchedAt"])).days
        if age <= SLUG_CACHE_DAYS:
            return data["slugs"]
    try:
        html = requests.get(ALL_URL, timeout=20).text
        slugs = {disp.strip(): slug for slug, disp in LINKED_SLUG_RE.findall(html)}
        import json
        SLUG_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SLUG_CACHE.write_text(json.dumps({"fetchedAt": date.today().isoformat(), "slugs": slugs},
                                         ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return slugs
    except requests.RequestException as e:
        log("elo", f"拉取 All 页失败（用空 slug 表）: {e}")
        return {}


def fetch_api(name: str) -> dict | None:
    """官方 CSV API；超时重试 1 次。"""
    url = API.format(quote(name))
    for attempt in (1, 2):
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(resp.text)))
            return rows[-1] if rows else None
        except requests.RequestException:
            if attempt == 2:
                return None


def fetch_fallback(display_name: str, slug_table: dict[str, str]) -> int | None:
    """主域 HTML 页兜底：返回 Elo 数字或 None。"""
    candidates = []
    if display_name in slug_table:
        candidates.append(slug_table[display_name])
    stripped = strip_accents(display_name)
    candidates.append(display_name.replace(" ", ""))
    if stripped != display_name:
        candidates.append(stripped.replace(" ", ""))
    seen = set()
    for slug in candidates:
        if slug in seen:
            continue
        seen.add(slug)
        try:
            html = requests.get(FALLBACK_BASE.format(quote(slug)), timeout=15).text
            m = ELO_RE.search(html)
            if m:
                return int(m.group(1))
        except requests.RequestException:
            continue
    return None


def main() -> None:
    aliases = load_aliases()
    targets = sys.argv[1:] or list(aliases)
    slug_table = load_slug_table()
    log("elo", f"slug 表 {len(slug_table)} 条（主域兜底用）")
    ok = skip = fail_api = fail_all = 0
    api_alive = True  # api 子域连续失败则全体转主域，避免逐队等超时
    for team_id in targets:
        info = aliases.get(team_id)
        if not info:
            log("elo", f"未知名 {team_id}（不在 _aliases.json）")
            fail_all += 1
            continue
        clubelo_name = info.get("clubelo")
        if not clubelo_name:
            skip += 1
            continue
        row = fetch_api(clubelo_name) if api_alive else None
        source = "clubelo.com"
        elo_val = None
        if row:
            elo_val = int(row["Elo"])
        else:
            if api_alive:
                fail_api += 1
                api_alive = False  # 一次失败即判定子域不可达，后续直接走兜底
                log("elo", "api.clubelo.com 不可达 → 全体切换主域兜底链")
            elo_val = fetch_fallback(info.get("clubeloSlug") or clubelo_name, slug_table)
            source = "clubelo.com-main"
        if elo_val is None:
            log("elo", f"{team_id} ← {clubelo_name}: 两条链路均失败（休赛期队无主域页面？）")
            fail_all += 1
            continue
        data = load_team(team_id, info["league"])
        data["elo"] = {
            "rating": elo_val,
            "rank": None,
            "country": row.get("Country") if row else None,
            "level": row.get("Level") if row else None,
            "from": row.get("From") if row else None,
            "source": source,
            "fetchedAt": date.today().isoformat(),
        }
        save_team(team_id, info["league"], data, zh=info.get("zh"))
        ok += 1
        log("elo", f"{team_id} ← {clubelo_name}: {elo_val}（{source}）")
    log("elo", f"完成：成功 {ok} · 跳过(无映射) {skip} · api不可达 {fail_api} · 失败 {fail_all}")


if __name__ == "__main__":
    main()
