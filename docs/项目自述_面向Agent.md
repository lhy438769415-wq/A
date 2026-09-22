# Brooks-AI 量化系统 · 项目自述（面向 AI Agent 版）

> **本文档是写给"下一个接手本项目的 AI Agent"的完整自述**，目标是让你在动手前对项目建立**无偏差**的全貌认知。
> 整理日期：2026-09-05。文中所有代码现状均带文件名+行号或实测数据出处；规模数字均为当日实测（统计命令：`wc -l` / `find ... | wc -l` / `grep -c "def test_"`）。
>
> **文档优先级（冲突时以此为准）**：本文件（自述）≈ `AGENTS.md`（简明规范）> `.agent/rules/*`（细则）> `docs/`（专题文档）> `README.md`（面向人，部分内容已陈旧）。
> **本文件不替代**：`DATA_SAFETY.md`（数据安全原文）、`config/sop_rules.md`（PA 理论裁决源）、`docs/项目全景速查手册.md`（一页速查）。

---

## 〇、先读我：三条最高优先级铁律（违反任何一条都是事故）

1. **数据安全铁律**（全文见 `DATA_SAFETY.md`）：`data/*.db*`（含 `.db`/`.db-wal`/`.db-shm`）是 584MB 的命根子。除非用户**当次消息**明确许可，绝不复制/备份/移动/删除，也不触发平台的"改前自动备份"。碰 `data/` 前先用大白话说清：要干嘛、为什么、会不会产生临时文件；碰完给大白话小结（做了什么/真身库动没动/有无临时文件/有无东西进回收站）。**不在无人看管时跑数据重活**。
2. **核实铁律（反臆想）**：任何"现状是 X"的断言，必须先 grep/read 真实源码（带行号）或实测；没核实就明说"存疑"，禁止凭印象编数字或行为描述。本项目的 AGENTS.md「绝对禁止」第 7 条与此互为强制。
3. **沟通红线**：用户是**交易员，不是程序员**。任何技术术语首次出现必须紧跟一句大白话注释（例如"WAL 模式——数据库的一种记事方式，先写便签再统一抄进账本"）。这条优先级高于简洁。宁可啰嗦，不可含糊。

---

## 一、项目身份（一句话 + 一张表）

**一句话大白话**：一个**只推信号、不下单**的 A 股扫描系统。每天收盘后扫一遍全市场约 3312 只票，按 Al Brooks 价格行为（PA，Price Action，指只看 K 线的开高低收和形态结构来做判断的理论）把符合形态的挑出来，落库、画 K 线图、推送到 Discord，**最终由人拍板买不买**。

| 项 | 值 |
|:---|:---|
| 项目名称 | Brooks-AI Quant System（版本 V10.0，版本记录见 `.agent/context/STATUS.md` 与 `README.md`） |
| 定位 | A 股**人机协作**量化扫描——系统负责 数据获取→标的筛选→机会推送→统计回测；交易决策 100% 由人 |
| 理论基础 | Al Brooks Price Action（价格行为学） |
| 二次审计 | DeepSeek API（AI 角色，可 `--no-ai` 旁路） |
| 数据源 | Baostock → 本地 SQLite（离线优先，T+1——即今天只能拿到昨天及以前的收盘数据） |
| 推送渠道 | Discord（文字简报 + K 线图批推） |
| 活跃目录 | `D:\life\Trading view\_Project_A\Data_from_Akshare\debugV7.1_for_workbuddy`（**只有这里可改**） |
| ⛔ 冻结目录 | 同级目录 `debugV7.1_for_antigravity` 是原始备份，**读/写/删/移动一律不做**，除非用户明确要求 |

**用户拍板过的用法（勿"优化"掉）**：
- 日线每天收盘后扫，**只推当日末根 K 线触发的信号**，昨日触发的不重推（但会落库进追踪）。last-bar-only 是正确行为，不是 bug。
- 界面按钮语义：**一个按钮只做一件事**（下载行情 / 策略扫描 / 红色终止）。
- 周线**零自动化**：不定时跑、不按星期自动切、不开机自动跑、不弹窗建议；状态栏只报数。
- 不实现"市场状态（牛熊/震荡）自动识别"——这个判断永远交给人。

---

## 二、运行环境（⚠️ 双 Python + git 有特殊约定，最易踩坑）

### 2.1 两个 Python，各管一摊

| 用途 | 解释器 | 说明 |
|:---|:---|:---|
| **引擎与测试**（hunter/策略/门禁/pytest） | `.venv/Scripts/python.exe`（Python 3.13） | AGENTS.md 明令"禁止使用系统 Python"跑引擎 |
| **GUI 操盘台**（gui_dashboard.py） | `C:\Python314\python.exe`（系统 Python 3.14 / Tk 8.6） | 桌面入口 VBS 实际调用的是它，**不是 .venv**。Tk 8.6 能力有限（如无 Treeview 列 style），这是历史踩坑结论 |

### 2.2 git：必须用系统真实 git，沙盒 git 有"误删"前科

