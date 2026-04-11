# 🦅 Brooks-AI Gap Strategy 进化计划 (Gap Strategy Evolution Plan)

## 1. 任务背景 (Mission Context)
**Role**: Antigravity 首席量化架构师 (Al Brooks PA 正统派)  
**目标**: 将演进路线全面定调为 **Gap Strategy（缺口策略）**，抛弃以往的摸底(MTR)思路，全面拥抱"动力+顺势"的突破模型。建立动态的**高胜率形态库 (High Win-Rate Pattern Library)**。

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

### Phase 1: 黄金标准挖掘 (Gold Set Mining) ✅ 已完成
- [x] 编写 `tools/prototype_scanner.py`。
- [x] 使用严苛物理逻辑在 `baostock.db`（3年日线）中检索教科书级案例。
- [x] 产出 `data/gold_standards.json` (至少 10 个案例)。

### Phase 2: 策略核心重构 (Local Code Refactoring) ✅ 已完成
- [x] 实现 `core/strategies/structural_gap_strategy.py` (V3.0 宏观结构突破)。
- [x] 注入 60 根 K 线 Lookback Window 突破判定引擎。
- [x] 限制 Gap Floor 物理硬约束（回调不可跌穿）。
- [x] 整合 V9.5 自演进过滤器（`gap_optimized_rules.json`）。

> **注**: 原计划修改 `mtr_strategy.py`，实际重新设计为独立的 `structural_gap_strategy.py`，
> 更好地体现了 Gap 策略独立于 MTR 的本质差异。MTR 作为储备策略保留。

### Phase 3: 自动化闭环拟合 (Iterative Calibration) ✅ 已完成
- [x] 运行全量市场回测 (3,299 只 A 股, 6 年日线, ~6,000 信号)。
- [x] 完成 LOOKBACK_WINDOW 60 vs 100 对比回测 (实验记录 #005)。
- [x] 确认 LB=60 为最优参数 (EV=+0.031R, 胜率 49.57%)。
- [x] 建立 `core/patterns/` 高胜率形态库 (牛旗三推 58% WR, +0.06 EV)。

### Phase 4: 视觉验证推送 (Visual Deployment) ✅ 已完成
- [x] 更新 `tools/notifier.py` — 支持结构缺口专属图表渲染。
- [x] 全市场扫描集成到 `hunter.py` 主流水线（4阶段架构）。
- [x] K 线图标注 Entry/SL/TP + Discord 多图分批推送。

> **注**: 推送目标从企业微信变更为 Discord，功能更强且支持多图。

---

## 4. 后续演进方向 (Future Work)
- [ ] `core/patterns/weekly_ioi.py` — 周线缺口+IOI收敛形态 (75% WR 待实现)
- [ ] 板块维度分析 — Reversal/Sweep 在不同板块的表现差异研究
- [ ] 成交量权重注入 — EMA20 Pullback 增加成交量过滤提升胜率

---

## 5. 约束与质量守则 (Rules)
1. **禁止未来函数**: 入场信号必须在收盘后计算，不得使用当日未发生的极值。
2. **物理一致性**: 严禁使用硬编码的固定价格阈值，所有判定必须基于 `ATR` 或棒体相对比例。
3. **性能要求**: 核心计算必须全向量化，扫描全市场（5000+ 股票）必须在 15 秒内完成。
