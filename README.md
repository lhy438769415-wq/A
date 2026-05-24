# Brooks-AI Quant System V9.8

> 基于 Al Brooks 价格行为理论的 A 股全自动量化扫描系统。
> 支持日线/周线双周期扫描、多策略信号检测、AI 二次筛选、Discord 实时推送。

---

## 系统架构

```
hunter.py (主入口)
    │
    ├── [日线扫描] → core/scanner.py → 基础策略筛选 → Discord 推送
    │
    └── [周线扫描与验证] → 高胜率插件化形态库 (High Win-Rate Pattern Library)
                              ├── Weekly Bull Flag (周线牛旗三推)
                              └── Weekly Gap IOI (突破缺口+内外内收敛)
```

## 目录结构

```
📦 Brooks-AI/
├── hunter.py                ← 统一主入口 (日线/周线扫描)
├── README.md
├── requirements.txt
├── setup.bat
│
├── core/                    ← 核心引擎
│   ├── calculator.py        技术指标计算 (向量化)
│   ├── data_provider.py     数据层 (Baostock 本地 DB)
│   ├── database.py          数据库管理
│   ├── scanner.py           日线扫描器
│   ├── strategy_registry.py 策略注册表
│   ├── strategies/          遗留策略实现
│   │   ├── mtr_strategy.py          MTR 主趋势反转
│   │   ├── three_k_strategy.py      3K 动量突破
│   │   └── structural_gap_strategy.py  结构性测量缺口
│   └── patterns/            高胜率形态库 (Gap Strategy 演进版)
│       ├── base.py                 Registry 与 Base 接口
│       ├── weekly_bull_flag.py     周线牛旗三推形态 (58% Win Rate, +0.06 EV)
│       └── weekly_ioi.py           周线缺口+IOI形态 (75% Win Rate, 极致爆发)
│
├── config/                  ← 配置
│   ├── settings.py          全局设置 (DB路径/字体/参数)
│   └── fonts/               字体文件
│
├── tools/                   ← 工具集
│   ├── notifier.py          Discord 推送 + K线图绘制
│   ├── scanner_weekly_gap.py    周线缺口扫描器 (V9.0 四因子评级)
│   ├── scan_gap_pinbar_weekly.py  周线 Gap+Pinbar 实战机会扫描器
│   ├── research_gap_pinbar_ev.py  Gap+Pinbar 组合形态 EV 研究
│   ├── watchlist.py             信号生命周期管理
│   ├── journal.py           AI 决策日志
│   ├── fetcher_baostock.py  Baostock 数据同步
│   ├── update_weekly_db.py  周线数据更新
│   └── ...                  回测/研究/可视化工具
│
├── data/                    ← 数据存储 (.gitignore)
│   ├── baostock.db          日线行情数据库 (~500MB)
│   ├── baostock_weekly.db   周线行情数据库
│   └── *.json               观察名单/验证报告
│
├── docs/                    ← 策略文档
│   ├── gap_evolution_plan.md  Gap Strategy 最新的演进规划
│   ├── MTR_V35_0_STRATEGY.md  当前版本策略说明
│   └── archive/             历史版本文档
│
├── strategy_lab/            ← 策略研究
│   ├── EXPERIMENT_LOG.md    实验记录
│   └── *.py / *.csv         回测脚本与数据
│
└── tests/                   ← 自动化测试
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 一个命令搞定所有 (交互式三选菜单)
python hunter.py
#   → 1. 扫描新机会 (日线/周线)
#   → 2. 信号追踪 (仪表盘 + Discord 推送)
#   → 3. 持仓管家

# 3. CLI 快捷方式 (定时任务/自动化)
python hunter.py --timeframe weekly          # 直接周线扫描
python hunter.py --track --report            # 追踪 + 报表
```

## 核心策略

| 策略 | 周期 | 简述 |
|:---|:---:|:---|
| **Gap Pattern Library** | 周线 | (NEW) 高胜率插件化形态库 (牛旗三推, IOI, etc.)，内置大样本回测框架与 EV 评测 |
| **MTR** (Major Trend Reversal) | 日线 | 主趋势反转信号，作为储备选项 |
| **3K** (Three-K Momentum) | 日线 | 三K动量突破 + 缺口测试确认 |
| **Structural Gap** | 周线 | 结构性测量缺口，V9.0 四因子积分评级 |

## 迭代版本记录