- **一律用** `D:/Program Files/Git/cmd/git.exe`（先 `cd` 进项目目录再执行；它只认 Windows 路径 `D:/...`，不认 `/d/...`，也别用 `-C` 参数）。
- **沙盒内置 git 外裹一层"安全删除"拦截层，对删除类 git 命令会误删整个子目录**（2026-09-02 实测两次：`git rm` 把 `tools/` 整目录删空；`git stash` drop 清空 `.git/refs`）。恢复手段：`.git` 对象库完好时 `git checkout HEAD -- <目录>`。
- **禁用清单**：`git rm`、`git stash`（已实测出事）；`git clean -fd`、`git reset --hard`、`git checkout -- <未跟踪文件>`（同属"删文件/清工作区"动作，一律当作会触发，绕开）。
- **安全替代（照抄即可）**：
  - 删已跟踪文件 → `rm <file>`（OS 层单文件删除实测安全）+ `git add <file>`
  - 回滚提交 → `git revert <sha>`（最安全）或 `git reset --soft`
  - 改名/移动 → `mv` + 两侧各 `git add`
  - 暂存/对比 → 用临时副本文件，不用 stash
- **提交纪律**：每个实质性改动后**立即本地 commit**（不强制 push）。历史教训：仓库曾一度无 commit，`data_provider.py` 被截断后不可恢复。
- 提交信息格式：`[类型] 简述`，类型 = feat/fix/refactor/docs/test；信息要说明"为什么改"。

### 2.3 依赖（requirements.txt）

pandas / numpy / matplotlib / mplfinance / baostock==0.8.9 / python-dotenv / requests / openpyxl / psutil / Pillow / ttkbootstrap / streamlit。**akshare 与 tushare 全仓零 import**，已从依赖移除（数据源 Baostock 单一）。

`.env`（不入库）注入：`DEEPSEEK_API_KEY`、`DISCORD_BOT_TOKEN`、`DISCORD_SERVER_ID`、`DISCORD_CHANNEL_ID` 等（见 `config/settings.py:22-32`）。

---

## 三、目录全景与归置铁律

### 3.1 目录表

| 目录 | 装什么（大白话） |
|:---|:---|
| `core/` | 发动机舱：取数、算指标、扫描、评级、信号生命周期（41 个 .py / 12,042 行，2026-09-22 实测） |
| `core/strategies/` | 策略实现 + 几何求解器（12 文件，见 §5） |
| `core/signal_tracker/` | 信号"户口本"：归档/去重/生命周期（8 子模块，见 §7） |
| `core/patterns/` | 高胜率形态库（周线牛旗三推等，实验性，未接主流水线） |
| `tools/` | 输出与外围：Discord 推送+画图、抓数、持仓管家、AI 日志库、月线快照工具（15 个 .py / 4,278 行） |
| `config/` | 参数与规矩本：settings.py、**sop_rules.md（PA 理论唯一裁决源）**、评级因子 json、字体、人设 |
| `tests/` | 自动化测试 + 回测脚本（27 个 .py / **199 个测试函数（当前实测）**，门禁守卫基线文件 `test_baseline.txt` 仍记 156 已过时） |
| `docs/` | 全部文档（61 个 .md：策略规范/方案/复盘/评审，见 §13 文档地图） |
| `logs/` | 运行日志 |
| `archive/` | 已退居二线的研究/回测脚本（git mv 归档，可逆，2026-07-25 起） |
| `strategy_lab/` | 策略研究笔记（周线伏击计划、月线快照人读版产出也在这里） |
| `data/` | 数据库 + 各种 json 状态文件（**大多不入库**，见 §6） |
| `research-multiagent-collab/` | 用户研究"多 AI Agent 同目录协作"的报告（8 个 .md，非本项目代码） |
| `.agent/` | 质量门禁 quality_gate.py、测试基线、rules 规则、STATUS.md（**不入库**） |
| `.agents/skills/` | 两个 skill：strategy-onboarding（策略接入 SOP）、code-reviewer |
| `hooks/` | pre-commit 钩子 + guard_source_shrink 源码收缩守卫 |

### 3.2 根目录文件逐一说明

| 文件 | 是什么 |
|:---|:---|
| `hunter.py`（1433 行） | 命令行主入口：交互菜单 + CLI，日线/周线扫描全流程编排 |
| `gui_dashboard.py`（1425 行） | 桌面操盘台界面（Tkinter + ttkbootstrap darkly 主题） |
| `launch_dashboard.py`（25 行） | 被桌面 VBS 调用的启动器 |
| `README.md` / `AGENTS.md` / `DATA_SAFETY.md` | 核心三文档，**留根目录** |
| `BOOTSTRAP.md` / `SOUL.md` / `USER.md` | 身份三件套（助手代号"阿布"、用户画像），**不擅动** |
| `.env` | 密钥，不擅动、不入库 |
| `hold_list.txt` | 持仓清单（格式如 `sh.600961,27.16`，供持仓管家用） |
| `universe_mainboard.txt` | 主板股票代码清单（逗号分隔，扫描宇宙备份） |
| `monthly_range_break_production_scan.json/.md`、`pinbar-month-scan-results-range-break.json/.md` | 月线试跑时期的产物（月线已降维为独立小工具，见 §4.4） |
| `debugV7.1_for_antigravity.code-workspace` | VS Code 工作区配置 |
| `requirements.txt` | 依赖清单 |

### 3.3 归置铁律（用户 2026-08-22 拍板）

- **根目录禁止新建文件夹**（未经用户明确允许）。
- 文档→`docs/`；测试/回测脚本→`tests/`；日志→`logs/`；校准回测产物→`archive/`。
- `.gitignore` 不入库项：`data/*.db*`、`config/fonts/*.ttf`、`logs/`、`.agent/`、`.workbuddy/`、`calibration_*`、`gap_h2_*.{png,csv,json}`。

---

## 四、分层架构与数据流

### 4.1 五层架构

