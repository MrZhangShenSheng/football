# 多场景博弈比分预测模型设计方案

> 版本：v0.1 | 日期：2026-09-30 | 作者：sszhang
> 
> 设计理念：**不用概率预测，用多场景共识博弈**

---

## 一、设计理念

### 1.1 为什么不用纯概率模型

| 问题 | 说明 |
|------|------|
| 市场已定价 | 公开特征（进球率、胜率等）市场已充分定价 |
| 抽水存在 | 约13%抽水，概率模型无法弥补 |
| 预测集中 | 概率模型只会预测常见低赔比分 |
| 无冷门能力 | 高赔率事件完全无法捕捉 |

### 1.2 核心假设

**不同结果来自不同机制**（参考 [Bayesian Outcome-Specific Ensemble](https://www.frontiersin.org/articles/10.3389/fams.2026.1754408)）：

- 主胜：主队压制性优势
- 平局：双方竞争均衡
- 客胜：客队超预期表现
- **冷门**：特殊场景触发（本模型重点）

### 1.3 博弈一致性原则

参考 [专家预测聚合算法](http://arxiv.org/pdf/1206.6814.pdf) 的核心思想：

```
多个独立视角 → 各自判断 → 一致时才是强信号
```

---

## 二、多场景框架

### 2.1 场景定义

每个场景是一个**独立的判断视角**，输出**定性结论**而非概率：

| 场景ID | 场景名称 | 核心问题 | 输出类型 |
|--------|----------|----------|----------|
| S1 | 攻防对比 | 谁的攻防更强？ | 强/弱/均衡 |
| S2 | 近期状态 | 谁的状态更好？ | 上升/平稳/下降 |
| S3 | 主客场特性 | 主场优势多大？ | 强/一般/弱/反转 |
| S4 | 风格匹配 | 风格是否克制？ | 克制/被克/中性 |
| S5 | 历史交锋 | H2H规律如何？ | 主优/客优/无规律 |
| S6 | 进球特征 | 双方进球模式？ | 高产/低产/不稳定 |
| S7 | 防守特征 | 双方防守模式？ | 稳固/脆弱/波动 |
| S8 | 比赛重要性 | 动机强度？ | 强/弱/不对称 |

### 2.2 场景输出标准化

每个场景输出**标准化的场景判断**：

```python
class SceneOutput:
    scene_id: str           # 场景ID
    judgment: str           # 判断结论（枚举值）
    confidence: str         # 置信度：high/medium/low
    evidence: List[str]     # 证据列表
    implication: Dict       # 对结果的暗示
```

**暗示映射示例**：

| 场景判断 | 对比分的暗示 |
|----------|--------------|
| S1:主攻强客守弱 | 主队多进球（2+） |
| S6:双方低产 | 总进球少（0-1） |
| S7:双方脆弱 | 可能大比分 |
| S2:双方下降 | 可能闷平 |

---

## 三、场景详细规则

### 3.1 场景S1：攻防对比

**输入特征**：
- 主队：场均进球、场均失球、近5场进球、近5场失球
- 客队：场均进球、场均失球、近5场进球、近5场失球

**判断规则**：

```python
def scene_s1_offense_defense(home, away):
    # 攻击力对比
    home_attack = home.gf_avg * 0.6 + home.gf_recent5 / 5 * 0.4
    away_attack = away.gf_avg * 0.6 + away.gf_recent5 / 5 * 0.4
    
    # 防守力对比
    home_defense = home.ga_avg * 0.6 + home.ga_recent5 / 5 * 0.4
    away_defense = away.ga_avg * 0.6 + away.ga_recent5 / 5 * 0.4
    
    # 主队攻击 vs 客队防守
    home_vs_away_def = home_attack - away_defense
    # 客队攻击 vs 主队防守
    away_vs_home_def = away_attack - home_defense
    
    # 判断逻辑
    if home_vs_away_def > 0.5 and away_vs_home_def < 0:
        return {"judgment": "主攻压制", "implication": {"home_goals": "2+", "away_goals": "0-1"}}
    elif away_vs_home_def > 0.5 and home_vs_away_def < 0:
        return {"judgment": "客攻压制", "implication": {"home_goals": "0-1", "away_goals": "2+"}}
    elif home_vs_away_def > 0.3 and away_vs_home_def > 0.3:
        return {"judgment": "双方互爆", "implication": {"total_goals": "4+"}}
    elif home_vs_away_def < -0.3 and away_vs_home_def < -0.3:
        return {"judgment": "双方低迷", "implication": {"total_goals": "0-1"}}
    else:
        return {"judgment": "均衡", "implication": {}}
```

### 3.2 场景S2：近期状态

**输入特征**：
- 近3场胜平负
- 近3场进球/失球趋势
- 连胜/连败/连平

**判断规则**：

```python
def scene_s2_recent_form(home, away):
    def form_score(team):
        # 近3场：胜+3 平+1 负+0
        points = sum([3 if r == 'W' else 1 if r == 'D' else 0 for r in team.recent3])
        # 进球趋势：递增/递减
        goal_trend = team.recent3_goals[-1] - team.recent3_goals[0]
        return points + goal_trend * 0.5
    
    home_form = form_score(home)
    away_form = form_score(away)
    
    if home_form >= 8 and away_form <= 3:
        return {"judgment": "主强客弱", "confidence": "high"}
    elif away_form >= 8 and home_form <= 3:
        return {"judgment": "客强主弱", "confidence": "high"}
    elif home_form <= 3 and away_form <= 3:
        return {"judgment": "双方低迷", "implication": {"total_goals": "low", "draw": "likely"}}
    # ...
```

### 3.3 场景S6：进球特征（重点场景）

**目标**：识别低比分（0:0）和高比分场景

**输入特征**：
- 场均进球
- 进球场次占比（零封率）
- 大球率（>2.5球占比）
- BTTS率（双方都进球占比）

**判断规则**：

```python
def scene_s6_scoring_pattern(home, away):
    # 零封倾向
    home_clean_sheet_rate = home.clean_sheet_rate  # 零封对手比例
    away_clean_sheet_rate = away.clean_sheet_rate
    
    # 被零封倾向
    home_blanked_rate = home.blanked_rate  # 被零封比例
    away_blanked_rate = away.blanked_rate
    
    # 0:0 信号
    zero_zero_signal = (
        home_blanked_rate > 0.3 and away_blanked_rate > 0.3 and
        home_clean_sheet_rate > 0.3 and away_clean_sheet_rate > 0.3
    )
    
    # 大比分信号
    high_score_signal = (
        home.over25_rate > 0.6 and away.over25_rate > 0.6 and
        home.btts_rate > 0.5 and away.btts_rate > 0.5
    )
    
    if zero_zero_signal:
        return {"judgment": "零球倾向", "implication": {"score": "(0,0)", "confidence": "medium"}}
    elif high_score_signal:
        return {"judgment": "互爆倾向", "implication": {"total_goals": "4+"}}
    # ...
```

### 3.4 场景S7：防守特征

**目标**：识别防守脆弱配对（容易出大比分）

```python
def scene_s7_defense_pattern(home, away):
    # 防守脆弱度
    home_defensive_fragility = home.ga_avg + home.ga_variance * 0.5
    away_defensive_fragility = away.ga_avg + away.ga_variance * 0.5
    
    # 双方都脆弱
    if home_defensive_fragility > 1.8 and away_defensive_fragility > 1.8:
        return {"judgment": "双脆弱", "implication": {"total_goals": "4+", "score_pattern": "high"}}
    
    # 一方脆弱
    if home_defensive_fragility > 2.0 and away_defensive_fragility < 1.2:
        return {"judgment": "主守脆弱", "implication": {"away_goals": "2+"}}
    # ...
```

---

## 四、博弈一致性判断

### 4.1 一致性定义

参考 [Dempster-Shafer证据理论](https://arxiv.org/html/1704.04000v1)，定义**证据融合规则**：

**场景暗示转换为证据质量函数**：

```python
def scene_to_belief(scene_output):
    """将场景输出转换为对各比分模式的belief"""
    belief = {}
    
    if scene_output.implication.get("total_goals") == "0-1":
        belief["low_score"] = 0.3 if scene_output.confidence == "high" else 0.15
    
    if scene_output.implication.get("score") == "(0,0)":
        belief["0:0"] = 0.2 if scene_output.confidence == "high" else 0.1
    
    # ...
    return belief
```

### 4.2 Dempster组合规则

```python
def dempster_combine(belief1, belief2):
    """Dempster规则融合两个证据"""
    combined = {}
    conflict = 0
    
    for h1, m1 in belief1.items():
        for h2, m2 in belief2.items():
            if h1 == h2:
                combined[h1] = combined.get(h1, 0) + m1 * m2
            else:
                conflict += m1 * m2
    
    # 归一化
    if conflict < 1:
        for h in combined:
            combined[h] /= (1 - conflict)
    
    return combined, conflict
```

### 4.3 一致性阈值

```python
def check_consensus(all_scene_outputs):
    """检查多场景是否达成一致"""
    
    # 收集所有场景的暗示
    implications = [s.implication for s in all_scene_outputs if s.implication]
    
    # 统计各暗示出现次数
    counter = Counter()
    for imp in implications:
        for key, value in imp.items():
            counter[(key, value)] += 1
    
    # 检查是否有强一致
    total_scenes = len(all_scene_outputs)
    consensus_results = []
    
    for (key, value), count in counter.items():
        consensus_ratio = count / total_scenes
        if consensus_ratio >= 0.6:  # 60%以上场景一致
            consensus_results.append({
                "dimension": key,
                "value": value,
                "consensus_ratio": consensus_ratio,
                "strength": "strong" if consensus_ratio >= 0.8 else "medium"
            })
    
    return consensus_results
```

---

## 五、比分映射规则

### 5.1 从一致性结论到具体比分

| 一致性结论 | 映射比分 | 赔率范围 |
|------------|----------|----------|
| 总进球:0-1 + 双方低迷 | 0:0, 1:0, 0:1 | 中-高赔 |
| 总进球:4+ + 双脆弱 | 3:2, 2:3, 3:1, 4:1 | 高赔 |
| 主攻压制 + 主状态强 | 2:0, 3:0, 3:1 | 中赔 |
| 零球倾向 强信号 | 0:0 | 高赔(~13) |

### 5.2 置信度分级

```python
def map_to_scores(consensus_results):
    """将一致性结论映射到具体比分"""
    
    predictions = []
    
    for cr in consensus_results:
        if cr["dimension"] == "total_goals" and cr["value"] == "0-1":
            if cr["strength"] == "strong":
                predictions.append({"score": (0, 0), "confidence": "high"})
                predictions.append({"score": (1, 0), "confidence": "medium"})
                predictions.append({"score": (0, 1), "confidence": "medium"})
        
        elif cr["dimension"] == "total_goals" and cr["value"] == "4+":
            # 需要结合其他维度判断具体比分
            # ...
    
    return predictions
```

---

## 六、下注决策规则

### 6.1 下注条件（必须同时满足）

| 条件 | 阈值 | 说明 |
|------|------|------|
| 场景一致率 | ≥60% | 至少60%场景支持同一结论 |
| 强信号场景数 | ≥2 | 至少2个场景给出高置信度 |
| 证据冲突度 | <0.3 | Dempster组合的冲突因子 |
| 赔率价值 | >1.0 | 隐含概率 vs 估计概率 |

### 6.2 注码分配

```python
def determine_stake(consensus, odds):
    """根据一致性强度和赔率决定注码"""
    
    if consensus.strength == "strong" and consensus.ratio >= 0.8:
        base_stake = 3  # 强信号：3单位
    elif consensus.strength == "medium":
        base_stake = 2  # 中等信号：2单位
    else:
        base_stake = 1  # 弱信号：1单位
    
    # 赔率调整
    if odds > 20:
        stake = base_stake * 0.5  # 高赔降注
    elif odds < 8:
        stake = base_stake * 1.5  # 低赔加注
    else:
        stake = base_stake
    
    return stake
```

---

## 七、与现有框架对比

| 维度 | 现有族模型 | 多场景博弈模型 |
|------|------------|----------------|
| 输入 | 特征向量 | 多维度场景判断 |
| 过程 | softmax概率 | 规则推理+证据融合 |
| 输出 | 概率分布 | 一致性结论 |
| 下注触发 | gap最大 | **场景一致** |
| 目标比分 | 常见低赔 | **特定高赔** |
| 优势场景 | 常规比赛 | **冷门场景** |

---

## 八、实现路线图

### Phase 1：场景定义与数据准备
- [ ] 定义8个核心场景的完整规则
- [ ] 整理每个场景需要的特征数据
- [ ] 建立场景判断的测试用例

### Phase 2：单场景验证
- [ ] 逐个场景回测：判断准确率
- [ ] 识别无效场景，剔除或修正
- [ ] 确定每个场景的有效阈值

### Phase 3：博弈融合验证
- [ ] 实现Dempster证据融合
- [ ] 测试不同一致性阈值
- [ ] 验证"一致时才下注"的有效性

### Phase 4：比分映射验证
- [ ] 验证一致性结论到比分的映射准确率
- [ ] 重点测试高赔比分（0:0, 大比分）的预测能力

### Phase 5：实盘验证
- [ ] 影子投注记录
- [ ] 与现有族模型对比
- [ ] 收益率评估

---

## 九、参考文献

1. [Bayesian Outcome-Specific Ensemble](https://www.frontiersin.org/articles/10.3389/fams.2026.1754408) - 结果专属子模型思想
2. [Predicting Football with Kelly Index](https://arxiv.org/abs/2211.15734) - 按难度分层建模
3. [Expert Prediction Aggregation](http://arxiv.org/pdf/1206.6814.pdf) - 专家预测聚合算法
4. [Dempster-Shafer Theory](https://arxiv.org/html/1704.04000v1) - 证据融合理论
5. [Dixon-Coles + Elo Model](https://github.com/Hicruben/world-cup-2026-prediction-model) - Elo评分+泊松修正
6. [Machine Learning in Football Betting](https://www.mdpi.com/2076-3417/10/1/46/html) - 集成方法最佳实践

---

## 十、待讨论问题

1. **场景权重**：不同场景对最终结论的权重是否应该不同？
2. **场景依赖**：部分场景可能有相关性，如何处理？
3. **时效性**：近期数据 vs 长期数据的权衡？
4. **比赛类型**：是否需要区分联赛/杯赛/友谊赛？
5. **数据源**：除了比分，是否需要引入xG、射门等数据？

---

*待主公审阅后进入实现阶段*
