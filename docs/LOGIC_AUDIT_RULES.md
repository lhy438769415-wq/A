# LOGIC_AUDIT_RULES.md — 策略/评级逻辑审计规则（SSOT）

> 本文件是 Brooks-AI Quant System **价格行为 (PA) 逻辑约束的单一事实源 (SSOT)**。
> 任何 AI / 协作者修改 `core/strategies/*`、`core/calculator.py`、评级相关代码或推送样式前，
> 必须先读本文件，确保改动不违背以下铁律。本文件引用而非重复细节；细节以被引文档为准。

## 0. 项目身份
- 系统：Brooks-AI Quant System（当前 V9.20），A 股量化扫描，**人机协作、只推信号不下单**。
- 理论根基：Al Brooks 价格行为 (Price Action)，数据源 Baostock 本地 SQLite（离线优先，T+1）。

## 1. PA 铁律 — volume 绝不进入信号/评级因子
- **用户是坚定 Al Brooks PA 交易员，量是 NEVER 必要考量。**
- `daily_bars`/`weekly_bars` 虽存 `volume` 列，`core/calculator.py` 亦算 `vol_ma20`/`relative_vol`，
  但**全策略无一处将其用于信号或评级**（`three_k_strategy.py` AI prompt 明令 "do not mention Volume"）。
- 任何策略/评级的因子只能取价格行为本身：
  实体 / 影线 / 位置 / 结构 / 缺口 / 连阴 / 盈亏比 / EMA 上下文。
- **禁止**引入"放量/缩量/量能确认"类因子。

## 2. 评级因子白名单 + 黑名单（必须能溯源到 SOP）
- **全周期裁决源 = `config/sop_rules.md`（16-Step SOP）。** 所有评级/信号因子必须能在该 SOP 找到理论依据（标注 Step 编号），无法溯源的因子不得进入评级。
- **白名单（纯 PA，可用）**：信号 K 质量 / 缺口 / 回调速度 / 连阴 / Always-In / 陷阱过滤 (FBO·Climax·第二腿) / RR≥2 / 磁力空间 / 微型形态 / 两腿对称。
- **黑名单（禁用）**：`volume` / `relative_vol` / `vol_ma20` / `ADX` / `EMA 斜率` / 均线多头排列。
  - 例外：`EMA20` 仅可作**磁力位**或 **Always-In 翻转的 MA_Gap 证据**（SOP Step 7 Method 3），不得作为趋势斜率或多头排列信号。

## 3. 不实现能力之外的判断
- 系统**不实现市场状态（牛熊/震荡）自动识别与提醒**，该判断交人工。
- 回测发现的 regime 依赖只作为观察结论呈现，**不落地为代码层的自动降级/风险提示**。

## 4. UX 评审硬约束（交付前必过）
- 任何面向最终用户的产物（推送样式 / K 线图 / 简报 / demo / 报告 / UI）在交付前，**必须站在终端用户体验角度评审一轮**。
- 评审清单要点：
  1. **分流 vs 深读**：扫读层（简报/列表）锁定目标，深读层（点开的图）负责细节，职责不重叠。
  2. **信息共位 / 去冗余**：同一信息只在一处出现。
  3. **降低消息高度**：Discord 推送减换行、减段落，批量图沿用多图打包（10/组）。
  4. **分组与标签**：按策略聚合、组内按评级 (A+/A/B/C) 分组、同评级顿号间隔、代码仅 6 位。
  5. **可复制性**：核心文字尽量落在图里（微信粘贴可复制）。
  6. **字体安全**：用中文字体时避开 ✓/✗ 等缺字形符号（改用 ●/○ 或纯文字）。
  7. **眼见为实**：布局拥挤度/格式判断，先渲染真实 mock 给用户确认再落地。

## 5. 审计方法学参考（回测/评级校准）
- 机构级经验见用户桌面 `C:\Users\Leo\Desktop\backtest_methodology.md`（项目既有知识库）。
- 关键经验锚（仅在回测/校准时引用，不影响实盘信号逻辑）：
  - 缺口家族 SL = GapFloor，TP = 2×GapFloor − PriorSwingLow。
  - 回测成本常量：印花税 0.05%（卖）/ 佣金 0.03%（双边）/ 滑点 0。
  - 信号生命周期三过滤：INVALIDATED / VOIDED / TIMEOUT（缺口家族启用，不计入分母）。

## 6. 工程护栏（防回归）
- 红线（由 `.agent/quality_gate.py` 门禁守卫）：无 `sys.path.insert`（仅 `core/paths.py` 豁免）、
  无 `logging.basicConfig`（统一 `get_logger`）、无裸 `except`、无 `import *`、DDL 仅限白名单、
  `get_logger` 调用不得早于其 import。
- 测试数守卫：≥ 156 个 `test_` 函数，删测试让门禁变绿会被拦截。