```
L1 入口     → hunter.py（CLI/菜单）+ gui_dashboard.py（桌面台）
L2 流水线   → core/scanner.py（日线）/ core/scan_engine.py（周线编排）
L3 策略     → BaseStrategy + StrategyRegistry（插件化、自描述、元数据驱动）
L4 输出     → tools/notifier（Discord+K线图）/ tools/watchlist（Facade→SignalTracker）
L5 基础     → core/database（SQLite WAL，schema 唯一主人）/ core/data_provider（Baostock 离线数据层）
```

### 4.2 日线流水线（每天收盘后跑）

```
GUI「下载行情」→ core/data_provider.update_daily_data_batch（:872，带 progress_callback/cancel_event）
GUI「策略扫描」→ hunter.run_pipeline_once（:809）→ 四阶段：
  1. _scan_market（:333）   数据层：一次取数→一次算指标（calculator.add_indicators）→6 官方策略命中最新 K 线
  2. _classify_signals（:399） AI 审计层：DeepSeek 二次筛选（--no-ai 可旁路；部分策略硬编码快速通道）
  3. _compose_report（:600）  简报组装
  4. _dispatch_charts（:733） 推送层：K 线图 + Discord
结果归档 → signal_archive 表（GUI 的唯一数据源）
```

hunter CLI 参数（`hunter.py:1065-1072`）：`--strategy`（可逗号分隔/ALL）、`--limit`、`--timeframe daily|weekly`、`--weeks`、`--track`、`--report`、`--no-ai`、`--debug`。交互主菜单 5 项（:1160-1168）：扫描新机会 / 信号追踪 / 持仓管家（Guardian）/ 数据同步 / 复盘录入。

hunter 还自带运行守护：崩溃兜底告警 `_notify_crash`（:1349）、心跳 `_write_heartbeat`（:1387，写 `data/last_run.json`）、数据库健康自检 `check_database_health`（:942，防空库静默运行）、DB 基线对比（:915-940）。配套工具 `tools/check_heartbeat.py`。

### 4.3 周线流水线（已接入 GUI，2026-08-30 完成 0-7 步）

```
数据：data_provider.update_weekly_data_batch（:1140，多数情况拿本地日线在内存里合成周线，不走网络）
扫描：core/scan_engine.run_weekly_scan（:872，带 progress_callback/cancel_event）
      → 只跑缺口家族 3 策略（WEEKLY_GAP_STRATS 集合 :928-930：STRUCTURAL_GAP/GAP_PINBAR/GAP_H2）
      → 推 Discord + 写 data/weekly_watchlist.json + strategy_lab/weekly_ambush_plan.md + 归档入库
      → 返回回执 dict：{'ran_gap','ran_3k','gap','k3_breakout','k3_gap_test','stocks'}
        （回执的意义：区分"跑了但 0 命中"与"根本没跑"；ran_3k 恒为 False，见下）
```

**⚠️ 周线策略边界（勿回退的决策）**：周线**只含 3 个缺口家族策略**。3K 曾被某次 agent 自作主张加进周线，用户 2026-08-30 明确"周线暂只考虑 gap"，已在 commit `6ef6371` 撤回；`run_weekly_scan` 现在会忽略误传的 3K。`scan_engine.py` 里 `scan_weekly_3k_signals`（:412）和 `format_push_weekly_3k`（:712）函数仍在（保留未删），但 `run_weekly_scan` 不再路由到它们——不要误以为周线还能跑 3K。MTR / AWIL / 3K 仅日线。

**周线 GUI 口径（用户已拍板）**：
- 顶部「日线|周线」切换，完全隔离（`gui_dashboard._tf_sql` :396-406 做 SQL 过滤：日线 `timeframe='daily'`；周线 `timeframe='weekly' AND date(scan_date)=date(created_at)`——后者是"排除历史回测回填"判据，**只对周线成立**，日线存在跨午夜扫描导致两日期差一天，不可套用）。
- 周线下拉**以周（周五）为单位**，选某周看「**截至该周仍存活**」的全部信号（`signal_date<=W AND (resolved_date IS NULL OR resolved_date>W)`），不是"该周新触发"。
- 周线模式 K 线图换**周 K**。
- "待建仓"栏：只读扫描产物 JSON，不动库（发现 8 的丁方案）。
- Discord 不改：继续全量推（当第三方备份）。

### 4.4 月线：已降维为独立只读小工具（2026-09-02 拍板）

- 只保留 `tools/monthly_pinbar_snapshot.py`：纯只读扫"本月最新月 K 有没有区间破位 Pinbar（长影线反转 K）"，**不归档、不推送、不追踪**，用户每月手动跑一次；产出 `strategy_lab/monthly_pinbar_snapshot.md`（人读）+ `data/monthly_pinbar_snapshot.json`（机读）。
- GUI / hunter / scan_engine / signal_tracker 的 monthly 代码**全部移除**（grep monthly 归零）。
- `MonthlyRangeBreakStrategy` 仍**注册**在 `StrategyRegistry._strategies`（strategy_registry.py:48-51），但**不在** `_OFFICIAL_LIST`（:57）——它只被快照工具复用，不参与日/周扫描流水线。2026-09-03 证实：一旦列入官方列表，日线扫描会混入月线信号（commit `bd0947b` 修复的正是这个）。
- 配套定时任务：仅在精确月末交易日 19:30 触发（平台侧 4 个 YEARLY 自动化，非每天）。

### 4.5 GUI 操盘台（gui_dashboard.py）

