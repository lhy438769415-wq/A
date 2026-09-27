# GAP + H2 策略完整量化条件（代码核实版）

> **核对待源（均真实读取，非凭记忆）**
> - 信号生成：`core/strategies/gap_h2_strategy.py` — `calculate_signals`（第 176–310 行）
> - 回测评估：`archive/tools/backtest_gap_h2.py` — `evaluate_trade`（第 30–91 行）、`LIFECYCLE_TIMEOUT_BARS = 30`（第 27 行）
> - 评级因子：`core/rating_core.py`（第 30–101 行）
>
> **一句话说明**：你之前给的样例把"回测生命周期过滤"混进了"信号生成条件"，且"条件 5 订单有效性过滤"在生产代码中并不存在。本文逐条标注真实来源与差异（差异汇总见第七节）。

> ⚠️ **版本来龙去脉（2026-09-15 二次核实）**：本文**信号触发逻辑（第二节 条件 1–4、第三节 买卖点）与初始项目 `old-gap_h2_strategy.py` 逐字一致**——本支线 `core/strategies/gap_h2_strategy.py` 对 `calculate_signals` **零改动**。
> 但**第六节「评级因子」是支线 Phase 2 校准时新增的**：初始项目没有 `compute_rating` 方法（旧版 318 行 / 新版 389 行，评级方法独占约 65 行）。评级只决定"信号对外显示什么打分/因子证据"，**不影响信号是否触发**。即：你清单里的信号条件从初始项目到现在没动过，只是支线后来多了一套"打分系统"。

---

## 一、参数设置

| 参数 | 值 | 源码位置 | 含义（大白话） |
|------|-----|---------|--------------|
| `LOOKBACK_WINDOW` | **60** 根 K 线 | `gap_h2_strategy.py:170`（`settings.STRUCT_GAP_LOOKBACK` 默认值 60） | 结构突破判定窗口（日线≈3 个月） |
| `MAX_PULLBACK_WINDOW` | **40** 根 K 线 | `gap_h2_strategy.py:172`（`settings.STRUCT_GAP_MAX_WINDOW`） | 两腿回调最长容忍时间 |
| `MIN_PULLBACK_WINDOW` | **2** 根 K 线 | `gap_h2_strategy.py:174` | 两腿回调最短要求时间 |

> 注意：回测文件 `backtest_gap_h2.py` 本身**不定义**这些参数，它直接调用 `GapH2Strategy().calculate_signals(df)`，参数来源是上面的生产策略 `__init__`。

---

## 二、信号触发完整条件（生产 `calculate_signals`，全部 AND）

### 条件 1 — 结构性突破（Gap 形成）
源码 `gap_h2_strategy.py:196-198`
```
is_hh_hl = (high > high.shift(1)) AND (low > low.shift(1))          # 创更高高 + 更高低
_gap_floor_raw = high.rolling(window=60).max().shift(2)             # 过去 60 根最高价，再 shift 2 锚定
is_breakout = is_hh_hl AND (low > _gap_floor_raw - 1e-3)
```
- `shift(2)`：锚点比突破日再往前推 2 根，避免使用当日/前日数据（防未来函数）。
- `GapFloor_raw`（缺口下沿原型）= `Max(High)` 取突破日往前 60 根窗口，再整体后移 2 根。
- ε = `1e-3`（浮点容忍量，全策略统一）。

### 条件 2 — 缺口持续存活（全程监控）
源码 `gap_h2_strategy.py:206-218`
```
组内累计最低价 min(Low_{breakout→now}) > GapFloor - 1e-3   →  gap_h2_open = True
```
- 突破后**每一根** K 线的组内最低价都不得触及缺口下沿，否则 `gap_h2_open=False`，信号作废。
- ε 真实值为 `1e-3`（= 0.001）。

### 条件 3 — 两腿回调状态机完整通过（H2 核心）
源码 `gap_h2_strategy.py:224-248`（窗口 `[MIN=2, MAX=40]`）
```
is_lhll = (high < high.shift(1)) AND (low < low.shift(1))   # 更低高 + 更低低（回调特征 K）
is_hh   = (high > high.shift(1))                            # 更高高（多头恢复）

Phase 1 → 突破后首根 LHLL        （空头第一次反扑）
Phase 2 → Phase 1 后首根 HH      （多头反攻，High 1）
Phase 3 → Phase 2 后首根 LHLL    （空头第二次反扑 → 触发信号 ✅）

组内去重：每次突破仅取首次满足条件者（_already 过滤，第 276 行）
```

