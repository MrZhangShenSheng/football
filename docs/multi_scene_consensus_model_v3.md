# 多场景博弈比分预测模型 —— 最终方案 v3.0

> 版本：v3.0 | 日期：2026-09-30 | 作者：sszhang
> 
> 核心成果：
> - 固定1倍：收益 **+133.5%**，回撤38.9%
> - 高价值2倍：收益 **+267.0%**，回撤77.7%

---

## 一、版本演进

| 版本 | 收益率 | 回撤 | 改进 |
|:----:|:------:|:----:|------|
| v1 | +19.0% | - | 原始多场景 |
| v2 | +36.3% | 54.9% | baseline优化 |
| **v3** | **+133.5%** | 38.9% | 准确验证框架 |
| **v3+倍率** | **+267.0%** | 77.7% | 高价值2倍策略 |

---

## 二、核心配置

### 2.1 场景阈值

```python
THRESHOLDS = {
    "gf_low": 1.0,      # 场均进球低于此值为低产
    "gf_high": 1.8,     # 场均进球高于此值为高产
    "ga_low": 0.8,      # 场均失球低于此值为防守好
    "ga_high": 1.5,     # 场均失球高于此值为防守差
    "cs_rate_high": 0.35,    # 零封率阈值
    "becs_rate_high": 0.35,  # 被零封率阈值
    "btts_high": 0.55,       # BTTS率阈值
    "over25_high": 0.55,     # 大球率阈值
    "form_good": 7,     # 近3场积分>=7为状态好
    "form_bad": 3,      # 近3场积分<=3为状态差
}
```

### 2.2 频率校准

```python
CALIBRATION_STRENGTH = 0.5  # 50%原始信号 + 50%历史频率
```

### 2.3 倍率策略

```python
# 赔率价值 = 预测概率 / 隐含概率
# 赔率价值 > 0.5 时使用2倍，否则1倍
MULTIPLIER_ODDS_VALUE_THRESHOLD = 0.5
```

---

## 三、置信度计算（6维度）

| 维度 | 说明 | 权重 |
|------|------|:----:|
| signal_strength | Top1信号值 | 15% |
| signal_concentration | Top1与Top2差距 | 10% |
| scene_consistency | 场景方向一致性 | 20% |
| scene_strength | 场景支持度总和 | 20% |
| data_sufficiency | 两队历史场次 | 5% |
| **odds_value** | **预测概率/隐含概率** | **30%** |

**赔率价值权重最高（30%），是倍率决策的核心指标。**

---

## 四、投注策略

### 4.1 2串1双选

```
每日选场：按最大信号排序，取前2场
每场选比分：按信号排序，取Top2比分
投注组合：2×2 = 4注
单注金额：2元 × 倍率（1或2）
每日成本：8-16元
```

### 4.2 倍率规则

```python
def get_multiplier(match_predictions):
    avg_odds_value = mean([p["confidence"]["odds_value"] for p in match_predictions])
    return 2 if avg_odds_value > 0.5 else 1
```

---

## 五、回测结果

### 5.1 固定1倍

| 指标 | 数值 |
|------|:----:|
| 起始资金 | 1000 元 |
| **结束资金** | **2335.1 元** |
| **收益率** | **+133.5%** |
| 命中 | 21/212（9.9%） |
| 回撤 | 38.9% |

### 5.2 高价值2倍

| 指标 | 数值 |
|------|:----:|
| 起始资金 | 1000 元 |
| **结束资金** | **3670.2 元** |
| **收益率** | **+267.0%** |
| 命中 | 21/212（9.9%） |
| 回撤 | 77.7% |

---

## 六、风险提示

1. **高价值2倍回撤77.7%** —— 资金可能跌至最高点的1/4
2. **几乎所有票都是2倍** —— 倍率分布{2: 212}，实际是固定2倍
3. **样本有限** —— 仅10个月212票验证

---

## 七、代码位置

| 文件 | 说明 |
|------|------|
| `engine/multi_scene_predictor_v3.py` | **生产版预测器** |
| `engine/scripts/research/confidence_multiplier_model.py` | 置信度模型研究 |
| `engine/scripts/research/verify_production_v3.py` | 生产版验证脚本 |
| `docs/multi_scene_consensus_model_v3.md` | 本文档 |

---

## 八、使用示例

```python
from engine.multi_scene_predictor_v3 import (
    MultiScenePredictorV3,
    TeamData,
    team_stats_to_team_data,
    calc_parlay_multiplier,
)

# 初始化预测器
predictor = MultiScenePredictorV3()

# 预测
result = predictor.predict(home_data, away_data, score_odds)

# 获取预测结果
top3_scores = result["sorted_scores"][:3]  # Top3预测比分
confidence = result["confidence"]           # 置信度
multiplier = result["multiplier"]           # 单场倍率

# 串关倍率
ticket_multiplier = calc_parlay_multiplier([result1, result2])
```

---

*主公确认，2026-09-30 定稿*
