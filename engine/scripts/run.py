#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一入口：一个命令管全部。人和 Claude 都只需记住本文件。

用法：
  python run.py update                    # 刷新当季+上季主流联赛赔率/xG 缓存 + 重建球队索引
  python run.py fit [联赛] [赛季]         # DC 拟合（默认西甲 2526；--auto 新鲜度自检）
  python run.py predict 联赛 主队 客队 [--market h,d,a]   # DC 预测 + 可选融合
  python run.py backtest [联赛] [赛季]    # walk-forward 回测（RPS/logloss）
  python run.py espn [联赛代码]           # ESPN 直连积分榜/赛果（日职/北欧等 fd 不覆盖联赛）
  python run.py cn [联赛ID]              # titan007 国内兜底积分榜（ESPN 不可达时用）
  python run.py backfill [日期]           # 赛果自动回填（ESPN按日+别名匹配；无覆盖联赛标不可得）
  python run.py corpus                    # 学习语料汇总 + 趋势报告（回填后跑）
  python run.py verify                    # 回归验证闭环：backfill→corpus→trend(断言)→calibrate→ablate
  python run.py learn [联赛...]           # 本地赛果联赛增量采集+拟合+版本发布（日职/沙特/瑞超）
  python run.py all                       # update + fit --auto + learn 一条龙（预测日跑这个）

联赛代码（football-data.co.uk）：SP1 西甲 F1 法甲 F2 法乙 E0 英超 D1 德甲 I1 意甲 ...
fit/predict/backtest 用联赛全名：spain-laliga / france-ligue1 / france-ligue2 ...
本地赛果联赛（espn history 回填）：japan / saudi / sweden；体彩源（sporttery league-results 回填）：korea
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
# 非fd联赛：espn_code → 本地联赛名（history 回填 + --source local 拟合）
LOCAL_LEAGUES = {
    "jpn.1": "japan",
    "ksa.1": "saudi",
    "swe.1": "sweden",
}
# 体彩源联赛（ESPN 不覆盖；sporttery_fetch.py league-results 回填，2026-08-23 韩职接入）
SPORTTERY_LEAGUES = ("korea",)
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
        sh("sporttery_fetch.py")
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
    elif cmd == "espn":
        # 例: python run.py espn jpn.1 / python run.py espn results esp.1 20260820
        sh("espn_fetch.py", *rest)
    elif cmd == "cn":
        # 例: python run.py cn standings 25 / python run.py cn teams 13
        sh("cn_fetch.py", *rest)
    elif cmd == "corpus":
        sh("corpus.py")
        sh("trend_report.py")
    elif cmd == "backfill":
        sh("backfill.py", *rest)
    elif cmd == "verify":
        # 回归验证闭环：回填 → 票务结算(backfill内) → 阶梯卡settle → 语料+趋势(断言A1-A4) → 融合重校(门槛自动跳过) → 系数消融(人审)
        sh("backfill.py", *rest)
        sh("boldplay.py", "settle")     # 阶梯卡推演结算（幂等安静：无出票/未完赛/已结算均跳过）
        sh("corpus.py")
        sh("trend_report.py")
        sh("calibrate.py")
        sh("ablate.py")
        sh("temperature.py", "--check")   # 温度状态断言（T/CI/fittedAt 自检，缺文件警告不阻断）
    elif cmd == "learn":
        # 非fd联赛闭环：当年 espn history 增量采集 → --source local 拟合 → 版本发布
        # 例: python run.py learn / python run.py learn japan
        from datetime import date as _date
        year = str(_date.today().year)
        targets = rest or list(LOCAL_LEAGUES.values())
        by_name = {v: k for k, v in LOCAL_LEAGUES.items()}
        for league in targets:
            if league in SPORTTERY_LEAGUES:
                sh("sporttery_fetch.py", "league-results", league, year)
                sh("dc_fit.py", league, "--source", "local", "--publish")
                continue
            code = by_name.get(league)
            if not code:
                log("run", f"未知本地联赛 {league}（可用: {', '.join(LOCAL_LEAGUES.values()) + ', ' + ', '.join(SPORTTERY_LEAGUES)}）")
                continue
            sh("espn_fetch.py", "history", code, year)
            sh("dc_fit.py", league, "--source", "local", "--publish")
        sh("temperature.py")   # 语料/fd 增量后池级温度重拟（幂等：CI 不过落盘 T=1）
    elif cmd == "all":
        sh("odds_fetch.py", "--season", "2526", *LEAGUES)
        sh("odds_fetch.py", "--season", CURRENT_SEASON, *LEAGUES)
        sh("sporttery_fetch.py")
        sh("build_index.py")
        sh("league_profile.py", "--all")
        for _, (league, season) in LEAGUES.items():
            sh("dc_fit.py", league, season, "--auto")
        for code, league in LOCAL_LEAGUES.items():
            sh("espn_fetch.py", "history", code, str(int(CURRENT_SEASON[:2]) + 2000))
            sh("dc_fit.py", league, "--source", "local", "--publish")
        for league in SPORTTERY_LEAGUES:
            sh("sporttery_fetch.py", "league-results", league, str(int(CURRENT_SEASON[:2]) + 2000))
            sh("dc_fit.py", league, "--source", "local", "--publish")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
