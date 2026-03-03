# 系统全场景逻辑完整审计报告 (System Logic Audit)

**版本状态**: V8.13 (Baostock Only / Pure Daily Mode)
**基准时间**: 2026-01-28

> [!WARNING]
> **局限性警告**: 由于切换至 Baostock 数据源，本系统不再支持“盘中/尾盘”实时分析。所有分析必须在交易日 18:00 数据归档后进行，仅用于制定次日交易计划。

---

## 1. 数据同步场景 (Data Sync)

### 1.1 涉及程序清单
- **入口**: `tools/data_manager.py` (CLI: `update_daily_data`) / `gui_dashboard.py` (GUI: `start_sync`)
- **控制器**: `core/data_provider.py`
- **执行器**: `tools/fetcher.py` -> `tools/fetcher_baostock.py`
- **存储层**: `core/database.py` (事务 WAL 模式)
- **配置**: `config/settings.py` (MAX_WORKERS=2)

### 1.2 完整执行流程

1.  **初始化 (Initialization)**
    -   `data_manager.update_daily_data()` -> `core.data_provider.update_daily_data_batch()`。
    -   **关键配置检查**: `MAX_WORKERS` 强制为 2 (在 `settings.py` 读取环境变量)，防止 Baostock 连接被拒。

2.  **任务发现 (Task Discovery)**
    -   **Phase 1: 存量维护 (Maintenance)**
        -   调用 `get_all_last_dates_from_db()` 获取本地库存。
        -   计算目标日期 `target_date` (今天或昨日)。
        -   遍历本地库存，筛选出 `last_date < target_date` 的股票加入任务列表。
    -   **Phase 2: 增量发现 (Discovery)**
        -   调用 `fetcher.fetch_stock_list_active()` 获取 Baostock 全市场列表。
        -   计算差集 `online_codes - local_codes`。
        -   发现的新股加入任务列表。
    -   **Phase 3: 快照同步 (Snapshot)**
        -   **状态**: ❌ **已代码级禁用** (相关逻辑已移除)。

3.  **并发下载 (Concurrent Worker)**
    -   `_fetch_worker(symbol)` 逻辑：
        -   **日期计算**:
            -   新股/无记录: `start_date="20230101"` (根据用户要求拉取3年数据)。
            -   存量: `start_date=last_date + 1 day`。
            -   **End Date**: 修正为 `today_str` (允许在盘后拉归档的当日数据)。
        -   **API 调用**: `bs_fetch_daily_history` (带重试装饰器，max_retries=1，delay=1s)。
        -   **异常处理**: 捕获 `WinError 10054` 等连接错误，但不中断主进程。
        -   成功后放入内存队列 `data_queue`。

4.  **异步写入 (Async DB Write)**
    -   `DatabaseWriter` 线程负责消费队列。
    -   **事务机制**: 批量 `INSERT OR IGNORE`，每 10 条或队列空时 `commit`。
    -   使用 `sqlite3` 的 WAL 模式提高并发写入性能。

---

## 2. 猎手模式 (Hunter Mode)

### 2.1 涉及程序清单
- **入口**: `hunter.py` (CLI: `run_pipeline_once`)
- **核心**: `core/scanner.py` (离线扫描), `core/api_client.py` (AI)
- **辅助**: `core/formatter.py` (Prompt), `tools/notifier.py` (图表/通知), `tools/journal.py` (审计)
- **数据源**: `core/data_provider.py` (纯离线读取)

### 2.2 完整执行流程 (Pure Daily Mode)

1.  **启动与准备**
    -   检查 14:30 软约束 (允许运行但给出警告)。
    -   **启动 AI Workers**: 开启 6 个消费者线程运行 `ai_worker` (注: AI推理线程数设为6，区别于数据下载的2，因本地CPU推理压力不同于网络IO)。
    -   `analysis_queue` (容量 5000) 缓冲扫描结果。