- 入口链：桌面 `Brooks-AI操盘台.vbs` → `launch_dashboard.py` → `gui_dashboard.py`（**系统 Python 3.14 运行**，pythonw 无终端窗口——这就是"禁拿日志当进度"的原因）。
- 主要功能（方法名可 grep 定位）：下载行情 `start_sync`（:1129）、日线扫描 `_start_scan_daily`（:1166）、周线扫描 `_start_scan_weekly`（:1187，内部**写死跑两轮追踪**，原因见 §7.2）、红色终止 `_on_stop`（:1306）、日/周切换 `_on_timeframe_change`（:380）、日期/截至周下拉、左侧策略分组栏、关注标记（树形列 #0 红点图片 `_make_dot_image` :742，数据在 `data/gui_favorites.json`）、K 线缩略图自适应、TradingView 跳转、状态栏按周期报数 `_update_status`（:1345）。
- 数据源 = `signal_archive` 表（唯一）。

---

## 五、策略系统

### 5.1 注册表现状（core/strategy_registry.py，2026-09-05 读码核实）

| 注册键 | 显示名（display_name） | 周期 | 实现文件 |
|:---|:---|:---|:---|
| `MTR_MASTER` | MTR反转 | 日线 | `core/strategies/mtr_strategy.py` |
| `STRATEGY_3K` | 3K动能 | 日线 | `three_k_strategy.py` |
| `STRATEGY_STRUCTURAL_GAP` | GAP H1 | 日线+周线 | `structural_gap_strategy.py` |
| `STRATEGY_GAP_PINBAR` | GAP Pinbar | 日线+周线 | `gap_pinbar_strategy.py` |
| `STRATEGY_GAP_H2` | GAP H2 | 日线+周线 | `gap_h2_strategy.py` |
| `STRATEGY_GAP_H2_ENHANCED` | GAP H2 增强（回测专用） | backtest（回测专用） | `gap_h2_enhanced_strategy.py` |
| `STRATEGY_AWIL` | AWIL趋势 | 日线 | `awil_strategy.py` |
| `STRATEGY_MONTHLY_RANGE_BREAK` | （月线区间破位Pinbar） | 月线（仅工具用） | `monthly_range_break_strategy.py` |

- `_strategies` 共 **8 项**（:24-57）；`_OFFICIAL_LIST`（:62）只含前 6 项（MTR_MASTER / STRATEGY_3K / STRUCTURAL_GAP / GAP_PINBAR / GAP_H2 / AWIL）= 日/周扫描实际遍历的策略池；`STRATEGY_GAP_H2_ENHANCED`（回测专用，不在官方池）与 `STRATEGY_MONTHLY_RANGE_BREAK`（快照工具专用）**不得**加回官方列表。
- 查询接口：`list_strategies()`（官方 6 项）、`get_metadata(name)`、`get_strategies_by_timeframe(tf)`。未知策略名**显式报错**（:93-98，不再静默回退 MTR——防 CLI 拼错名字悄悄跑错策略）。

### 5.2 自描述协议（新策略接入只改 3 处）

每策略必须实现三件套（完整模板见 `.agents/skills/strategy-onboarding/SKILL.md`）：
1. `get_metadata()` — display_name / supported_timeframes / sl_column / entry_column / tp_columns / tp_multiplier 等元数据
2. `get_signal_info(df)` — 告诉扫描器从 DataFrame 哪些列取信号/止损/止盈
3. `annotate_chart(ax, ...)` — 在 K 线图上画策略标注

接入 3 步：`core/strategies/` 新建文件继承 `BaseStrategy` → `_strategies` 注册 → 实现三件套。**无需再改** scanner / notifier / hunter / scan_engine（它们全部经注册表动态取元数据）。

**命名规范（用户拍板，勿回退）**：策略对外花名 = `get_metadata()['display_name']`，是 Discord 文案、K 线图标题、分组简报、AI 持仓文案、诊断日志的**唯一数据源**；`type` 字段 = 注册表 key（机器用）。

### 5.3 其他策略层文件

- `mtr_structural_v35.py` — MTR 结构化引擎；`geometric_engine.py` — 几何趋势线求解器（被 awil/mtr 复用，非注册策略）。
- `core/patterns/` — `BasePattern`+`PatternRegistry`+`weekly_bull_flag.py`（周线牛旗三推，实验性，未接主流水线）。

---

## 六、数据库（schema 冻结——最高优先级护栏）

### 6.1 两库两主人

| 数据库 | 唯一 schema 主人（DDL 白名单） | 拥有的表 |
|:---|:---|:---|
| `data/baostock.db`（约 593MB） | `core/database.py` | daily_bars / weekly_bars / abu_indicators / signal_archive / trade_reviews |
| `data/ai_journal.db` | `tools/journal.py` | hunter_journal / guardian_journal |

**DDL（Data Definition Language，建表/改表语句）只允许出现在这两个文件**。白名单之外任何文件出现 `CREATE/ALTER TABLE` 会被质量门禁直接阻断提交。禁止绕过代码用 DB 工具手改线上库结构。

**数据规模（2026-09-22 只读重数）**：daily_bars 2,868,090 行（约 287 万）、weekly_bars 2,049,030 行（约 205 万）、signal_archive 14,696 条（daily 11,531 / weekly 3,154 / monthly 11；其中 6,162 条为 `BT_` 回测污染行，见 §7.3 发现 6）。

