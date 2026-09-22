# Brooks-AI Quant System V10.0

> 基于 **Al Brooks 价格行为（PA）理论**的 A 股量化扫描系统。
> 本地离线行情 + 多周期（日/周/月）策略扫描 + Discord 实时推送 + 桌面/Web 看板。
> **只推信号、不下单**——交易员在 TradingView 精筛、在券商下单，系统只做"找结构 + 提醒 + 标注"。

---

## 这套系统到底干嘛的（人话版）

1. **扫描**：每天/每周用 PA 规则扫全市场 A 股，找出"可能要走出行情的形态"（缺口家族、主趋势反转、顺势 H2 等）。
2. **标注**：自动在 K 线上画出买点、止损、目标位，并算出风险收益比。
3. **推送**：把信号和 K 线图推到 Discord（含买卖点标注），当做"待办提醒"。
4. **你来做决定**：第二天开盘前，你人工在 TradingView 复核、挂突破条件单；持仓管理（动态止盈/跟踪止损）由你在券商端完成。

> 系统**不自动交易、不自动下单**。它是一台"形态雷达 + 看图助手"，最终买卖由人拍板。

---

## 系统架构

```
                 ┌─────────────────────────────────────────────┐
                 │            数据层 (离线 T+1)                  │
                 │  Baostock 本地 SQLite (data/baostock.db)      │
                 │  core/data_provider.py · database.py          │
                 └───────────────────────┬─────────────────────┘
                                         │
            ┌────────────────────────────┼────────────────────────────┐
            ▼                             ▼                            ▼
   ┌─────────────────┐         ┌────────────────────┐      ┌──────────────────┐
   │  日线扫描         │         │  周线扫描            │      │  月线快照(只读)    │
   │  core/scanner.py │         │  core/scan_engine.py │      │  tools/          │
   │  _OFFICIAL_LIST  │         │  WEEKLY_GAP_STRATS   │      │  monthly_        │
   │  (6 策略)         │         │  (缺口三家族)         │      │  pinbar_         │
   │                  │         │                     │      │  snapshot.py     │
   └────────┬─────────┘         └─────────┬───────────┘      └──────────────────┘
            │                             │
            ▼                             ▼
   ┌──────────────────────────────────────────────────────────┐
   │  core/strategy_registry.py  —  策略注册表 (8 个策略)         │
   │  core/strategies/  —  策略实现 (MTR / 3K / GAP 家族 / AIL)  │
   │  core/calculator.py  —  技术指标 (向量化)                  │
   │  core/rating.py  —  PA 因子评级                            │
   └───────────────────────────────┬──────────────────────────┘
                                   │
            ┌──────────────────────┼──────────────────────┐
            ▼                      ▼                       ▼
   ┌─────────────────┐   ┌────────────────────┐   ┌──────────────────┐
   │  Discord 推送     │   │  桌面操盘台 (Tk GUI)  │   │  Web 看板         │
   │  tools/          │   │  launch_dashboard   │   │  tools/          │
   │  notifier.py     │   │  .py +              │   │  web_viewer.py   │
   │  (含 K 线图绘制)  │   │  gui_dashboard.py   │   │  (规划中 v2)      │
   └─────────────────┘   └────────────────────┘   └──────────────────┘
```

> 注：早期版本的 `core/patterns/`（周线牛旗/IOI 形态库）已不再是周线扫描主路径；当前周线只跑"缺口三家族"（见下）。

---

## 核心策略（注册表实测，2026-09-22）

系统共注册 **8 个策略**，按扫描池分三类：