2.  **Stage 2: 技术面海选 (Scanning)**
    -   并发 `core.scanner.run_scanner(code)` (MAX_WORKERS=2)。
    -   **数据读取**: `get_stock_data(code, limit=150)` (读取本地 DB，无网络)。
    -   **完整性检查**: 长度 < 65 则跳过 (`STRATEGY_MIN_DATA_LENGTH`)。
    -   **指标计算**: `add_indicators` (EMA20/60, ATR, ADX, VolMA)。
    -   **策略执行**:
        -   **Strategy Factory (策略工厂)**: `StrategyRegistry.get_strategy(name)` 动态加载。
        -   **支持策略**:
            -   `StrategyV7`: 检查 EMA 排列、Gap Bar、趋势回调强度 (默认)。
            -   `MTR_V1`: 大趋势反转策略 (新增, V8.14+)。
        -   **并发扫描**: 支持 `hunter.py --strategy=ALL` 同时运行所有注册策略。
    -   **命中处理**: 若触发信号，计算止损止盈 (ATR倍数)，封包 `scanner_result` 推入 `analysis_queue`。

3.  **Stage 3: AI 深度分析 (AI Logic)**
    -   **纯日线模式 (Pure Daily)**: 代码中**无** `process_stage_2_intraday_ai` 函数，统一使用 `process_ai_daily`。
    -   **Prompt 生成**: `core.formatter.format_for_ai`
        -   读取 `config/sop_rules.md`。
        -   截取最近 60 根 K 线数据。
        -   注入宏观特征 (趋势强度、EMA 距离)。
    -   **推理**: 调用 DeepSeek API。
    -   **决策解析**: 提取 YES/NO 及理由。
    -   **拒绝处理**: 若 AI 返回 NO，记录日志并淘汰。

4.  **Stage 4: 结果处理与推送**
    -   **图表绘制**: `tools.notifier.prepare_daily_chart` -> `mplfinance`。
        -   在图表左上角印制 AI 核心理由。
        -   绘制 EMA 线和 SL/TP 价格线。
    -   **审计归档**: `tools.journal.log_hunter_decision` 写入 `ai_journal.db`。
    -   **最终推送**:
        -   拼图 (`stitch_images`)。
        -   发送企业微信文本简报 (包含买入/止损价位)。
        -   发送图片。

---

## 3. 持仓模式 (Position Mode)

### 3.1 涉及程序清单
- **入口**: `for_hold.py` (CLI)
- **配置**: `hold_list.txt` (格式: `code,cost`)
- **复用**: `core/formatter.py` (同一套 AI 逻辑)

### 3.2 完整执行流程

1.  **持仓加载**
    -   读取 `hold_list.txt`，解析代码和成本。

2.  **单股诊断 (Single Stock Check)**
    -   调用 `analyze_single_stock_micro`。
    -   **数据读取**: `get_stock_data(limit=150)` (纯日线，确保指标计算有效)。
    -   **复用 Prompt**: 构造伪造的 `scanner_result (type='HOLDING_CHECK')`，复用 `hunter` 的 `format_for_ai`。
        -   这意味着持仓诊断使用的是与猎手选股 **完全相同** 的 PA 评价标准（是否顺势、是否回调到位）。
    -   **AI 建议**: 根据 YES/NO 转化为 看涨/谨慎 建议。

3.  **报告推送**
    -   计算浮动盈亏。
    -   单条推送：每只持仓股单独发送一条微信消息 (这点与 Hunter 的批量汇总不同)。
    -   写入 `guardian_journal` 表。

---

## 4. 白话版程序映射 (Simplified Process Mapping)

以下是各场景中“谁在做什么”的具体映射：

### 4.1 🔄 数据同步 (Data Sync)
1.  **发号施令**: 运行 `data_manager.py` (或在 GUI 点按钮)。
2.  **列清单**:
    -   `data_provider.py` 去翻 `baostock.db`，看谁的日线数据不是最新的。
    -   `fetcher_baostock.py` 去问 Baostock 服务器：“现在整个市场有哪些股票？”，算出差集发现新股。