改表正确流程：只在主人文件改 DDL → 跑 `core/schema_guard.py` + `tests/test_schema_integrity.py` → 评审 → 提交。

### 6.2 两层护栏

- **代码层**：`.agent/quality_gate.py` 静态扫描 DDL 白名单（DDL_ALLOWLIST，:48-51）。
- **运营层**：`core/schema_guard.py` 直接打开真实库跑 `PRAGMA integrity_check`（抓损坏）+ 实际表/列与"实时解析自主人源码的声明 schema"比对（抓带外漂移）。

### 6.3 data/ 目录文件清单（碰任何东西前先过 §〇 铁律 1）

`baostock.db`(+wal/shm)、`ai_journal.db`(+wal/shm)、`stock_names.json`（股票中文名缓存，避免联网被封）、`gui_favorites.json`（GUI 关注列表）、`last_run.json`（心跳）、`db_baseline.json`（库大小/时间基线，hunter 健康自检用）、`weekly_gap_watchlist.json` / `weekly_watchlist.json`（周线扫描产物）、`monthly_pinbar_snapshot.json`（月线快照）、`crash_log.txt`（崩溃日志）、`watchlist.json` / `signal_tracker_report.json` / `validation_report*.json` / `gold_standards.json` / `notion_pa_journal.json`（历史/工具产物）。

**WAL 小知识**：`.db-wal`/`.db-shm` 是 SQLite WAL 模式（先写"便签"再合并进主库的记事方式）自动产生的辅助文件，只读库也会产生。平台的"安全删除"机制曾把它们的"出现又消失"误判为删文件送进回收站（2026-08-12 事故），DATA_SAFETY.md 四铁律即由此而来。**真身库历次检查均完好。**

---

## 七、信号生命周期与已知缺陷（🔴 新 agent 必读，别踩同样的坑）

### 7.1 子包结构（core/signal_tracker/，P11 拆分后 8 模块）

`__init__.py` 对外导出：`archive_signal`（归档）/ `track_signals`（状态推进）/ `get_resolved_gaps` / `generate_report` + `format_tracker_discord_msg`（报表）/ `run_tracker_dashboard`（仪表盘入口）/ compat 兼容函数。子模块：`_shared` / `archive` / `tracking` / `gaps` / `report` / `dashboard`(约 539 行) / `compat`。

`tools/watchlist.py` 是 **Facade（门面——外壳转手，真活在 SignalTracker）**，委托到本子包。**禁止引入任何独立于 SignalTracker 的新状态追踪**（AGENTS.md 绝对禁止）。

### 7.2 🔴 缺陷一：track_signals 一次调用只推进一步（已绕过，未根治）

`_track_pending` 把 PENDING（等待中）推到 ACTIVE（已入场）后，**本轮不再查持仓超期**。任何补跑/跟进场景必须**跑两轮**才收敛（实测第 1 轮结 185 条、第 2 轮归零）。GUI 的 `_start_scan_weekly` 已写死跑两轮并加注释。`track_signals` 有 `real_scan_only` 参数（默认 False 向后兼容），开启后跳过回测回填，**仅对周线成立**。

### 7.3 🔴 缺陷二：signal_archive 的 status 列当前不可信（发现 6，待用户批准写库修复）

周线状态机自 2026-04-11 停摆：`track_signals()` 的唯一入口 `run_tracker_dashboard()` 只由 `hunter.py --track`/菜单触发，用户 4 月起没跑过。240 条周线 PENDING 的 `updated_at` 恒等于 `created_at`；干跑 258 条 → 195 应转已入场 / 43 失效 / 11 止盈 / 6 止损 / 1 过期（仅 3.5 秒）。日线同病（694 条 7 月前仍挂 PENDING）。**做"存活"清单不能直接信 status 列**——GUI 周线清单已改用"截至某周存活"的日期口径规避。

### 7.4 已定夺的两件事（勿再翻案）

- **发现 7（重复归档）**：`signal_id` 格式中途从缺周期改成带周期，`INSERT OR IGNORE` 防重失效，周线 526 行里 150 行是双份（日线 70 行）。**用户拍板不清库**，显示层去重已足够；库里一行没动。
- **发现 8（待建仓信号）**：扫描 28 条只有 12 条入库，差的 16 条是"形态已成立、价格未到位"。用户选**丁方案**：界面只读扫描产物 JSON 加"待建仓"栏，**不动归档规则、不动库**。

### 7.5 通用教训（写进肌肉记忆）

- 凡口径依赖某个字段，**先验该字段时效性**（按周期查 `MAX(updated_at)`、比 `updated_at==created_at` 比例）再动手。
- **多阶段/多策略批量任务必须带回执**，回执要能区分"执行了但 0 结果"与"根本没执行"。

---

## 八、评级与 PA 理论约束（交易层面的第一性）

1. **PA 铁律**：因子（用来打分的依据）只能是价格行为本身——开盘/最高/最低/收盘/形态结构。**成交量、技术指标、基本面一律不得进因子**。已实测核对：`core/strategies/` 中 volume 仅以"禁止"注释出现，无逻辑引用。
2. **裁决源**：`config/sop_rules.md`（33KB / 16-Step SOP，Al Brooks PA 理论权威源）。任何评级因子必须能在其中溯源（标注 Step 号），无法溯源禁入。
3. **字母评级已证伪**：A+/A/B/C/D 在 9/9 策略批次中验证为统计噪声（不单调），**已去字母化，不得恢复**；界面只显命中因子证据。
4. **EV（期望值）公式**：`EV = 胜率×avg_winR − (1−胜率)×avg_lossR`（R = 以止损距离为 1 的盈亏倍数）。EV>0 才及格，胜率不是唯一门槛。回测结论：周线缺口家族 EV 为正（真实 edge），日线普遍弱。
5. **缺口逻辑两条硬规则**：
   - 存活判定**不能只看收盘价**，必须同时确认最低价（low）没击穿缺口下沿（gap_floor）——下影线击穿后缺口性质已变。
   - 生命周期过滤**必须包含信号当根 K 线**（很多反转 K 本身就是回测缺口那根，从 +1 开始查会漏判）。
