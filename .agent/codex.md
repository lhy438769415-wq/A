# Brooks-AI 量化系统 · 项目代码全书 (Codex)

> 生成：2026-07-22 ｜ 方法：5 路只读勘察（Explore subagent）覆盖全项目 + 关键事实由主代理亲自核实
> 用途：系统全景速查，每文件一行定位 + 交互开关 + 死代码 + 文档矛盾 + 待裁决项
> 注意：本文件是"理解快照"，非规范源；规范以 `.agent/rules/` 与 `docs/` 原文为准（其矛盾见 §4）

---

## 0. 亲自核实的关键事实（主代理复核，非 subagent 摘要）

| 事实 | 结论 | 证据 |
|---|---|---|
| AI 审计开关 | **三层结构**：①全局开关 `use_ai`（hunter.py:1026 input，每次选完日线策略都弹，对所有策略出现）②硬编码快速通道名单（hunter.py:385）强制 5 策略跳过 AI，与开关无关 ③仅 AWIL 受开关控制 | 亲读 hunter.py:383-413, 1022-1046 |
| 测试基线 | 实测 **143** 个 `def test_`；`test_p1_p2_regression.py` 含 **98** 个 → **98/98 真，25/25 陈旧** | 亲跑 + grep 统计 |
| 红线替代物 | `core/paths.py` / `core/log_config.py` **确认不存在** | find 核验 |
| DDL 单一来源 | **已收敛(2026-07-23)**：baostock.db←core/database.py，ai_journal.db←tools/journal.py；原 signal_tracker/review_bridge 的重复定义已委托回主人 | 实改+门禁复核 |
| 双扫描引擎 | 日线 `core/scanner.run_scanner`；周线 `tools/scanner_weekly_gap`（独立）；`scanner_weekly_3k` 写好人未接 | 多路报告一致 |

---

## 1. 交互开关总表

| 开关 | 类型 | 位置 | 所属流程 | 影响 | 默认 |
|---|---|---|---|---|---|
| `--strategy` | CLI | hunter:824 | 策略选择 | 全部 | None→菜单 |
| `--limit` | CLI | hunter:825 | 扫描范围 | 全部 | 0=全市场 |
| `--timeframe` | CLI | hunter:826 | 日/周 | 全部 | None→菜单 |
| `--weeks` | CLI | hunter:827 | 周线回看 | 周线 | 4 |
| `--track` | CLI | hunter:828 | 信号仪表盘 | 全部 | 关 |
| `--report` | CLI | hunter:829 | 统计报表 | 全部 | 关 |
| `--no-ai` | CLI | hunter:830 | **AI 旁路** | 全局(日线非快通) | 关=启用AI |
| `mode_choice` | input | hunter:892 | 主菜单 | — | 1=扫描 |
| `tf_choice` | input | hunter:930 | 周期 | 日/周 | 1=日线 |
| 周线策略序号 | input | hunter:953 | 周线策略 | 周线 | 首个 |
| 日线策略序号 | input | hunter:1001 | 日线策略 | 日线 | 首个 |
| **AI 二次审计** | input | hunter:1026 | **AI 决策** | 全局(不影响5快通) | 1=启用 |
| `sync_choice` | input | hunter:771 | 数据同步 | — | 3=全量 |
| review inputs | input | review_bridge 多处 | 复盘录入 | — | 各默认值 |

**关键澄清（回应"其他策略也有手动开关"）**：全局 AI 开关对所有日线策略都弹，用户观察正确；但 5 个策略被硬编码强制跳过，使该开关对它们无效——这是 item2 要修的根因。

---

## 2. 模块定位速查（每文件一行）

### 入口 / 编排
- `hunter.py` — 唯一主入口 + 四阶段流水线编排 + 6个AI Worker线程 + 全局AI开关
- `gui_dashboard.py`（根）— ttkbootstrap 桌面台；**断链**：调用不存在的 `hunter.run_ask_stock`

### 核心引擎 `core/`
- `scanner.py` — 日线向量化扫描引擎（run_scanner）
- `strategy_registry.py` — 策略注册表（6策略元数据驱动）
- `calculator.py` — 指标+PA特征（add_indicators/calculate_targets）
- `data_provider.py` — 数据层唯一出口（WAL线程/日→周聚合/中文名缓存）
- `database.py` — SQLite管理（WAL/close_all_connections）；**含 abu_indicators 孤儿表**
- `api_client.py` — DeepSeek封装（重试+Mock）
- `formatter.py` — AI协议层（parse_response/format_guardian_prompt）；`HUNTER_V1`回退隐患
- `signal_tracker.py`(1409行) — 信号生命周期真身（P11待拆）
- `signal_db.py` — **死代码**（seen_signals 0引用）
- `feature_pipeline.py` — **死代码**（abu_indicators 无写入方）
- `monitor.py` — 仅GUI用监控线程
- `backtest.py` — 仅GUI用占位VectorBacktester
- `review_bridge.py` — 复盘数据桥（trade_reviews 委托 core/database.py 建表，自身无 DDL）

