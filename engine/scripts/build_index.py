#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重建 data/01-teams/_index.json 路由索引。

索引纪律（RAG 调研最佳实践）：
- 索引只是路由表不是内容库：每个球队一行，只含 路径/联赛/一句话摘要/能力标记
- 索引错误不致命：Claude 永远可以 glob 兜底
- 球队文件用英文规范 ID 命名（lech-poznan.json），中文名在 JSON 内部
"""
import json
import sys
from pathlib import Path

TEAMS_DIR = Path(__file__).resolve().parents[2] / "data" / "01-teams"


def build() -> dict:
    index: dict = {"_meta": {"rebuiltAt": None, "count": 0, "note": "路由表：一行一球队，内容本体在球队文件内"}, "teams": {}}
    for league_dir in sorted(p for p in TEAMS_DIR.iterdir() if p.is_dir()):
        for team_file in sorted(league_dir.glob("*.json")):
            if team_file.name.startswith("_"):
                continue
            try:
                data = json.loads(team_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                print(f"warn: 跳过 {team_file}: {e}", file=sys.stderr)
                continue
            team_id = team_file.stem
            elo = data.get("elo") or {}
            xg = data.get("xg") or {}
            form = data.get("recentForm") or []
            index["teams"][team_id] = {
                "league": league_dir.name,
                "path": str(team_file.relative_to(TEAMS_DIR)).replace("\\", "/"),
                "zh": data.get("team"),
                "summary": f"{len(form)}场近况 · Elo {elo.get('rating', '—')} · {'有xG' if xg else '无xG'}",
                "hasElo": bool(elo.get("rating")),
                "hasXg": bool(xg),
                "lastUpdated": data.get("lastUpdated"),
            }
    index["_meta"]["count"] = len(index["teams"])
    return index


def main() -> None:
    index = build()
    from datetime import date

    index["_meta"]["rebuiltAt"] = date.today().isoformat()
    out = TEAMS_DIR / "_index.json"
    out.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK: {index['_meta']['count']} 支球队 → {out}")


if __name__ == "__main__":
    main()
