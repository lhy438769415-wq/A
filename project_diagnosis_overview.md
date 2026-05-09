# Brooks-AI Quant System (V10.0) - 项目诊断全貌报告

## 1. 项目概述 (Project Overview)
**Brooks-AI** 是一套基于 Al Brooks 价格行为理论 (Price Action) 的 A 股全自动量化扫描与自动化决策系统。
该系统当前主要面向日线（Daily）与周线（Weekly）双周期，能够进行全市场股票的技术面扫描、指标计算、多重策略（动量/缺口/回调）信号检测、基于 DeepSeek 的 AI 深度审计、以及 Discord 的实时信号追踪与多图表推送。

**系统运行主要特征**：
- **完全自动化流程**：数据更新 -> 全市场扫描 -> 策略初筛 -> Watchlist跟踪 -> AI或直通车审核 -> 微信/Discord通知。
- **离线与断网容错**：最近版本优化了 Baostock 获取股票名称的网络依赖，并加入了 JSON 本地缓存。
- **高性能与向量化**：核心扫描指标计算使用 pandas 向量化计算，抛弃了 for 循环，支持使用 ThreadPoolExecutor 进行多线程全市场并发扫描。

---

## 2. 系统核心架构与主流程 (Architecture & Main Pipeline)

### 2.1 主入口控制 (`hunter.py`)
整个系统的核心总控，支持交互式命令行菜单与定时任务 CLI 直通：
- **日线扫描 (`daily`)**：`_scan_market` 获取数据计算指标 -> `_classify_signals` 更新观察池并进行 AI 审计/分流 -> `_compose_report` 整合结果 -> `_dispatch_charts` 推送 Discord。
- **周线扫描 (`weekly`)**：直通高胜率缺口及形态扫描（如 `tools/scanner_weekly_gap.py`）。
- **信号追踪 (`track & report`)**：运行系统内置的 Watchlist / 仪表盘，推送持仓与信号状态。

### 2.2 核心模块划分 (`core/`)
- **`data_provider.py` & `database.py`**：数据层，封装基于 Baostock 的本地 SQLite DB，包含日线和周线行情的存取及同步。
- **`scanner.py`**：核心向量化扫描器。对单只股票预加载数据、一次性计算技术指标 (`calculator.py`)、然后遍历各策略检测 K 线信号。
- **`calculator.py`**：纯技术指标计算库（如 EMA、ATR、MACD 等），无状态且已被充分测试。
- **`strategy_registry.py`**：策略注册中心，用于动态装载各类策略。
- **`strategies/`** 与 **`patterns/`**：实现具体量化策略的地方，包含传统的日线模型和 V9.5 以后引入的高胜率周线形态模型。
- **`signal_tracker.py`**：负责管理信号的生命周期（如触发、止盈、止损、失效）及本地数据库归档持久化。

### 2.3 工具层 (`tools/`)
- **`notifier.py`**：核心的消息推送层，负责将包含 K 线与标注信息的图表进行渲染（matplotlib/mplfinance）并发送到 Discord。包含图片大小优化、重试限制（解决 Rate Limit API 问题）。
- **`watchlist.py`**：在盘中/盘后持续跟踪未被触发或已触发信号的生命状态。
- **各类扫描工具与分析脚本**：例如 `scanner_weekly_gap.py`（周线缺口形态）、`evolve_gap_strategy.py`、`backtest_*.py`（各类回测脚本）。

---

## 3. 核心交易策略库 (Strategy Library)

系统已演进至采用**高胜率插件化形态库**的阶段，包含四大主要流派：
1. **Gap Pattern Library (周线)**：
   - `weekly_bull_flag.py` (周线牛旗三推)
   - `weekly_ioi.py` (突破缺口+内外内收敛)
2. **MTR (Major Trend Reversal, 日线)**：作为储备策略，主趋势反转信号。命中后会交由 AI 模型（DeepSeek）结合上下文决定是否放行。
3. **3K Momentum (日线)**：三K线动量突破形态 + 缺口测试验证。
4. **Structural Gap (周线)**：结构性测量缺口，支持 V9.0 的四因子积分评级（🌟 极品 / 👍 常态 / ⚠️ 低预期），此策略支持免 AI 直接过检（直通车）。

---

## 4. 数据流转与存储机制

1. **持久化存储**：
   - `data/baostock.db` (日线数据 ~500MB)
   - `data/baostock_weekly.db` (周线数据)
   - `data/stock_names.json` (防卡死本地标的名称缓存)
   - `data/journal.db` (AI 判决历史存档)
2. **信号生命周期**：
   - **扫描期**：生成原始候选名单。
   - **观察期 (Watchlist)**：价格暂未触发买点 (Buy Stop)，但在监控中。
   - **触发期 (TRIGGERED)**：突破入场，进入持仓。
   - **失效期 (INVALIDATED)**：形态破位或止损。

---

## 5. 项目痛点与近期修复诊断关键点
诊断代码和后续开发需重点关注以下近期解决的回归和故障点，防止系统倒退：
- **网络与并发死锁问题**：由于 Baostock 请求的超时导致周扫描长时间卡死。目前通过 `config/stock_names.json` 本地缓存进行弱网/无网防护。
- **Discord 图片截断/大小限制**：针对 Discord 25MB 上传限制及 Rate Limit 429 问题，图像 DPI 降到了 150，增加了分批重试和 `send_discord_images` 批量功能。
- **DeepSeek 解析鲁棒性**：AI 返回由于带 XML 标签 (`<ANALYSIS>`)，导致通过 `re.sub` 获取原因时容易截断。需要关注 `formatter.py` 或 `hunter.py:prepare_daily_chart` 中强力清洗逻辑的稳定性。
- **UI & 绘图依赖**：使用了 `matplotlib.use('Agg')` 防卡死。对 K 线渲染、支持中文的 SimHei 字体（`config/fonts/`）依赖极大。
- **业务回测数据**：需要持续关注在 `tools/` 里的回测脚本（例如 `backtest_gap_lookback_compare.py`，证明参数 `Lookback=60` 为优）与主策略代码逻辑是否一致，严防未来数据前瞻 (Look-ahead Bias)。

## 6. 开发者须知
- 项目全面拥抱 **Python (3.8+)**。
- 请严格遵循 Al Brooks (PA) 注释规范。
- 禁止在核心 `calculator.py` 与 `scanner.py` 中写 `for` 循环遍历 K 线数据。
- 进行任何信号生成器相关的核心模块改动后，需执行回测与回归测试，防止破坏原有的胜率与盈亏比（EV）基准。