### 策略 / 形态 `core/strategies` + `core/patterns`
- `base.py` — 自描述基类（get_metadata/get_signal_info/annotate_chart）；**无 ai_audit 字段**
- `mtr_strategy.py` — MTR反转（用 structural 引擎；geometric 引擎未接线）
- `three_k_strategy.py` — 3K动能
- `structural_gap_strategy.py` — 结构缺口（日+周）
- `gap_pinbar_strategy.py` — Gap+Pinbar（日+周）
- `gap_h2_strategy.py` — Gap+H2（日+周）
- `awil_strategy.py` — AWIL趋势（日线；唯一自带annotate）
- `mtr_structural_v35.py` — MTR结构引擎（_check_trendline_break 废弃）
- `geometric_engine.py` — 几何趋势线引擎（analyze_bar 死代码）
- `patterns/base.py` — 形态注册表（与StrategyRegistry 双轨）
- `patterns/weekly_bull_flag.py` — 周线牛旗（未接主流水线）

### 工具核心 `tools/`（被 hunter 直接调用 5 个）
- `notifier.py` — Discord推送+图表（format_*_alert×3/stitch_images 死代码）
- `watchlist.py` — WatchlistManager Facade（委托 signal_tracker）
- `for_hold.py` — 持仓管家（真正用AI的地方）
- `fetcher_baostock.py` — Baostock适配器（经data_provider间接）
- `scanner_weekly_gap.py` — 周线缺口扫描（活跃）；stitch_images死导入
- `scanner_weekly_3k.py` — 周线3K扫描（**未接主流程**）
- `data_manager.py` — 旧数据Facade（仅GUI/strategy_lab）
- `journal.py` — AI决策日志DB（**ai_journal.db 唯一 schema 主人**，hunter_journal/guardian_journal）
- `web_viewer.py` / `quiz_server.py` / `prototype_scanner.py` / `sync_notion_reviews.py` / `research_gap_pinbar_ev.py` — 独立运行模块

### 工具脚本类 `tools/`（约39个疑似死代码，见 §3）
回测/研究/扫描/分析/绘图/报告/其他类，详见 §3。

### 配置 `config/`
- `settings.py` — 全局配置（DB/DeepSeek/Discord/MAX_WORKERS=5）
- `personas.py` — **0引用死代码**（Gemini/数字阿布备件）
- `sop_rules.md` — 33KB PA规则参考（人工阅读）
- `stock_names.json` — 中英文名缓存（实际在 config/，README称 data/，路径漂移）
- `trading_calendar.json` — 交易日历

### 测试 `tests/`（143个函数）
calculator/3K/AWIL/MTR flow/phase1/weekly+noai/split_msg/p1_p2回归(98) + bs_net(非pytest)

### 科研 `strategy_lab/`（整体历史资产，可归档）
全部 .py 未被主项目 import；成果已"毕业"至 core/strategies

### 文档 `docs/`（34篇，矛盾密集见 §4）

---

## 3. 死代码清单（分级）

| 级别 | 文件 | 证据 | 处置建议 |
|---|---|---|---|
| 共识8（已核实0引用） | signal_db / backtest / data_manager / scan_three_k / scan_v34 / test_signals / test_weekly_history / read_stats | grep 0引用 | 可删（不碰DDL） |
| 额外3候选（0引用但"备件"） | feature_pipeline / personas / monitor | grep 0引用 | 待你单独拍板 |
| tools脚本类疑似39 | backtest_*×10 / research_*×2 / scan_*×5 / analyze/extract×4 / plot×2 / report×2 / 其他14 | grep 0引用 | 确认无手动运行需求后清理 |
| strategy_lab整体 | 全部 | 主项目0 import | 归档 |
| core内部死代码 | geometric.analyze_bar / mtr_structural._check_trendline_break / data_provider.preload_snapshots/get_stock_data_hybrid | 0引用/Deprecated | 清理 |
| 断链 | gui_dashboard→hunter.run_ask_stock（不存在） | AttributeError | 修或移除按钮 |

---

## 4. 文档矛盾清单（C1–C13，只读核验）

