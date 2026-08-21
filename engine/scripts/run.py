#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一入口：一个命令管全部。人和 Claude 都只需记住本文件。

用法：
  python run.py update                    # 刷新当季+上季主流联赛赔率/xG 缓存 + 重建球队索引
  python run.py fit [联赛] [赛季]         # DC 拟合（默认西甲 2526；--auto 新鲜度自检）
  python run.py predict 联赛 主队 客队 [--market h,d,a]   # DC 预测 + 可选融合
  python run.py backtest [联赛] [赛季]    # walk-forward 回测（RPS/logloss）
  python run.py all                       # update + fit --auto 一条龙（预测日跑这个）

联赛代码（football-data.co.uk）：SP1 西甲 F1 法甲 F2 法乙 E0 英超 D1 德甲 I1 意甲 ...
fit/predict/backtest 用联赛全名：spain-laliga / france-ligue1 / france-ligue2 ...
"""
import subprocess
import sys

from common import log

# 常用联赛：fd 代码 → (全名, 建议拟合赛季)
LEAGUES = {
    "SP1": ("spain-laliga", "2526"),
    "F1": ("france-ligue1", "2526"),
    "F2": ("france-ligue2", "2526"),
    "E0": ("england-premier", "2526"),
    "D1": ("germany-bundesliga", "2526"),
    "I1": ("italy-serie-a", "2526"),
}
CURRENT_SEASON = "2627"


def sh(*args: str) -> None:
    log("run", "> " + " ".join(args))
    subprocess.run([sys.executable, *args], check=False, cwd=str(__import__("pathlib").Path(__file__).parent))


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    rest = sys.argv[2:]

    if cmd == "update":
        codes = rest or list(LEAGUES)
        # 上季（拟合用）+ 当季（锚用）
        sh("odds_fetch.py", "--season", "2526", *codes)
        sh("odds_fetch.py", "--season", CURRENT_SEASON, *codes)
        sh("build_index.py")
        sh("league_profile.py", "--all")
    elif cmd == "fit":
        if rest:
            league, season = rest[0], rest[1] if len(rest) > 1 else "2526"
        else:
            league, season = "spain-laliga", "2526"
        sh("dc_fit.py", league, season, "--auto")
    elif cmd == "predict":
        if len(rest) < 3:
            log("run", '用法: python run.py predict <联赛全名> "<主队fd名>" "<客队fd名>" [--market h,d,a]')
            return
        sh("dc_predict.py", *rest)
    elif cmd == "backtest":
        league = rest[0] if rest else "spain-laliga"
        season = rest[1] if len(rest) > 1 else "2526"
        sh("backtest.py", league, season)
    elif cmd == "all":
        sh("odds_fetch.py", "--season", "2526", *LEAGUES)
        sh("odds_fetch.py", "--season", CURRENT_SEASON, *LEAGUES)
        sh("build_index.py")
        sh("league_profile.py", "--all")
        for _, (league, season) in LEAGUES.items():
            sh("dc_fit.py", league, season, "--auto")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