6. **跨周期对齐**：日线策略引用周线数据时只能用**上周已走完**的周线，禁用本周未完成的；跨周期 merge 必须注释对齐方式。

---

## 九、质量工程与门禁

### 9.1 质量门禁 `.agent/quality_gate.py`（纯标准库、零依赖）

- **红线模式扫描**（FORBIDDEN_PATTERNS :31-37）：`sys.path.insert` / `logging.basicConfig` / 裸 `except:` / `from x import *`。唯一豁免：`core/paths.py`（sys.path.insert 的合法注入点）。
- **DDL 白名单**（:48-51）：见 §6.1。
- **测试数守卫**：`def test_` 计数对比 `.agent/test_baseline.txt` 基线（当前实测 **199**；基线文件仍记 **156 已过时**，gate 因 199≥156 通过），**防删测试让检查变绿**。
- 扫描排除目录（:27-28）：`.git/__pycache__/.venv/node_modules/.workbuddy/.agent/quant/strategy_lab/docs/tests`（注意 tests 本身不扫红线，但测试数守卫覆盖）。
- 用法：`python .agent/quality_gate.py [--strict|--init-baseline]`，违规则退出码非 0。

### 9.2 pre-commit 钩子（hooks/pre-commit）

每次 commit 前自动跑两道：① `hooks/guard_source_shrink.py`（源码收缩守卫——拦截被清空/截断的 .py 进历史，防"文件被改坏"类事故）；② quality_gate。克隆后需 `cp hooks/pre-commit .git/hooks/pre-commit` 安装一次。

### 9.3 编码规范要点（.agent/rules/coding-standards.md）

命名 snake_case/PascalCase/UPPER_CASE；导入三段式（标准库→第三方→本地）禁止通配；**异常必须捕获具体类型且带业务上下文记日志**（`logger.error(f"[{strategy}] {code} 扫描失败: {e}")`），批量操作单失败不断整体；日志统一 `from core.log_config import get_logger`；新函数必须类型注解 + Google 风格 docstring；**禁止 for 循环逐行遍历 DataFrame**（iterrows/itertuples/iloc 全量循环），必须 pandas/numpy 向量化（swing points 等稀疏结构例外，需注释说明）；新文件 ≤500 行、新函数 ≤80 行。

### 9.4 测试

- 命令：`.venv/Scripts/python.exe -m pytest --maxfail=2`（按文件分批跑可绕过 pytest 框架自身的 capture I/O bug——全量跑失败是框架问题不是代码问题；部分用例需联网 Baostock，离线跳过/失败属预期）。
- 关键文件：`test_p1_p2_regression.py`（98 用例回归）/ `test_schema_integrity.py`（真实库 + 人为加列必被抓）/ `test_rating.py` / `test_calculator.py` / `test_awil_strategy.py` / `test_three_k_strategy.py` 等。
- **改 Python 代码后必须跑门禁**（本项目用户级 skill：quality-gate）。

### 9.5 GUI 冒烟（凡改 gui_dashboard.py 必做）

门禁和 ruff 都查不出 Tk 版本能力缺口。必须用 `C:\Python314\python.exe` 真跑一个"构造界面"最小脚本，确认无 TclError/NameError；改完再补 `ruff check --select F821`。

---

## 十、GUI 红线速查（D1-D6，与全景手册同源）

| # | 红线 |
|:---|:---|
| D1 | 后台长操作必须 `progress_callback(done,total,info)` + `root.after` 节流更新进度条；**禁拿日志当进度**（pythonw 无终端）；回调在后台线程，必须 try/except 保护 |
| D2 | 改完 gui_dashboard.py 必须跑 §9.5 冒烟 |
| D3 | 单格着色用图片或独立控件，**禁用 Tk 9.0 才有的 Treeview 列 style**（本机 Tk 8.6，用了必崩） |
| D4 | 附加面板用 `place` 浮层贴视觉留白，**禁新开 grid 列抢 K 线图宽度** |
| D5 | K 线图一律复用 `notifier.generate_chart_bytes`，**禁另写绘图**；横轴用整数 0..N-1，禁传 Timestamp |
| D6 | 一个按钮只做一件事；红色「终止」用于误触取消 |

---

## 十一、用户画像与协作约定（来自 SOUL.md / USER.md / BOOTSTRAP.md，根目录身份三件套）

- **用户**：A股 PA 实战交易员，**非程序员**；人机协作、只推信号不下单；重工程质量与可回测。
- **助手身份**：代号「阿布」，PA 风控与量化专家（懂 Al Brooks PA 也懂工程），全包产品构建者——不是通用工具助手。
- **协作准则**：严谨求证（每步给代码实证或实测，不臆断）；通俗优先（大白话/比喻 → 专业细节）；范围内自主、重大先问；每日小结（主动汇报，不被动等问）；不发散（改动严格收敛在用户明确要求 + 确证真 bug，先精确 scope 再动手，逐项闭合）。
- **交付偏好**：Markdown 报告/文档 + 图表可视化为主；不偏好独立网页 App（除非用户要求）；全中文交互、中文注释、报错给中文翻译。
- **沟通红线与核实铁律**：见 §〇。**不可一味迎合用户**——期望客观对比分析、诚实指出先前判断的不准确之处。

