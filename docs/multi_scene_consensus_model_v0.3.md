# 多场景博弈比分预测模型 —— 实现版 v0.3

> 版本：v0.3 | 日期：2026-09-30 | 作者：sszhang
> 
> 基于主公确认：阈值先用常规值后续调优、所有比分并行预测、场景需要权重、目标市场=体彩竞彩比分+总进球数

---

## 一、设计调整

### 1.1 核心变化

| 项目 | v0.2 | v0.3 |
|------|------|------|
| 目标比分 | 先0:0再扩展 | **所有比分并行** |
| 场景权重 | 等权 | **可配置权重** |
| 阈值策略 | 待定 | **先用常规值，迭代优化** |
| 目标市场 | 未定 | **体彩竞彩比分+总进球数** |

### 1.2 比分分组

将31种比分按特征分组，每组有对应的场景触发条件：

| 组别 | 比分 | 特征 | 核心触发场景 |
|------|------|------|--------------|
| **零球组** | 0:0 | 双方都不进球 | S4零封+S6保守 |
| **主零封组** | 1:0, 2:0, 3:0 | 主胜且零封 | S1主攻强+S4主零封 |
| **客零封组** | 0:1, 0:2, 0:3 | 客胜且零封 | S1客攻强+S4客零封 |
| **小平组** | 1:1 | 常见平局 | S1均衡+S2状态接近 |
| **大平组** | 2:2, 3:3 | 高比分平局 | S4互爆+S5双脆弱 |
| **主多球组** | 2:1, 3:1, 3:2 | 主胜不零封 | S1主强+S6开放 |
| **客多球组** | 1:2, 1:3, 2:3 | 客胜不零封 | S1客强+S6开放 |
| **大比分组** | 4:0, 4:1, 4:2... | 单方大胜 | S5单方脆弱 |

---

## 二、场景权重设计

### 2.1 权重矩阵

不同比分组对应不同的场景权重：

```python
SCENE_WEIGHTS = {
    # 场景权重 [S1攻防, S2状态, S3主客, S4模式, S5防守, S6节奏]
    "zero_zero":     [0.15, 0.10, 0.05, 0.35, 0.20, 0.15],  # 0:0 重点看S4模式
    "home_clean":    [0.25, 0.15, 0.15, 0.20, 0.15, 0.10],  # 主零封 重点看S1攻防
    "away_clean":    [0.25, 0.15, 0.10, 0.20, 0.20, 0.10],  # 客零封 重点看S1+S5
    "small_draw":    [0.20, 0.25, 0.10, 0.15, 0.15, 0.15],  # 小平 重点看S2状态
    "big_draw":      [0.15, 0.10, 0.05, 0.30, 0.25, 0.15],  # 大平 重点看S4+S5
    "home_multi":    [0.25, 0.20, 0.15, 0.15, 0.10, 0.15],  # 主多球
    "away_multi":    [0.25, 0.20, 0.10, 0.15, 0.15, 0.15],  # 客多球
    "big_score":     [0.20, 0.10, 0.05, 0.25, 0.30, 0.10],  # 大比分 重点看S5防守
}
```

### 2.2 权重计算逻辑

```python
def calculate_score_signal(scene_outputs, score, weights):
    """计算某比分的综合信号强度"""
    score_group = get_score_group(score)
    w = weights.get(score_group, [1/6]*6)
    
    total_signal = 0
    for i, scene in enumerate(scene_outputs):
        # 场景支持度：-1（反对）到 +1（支持）
        support = scene.get_support_for_score(score)
        total_signal += w[i] * support
    
    return total_signal  # -1 到 +1
```

---

## 三、常规阈值设定

### 3.1 攻防指标阈值

| 指标 | 低 | 中 | 高 | 数据来源 |
|------|:--:|:--:|:--:|----------|
| 场均进球 | <1.0 | 1.0-1.8 | >1.8 | 联赛平均约1.3-1.5 |
| 场均失球 | <0.8 | 0.8-1.5 | >1.5 | 联赛平均约1.3-1.5 |
| 胜率 | <30% | 30%-50% | >50% | |
| 零封率 | <20% | 20%-35% | >35% | 强队约30-40% |
| 被零封率 | <20% | 20%-35% | >35% | 弱队约30-40% |
| BTTS率 | <40% | 40%-55% | >55% | 联赛平均约50% |
| 大球率 | <40% | 40%-55% | >55% | 联赛平均约50% |

### 3.2 状态指标阈值

| 指标 | 差 | 中 | 好 |
|------|:--:|:--:|:--:|
| 近3场积分 | 0-3 | 4-6 | 7-9 |
| 近3场净胜 | <-2 | -2~+2 | >+2 |
| 动量 | <-0.5 | -0.5~+0.5 | >+0.5 |

---

## 四、输出格式

### 4.1 比分预测输出

```python
{
    "match_id": "20260930_001",
    "home": "曼城",
    "away": "利物浦",
    "predictions": {
        # 所有比分的信号强度和置信度
        (0, 0): {"signal": 0.72, "confidence": "high", "rank": 3},
        (1, 1): {"signal": 0.45, "confidence": "medium", "rank": 1},
        (1, 0): {"signal": 0.38, "confidence": "medium", "rank": 2},
        (2, 1): {"signal": 0.31, "confidence": "low", "rank": 4},
        # ...
    },
    "total_goals": {
        "0-1": {"signal": 0.35, "confidence": "medium"},
        "2-3": {"signal": 0.42, "confidence": "medium"},
        "4+":  {"signal": 0.23, "confidence": "low"},
    },
    "scene_details": {
        "S1": {"judgment": "均衡", "home_attack": 1.5, "away_attack": 1.4},
        "S2": {"judgment": "双方平稳", "home_form": 5, "away_form": 6},
        # ...
    },
    "bet_recommendations": [
        {"market": "correct_score", "selection": "0:0", "odds_ref": 12.0, "confidence": "high"},
        {"market": "total_goals", "selection": "under_2.5", "confidence": "medium"},
    ]
}
```

### 4.2 下注建议阈值

| 信号强度 | 置信度 | 建议 |
|----------|--------|------|
| >0.7 | high | 推荐下注 |
| 0.5-0.7 | medium | 可考虑 |
| 0.3-0.5 | low | 观望 |
| <0.3 | - | 不推荐 |

---

## 五、代码实现

待实现模块：

```
engine/scripts/research/
├── multi_scene_model.py          # 主模块
│   ├── SceneS1_OffenseDefense    # 攻防对比
│   ├── SceneS2_RecentForm        # 近期状态
│   ├── SceneS3_HomeAway          # 主客场特性
│   ├── SceneS4_ScoringPattern    # 进球模式
│   ├── SceneS5_DefenseStability  # 防守稳定性
│   ├── SceneS6_MatchTempo        # 比赛节奏
│   ├── MultiScenePredictor       # 多场景预测器
│   └── SCENE_WEIGHTS             # 权重配置
│
├── multi_scene_backtest.py       # 回测验证
└── multi_scene_analyze.py        # 信号分析
```

---

*开始实现代码*