| 注册名 | 显示名 | 周期 | 类型 | 简述 |
|:---|:---:|:---:|:---:|:---|
| `MTR_MASTER` | MTR | 日线 | 生产扫描 | 主趋势反转（5 点结构序列 + 斐波那契测量） |
| `STRATEGY_3K` | 3K | 日线 | 生产扫描 | 连续 3 根阳线 + 缺口/陷阱动量突破 |
| `STRATEGY_AWIL` | AIL | 日线 | 生产扫描 | EMA20 上方两腿回调 H2 顺势入场 |
| `STRATEGY_STRUCTURAL_GAP` | GAP H1 | 日+周 | 生产扫描 | 结构性测量缺口（突破缺口 + 回测反转） |
| `STRATEGY_GAP_PINBAR` | GAP PINBAR | 日+周 | 生产扫描 | 缺口测试 Pinbar（缺口上沿刺入 + EMA20 穿刺） |
| `STRATEGY_GAP_H2` | GAP H2 | 日+周 | 生产扫描 | 缺口 + H2 两腿回调（缺口后回踩不破地板） |
| `STRATEGY_GAP_H2_ENHANCED` | GAP H2 增强(实验) | 回测专用 | 仅回测 | 增强基底实验版，不进日/周生产扫描池 |
| `STRATEGY_MONTHLY_RANGE_BREAK` | 月线区间破位Pinbar | 月线 | 只读快照 | 月线区间破位长下影回收，不入扫描池 |

**扫描池划分（实测）：**
- **日线池**（`_OFFICIAL_LIST`）：MTR、3K、AIL、GAP H1、GAP PINBAR、GAP H2
- **周线池**（`WEEKLY_GAP_STRATS`）：GAP H1、GAP PINBAR、GAP H2 —— 即"缺口三家族"
- **月线**：月线破位 Pinbar 只读快照（手动跑，不推送）
- **GAP H2 增强**：仅用于回测对比，不进生产

各策略的逐条规则清单见 `docs/gap_h2_strategy_card.md` 与 `docs/mtr_strategy_card.md`（含典型形态示意图），其余策略文档在 `docs/` 下按名称检索。

---

## 目录结构

```
📦 Brooks-AI/
├── hunter.py                 ← 命令行统一入口 (日线/周线扫描, 信号追踪)
├── gui_dashboard.py          ← 桌面操盘台主界面 (Tk GUI)
├── launch_dashboard.py       ← 桌面操盘台启动器
├── README.md / AGENTS.md / DATA_SAFETY.md   ← 三份核心说明（不擅动）
├── requirements.txt          ← Python 依赖
│
├── core/                     ← 核心引擎
│   ├── scanner.py            日线扫描器
│   ├── scan_engine.py        周线扫描编排
│   ├── strategy_registry.py  策略注册表 (8 策略)
│   ├── data_provider.py      数据层 (Baostock 本地 DB)
│   ├── database.py           数据库管理 (唯一 schema 主人)
│   ├── calculator.py         技术指标计算 (向量化)
│   ├── rating.py / rating_core.py  PA 因子评级
│   ├── api_client.py         DeepSeek 接口 (保留, 未接入生产流水线)
│   ├── patterns/             形态求解器 (含 weekly_bull_flag 等历史模块)
│   ├── signal_tracker/       信号生命周期管理
│   └── strategies/           策略实现 (11 文件, 见上"核心策略")
│
├── tools/                    ← 工具集
│   ├── notifier.py           Discord 推送 + K 线图绘制
│   ├── watchlist.py          信号观察名单 / 生命周期
│   ├── fetcher_baostock.py   Baostock 数据同步
│   ├── journal.py            AI 决策日志
│   ├── web_viewer.py         Web 看板 (规划中 v2)
│   ├── monthly_pinbar_snapshot.py  月线快照工具
│   └── ...                   心跳/部署/评级校验等
│
├── config/                   ← 配置 (settings.py / sop_rules.md / fonts/)
├── data/                     ← 数据存储 (.gitignore, 不入库)
│   └── baostock.db           行情库 (~590MB, 含日/周/月线)
│
├── docs/                     ← 策略与工程文档 (大量 .md)
├── strategy_lab/             ← 策略研究 / 回测脚本
├── tests/                    ← 自动化测试 (156 测试函数, 门禁守卫)
├── archive/                  ← 归档历史件
└── .agent/ .workbuddy/       ← 本地 Agent 上下文与记忆 (gitignore)
```