---

## 十二、当前状态快照（2026-09-22 刷新）

### 12.1 规模（当日实测）

core 41 py / 12,042 行（core/strategies 12 py）；tools 15 py / 4,316 行；tests 27 py / 199 测试函数（当前实测；门禁基线 test_baseline.txt 仍记 156 已过时）；顶层 hunter 1433 + gui_dashboard 1425 + launch_dashboard 25；门禁红线 0。

### 12.2 进行中/待办

| 项 | 状态 |
|:---|:---|
| 周线接入操盘台 8 步方案 | 第 0-7 步**已完成**（判据/底层进度终止/界面切换隔离/状态栏报数/补跑跟进/截至周口径去重/周 K 图/待建仓栏）；**第 8 步 = 用户实跑验收，未做**。方案文档 `docs/周线接入操盘台_方案.md` |
| GAP H2 动态入场研究 | ✅ **已完成**（09-21）：生产“活跃投影”（日线持续标注当前挂单价，顺回调下移、内包 K 也下移、HH 收口才成交）+ 回测自算动态入场；4 组配置回测（A 原版出场 EV 最高 / B 新出场胜率最高）；HH-only 判据经 Al Brooks 核实 |
| GAP-H2 / MTR 策略卡 + 示意图 | ✅ **已完成**（09-22）：两份“六类交易员视角”策略卡（`docs/gap_h2_strategy_card.md` / `docs/mtr_strategy_card.md`，逐条对照生产代码核实、纠正旧 spec 漂移）+ 两张利旧 `notifier` 出图工具的典型形态示意图 PNG，已嵌入卡内并入库 |
| 发现 6（status 停摆） | 🔴 待用户批准写库修复，见 §7.3 |
| track_signals 单步缺陷 | 🔴 已绕过（跑两轮），未根治，见 §7.2 |
| 低优先级 backlog | 漏扫日提醒 / 回测期 backfill（补信号回填）/ watchlist 每日定点推送 |

### 12.3 已知架构债

- `core/scan_engine.py:30` 顶层 `from tools.notifier`（core→tools 层级倒置，task #136，不阻塞）。
- hunter `main()` 胖入口（约 370 行）。
- 三个 Gap 策略骨架代码重复。

### 12.4 最近演进时间线（git log 摘要，新→旧）

- 09-22 `cf3457b` GAP-H2/MTR 策略卡 + 典型形态示意图（利旧 notifier 出图，合成数据，嵌入卡内）
- 09-21 `b7844e8`+`6102028` GAP H2 动态入场：生产活跃投影（日线持续提醒）+ 回测自算（HH-only 判据，经 Al Brooks 核实）
- 09-09 `9c1c2bd` Baostock 夜间高峰分时段限流 + 黑名单专项说明
- 09-08 Web 操盘台 v2 原型系列（需求规格 / 高保真 / UX·场景评审 / 纠偏重评估，二期事项未动生产代码）
- 09-05 `3212971` 新增面向 Agent 的项目自述文件
- 09-03 `bd0947b` 月线策略移出 _OFFICIAL_LIST，修复日线扫描混入月线信号
- 09-02 月线彻底降维（`16f35a2` 移除 GUI/hunter/scan_engine/signal_tracker 全部 monthly 代码）；固化沙盒 git 删除 bug 长期约束
- 08-30 周线接入定稿并完成第 0-7 步；周线剔除 3K（`6ef6371`）；扫描回执化（`6597383`）；界面周期隔离（`a71e4c5`）等
- 08-28 按钮拆分 / 关注标记（树形列红点）/ 关注列表浮层化
- 08-25 进度条 + cancel_event 终止；`NameError: EW` 修复并引入 ruff F821
- 08-22 git 仓库修复
- 07-25 V10.0：架构收敛单引擎 + 自动门禁 + 运行监控（崩溃告警/心跳）

---

## 十三、文档地图（按需读取）+ ⚠️ 陈旧文档警示

### 13.1 权威/常读文档

| 要了解… | 读… |
|:---|:---|
| 简明规范（每个 agent 必读） | `AGENTS.md`（根目录） |
| 数据安全原文 | `DATA_SAFETY.md`（根目录） |
| PA 理论 / 评级因子溯源 | `config/sop_rules.md`（16-Step SOP） |
| 策略接入 SOP | `.agents/skills/strategy-onboarding/SKILL.md` |
| 一页速查 | `docs/项目全景速查手册.md` |
| 代码全书（模块字典） | `docs/PROJECT_CODEBOOK.md`（2026-07-25 数据） |
| GAP 策略规范 | `docs/gap_h2_strategy_spec.md` / `brooks_ai_gap_strategy_spec.md` |
| MTR 策略规范 | `docs/MTR_V35_0_STRATEGY.md` |
| 周线接入方案（含 9 大发现与拍板记录） | `docs/周线接入操盘台_方案.md` |
| 回测方法学 | `docs/backtest_methodology.md` / `回测方法学与计划_人工评审稿.md` |
| 事故复盘/审计 | `docs/incident_postmortem_2026-07-31.md` / `integrity_audit_2026-08-01.md` / `accident_prevention_constraints.md` |
| 系统手册 | `docs/SYSTEM_MANUAL.md` |
| 当前版本/待办 | `.agent/context/STATUS.md`（已于 2026-09-22 刷新，结合 §12 看） |
| 项目长期记忆 | `.workbuddy/memory/MEMORY.md` + 同目录日期日志 |

