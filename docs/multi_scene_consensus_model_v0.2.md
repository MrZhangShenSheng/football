# 多场景博弈比分预测模型 —— 详细设计文档

> 版本：v0.2 | 日期：2026-09-30 | 作者：sszhang
> 
> 基于v0.1框架，细化到可实现级别

---

## 一、数据盘点

### 1.1 当前可用数据（来自 TeamStats）

| 数据项 | 字段 | 说明 |
|--------|------|------|
| 场次数 | `n` | 历史总场次 |
| 总进球 | `gf` | 累计进球数 |
| 总失球 | `ga` | 累计失球数 |
| 胜场数 | `win` | 累计胜场 |
| 净胜球 | `gd` | 累计净胜球 |
| 零封场 | `cs` | 零封对手场次（clean sheet）|
| 被零封场 | `becs` | 被对手零封场次 |
| BTTS场 | `btts` | 双方都进球场次 |
| 大球场 | `over25` | 总进球>2.5场次 |
| 主场进球 | `gf_side[0]` | 主场累计进球 |
| 客场进球 | `gf_side[1]` | 客场累计进球 |
| 近3场净胜 | `recent_gd` | 最近3场净胜球列表 |
| 近10场明细 | `recent` | (对手, 进球, 失球) 元组列表 |

### 1.2 可计算的派生指标

| 指标 | 计算方式 | 用途 |
|------|----------|------|
| 场均进球 | `gf / n` | 攻击力 |
| 场均失球 | `ga / n` | 防守力 |
| 胜率 | `win / n` | 整体实力 |
| 零封率 | `cs / n` | 防守稳定性 |
| 被零封率 | `becs / n` | 进攻稳定性 |
| BTTS率 | `btts / n` | 比赛开放度 |
| 大球率 | `over25 / n` | 进球倾向 |
| 近期动量 | `mean(recent_gd) - gd/n` | 状态变化 |
| 进球方差 | 从recent计算 | 稳定性 |

### 1.3 暂缺数据（后续可扩展）

| 数据 | 用途 | 采集难度 |
|------|------|----------|
| H2H历史 | 交锋规律 | 中（需关联查询） |
| 半场比分 | 半场预测 | 高（需新数据源） |
| xG数据 | 真实攻防能力 | 高（付费数据） |
| 伤停情况 | 阵容影响 | 已有（但市场已定价） |
| 赛程密度 | 疲劳因素 | 中（可计算） |

---

## 二、场景详细定义

### 场景 S1：攻防强度对比

**目标**：判断双方攻防力量对比，预测进球分布

**输入数据**：
```python
# 主队
home_gf_avg = home.gf / home.n          # 场均进球
home_ga_avg = home.ga / home.n          # 场均失球
home_gf_home = home.gf_side[0][0] / max(home.gf_side[0][1], 1)  # 主场场均进球

# 客队
away_gf_avg = away.gf / away.n
away_ga_avg = away.ga / away.n
away_gf_away = away.gf_side[1][0] / max(away.gf_side[1][1], 1)  # 客场场均进球
```

**判断规则**：

| 条件 | 判断 | 暗示 |
|------|------|------|
| `home_gf_home > 1.8` AND `away_ga_avg > 1.5` | 主攻锋利 | 主队2+球 |
| `away_gf_away > 1.5` AND `home_ga_avg > 1.5` | 客攻有威胁 | 客队1+球 |
| `home_gf_avg < 1.0` AND `away_gf_avg < 1.0` | 双方进攻疲软 | 总进球≤2 |
| `home_ga_avg < 0.8` AND `away_ga_avg < 0.8` | 双方防守稳固 | 总进球≤2 |
| `home_gf_avg > 1.8` AND `away_gf_avg > 1.8` AND `home_ga_avg > 1.3` AND `away_ga_avg > 1.3` | 双方互爆型 | 总进球4+ |

**输出结构**：
```python
{
    "scene": "S1_攻防强度",
    "judgment": "主攻压制" | "客攻有威胁" | "双方低迷" | "双方互爆" | "均衡",
    "confidence": "high" | "medium" | "low",
    "home_goal_hint": "2+" | "1-2" | "0-1" | None,
    "away_goal_hint": "2+" | "1-2" | "0-1" | None,
    "total_goal_hint": "4+" | "2-3" | "0-2" | None,
    "evidence": ["主场场均进球1.9", "客队客场失球1.8"]
}
```

---

### 场景 S2：近期状态

**目标**：判断双方近期走势，识别状态差异