| 版本 | 日期 | 主要变更 |
|:---:|:---:|:---|
| V9.15 | 2026-05-24 | **缺口与 MTR 策略标注修复、美化与日线去重优化**：(1) 彻底修复 `tools/notifier.py` 内部由于合并错误导致的 `SyntaxError` 语法崩溃问题；(2) 完美恢复 MTR 策略经典的四阶段波段反转标注；(3) 针对所有缺口策略（`STRUCTURAL_GAP`, `GAP_PINBAR`, `GAP_H2`）引入防御性 Fallback 机制以确保画图不崩溃；(4) 大幅升级并优化 PA 标注视觉，包括波段低点起跳支点、Gap Zone 虚线矩形及入场/止盈 TP 圆角气泡框；(5) 针对新策略 `Gap+H2` 和 `Gap+Pinbar` 绕过 Watchlist 去重拦截限制，确保日线新信号每次均能照常推送。 |
| V9.14 | 2026-05-24 | **日周流程统一与多策略扫描增强**：(1) 简化日线机会扫描流程：增加 `--no-ai` 命令行参数和交互式 AI 旁路开关，支持“纯技术面直通”模式，显著提升大批量检索效率。(2) 增强周线机会扫描：周线引入策略选择交互菜单及命令行选项，完全兼容新开发的 `Gap+H2` 等策略，字段反射映射与扫描器解耦，无任何硬编码公式。 |
| V9.13 | 2026-05-19 | **数据同步双重 Bug 修复**：(1) 定位 `as_completed(timeout=30)` 是全局超时而非单任务间隔超时，30 秒一到即强制终止整个迭代器，导致每次只能同步约 400 只股票。修复为 `future.result(timeout=60)` 单任务超时。(2) 修复 `bs_fetch_stock_list()` 缺少 `type==1` 过滤，将指数(上证红利/上证B股等 ~229 个 type=2)误判为深市主板股票，每次"发现"~400只伪新股并浪费下载时间。 |
| V9.12 | 2026-05-15 | **Gap + Pinbar (缺口测试) 形态全市场 EV 研究**：独立构建 `tools/research_gap_pinbar_ev.py` 研究脚本进行日线/周线双盲扫。在核心识别逻辑中植入原汁原味的 Al Brooks 价格行为理论（强制要求 Pinbar 低点探入或接近缺口上沿并在 EMA20 附近开放），并得出极其强烈的统计学结论——周线级别“突破缺口 + 缺口测试 Pinbar + 缺口下沿止损” 具备 **+0.456R** 的极高单笔数学期望，且优于基于波段低点的宽止损策略。 |
| V9.11 | 2026-05-15 | **Discord 多图推送高可用修复**：修复大批量高分辨率信号图推送时引起的 `Read timed out` 与断连问题。为发送接口加入 3 次容错重试机制、将网络超时放宽至 150 秒，并下调 K 线图输出分辨率 (DPI) 以压降大体积负载，恢复原设定的 10 图连发机制，确保每日推送完整连贯。 |
| V9.10 | 2026-05-09 | **Baostock 数据源网络死锁修复**：修复由于 Baostock 官方在 2026-04-22 升级底层 API 及服务器节点导致的 `bs.login()` 无限挂起死锁问题，升级依赖环境 `baostock` 至 `0.9.1` 最新版本，恢复历史数据同步功能的正常运行。 |
| V9.9 | 2026-04-11 | **Discord 推送与生命周期修复**：重构 `notifier.py` 突破 2000 字符推送截断限制（按行智能分段）；移除由于时间拖延导致的 D 级人为丢弃限制，恢复 "只要缺口开放即持续观察" 规则。 |
| V9.8 | 2026-04-04 | **Gap 策略全量回测闭环**：完成 LB=60 vs 100 对比回测 (确认 60 为最优)；Gap 演进计划全阶段闭环；配置显式化；清理临时文件；补全测试文档 |
| V9.7 | 2026-03-27 | **Phase2 架构重构**：拆分 `hunter.py` God Function 为 4 子函数；Signal Tracker `iterrows` 向量化；新增 15 个 `calculator` 单元测试 |
| V9.6 | 2026-03-27 | **Phase1 代码审计优化**：修复 EMA20 双重绘制；清理 MTR V29/V30 死代码(-65行)；移除 abu_indicators 僵尸表 JOIN；统一数据层导入；SQL 参数化查询；新增 8 个回归测试 |
| V9.5 | 2026-03-09 | MTR 全面升维至 **Gap Strategy**；建立 `core/patterns` 插件化形态库；新增周线牛旗三推、周线IOI收敛高胜率核武器；集成动能+无时限 EV 回测框架 |
| V9.3 | 2026-03-05 | 日线同步性能优化（MAX_WORKERS 4→6、DB 批量 commit、SQLite cache）；信号追踪按状态分类推送（止盈→止损→失效→持仓→等待），10 图连发 |
| V9.2 | 2026-03-05 | 修复 signal_date 关键 Bug (trade_date vs date)；数据同步集成主菜单；Discord 多图推送；A+ 三级分层仪表盘 |
| V9.1 | 2026-03-02 | Signal Tracker 信号追踪器；交互式三选主菜单；个股仪表盘 |
| V9.0 | 2026-03-01 | 周线 Structural Gap 四因子积分评级 (经 4988 样本鲁棒性验证) |
| V8.8 | 2026-02-28 | Hunter 日线/周线统一入口；系统文件整理 |
| V8.5 | 2026-02-25 | 3K 策略回测框架；信号生命周期管理 |
| V8.0 | 2026-02-22 | MTR 信号首次触发机制；观察名单三级推送 |
| V7.1 | 2026-02-20 | Baostock 本地数据库；离线扫描架构 |
