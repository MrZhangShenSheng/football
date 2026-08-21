# 🏟️ Football — 竞彩足球预测知识库

本地足球知识图谱 + Dixon-Coles/Elo 统计模型 + 检索增强预测（Agentic RAG）+ 趋势挖掘引擎。
设计文档：`docs/2026-08-21-rag-design.html`

## 目录结构（= 四层架构）

```
football/
├── .claude/CLAUDE.md      ← ④ 记忆层：项目级约定，进入项目自动加载
├── data/                  ← ① 数据层（编号 = 数据生命周期顺序）
│   ├── 01-teams/          #    球队画像 JSON（Elo/xG/近况/伤停）+ _aliases.json 实体映射
│   ├── 02-results/        #    赛果回填（YYYY-MM-DD.json）+ _h2h_index.json
│   ├── 03-predictions/    #    预测报告 HTML（仅用户输出用 HTML+SVG）
│   ├── 04-summaries/      #    五维统计 _stats.json + 复盘 HTML
│   └── 05-trends/         #    趋势发现 JSON
├── engine/                ← ② 计算层（Python）
│   ├── scripts/           #    dc_fit / dc_predict / backtest / elo_fetch / xg_fetch / odds_fetch
│   └── cache/             #    DC 参数缓存（{league}_dc.json）+ fusion.json
├── skill/                 ← ③ 检索入口：SKILL.md（junction → ~/.claude/skills/）
└── docs/                  #    设计文档
```

## 使用方式

- 触发 `/football-betting-prediction` → 走 skill 全流程，产出自动归档到 `data/`
- 查询类问题（球队近况/历史统计）→ Claude 检索本地数据直接回答
- 赛后 → `data/02-results/` 自动回填赛果，`data/04-summaries/` 生成复盘（含 CLV）

## 核心约定（详见 .claude/CLAUDE.md）

- 概率锚 = Pinnacle 收盘价去水；体彩赔率仅用于可买性/奖金/CLV
- 内部文件 JSON/纯文本；仅用户报告用 HTML+内嵌 SVG
- 多数据源实体经 `data/01-teams/_aliases.json` 规范 ID 打通