### 条件 4 — 高潮规避器（TP 未提前触达）
源码 `gap_h2_strategy.py:259-273`
```
_target_uncond = 2 * GapFloor - PriorSwingLow
组内 max(High_{breakout→now}) < _target_uncond   →  _mm_not_reached = True
```
- 若组内最高价已达/超过测量目标 TP，说明行情已高潮释放，入场意义消失，信号被剔除。
- TP 公式：`TP = 2 × GapFloor − PriorSwingLow`（Al Brooks 镜像对称测量目标）。

### ⚠️ 关于"条件 5 — 订单有效性过滤"
**生产代码中不存在这条独立过滤器。** 你样例写的 `SL > 0 AND TP > Entry AND Entry > SL` 是描述性总结：
- `SL = GapFloor`（价格恒正，隐式 > 0）；
- `Entry = 信号 K 的 high`（突破后远高于 floor，隐式 > SL）；
- `TP = 2×floor − priorlow`（priorlow < floor，故 TP 通常 > Entry，隐式成立）。
但 `calculate_signals` 里**没有显式断言**这三者的不等式。回测 `evaluate_trade` 仅检查了 `pd.isna(entry/sl/tp)`（第 39 行），也未显式检查 `TP>Entry`。因此本文不把它列为"信号生成条件"。

---

## 三、买卖点定义（源码 `gap_h2_strategy.py:286-297`）

| 参数 | 公式 | 锚点来源 |
|------|------|---------|
| **Entry**（Buy Stop） | 信号 K 线（第二次回调 LHLL）的 **High** | 次日挂单，跳空高开按 `Open` 成交（回测 `actual_entry = max(entry, open)`，第 70 行） |
| **SL** | `GapFloor`（60 周期最高价，shift 2） | 缺口下沿，支撑失效即离场 |
| **TP** | `2 × GapFloor − PriorSwingLow` | Al Brooks 镜像对称测量目标 |

其中：
- `GapFloor` = `Max(High_past60)` 锚定在突破日，之后 `ffill`（第 197、206 行）
- `PriorSwingLow` = `Min(Low_past60)` 锚定在突破日，之后 `ffill`（第 203、209 行）

---

## 四、信号 K 线质量辅助指标（源码 `gap_h2_strategy.py:251-254` + `rating_core.py:30-39`）

```
q = (Close − Low) / (High − Low) ∈ [0, 1]
```
| 区间 | 评级权重 | 含义 |
|------|:------:|------|
| `q > 0.8` | **+1.0** | 强势大阳线，多头完全掌控 ✅（与样例一致） |
| `0.5 ≤ q ≤ 0.8` | **0.0** | 中性（样例漏列，代码有此档） |
| `q < 0.5` | **−1.0** | 上影线过长或收在下半部，质量差 ✅（与样例一致） |

> 此指标**不参与信号触发**（条件 1–4 不含 q），仅用于评级和图表展示。

---

## 五、出场规则（回测 `evaluate_trade` 实现，第 57–89 行）

### 入场前（WAITING 阶段）— 生命周期三过滤
| 触发 | 状态 | 源码 |
|------|------|------|
| 等待期 `Low < GapFloor − 1e-3` | `INVALIDATED`（缺口回填撤单） | 第 63–64 行 |
| 等待期 `High ≥ TP` | `VOIDED`（止盈先达，作废） | 第 65–66 行 |
| 等待期 `bars_waited > 30` | `TIMEOUT`（超时失效） | 第 67–68 行 |

> **关键区分**：这里的 `30` 是回测**等待入场**的超时（`LIFECYCLE_TIMEOUT_BARS = 30`，第 27 行），与信号生成的 `MAX_PULLBACK_WINDOW = 40` 是**两个不同参数**，不可混淆。
> 这三过滤仅存在于回测模拟；**生产 `calculate_signals` 不执行**——生产只输出信号 + SL/Entry/TP，不模拟撤单/作废/超时。

### 入场与持仓（IN_TRADE 阶段）
| 触发 | 状态 | 出场价 | 源码 |
|------|------|--------|------|
| 等待期 `High ≥ Entry` | 实际入场 `actual_entry = max(Entry, Open)` | — | 第 69–72 行 |
| 入场当天 `Low ≤ SL` | `LOSS`（same_day_stop） | 按 `SL` | 第 73–75 行 |
| 入场当天 `High ≥ TP` | `WIN`（same_day_tp） | 按 `TP` | 第 76–78 行 |
| 持仓中 `Low ≤ SL` | `LOSS`（stop_loss） | `min(SL, Open)`（跳空低开按 Open） | 第 80–82 行 |
| 持仓中 `High ≥ TP` | `WIN`（take_profit） | **按 `TP`**（回测未改 Open） | 第 83–85 行 |

