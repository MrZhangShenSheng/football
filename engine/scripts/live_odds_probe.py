"""临场价源探测：the-odds-api(读 ODDS_API_KEY) / Pinnacle 直连 / oddsportal 页面。产出 feasibility JSON。开发者 sszhang"""
import json, os, time
from datetime import date, datetime
import requests

from common import ROOT

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

def probe_result(source: str, ok: bool, detail: str) -> dict:
    return {"source": source, "ok": ok, "detail": detail, "probedAt": datetime.now().isoformat(timespec="seconds")}

def verdict(results: list) -> str:
    return "layer1_live" if any(r["ok"] for r in results) else "layer1_prior"

def probe_odds_api() -> dict:
    key = os.environ.get("ODDS_API_KEY", "")
    if not key:
        return probe_result("the-odds-api", False, "无 ODDS_API_KEY 环境变量（免费注册 the-odds-api.com，500次/月）")
    try:
        r = requests.get("https://api.the-odds-api.com/v4/sports/soccer_epl/odds/",
                         params={"apiKey": key, "regions": "eu", "markets": "h2h"}, headers=UA, timeout=15)
        n = len(r.json()) if r.ok else 0
        return probe_result("the-odds-api", r.ok and n > 0, f"HTTP {r.status_code} 场次数 {n}")
    except Exception as e:
        return probe_result("the-odds-api", False, f"{type(e).__name__}: {e}")

def probe_pinnacle() -> dict:
    try:
        r = requests.get("https://api.pinnacle.com/papi/1.0/fixtures?sportId=1",
                         headers=UA, timeout=10)
        return probe_result("pinnacle直连", r.ok, f"HTTP {r.status_code}（预期401/403=可达未授权，200=通）")
    except Exception as e:
        return probe_result("pinnacle直连", False, f"{type(e).__name__}: {e}")

def main() -> None:
    results = [probe_odds_api(), probe_pinnacle()]
    out = {"ranAt": str(date.today()), "results": results, "verdict": verdict(results),
           "usage": "layer1_live → 层1 用临场价；layer1_prior → 层1 降级为上轮收盘先验"}
    # ROOT 绝对定位（2026-09-04 审计 P1：相对写在 cwd=engine/scripts 下响亮崩）
    with open(ROOT / "engine/cache/live_odds_feasibility.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()
