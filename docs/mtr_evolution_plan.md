# 🦅 Brooks-AI MTR 进化计划 (MTR Evolution Plan)

## 1. 任务背景 (Mission Context)
**Role**: Antigravity 首席量化架构师 (Al Brooks PA 正统派)  
**目标**: 将 `MTR Strategy V29.5` 升级为基于“像素级”物理结构识别的 `MTR V30.0`，严格遵循 Al Brooks 修正版逻辑图。

---

## 2. 物理逻辑定义 (Physical Definitions)
根据附件逻辑图，所有代码重构必须覆盖以下四大支柱：

| 支柱 (Pillar) | 物理定义 (Physical Definition) | 向量化代码映射 (Code Mapping) |
| :--- | :--- | :--- |
| **Trend (趋势)** | 创出最低点之前的显著 Swing High (Strategic MLH)。 | `df['strategic_mlh']`: 溯源极值前的波段高点。 |
| **Break (突破)** | Surprise Bar 强力突破 EMA20 **且** 突破 MLH。 | `df['is_leg1_break']`: Close > EMA20 & Close > MLH. |
| **Test (测试)** | 回调动能衰竭 (Momentum Decay)，形成 Higher Low (HL)。 | `df['is_hl_test']`: Low > Prior_Low & Body_Mean_Pullback < Body_Mean_Trend. |
| **Signal (信号)** | 测试完成后出现 Bull Signal Bar，下一棒确认入场。 | `df['signal_mtr']`: HL 后的第一个阳线突破。 |

---

## 3. 执行阶段 (Phases)

### Phase 1: 黄金标准挖掘 (Gold Set Mining) 
- [ ] 编写 `tools/prototype_scanner.py`。
- [ ] 使用严苛物理逻辑在 `baostock.db`（3年日线）中检索教科书级案例。
- [ ] 产出 `data/gold_standards.json` (至少 10 个案例)。

### Phase 2: 策略核心重构 (Local Code Refactoring)
- [ ] 修改 `core/strategies/mtr_strategy.py`。
- [ ] 注入 `Strategic MLH` 与 `Leg 1 Strength` 计算引擎。
- [ ] 限制 $HL > Low$ 的物理硬约束。
- [ ] 整合 V29.5 原有的高期望值过滤器（0.23R 过滤层）。

### Phase 3: 自动化闭环拟合 (Iterative Calibration)
- [ ] 运行自动化回测对照 `gold_standards.json`。
- [ ] 计算 Recall (召回率) 指标。
- [ ] 自动/半自动微调 `momentum_decay` 阈值，直到 **Recall >= 90%**。

### Phase 4: 视觉验证推送 (Visual Deployment)
- [ ] 更新 `tools/notifier.py`。
- [ ] 在全市场扫描中识别匹配度 > 90% 的潜伏信号。
- [ ] 生成包含 `MLH`、`Surprise`、`HL` 标注的 K 线图并推送至企业微信。

---

## 4. 约束与质量守则 (Rules)
1. **禁止未来函数**: 入场信号必须在收盘后计算，不得使用当日未发生的极值。
2. **物理一致性**: 严禁使用硬编码的固定价格阈值，所有判定必须基于 `ATR` 或棒体相对比例。
3. **性能要求**: 核心计算必须全向量化，扫描全市场（5000+ 股票）必须在 15 秒内完成。