| # | 矛盾 | A说 | B说 |
|---|---|---|---|
| C1 | 版本号 | SYSTEM_MANUAL=V8.30；多文档=V9.8 | STATUS/README=**V9.20**；project_diagnosis=V10.0 |
| C2 | 测试基线 | STATUS=25/25 | CHANGELOG=98/98（实测143函数，98/98真） |
| C3 | 逻辑宪法 | 多篇引用 LOGIC_AUDIT_RULES.md | **文件不存在** |
| C4 | 红线替代物 | AGENTS指定 core/paths.py+log_config.py | **两文件均不存在**；optimization_fix_plan另给 logging_helpers+pyproject 方案（冲突） |
| C5 | 债务计数 | 38/35 处 | 61/49；STATUS 38/41（三套口径） |
| C6 | 自描述完成？ | CHANGELOG称P1已完成消灭硬编码 | 仍有周线 if 'PINBAR' in name hack + 双引擎 |
| C7 | 策略数 | SYSTEM_MANUAL=2 | CHANGELOG=5；STATUS=6；README目录树漏列3个 |
| C8 | 项目路径 | CHANGELOG写 antigravity | 实际 workbuddy |
| C9 | 引用文件缺失 | CHANGELOG引 gap_optimized_rules.json / mtr_v28_debate.py | 均不存在 |
| C10 | Python版本 | AGENTS=3.13 | project_diagnosis=3.8+ |
| C11 | MTR版本 | SYSTEM_MANUAL=V29.5 | MTR_V35_0=V35；CHANGELOG=V35/36 |
| C12 | STATUS位置 | AGENTS路由 .agent/context/STATUS.md | 惯例 docs/STATUS.md 不存在 |
| C13 | 裸except优先级 | 原★★★★★ | optimization降★★ |

---

## 5. 架构要点

- **双引擎**：日线 `core/scanner` 干净元数据驱动；周线 `tools/scanner_weekly_gap` 独立且列映射用后缀hack；`scanner_weekly_3k` 游离。
- **自描述 P1 已落地**：scanner/notifier/hunter 经 StrategyRegistry 取 metadata，但**周线引擎未完全复用**；`ai_audit` 字段不存在（AI仅prompt概念）。
- **红线已补替代物(2026-07-23)**：sys.path.insert→core/paths.py、basicConfig→core/log_config.py（仍 31 处存量待根治，非运行阻断）；**DDL 已收敛为双库单一来源白名单**（core/database.py + tools/journal.py），由 quality_gate 阻断 + core/schema_guard.py 运行时守护。
- **AI 真实战场在 Guardian**（for_hold），扫描侧日线 AI 受硬编码名单钳制。

---

## 6. 待你裁决

1. **item2 AI对齐**：`use_ai` 对5策略无效；"AWIL与其他一致"若=硬编码跳过，则开关变摆设。建议元数据 `ai_audit` 字段（默认False，将来可个别启用）。
2. **死代码清理范围**：共识8可删；额外3+tools 39+strategy_lab 是否一并？
3. **paths.py / log_config.py 补建**：清理红线违规的前置条件，是否现在建？
4. **双引擎收敛**：周线改用日线P1范式，消除后缀hack（最复杂，排最后）。
5. **文档矛盾**：C1–C13 是否立项统一（建 SSOT）？

---

## 7. 数据库结构冻结（Schema 保护）— 2026-07-23 生效

系统第一性 = 数据完整性 / 可用性。库结构（SQLite）是经多版沉淀的稳定地基，被**冻结**并由护栏强制执行：

| 数据库 | 唯一 schema 主人 | 拥有的表 |
|---|---|---|
| `data/baostock.db` | `core/database.py` | daily_bars / weekly_bars / abu_indicators / signal_archive / trade_reviews |
| `data/ai_journal.db` | `tools/journal.py` | hunter_journal / guardian_journal |

**护栏两层**：
1. **代码层（quality_gate）**：白名单外任何文件出现 `CREATE/ALTER TABLE` → 阻断提交。
   - 收敛动作：signal_archive 曾于 database.py 与 signal_tracker.py 各定义一份（列一致，已委托回 database.py）；trade_reviews 由 review_bridge 委托回 database.py。
2. **运营层（core/schema_guard.py）**：直接打开真实库文件，跑 `PRAGMA integrity_check`（抓损坏）+ 实际表/列与"实时解析自主人源码的声明 schema"比对（抓带外漂移）。
   - 期望 schema 从主人源码解析，单一来源、不会脱节。
   - `tests/test_schema_integrity.py`：真实库应通过 + 人为加列必被抓（已验证）。

**改库结构正确流程**：只在主人文件改 DDL → 跑 schema_guard + pytest → 评审 → 提交。禁止绕过代码手改线上库。