### 强制收尾
| 情形 | 状态 | 源码 |
|------|------|------|
| 循环结束仍 `IN_TRADE` | `HOLDING`（reason=`eof`） | 第 87–88 行 |

> ⚠️ **与你样例的差异**：回测**并未按末日 Close 强平**，而是返回 `HOLDING(eof)`（持仓未平，不计入已结案）。止盈也仍是按 `TP`（未改成 Open）；只有**止损**用了 `min(SL, Open)`。样例"止盈跳空按 Open""强制按末日 Close"两点与代码不符。

---

## 六、评级因子（7 个，源码 `gap_h2_strategy.py:128-160` + `rating_core.py`）

> **权重是离散贡献分叠加，不是百分比**：`raw = Σ(weight)`，`score = clamp(50 + 10 × raw)`。文档 `gap_h2_strategy_spec.md` 写的"四因子各 25%"是旧版（V9.15），已不准确。

| # | 因子 | 输入变量 | 真实权重分段（`rating_core.py`） | 大白话 |
|---|------|---------|-------------------------------|--------|
| 1 | 信号 K 质量 | `q=(C−L)/(H−L)` | `>0.8 → +1`；`<0.5 → −1`；其余 `0` | 收盘越高多头越控盘 |
| 2 | 回调速度 | `pb_bars`（突破到信号的 K 数） | `≤4 → +2`；`>7 → −2`；`4~7 → 0` | 急跌急复=空头被套 |
| 3 | 缺口宽度% | `gap_pct=(顶−floor)/floor×100` | `>7 → +2`；`<3 → −1`；`3~7 → 0` | 缺口越宽支撑越强 |
| 4 | 连阴数 | 回调最长连续阴线 | `≥3 → −1`；`<3 → 0` | 阴线连太多=卖压未尽 |
| 5 | 时间衰减 | `bars_passed`（信号后经过 K 数） | `>10 → −2`；`>5 → −1`；`≤5 → 0` | 拖太久预期贬值 |
| 6 | 缺口全程存活 | `gap_h2_open` | 存活 `→ +1`；破过 `→ 0` | 缺口从头到尾没破=强 |
| 7 | 两腿对称性 | `leg2_shallow`（L2 低点 ≥ L1?） | **−1**（若浅）/ `0`（若深） | ⚠️ 回测翻转：经典"L2 浅更优"实际输钱，故降权 |

> ⚠️ **因子 7 方向被回测翻转**（`gap_h2_strategy.py:148-152` 注释）：经典 H2 认为"第二次回调浅于第一次（L2≥L1）"更优，但回测发现反向下（命中组 34.7% vs 未中 42.7%），故权重从 +1 改为 **−1**。
> **字母评级（A+/A/B/C/D）已证实为统计噪声**（9/9 批次验证不单调），系统现已"去字母化"——对外只看**命中因子证据 + EV（期望值）**，不靠字母。

---

## 七、与你样例的差异汇总

| # | 样例说法 | 代码真实情况 | 结论 |
|---|---------|------------|------|
| 1 | 条件 5 订单有效性过滤 `SL>0 AND TP>Entry AND Entry>SL` | 生产 `calculate_signals` 无此独立过滤器，仅隐式成立 | ⚠️ 描述性总结，非代码硬过滤 |
| 2 | "等待超 30 根 → TIMEOUT" 列入信号条件 | `30` 是回测**等待入场**超时（`LIFECYCLE_TIMEOUT_BARS=30`），与信号生成 `MAX_PULLBACK_WINDOW=40` 不同 | ⚠️ 两个参数，勿混 |
| 3 | 生命周期三过滤当作信号生成条件 | 仅在回测 `evaluate_trade` 模拟，生产不执行 | ⚠️ 来源不同 |
| 4 | 强制出场"按末日 Close" | 回测返回 `HOLDING(eof)`，**未按 Close 强平** | ⚠️ 不准 |
| 5 | 止盈"跳空高开按 Open" | 回测止盈仍按 `TP`，仅止损用 `min(SL, Open)` | ⚠️ 不准 |
| 6 | 评级"四因子各 25%" | 实为 7 因子离散权重叠加（`score=clamp(50+10·Σw)`） | ⚠️ 旧文档过时 |
| 7 | q 阈值 `>0.8 / <0.5` | 与 `rating_core.py` 完全一致（中间 0.5~0.8 中性 0） | ✅ 准确（补全中性档） |
| 8 | 参数 60/40/2 | 与代码一致，但来源是生产 `__init__` 而非回测文件 | ✅ 准确（厘清来源） |

---

*生成：2026-09-15，基于源码逐行核对。回测结果基于历史数据，不代表未来收益。*
