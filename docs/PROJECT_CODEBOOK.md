# 项目代码全书 — Brooks-AI Quant System

> 本文档是项目源码的**权威导航与模块字典**，面向需要深入任意模块时的"按需读取"入口。
> 由 task #4 汇总，数据于 **2026-07-25 归档后**实测核实（非记忆估算）。
> 库结构冻结、路径/日志基础设施等硬性规则以 `AGENTS.md` + `core/schema_guard.py` 为准，本文不重复定义。

---

## 1. 项目身份

| 项 | 值 |
|:---|:---|
| 名称 | Brooks-AI Quant System |
| 版本 | V9.20（见 `.agent/context/STATUS.md`） |
| 定位 | A 股**人机协作**量化扫描（只推信号、不下单） |
| 理论基础 | Al Brooks Price Action（价格行为） |
| 数据源 | Baostock 本地 SQLite，离线优先，T+1 |
| 二次审计 | DeepSeek API（AI 角色） |
| 推送 | Discord（含 K 线图批推） |
| 运行环境 | `.venv/Scripts/python.exe`（**禁止系统 Python**） |

---

## 2. 规模总览（2026-07-25 实测）

| 范围 | 文件数 | 行数 |
|:---|---:|---:|
| **core/** | 39 | 10,044 |
| ├─ root 模块 | 18 | 4,298 |
| ├─ strategies/ | 10 | 4,078 |
| ├─ signal_tracker/ | 8 | 1,478 |
| └─ patterns/ | 3 | 189 |
| **tools/**（保留） | 13 | 3,792 |
| **tests/** | 12 | 2,111 |
| **config/** | 3（+json/md） | 141 |
| **顶层 *.py** | 4 | 2,375 |
| **archive/**（已归档研究件） | 45 | — |
| 质量门禁扫描范围 | 90 `.py` | 156 测试 |

> 注：顶层 4 = `hunter.py`(1099) + `gui_dashboard.py`(462) + `gap_h2_backtest.py`(660) + `gap_h2_charts.py`(154)。

---

## 3. 分层架构

```
L1 入口   → hunter.py            (交互菜单 + CLI, 1099 行)
L2 流水线 → run_scanner          (一次取数→一次算指标→多策略命中最新K线)
L3 策略   → BaseStrategy + StrategyRegistry  (插件化, 自描述, 元数据驱动)
L4 输出   → notifier (Discord+图表) / watchlist (Facade→SignalTracker)
L5 基础   → database (SQLite WAL) / data_provider (Baostock 离线)
```

---

## 4. core/ 模块字典

### 4.1 根模块（18 文件 / 4,298 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `data_provider.py` | 1164 | 数据层核心：WAL 写入线程、周线聚合、`stock_names.json` 本地持久化、黑名单/超时保护 |
| `review_bridge.py` | 623 | AI 二次审计桥（DeepSeek 角色接入） |
| `calculator.py` | 361 | 指标：`add_indicators`(EMA20/ATR/close_loc/is_bullish)、`calculate_targets` |
| `database.py` | 319 | **schema 主人**：`baostock.db` DDL（daily_bars/weekly_bars/abu_indicators/signal_archive/trade_reviews），WAL + `close_all_connections` |
| `scan_engine.py` | 341 | **P2-heavy 提取的周线共享扫描核心**（见 §7） |
| `schema_guard.py` | 183 | 库结构守护：PRAGMA integrity_check + 实际表/列比对，建议纳入 CI |
| `formatter.py` | 229 | 简报/报告格式化 |
| `backtest_engine.py` | 199 | 统一成本敏感回测引擎（参数化退出模拟） |
| `feature_pipeline.py` | 124 | 特征管线 |
| `rating.py` | 123 | 评级计算（对外接口） |
| `rating_core.py` | 100 | 评级核心逻辑 |
| `monitor.py` | 91 | 回调监控 |
| `api_client.py` | 83 | API 客户端 |
| `scanner.py` | 149 | 日线扫描器 |
| `strategy_registry.py` | 138 | **6 策略插件注册表**（自描述元数据） |
| `log_config.py` | 33 | 日志基础设施（替代 `logging.basicConfig`） |
| `paths.py` | 38 | 路径基础设施（替代 `sys.path.insert`） |
| `__init__.py` | 0 | — |

### 4.2 `core/strategies/`（10 文件 / 4,078 行）

| 文件 | 行数 | 说明 |
|:---|---:|:---|
| `structural_gap_strategy.py` | 696 | STRATEGY_STRUCTURAL_GAP（GAP H1） |
| `mtr_structural_v35.py` | 592 | MTR 结构化 V35 引擎（变体/核心算法） |
| `awil_strategy.py` | 519 | STRATEGY_AWIL（V9.20 新增） |
| `three_k_strategy.py` | 483 | STRATEGY_3K（3K 动能） |
| `mtr_strategy.py` | 414 | MTR_MASTER |
| `gap_pinbar_strategy.py` | — | STRATEGY_GAP_PINBAR |
| `gap_h2_strategy.py` | — | STRATEGY_GAP_H2 |
| `geometric_engine.py` | — | 几何求解器（被 awil/mtr 等复用，非注册策略） |
| `base.py` | — | `BaseStrategy`：元数据驱动 `get_metadata`/`compute_rating`/`get_signal_info`/`annotate_chart` |
| `__init__.py` | — | — |

> 6 个**已注册**策略见 §5。所有策略共用 `calculate_signals` / `compute_rating` / `get_metadata`（日周通吃），差异仅在信号逻辑。

### 4.3 `core/signal_tracker/`（8 文件 / 1,478 行，P11 拆分后）

Facade `tools/watchlist.py` → 此子包。组成：`__init__` / `_shared` / `archive` / `compat` / `dashboard`(539) / `gaps` / `report` / `tracking`。
职责：信号归档、去重、生命周期（INVALIDATED/VOIDED/TIMEOUT）、状态追踪。**禁止**在白名单外新建独立状态追踪（AGENTS.md 绝对禁止）。

### 4.4 `core/patterns/`（3 文件 / 189 行）

`BasePattern` + `PatternRegistry` + `weekly_bull_flag.py`（周线牛旗三推；周线 IOI 同为高胜率插件化形态）。

---

## 5. 策略注册表（6 项，已实测核对）

注册表：`core/strategy_registry.py`（`_strategies` 字典，自描述元数据）。

| 注册键 | 显示名 | 时间框架 | 实现文件 |
|:---|:---|:---|:---|
| `MTR_MASTER` | MTR反转 | daily | `mtr_strategy.py` |
| `STRATEGY_3K` | 3K动能 | daily | `three_k_strategy.py` |
| `STRATEGY_STRUCTURAL_GAP` | GAP H1 | daily, weekly | `structural_gap_strategy.py` |
| `STRATEGY_GAP_PINBAR` | GAP Pinbar | daily, weekly | `gap_pinbar_strategy.py` |
| `STRATEGY_GAP_H2` | GAP H2 | daily, weekly | `gap_h2_strategy.py` |
| `STRATEGY_AWIL` | AWIL趋势 | daily | `awil_strategy.py` |

- 日线全量：`list_strategies()`（6 项全扫）。
- 周线：`get_strategies_by_timeframe('weekly')` → 后 3 项（STRUCTURAL_GAP / GAP_PINBAR / GAP_H2）。
- **注册表顺序风险**：`hunter.py` 周线委托用 `get_strategies_by_timeframe('weekly')[:1]` 取默认策略。3K 的 `supported_timeframes` 保持 `['daily']`（**未补 weekly 元数据**），否则会让 gap 默认误扫 3K。

---

## 6. tools/ 保留模块（11 文件 / 3,221 行）

| 文件 | 行数 | 职责 |
|:---|---:|:---|
| `notifier.py` | 746 | matplotlib 图表 + Discord 推送；2000 字符分段；10 图连发重试 |
| `rating_calibration_backtest.py` | 604 | 评级校准回测（Phase 2，配置驱动 6 策略×日/周） |
| `fetcher_baostock.py` | 539 | Baostock 数据抓取 |
| `web_viewer.py` | 324 | 本地 Web 查看器（读 `weekly_gap_watchlist.json`） |
| `for_hold.py` | 296 | 持仓/持有辅助 |
| `watchlist.py` | 204 | Facade → SignalTracker |
| `journal.py` | — | **schema 主人**：`ai_journal.db`（hunter_journal/guardian_journal） |
| `deploy_dashboard.py` | — | 仪表盘部署 |
| `import_backtest_wins.py` | — | 导入回测胜局 |
| `fetcher.py` | — | 抓取器 Facade |
| `__init__.py` | — | — |

> 其余 32 个 `tools/*.py/html` 研究/回测脚本已于 2026-07-25 归档至 `archive/tools/`（commit `84e6dc0`），不再参与生产。

> 周线扫描器 `scanner_weekly_gap.py` / `scanner_weekly_3k.py` 已于 2026-07-25 Phase 3 并入 `core/scan_engine.py` + `hunter.py`（彻底单引擎），生产入口统一为 `python hunter.py --timeframe weekly [--strategy STRATEGY_3K]`。

---

## 7. 周线双引擎收敛（P2-heavy，已落地）

`core/scan_engine.py` 提取了周线扫描共享核心，消除散落在两个 scanner 的内联重复：

- `fetch_weekly_data(full_code, weeks=200)` — 委托 `data_provider`
- `prepare_weekly_df(full_code, weeks=200)` — fetch + `add_indicators`（不含 `calculate_signals`，由各策略实例调用）
- `scan_single_code_weekly(code, recent_weeks=4, strategies)` — 单码扫描（pending 派生 + 生命周期过滤）
- `scan_weekly_gap_signals(all_codes, strategies, recent_weeks=4)` — 全市场并发（`ThreadPoolExecutor(4)`）
- 私有：`_get_strategy_cols`（元数据优先+后缀兜底）、`_letter_to_ev_text`

两个 scanner 现仅保留格式化/Discord/JSON/MD 写入层（零行为变化），JSON 形状兼容（`weekly_gap_watchlist.json['signals_gap']` 不变）。

---

## 8. 配置 `config/`

| 文件 | 说明 |
|:---|:---|
| `sop_rules.md` | **33KB / 16-Step SOP**，Al Brooks PA 理论权威源；所有评级因子必须能在此溯源（标注 Step 编号），无法溯源的因子禁止进入评级 |
| `settings.py` (94) | 运行设置 |
| `personas.py` (47) | AI 角色人设 |
| `rating_factors_*.json` | 评级校准产出（`rating_factors_full_v2.1.json` 等） |
| `stock_names.json` / `trading_calendar.json` | 中文名持久化 / 交易日历 |
| `fonts/` | 中文字体（图表用 SimHei，避开 ✓/✗ 缺字形符号） |

---

## 9. 测试 `tests/`（12 文件 / 2,111 行）

关键守护：`test_p1_p2_regression.py`(694) / `test_awil_strategy.py`(251) / `test_rating.py`(237) / `test_calculator.py`(214) / `test_three_k_strategy.py`(204) / `test_phase1_regression.py`(157) / `test_weekly_and_noai_flow.py`(117) / `test_bs_net` / `test_schema_integrity` / `test_split_msg` / `test_mtr_flow` / `README.md`。
运行：`.venv/Scripts/python.exe -m pytest --maxfail=2`。

---

## 10. 数据流程

**日线流水线**（`hunter.run_scanner`）：一次取数(`data_provider`) → 一次算指标(`calculator.add_indicators`) → 多策略 `calculate_signals` 命中最新 K 线 → `compute_rating` → `formatter` → `notifier`(Discord+图表) → `watchlist`/SignalTracker 归档。

**周线流水线**（Phase 3 彻底单引擎）：`hunter.py --timeframe weekly`（CLI 直通 / 交互菜单）统一调用 `core/scan_engine.run_weekly_scan`，按家族路由——gap 家族（STRUCTURAL_GAP/PINBAR/H2，含 Signal Tracker 归档）走 `scan_weekly_gap_signals`+`format_push_weekly_gap`；3K（`--strategy STRATEGY_3K` 或菜单项，不归档）走 `scan_weekly_3k_signals`+`format_push_weekly_3k`。产出 JSON（`weekly_gap_watchlist.json` / `weekly_watchlist.json`）+ MD 计划 + Discord。原 `scanner_weekly_gap.py` / `scanner_weekly_3k.py` 已并入 `scan_engine` + `hunter`（2026-07-25）。

**评级校准回测**：`tools/rating_calibration_backtest.py` + `core/backtest_engine.py` → `config/rating_factors.json` + `calibration_report.txt`（含训练/测试切分、Wilson 95% CI、净 EV）。

---

## 11. 设计铁律与护栏（抽查核对通过）

1. **PA 铁律 — volume 绝不进入信号/评级因子**：实测 `core/strategies/` 中 `volume`/`vol_ma`/`relative_vol` 仅作为 ⛔ 禁止性注释出现，**未进入任何信号或评级逻辑**。评级因子只能取价格行为本身（实体/影线/位置/结构/缺口/连阴/盈亏比/EMA 上下文磁力位）。
2. **评级全周期裁决源 = `config/sop_rules.md`**：因子须能溯源到 16-Step SOP（标注 Step 编号），否则不得进入评级。
3. **库结构冻结**：DDL 单一来源（`database.py` ← `baostock.db`；`journal.py` ← `ai_journal.db`），`schema_guard.py` 守护，quality_gate 阻断违例。
4. **路径/日志基础设施**：禁止新增 `sys.path.insert`（用 `core/paths.py`）、禁止新增 `logging.basicConfig`（用 `core/log_config.py`）。
5. **人机协作**：系统只推信号、不下单；**不实现市场状态（牛熊/震荡）自动识别**，该判断交人工。
6. **UX 评审硬约束**：面向终端用户的产物交付前必须过一轮用户体验评审（分流 vs 深读、信息共位去冗余、降消息高度、分组标签、可复制性、字体安全、眼见为实）。

---

## 12. 归档 `archive/`

- **何时/为何**：2026-07-25（commit `84e6dc0`）。`tools/` 下 32 个研究/回测脚本 + 整个 `strategy_lab/`（13 文件，硬依赖已删的 `data_manager`）经调查确认**无生产引用**、但属有价值研究历史，故 `git mv` 归档（可逆、保留 git 历史），非删除。
- **影响**：主流程仅保留 13 个 `tools/*.py`；`gui_dashboard.py` / `notifier.py` 对 `data_manager` 的引用已 `try/except` 优雅降级，不受影响。
- **验证**：归档后 `pytest 111 passed`、质量门禁 90 `.py` / 156 测试 / 红线 0。

---

## 13. 抽查对账记录（本文数据来源）

| 核对项 | 方法 | 结果 |
|:---|:---|:---|
| 模块规模 | `.venv` Python 遍历计数（排除 archive/data/.venv） | core 10044 / tools 3792 / tests 2111 / 顶层 2375 |
| 6 策略注册 | 读 `core/strategy_registry.py` `_strategies` | 6 项 + `_OFFICIAL_LIST` 一致 |
| 时间框架 | grep 各策略 `get_metadata` `supported_timeframes` | 日×3 / 日+周×3，与注册表一致 |
| 周线共享核心 | grep `core/scan_engine.py` 公共 API | 5 公开函数 + 2 私有助手已落地 |
| 无断链 | grep 保留代码对 45 个归档模块的 import | **无任何保留文件引用归档模块** |
| PA volume 铁律 | grep `core/strategies/` `volume` | 仅 ⛔ 禁止注释，无逻辑引用 |
| SOP 权威源 | `ls config/sop_rules.md` | 33,188 字节存在 |
| 质量门禁 | 跑 `.agent/quality_gate.py` | 90 `.py` / 156 测试 / 红线 0 / DDL 白名单 OK |

---

## 14. 路由表（按需深入时读取）

| 需要了解… | 读取… |
|:---|:---|
| 当前版本/最近改动/待办 | `.agent/context/STATUS.md` |
| 策略接入 SOP | `.agents/skills/strategy-onboarding/SKILL.md` |
| Al Brooks PA 理论 / 评级因子溯源 | `config/sop_rules.md` |
| GAP 策略规范 | `docs/gap_h2_strategy_spec.md` |
| MTR 策略规范 | `docs/MTR_V35_0_STRATEGY.md` |
| P2-heavy 收敛方案与偏离决策 | `docs/P2_heavy_dual_engine_convergence_plan.md` |
| 评级校准方法论 | `docs/回测方法学与计划_人工评审稿.md` / `backtest_methodology.md` |
| 完整架构交接 | `docs/CHANGELOG_agent_handoff.md` |
| 库结构冻结规则 | `AGENTS.md` + `core/schema_guard.py` |
| 已归档研究脚本 | `archive/tools/` + `archive/strategy_lab/` |

---

*本文档随代码演进维护；结构变更后请同步更新 §2/§4/§13。*