**输入数据**：
```python
# 近3场净胜球
home_recent_gd = home.recent_gd  # 列表，如 [2, -1, 1]
away_recent_gd = away.recent_gd

# 近3场详细（从recent取最后3场）
home_recent3 = home.recent[-3:] if len(home.recent) >= 3 else home.recent
away_recent3 = away.recent[-3:] if len(away.recent) >= 3 else away.recent
```

**派生计算**：
```python
def calc_form_score(recent3):
    """近3场状态分：胜+3 平+1 负+0"""
    score = 0
    for opp, scored, conceded in recent3:
        if scored > conceded:
            score += 3
        elif scored == conceded:
            score += 1
    return score

def calc_goal_trend(recent3):
    """进球趋势：递增/递减/平稳"""
    if len(recent3) < 2:
        return "unknown"
    goals = [r[1] for r in recent3]
    if goals[-1] > goals[0] + 0.5:
        return "rising"
    elif goals[-1] < goals[0] - 0.5:
        return "falling"
    return "stable"
```

**判断规则**：

| 主队状态分 | 客队状态分 | 判断 | 暗示 |
|------------|------------|------|------|
| ≥7 | ≤3 | 主强客弱 | 主胜概率高 |
| ≤3 | ≥7 | 客强主弱 | 客胜概率高（冷门信号）|
| ≤3 | ≤3 | 双方低迷 | 闷平可能 |
| ≥7 | ≥7 | 双方强势 | 对攻，大球可能 |
| 4-6 | 4-6 | 状态接近 | 难以判断 |

**输出结构**：
```python
{
    "scene": "S2_近期状态",
    "judgment": "主强客弱" | "客强主弱" | "双方低迷" | "双方强势" | "状态接近",
    "confidence": "high" | "medium" | "low",
    "result_hint": "home_win" | "away_win" | "draw" | None,
    "evidence": ["主队近3场2胜1平得7分", "客队近3场3连败得0分"]
}
```

---

### 场景 S3：主客场特性

**目标**：评估主场优势/劣势程度

**输入数据**：
```python
# 主队主场数据
home_home_gf = home.gf_side[0][0] / max(home.gf_side[0][1], 1)
home_home_ga = home.ga_side[0][0] / max(home.ga_side[0][1], 1)
home_home_games = home.gf_side[0][1]

# 客队客场数据
away_away_gf = away.gf_side[1][0] / max(away.gf_side[1][1], 1)
away_away_ga = away.ga_side[1][0] / max(away.ga_side[1][1], 1)
away_away_games = away.gf_side[1][1]

# 主客场差异
home_diff = home_home_gf - home.gf_side[1][0] / max(home.gf_side[1][1], 1)  # 主场比客场多进多少
away_diff = away_away_gf - away.gf_side[0][0] / max(away.gf_side[0][1], 1)  # 客场比主场多进多少
```

**判断规则**：

| 条件 | 判断 | 暗示 |
|------|------|------|
| `home_diff > 0.5` AND `away_diff < -0.3` | 主场龙客场虫 | 主场优势大 |
| `home_diff < 0` AND `away_diff > 0.3` | 反向特性 | 客队不惧客场 |
| `home_home_gf > 2.0` AND `home_home_ga < 1.0` | 主场堡垒 | 主队难被攻破 |
| `away_away_gf > 1.5` AND `away_away_ga < 1.2` | 客场强龙 | 客队有客场战斗力 |

---

### 场景 S4：进球模式（核心场景）

**目标**：识别特殊进球模式，捕捉高赔事件

**输入数据**：
```python
# 零封相关
home_cs_rate = home.cs / home.n    # 零封率
home_becs_rate = home.becs / home.n  # 被零封率
away_cs_rate = away.cs / away.n
away_becs_rate = away.becs / away.n

# 大球相关
home_over25_rate = home.over25 / home.n
away_over25_rate = away.over25 / away.n

# BTTS相关
home_btts_rate = home.btts / home.n
away_btts_rate = away.btts / away.n
```

**判断规则（重点！）**：

| 条件 | 判断 | 目标比分 | 平均赔率 |
|------|------|----------|----------|
| `home_becs_rate > 0.35` AND `away_becs_rate > 0.35` AND `home_cs_rate > 0.30` AND `away_cs_rate > 0.30` | **零零信号** | 0:0 | ~13 |
| `home_over25_rate > 0.60` AND `away_over25_rate > 0.60` AND `home_btts_rate > 0.55` AND `away_btts_rate > 0.55` | **互爆信号** | 3:2, 2:3, 3:3 | 25-55 |
| `home_cs_rate > 0.45` AND `away_becs_rate > 0.40` | **主零封信号** | 2:0, 3:0 | 10-15 |
| `away_cs_rate > 0.45` AND `home_becs_rate > 0.40` | **客零封信号** | 0:2, 0:3 | 15-30 |

