# Football 足球预测知识库

## 项目定位

本地足球知识图谱 + Dixon-Coles/Elo 统计模型 + 检索增强预测（Agentic RAG）+ 趋势挖掘引擎。
设计文档：`docs/2026-08-21-rag-design.html`（唯一权威版本）。

## 目录结构（= 四层架构）

- `data/01-teams/`: 球队画像 JSON（Elo/xG/近况/伤停/主客场/休息天数）+ `_aliases.json` 实体映射表 + `_index.json` 路由索引
- `data/02-results/`: 赛果回填 JSON（YYYY-MM-DD.json）+ `_h2h_index.json`（仅叙事参考）
- `data/03-predictions/`: 预测报告 HTML（仅用户输出用 HTML+SVG）
- `data/04-summaries/`: 五维统计 `_stats.json` + 复盘 HTML
- `data/05-trends/`: 趋势发现 JSON
- `engine/scripts/`: Python 脚本（dc_fit/dc_predict/elo_fetch/xg_fetch/odds_fetch/backtest/calibrate/build_index）
- `engine/cache/`: DC 参数缓存（{league}_dc.json）+ fusion.json 融合系数
- `skill/SKILL.md`: 预测 skill v4.1（junction 到 ~/.claude/skills/）
- `docs/`: 设计文档

## 检索铁律

1. 预测时先查本地 `data/01-teams/_index.json`，只搜本地缺的数据（联赛分组批量）
2. Dixon-Coles 缓存过期（新增比赛 ≥5 场）→ 调 `engine/scripts/dc_fit.py` 重拟合（ξ=0.005 默认）
3. 概率锚 = Pinnacle 收盘价去水；体彩赔率只用于可买性/奖金/CLV 计算
4. 融合：p_final = σ(0.4·logit(p_DC) + 1.0·logit(p_pinnacle))，系数存 engine/cache/fusion.json
5. H2H 交锋仅叙事参考，不进预测权重（学术验证预测力垫底）
6. 多源球队名经 `data/01-teams/_aliases.json` 规范 ID 解析；球队文件名用英文规范 ID（如 lech-poznan.json）
7. 读取本地文件先校验 lastUpdated；过期数据刷新或在报告标注"数据截至 X 日"
8. 索引文件只做路由表（一行一条目，不放内容本体）；索引可由 build_index.py 重建，glob 永远可兜底
9. 内部文件 JSON/纯文本，禁止 HTML/SVG；仅用户报告用 HTML+内嵌 SVG
10. 报告附数据来源清单（引用了哪些本地文件），供忠实度抽查

## 数据源（2026-08-21 实测可用性）

| 源 | 状态 | 用途 |
|:---|:---|:---|
| 体彩官方 API | ✅ WebFetch 可用 | 赛程 + 赔率 |
| football-data.co.uk | ✅ requests 直连（engine/scripts/odds_fetch.py） | **Pinnacle 收盘价（PPCH/PPCD/PPCA）+ B365 收盘 + xG（HxG/AxG）+ 比分**，主流联赛当季 CSV |
| ESPN | ✅ WebFetch 可用 | 赛果、排名 |
| clubelo.com | ❌ 本机+服务端均不可达（elo_fetch.py 已就绪，网络恢复即用） | Elo |
| understat.com | ⚠️ 2026 赛季数据未开（xg_fetch.py 备选） | xG（主链路已走 fd CSV） |
| 搜索引擎 | ❌ 配额耗尽至 8-31 | 补充检索 |

> fd CSV 覆盖：英西德意法荷比葡土希俄主流联赛；沙特/日职/北欧/南美不覆盖（此类场次预测时 Elo/xG/收盘价标锚缺失）。

## 评估铁律

- 主指标：CLV（逐单算：出票赔率/Pinnacle 收盘赔率 - 1）+ RPS + log loss
- 命中率仅作展示；修正系数须消融回测，无正增益即删（首回合平局保护=首批验证对象）
- 回测一律 walk-forward，成交按 Pinnacle 收盘价计
- 检索覆盖率：每轮记录"本地命中 vs 联网补采"字段清单，发现知识库采集洞

## 升级触发线（现在不做）

- `data/02-results/` 超 500 个日期文件（约两赛季）→ 迁移 SQLite FTS5（队名+日期索引），其余目录保持文件