### 13.2 ⚠️ 陈旧文档警示（防止被旧文档带偏——"不能有理解偏差"的关键）

1. **`README.md` 架构图与目录树部分过时**：它仍画着 `tools/scanner_weekly_gap.py`、`tools/update_weekly_db.py` 等文件——这些已并入 `core/scan_engine.py` + `hunter.py`（2026-07-25 V10.0 收敛），**`tools/update_weekly_db.py` 现已不存在**（`scan_engine.py:920` 的报错提示还指向它，属陈旧文案，实际周线同步走 `data_provider.update_weekly_data_batch`）。README 的迭代版本记录表仍有史料价值。
2. **`docs/CHANGELOG_agent_handoff.md`**：写于 2026-05-26，项目路径写的是**已冻结的 antigravity 目录**，P4-P9 债务早已修复，signal_tracker 当时的 1200 行单文件现已拆成 8 子模块包。只作历史交接史料读。
3. **`.agent/codex.md`**：2026-07-22 的"理解快照"，明确自称非规范源。其中"双扫描引擎""signal_tracker.py 1409 行""红线替代物不存在"等结论已过时（替代物 core/paths.py、core/log_config.py 已存在；周线已单引擎）。其中 §4 文档矛盾清单（C1-C13）曾推动过统一，仍有参考价值。
4. **`.agent/context/STATUS.md`**：已于 2026-09-22 刷新（V10.0、8 策略注册、迭代至 09-22）；测试函数数当前实测 199，门禁基线 test_baseline.txt 仍记 156（已过时，本轮 re-init 为 199）。
5. **`docs/项目全景速查手册.md`**：2026-08-30 版，质量高，但"周线方案等你喊开工"一节已过时（0-7 步已完成）；以本文 §12 为准。

---

## 十四、常用命令速查（引擎一律 .venv，GUI 一律系统 Python）

```bash
# ---- 引擎与测试（.venv Python 3.13）----
.venv/Scripts/python.exe hunter.py                      # 交互主菜单（5 项）
.venv/Scripts/python.exe hunter.py --timeframe weekly   # 周线扫描（缺口家族）
.venv/Scripts/python.exe hunter.py --timeframe daily --no-ai   # 日线纯技术面直通
.venv/Scripts/python.exe hunter.py --track --report     # 信号追踪 + 报表
.venv/Scripts/python.exe -m pytest tests/ -v --tb=short # 测试（分批跑绕 pytest capture bug）
.venv/Scripts/python.exe .agent/quality_gate.py         # 质量门禁

# ---- 月线快照（每月手跑一次，纯只读）----
cd 项目根目录 && PYTHONPATH=. .venv/Scripts/python.exe tools/monthly_pinbar_snapshot.py

# ---- GUI 冒烟（凡改 gui_dashboard.py 必做，系统 Python 3.14）----
C:/Python314/python.exe -c "import gui_dashboard; gui_dashboard.main()"  # 或最小构造脚本

# ---- git（系统 git，先 cd 进项目目录）----
cd "D:/life/Trading view/_Project_A/Data_from_Akshare/debugV7.1_for_workbuddy"
"D:/Program Files/Git/cmd/git.exe" log --oneline -10
"D:/Program Files/Git/cmd/git.exe" add <files> && "D:/Program Files/Git/cmd/git.exe" commit -m "[fix] 为什么改"
# ⛔ 禁用：git rm / git stash / git clean / git reset --hard（见 §2.2）
```

---

## 附：术语大白话表（首次接触本项目者速览）

| 术语 | 大白话 |
|:---|:---|
| PA（Price Action） | 价格行为学：只看 K 线的开高低收和形态结构做判断，不看指标/成交量/消息面 |
| EV（期望值） | 长期平均每笔赚多少个 R；EV>0 这个套路才值得做 |
| R（R 倍数） | 以止损距离为 1 的盈亏度量：赚 2R = 赚了 2 倍止损距离 |
| Gap（缺口） | 今开与昨收之间没交易过的空档，视为支撑/阻力的"处女地" |
| Pinbar | 长影线反转 K 线 |
| MTR | Major Trend Reversal，主趋势反转 |
| H2 / AWIL | 两腿回调后第二高点入场 / Always-In-Long 顺势策略 |
| WAL | SQLite 的记事方式：先写"便签"（.db-wal）再合并进主库 |
| DDL | 建表/改表语句；本项目只许两个"主人文件"写 |
| Facade（门面） | 只转发不干活的外壳，真活在被委托的模块里 |
| 质量门禁 | 提交前自动"查违禁品"的脚本，违规则拦下提交 |
| 回执 | 批量任务返回的"我跑了什么、各跑出几条"清单，用于区分"0 命中"与"没跑" |
| T+1 | 今天只能拿到昨天及以前的收盘数据 |
| last-bar-only | 只认当日最后一根 K 线触发的信号，昨日不重推 |

---

*本文件由 WorkBuddy（阿布）于 2026-09-05 基于 2026-09-05 实测 + 项目记忆整理。项目演进后请同步更新本文件（尤其 §5 注册表、§12 状态快照、§13 陈旧警示），保持它是"最可信的一份自述"。*