**输出结构**：
```python
{
    "scene": "S4_进球模式",
    "judgment": "零零倾向" | "互爆倾向" | "主零封" | "客零封" | "常规",
    "confidence": "high" | "medium" | "low",
    "score_hint": [(0,0)] | [(3,2), (2,3)] | [(2,0), (3,0)] | ...,
    "total_goal_hint": "0" | "4+" | "2-3" | None,
    "evidence": ["主队被零封率38%", "客队被零封率42%", "双方零封率均>30%"]
}
```

---

### 场景 S5：防守稳定性

**目标**：识别防守脆弱配对

**输入数据**：
```python
# 从recent计算失球方差
def calc_ga_variance(recent):
    if len(recent) < 3:
        return 0
    ga_list = [r[2] for r in recent]
    mean_ga = sum(ga_list) / len(ga_list)
    variance = sum((g - mean_ga) ** 2 for g in ga_list) / len(ga_list)
    return variance

home_ga_var = calc_ga_variance(home.recent)
away_ga_var = calc_ga_variance(away.recent)

# 失球稳定性指标
home_defensive_fragility = home.ga / home.n + home_ga_var * 0.3
away_defensive_fragility = away.ga / away.n + away_ga_var * 0.3
```

**判断规则**：

| 条件 | 判断 | 暗示 |
|------|------|------|
| `home_defensive_fragility > 1.8` AND `away_defensive_fragility > 1.8` | 双方脆弱 | 大比分可能 |
| `home_defensive_fragility < 0.8` AND `away_defensive_fragility < 0.8` | 双方稳固 | 小球可能 |
| `home_defensive_fragility > 2.0` AND `away_defensive_fragility < 1.0` | 主守脆弱 | 客队多进球 |

---

### 场景 S6：比赛节奏预测

**目标**：预测比赛的开放程度

**输入数据**：
```python
# BTTS率反映比赛开放度
combined_btts = (home_btts_rate + away_btts_rate) / 2

# 大球率
combined_over25 = (home_over25_rate + away_over25_rate) / 2

# 进球总量预期
expected_goals = home.gf / home.n + away.gf / away.n
```

**判断规则**：

| 条件 | 判断 | 暗示 |
|------|------|------|
| `combined_btts > 0.6` AND `combined_over25 > 0.6` | 开放型 | 双方进球，大球 |
| `combined_btts < 0.4` AND `combined_over25 < 0.4` | 保守型 | 小球，可能0:0 |
| `expected_goals < 2.0` | 低产对决 | 总进球≤2 |
| `expected_goals > 3.5` | 高产对决 | 总进球≥3 |

---

## 三、博弈融合规则

### 3.1 场景暗示标准化

每个场景输出映射到**标准暗示集**：

```python
HINT_CATEGORIES = {
    "total_goals": ["0", "1", "2", "3", "4+"],
    "home_goals": ["0", "1", "2", "3+"],
    "away_goals": ["0", "1", "2", "3+"],
    "result": ["home_win", "draw", "away_win"],
    "score_pattern": ["low_score", "medium_score", "high_score"],
    "special_score": ["0:0", "1:1", "2:2", "big_score"]
}
```

### 3.2 一致性计算

```python
def calculate_consensus(scene_outputs):
    """计算多场景一致性"""
    
    # 收集所有暗示
    hints_by_category = defaultdict(list)
    for so in scene_outputs:
        if so.get("total_goal_hint"):
            hints_by_category["total_goals"].append(so["total_goal_hint"])
        if so.get("result_hint"):
            hints_by_category["result"].append(so["result_hint"])
        if so.get("score_hint"):
            hints_by_category["special_score"].extend(so["score_hint"])
    
    # 计算每个类别的一致性
    consensus = {}
    for category, hints in hints_by_category.items():
        if not hints:
            continue
        counter = Counter(hints)
        most_common, count = counter.most_common(1)[0]
        total = len(hints)
        consensus[category] = {
            "value": most_common,
            "support": count,
            "total": total,
            "ratio": count / total
        }
    
    return consensus
```

### 3.3 下注决策规则