---

## 快速开始

```bash
# 1. 安装依赖 (推荐项目内置 .venv)
pip install -r requirements.txt

# 2. 同步行情数据 (首次或定期)
python tools/fetcher_baostock.py

# 3. 启动桌面操盘台 (日常使用入口, Tk GUI)
python launch_dashboard.py
#   → 界面含「下载行情 / 策略扫描 / 信号看板 / 周线模式」等
#   → 扫描结果含买卖点标注 + Discord 推送开关

# 4. 命令行扫描 (定时任务 / 自动化)
python hunter.py --timeframe weekly     # 直接跑周线扫描 (缺口三家族)
python hunter.py --timeframe daily      # 日线扫描
python hunter.py --track --report       # 信号追踪 + 报表
```

> **Discord 推送**需要 `.env` 里配置 `DISCORD_WEBHOOK_URL`；未配置时扫描照常跑、只是不推。

---

## 工程纪律（给接手的同学/AI）

- **PA 铁律**：评级因子只能是价格行为（OHLC/形态结构），成交量/指标/基本面禁入。
- **质量门禁**：每次提交前 `pre-commit` 自动跑 `quality_gate`（红线检查 + 测试数守卫），测试基线 **156**、红线 **0**。
- **数据不入库**：`data/*.db*`、`.agent/`、`.workbuddy/`、`logs/` 等已在 `.gitignore`，推送 GitHub 时不会带上本地行情库。
- **核心三文档不擅动**：`README.md` / `AGENTS.md` / `DATA_SAFETY.md` 与身份三件套（`BOOTSTRAP.md`/`SOUL.md`/`USER.md`）非经确认不改。
- **更完整、最新的项目自述**（面向 Agent）见 `docs/项目自述_面向Agent.md`；项目快照见 `.agent/context/STATUS.md`。

---

## 迭代版本记录

| 版本 | 日期 | 主要变更 |
|:---:|:---:|:---|
| V10.0 | 2026-07-25 | **架构收敛 + 工程守门 + 高可用（质变）**：入口/编排单引擎；自动门禁 hook（quality_gate 红线/测试数守卫）；运行监控/崩溃告警；全流程 156 测试全绿、0 红线。 |
| V10.0+ | 2026-09-03 | 月线策略移出 `_OFFICIAL_LIST`，修复日线扫描混入月线信号；明确"周线只跑缺口三家族"。 |
| V10.0+ | 2026-09-05 | 新增 `docs/项目自述_面向Agent.md`（多 Agent 接手必读，含架构/注册表/已知缺陷/文档陈旧警示）。 |
| V10.0+ | 2026-09-19 | 周线口径纠偏：周线入口仅传缺口三家族，3K/MTR/AIL 仅日线；修正日/周线共用权重符号的根因。 |
| V10.0+ | 2026-09-22 | GAP-H2 / MTR **策略卡 + 典型形态示意图**（利旧 `notifier` 出图工具 + 合成数据）；项目信息 `STATUS.md` / `项目自述` 同步刷新并推 GitHub；本 README 重写（去除过时的"AI 二次筛选"等假功能描述）。 |
| V9.20 | 2026-07-20 | 新增 AWIL 策略（Always In Long H2 顺势入场）。 |
| V9.5 | 2026-03-09 | MTR 升维至 Gap Strategy；建立 `core/patterns` 插件化形态库。 |
| V9.0 | 2026-03-01 | 周线 Structural Gap 四因子积分评级。 |
| V8.8 | 2026-02-28 | Hunter 日线/周线统一入口。 |
| V7.1 | 2026-02-20 | Baostock 本地数据库；离线扫描架构。 |

> 更多历史条目见 `docs/RELEASE_V10.0.md` 与 `docs/archive/`。

---

*本系统为个人量化研究工具，所有信号仅供学习参考，不构成投资建议。*