3.  **跑腿干活**:
    -   `fetcher_baostock.py` 拿着清单，一个一个去下载日线数据 (CSV格式)。
    -   每下载好一个，就扔进一个内存队列 (Queue)。
4.  **入库存档**:
    -   `database.py` 蹲在队列另一头，拿出数据，写入 `baostock.db` 文件。

### 4.2 🔭 猎手模式 (Hunter Mode)
1.  **启动**: 运行 `hunter.py`。
2.  **海选 (大海捞针)**:
    -   `scanner.py` 把几千只股票的日线数据从 `baostock.db` 读出来。
    -   `calculator.py` 算出均线 (EMA)、波动率 (ATR) 等指标。
    -   `strategies.py` 拿着尺子量：是不是 20-Gap Bar？是不是强势回调？
    -   选出几十只“看着像样”的选手。
3.  **面试 (AI把关)**:
    -   `formatter.py` 把这只股票的 K 线图变成一段文字描述 (Prompt)。
    -   `api_client.py` 把这段文字发给 DeepSeek 老师。
    -   DeepSeek 老师说 YES 或 NO。
4.  **发榜 (通知)**:
    -   `notifier.py` 给通过的股票画图 (把 AI 的评语印在图上)，拼成长图，推送到微信。
    -   `journal.py` 把这次面试的过程记在 `ai_journal.db` 小本本上。

### 4.3 🛡️ 持仓模式 (Position Mode)
1.  **查房**: 运行 `for_hold.py`，它先看一眼 `hold_list.txt` 里有哪些“自家人”。
2.  **体检**:
    -   `data_provider.py` 调出这只股的病历 (日线数据)。
    -   `formatter.py` 用**和猎手模式一模一样**的标准，生成体检报告 (Prompt)。
3.  **诊断**:
    -   DeepSeek 老师看一眼，告诉你：这只股现在是“健康 (Bullish)”还是“亚健康 (Bearish)”。
4.  **医嘱**:
    -   `notifier.py` 直接发微信告诉你：现价多少，赚了多少，AI 建议拿着还是跑路。

---

## 5. 高级工程特性 (Advanced Engineering Characteristics)
*(本章节记录了 V8.13-V8.14 引入的系统级架构升级)*

### 5.1 Hunter/Guardian 双模提示词工程 (Dual Mode Prompt Engineering)
系统不再使用单一的 "Analysis Prompt"，而是针对 **建仓** 和 **持仓** 场景设计了两套完全独立的思维链 (Chain of Thought)，以解决 "屁股决定脑袋" 的心理偏差。

| 特性 | Hunter Mode (猎手 / 找机会) | Guardian Mode (守卫 / 管持仓) |
| :--- | :--- | :--- |
| **思维核心** | **Signal Identification (信号识别)** | **Health Check (健康度检查)** |
| **敏感度** | **High Sensitivity**: 对微小的反转信号保持敏锐。 | **Low Sensitivity**: 容忍正常回调，避免惊弓之鸟。 |
| **XML 结构** | 侧重 `<SETUP_QUALITY>` (盈亏比、结构等级)。 | 侧重 `<CLIMAX_WARNING>` (乖离率、趋势力竭)。 |
| **关键指令** | "寻找入场点，若无明确信号则通过。" | "寻找离场理由，若无致命风险则持有。" |

### 5.2 动态 XML 构建技术 (Dynamic XML Construction)
*   **Old Way**: 依赖 `config/sop_rules.md` 静态模板拼接，灵活性差。
*   **New Way**: `core/formatter.py` 实现了 **Dynamic XML Injection**。
    *   Python 在运行时计算客观事实 (如 `Trend_Slope`, `Structure_Type`)。
    *   构建结构化的 `<MARKET_CONTEXT>` XML 数据块。
    *   AI 被强制要求基于 XML 事实进行推理，而非仅看 ASCII 图表。