```python
def make_bet_decision(consensus, min_support=3, min_ratio=0.6):
    """根据一致性决定是否下注"""
    
    decisions = []
    
    # 规则1：0:0 特殊判断
    if "special_score" in consensus:
        sc = consensus["special_score"]
        if sc["value"] == "0:0" and sc["support"] >= 3 and sc["ratio"] >= 0.5:
            decisions.append({
                "bet_type": "correct_score",
                "selection": (0, 0),
                "confidence": "high" if sc["support"] >= 4 else "medium",
                "reason": f"{sc['support']}/{sc['total']}场景支持0:0"
            })
    
    # 规则2：大比分判断
    if "total_goals" in consensus:
        tg = consensus["total_goals"]
        if tg["value"] == "4+" and tg["support"] >= 3:
            decisions.append({
                "bet_type": "total_goals",
                "selection": "over_3.5",
                "confidence": "high" if tg["support"] >= 4 else "medium"
            })
        elif tg["value"] in ["0", "1"] and tg["support"] >= 3:
            decisions.append({
                "bet_type": "total_goals", 
                "selection": "under_1.5",
                "confidence": "high" if tg["support"] >= 4 else "medium"
            })
    
    # 规则3：无一致性则跳过
    if not decisions:
        return {"action": "skip", "reason": "场景无一致性"}
    
    return {"action": "bet", "decisions": decisions}
```

---

## 四、目标市场优先级

### 4.1 Phase 1：0:0 预测器

**为什么先做0:0**：
- 平均赔率 ~13，有价值
- 出现频率 6.5%（330/4979场）
- 有清晰的触发条件（双方低产+双方防守好）

**触发条件组合**：
```
S1: 双方进攻疲软 (home_gf < 1.0 AND away_gf < 1.0)
AND S4: 零零倾向 (home_becs_rate > 0.30 AND away_becs_rate > 0.30)
AND S5: 双方稳固 (home_ga_avg < 1.0 AND away_ga_avg < 1.0)
AND S6: 保守型 (combined_btts < 0.45)
```

### 4.2 Phase 2：大比分预测器

**触发条件组合**：
```
S1: 双方互爆型 (攻击力都强 AND 防守都弱)
AND S4: 互爆信号 (over25 > 0.6 AND btts > 0.55)
AND S5: 双方脆弱 (defensive_fragility > 1.8)
AND S6: 开放型 (combined_btts > 0.6)
```

### 4.3 Phase 3：冷门客胜

**触发条件组合**：
```
S2: 客强主弱 (客队状态好 AND 主队状态差)
AND S3: 主场优势弱 (主队主客场差异小)
AND S5: 主守脆弱 (home_defensive_fragility > 1.5)
```

---

## 五、实现计划

### 5.1 代码结构

```
engine/scripts/research/
├── multi_scene_model.py          # 主模块
│   ├── class SceneAnalyzer       # 场景分析器基类
│   ├── class S1_OffenseDefense   # 攻防对比场景
│   ├── class S2_RecentForm       # 近期状态场景
│   ├── class S3_HomeAway         # 主客场特性场景
│   ├── class S4_ScoringPattern   # 进球模式场景
│   ├── class S5_DefenseStability # 防守稳定性场景
│   ├── class S6_MatchTempo       # 比赛节奏场景
│   ├── class ConsensusEngine     # 博弈融合引擎
│   └── class BetDecisionMaker    # 下注决策器
│
├── multi_scene_backtest.py       # 回测验证
└── multi_scene_production.py     # 生产脚本
```

### 5.2 开发顺序

1. **Week 1**：实现6个场景分析器 + 单元测试
2. **Week 2**：实现博弈融合引擎 + 0:0预测器
3. **Week 3**：历史回测验证 + 参数调优
4. **Week 4**：大比分预测器 + 综合评估

### 5.3 验证指标

| 指标 | 目标 |
|------|------|
| 0:0 预测精确率 | > 10%（vs 随机6.5%）|
| 0:0 召回率 | > 20%（识别出20%的0:0场次）|
| 信号触发率 | 5%-15%（不能太频繁也不能太稀疏）|
| ROI（假设下注）| > 0%（正期望）|

---

## 六、待确认问题

1. **阈值确定**：各场景的判断阈值需要通过历史数据校准
2. **场景权重**：是否所有场景权重相同，还是S4（进球模式）权重更高？
3. **置信度映射**：high/medium/low 如何量化？
4. **回测方式**：逐日滚动 or 固定切分点？
5. **目标市场**：先做竞彩比分还是亚盘大小球？

---

*待主公确认后开始实现*