### 5.3 策略工厂与并发扫描 (Strategy Factory & Concurrency)
*   **Strategy Registry**: 实现了标准的工厂模式。`scanner.py` 不再硬编码策略逻辑，而是遍历 `StrategyRegistry`。
*   **Multi-Strategy Scanning**: 支持 `hunter.py --strategy=ALL`，可在一个扫描周期内同时运行 V7 (顺势) 和 MTR (反转) 等多个策略，并将结果合并去重。

### 5.4 物理引擎与动量注入 (Physics Engine & Momentum Injection)
*(Added in V1.9)*
*   **Problem**: 纯几何形态无法识别"假突破"或"接飞刀"风险。
*   **Solution**: `calculator.py` 新增了全向量化的物理特征计算：
    *   **Urgency**: `gap_down_count_20` (缺口动能)。
    *   **Resistance**: `linreg_res` (线性回归阻力)。
    *   **Rejection**: `relative_vol` (相对量能验证)。
*   **Injection**: 这些特征通过 `<MOMENTUM_FACTS>` 块动态注入 Prompt，强制 AI 关注物理势能而非仅仅是图形。


## 6. V28.5 重大更新：MTR 阿布正统逻辑回归 (MTR V28.5 Orthodox Brooks)
*(Added in 2026-02-07)*

### 6.1 核心逻辑升维 (MTR V28.5 Orthodox)
经过四轮与“模拟 Al Brooks”的深度研讨，MTR 策略完成了从机械参数向**纯粹物理结构**的进化。

1.  **战略性次高点 (Strategic MLH)**:
    *   **Old**: 机械连接 10 日高点作为趋势线。
    *   **New**: 识别“导致了新低的最后摆动高点 (Swing High)”。这是空头在下跌趋势中最后一次成功的防守堡垒，其被打破具有极高的物理确定性。

2.  **力量宣言 (Surprise Bar)**:
    *   引入 **>1.5x ATR 强阳线** 作为趋势种子。即便价格尚未突破 MLH，只要出现如此强度的反击，即视为趋势已经中断（Seed of Trend），允许提前进入观察阶段。

3.  **ABC 二段式回调 (Two-Legged Pullback)**:
    *   捕捉回调内部的微观波动。要求回调显示出**动能衰竭 (Momentum Decay)**：
        - 回撤斜率比主跌段变缓 60% 以上。
        - 阴线实体平均大小显著萎缩。
        - 识别“ABC”两段式下探结构，在第二段下探不破前低时确认 Higher Low 的物理韧性。

4.  **动态管理与阶梯止损 (MM & EMA20 Guard)**:
    *   **Measured Move (测算目标)**: 目标位由“第一腿”的高度（Extreme High - Extreme Low）直接投影，取代固定 R 倍数。
    *   **EMA20 守护**: 价格入场后，若能成功收盘于 EMA20 上方至少 2 根 K 线，止损自动移至 **Breakeven (保本位)**，锁定无风险持仓。

### 6.2 信号分级与确认 (Confirmation Mechanism)
*   **Signal Bar**: 不再仅限阳线，长下影线（Hammer）也被纳入。
*   **Next Bar Confirmation**: 入场点必须由下一根 K 线**突破信号棒高点**来触发，拒绝任何形态完美但动能缺失的伪信号。

### 6.3 性能与数学期望
*   **回测表现**: 在 300 只随机采样股中，Expectancy 为 **+0.19R**，较 V27 提升了 72%。
*   **资源占用**: 维持串行处理模式，确保 Free Memory > 500MB，CPU 负载均衡。

---

## 7. 术语正名 (Terminology Alignment)
全系统已完成向 Al Brooks 经典价格行为学（PA）术语的对齐：
*   **MLH (Major Lower High)**: 战略性次高点（趋势线锚点）。
*   **1ST_LEG**: 第一腿（Surprise 或 MLH 突破）。
*   **HL_TESTING**: 更高低点测试（PB 阶段）。
*   **SIGNAL_BAR**: 信号棒（入场前奏）。
*   **ENTRY**: 确认入场（物理触发点）。
