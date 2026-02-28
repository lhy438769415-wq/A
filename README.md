# Brooks-AI Quant System V36.2 (3K Integration)

## 📌 项目概述
本系统是基于 **Al Brooks 价格行为 (Price Action) 理论** 构建的自动化量化交易系统。
**V36.1 版本 (Structural Fix)** 在 V36.0 七维度评分基础上，修复了 MTR 结构识别的三个核心缺陷：L1 必须为区间最深低点、H1 取整轮反弹最高点、TL 必须在 EMA20 下方。系统仅依赖 **Baostock** 单一数据源，采用全量初始化策略，确保拥有完整的 3 年+ 历史数据。分析程序完全离线运行，只读本地数据库，杜绝任何外部网络请求风险。
AI 分析师角色已全面升级为 **Al Brooks 本人**，专注于 Context, Structure, Probability，严禁非 PA 术语。

---

## 📁 项目文件目录结构

> [!WARNING]
> **局限性警告 (Disclaimer)**
> 本系统采用 **Baostock Only** (T+1) 数据架构，这也意味着**无法获取当日盘中实时数据**。
> - ⛔ **严禁盘中运行**: 盘中运行 `hunter.py` 看到的只能是**昨日 (T-1)** 的形态。
> - ✅ **仅限盘后复盘**: 请在每日 **18:00** 后（数据归档完毕）运行数据更新和猎手扫描，用于制定**次日**交易计划。

## 📁 目录结构
├── hunter.py               # 🧠 核心逻辑 - 策略执行与 AI 调用
├── config/
│   ├── settings.py         # ⚙️ 全局配置 - 线程数 (Max Workers=2)、策略参数
│   └── trading_calendar.json  # 📅 本地交易日历
├── core/
│   ├── data_provider.py    # 📊 数据提供层 - 负责全量/增量同步
│   ├── database.py         # 💾 数据库连接池 (baostock.db)
│   ├── calculator.py       # 📈 技术指标计算（EMA, ATR, ADX 等）
│   ├── scanner.py          # 🔭 全市场扫描器 - 技术面初筛
│   └── strategies.py       # 📋 策略规则定义（3K, MTR）
├── tools/
│   ├── fetcher.py          # 🚀 数据获取层 - Baostock 只读接口
│   ├── fetcher_baostock.py # 📦 Baostock 适配器（核心）
│   └── fetcher_tushare.py  # 🚫 已弃用 (保留备用)
├── data/
│   └── baostock.db         # 🗄️ SQLite 本地数据库（全量历史数据）
├── strategy_lab/           # 🧪 策略实验室（纯离线回测）
├── tests/                  # 🧪 测试用例
├── .env                    # 🔐 API Keys（DeepSeek Key）
├── hold_list.txt           # 📝 持仓列表配置
└── README.md               # 📖 本文档
```

---

## ⚙️ 核心工作流 (Workflow)

### 1. 🔄 数据更新 (Data Sync)

| 阶段 | 负责程序 | 说明 |
|:---|:---|:---|
| 入口 | `gui_dashboard.py` | 用户点击"更新数据"按钮 |
| 获取 | `tools/fetcher.py` | **独家调用 Baostock** (无路由/降级) |
| 控制 | `core/data_provider.py` | 首次运行自动拉取 2023 至今全量数据 |
| 写入 | `core/database.py` | 事务写入 `baostock.db`（WAL 模式）|

**初始化**: 首次运行会自动进行全量初始化（约 1-2 小时），期间 `MAX_WORKERS` 强制限制为 2 以防封禁。
**日常更新**: 建议每日 **18:30** 后运行。

---

### 2. 🔭 猎手扫描 (Hunter Mode)

| 阶段 | 负责程序 | 说明 |
|:---|:---|:---|
| 入口 | `gui_dashboard.py` / `hunter.py` | 用户点击"猎手扫描"按钮或运行脚本 |
| 读取 | `core/data_provider.py` | **纯离线读取**（只读本地 DB，不联网）|
| 计算 | `core/calculator.py` | 向量化计算 EMA/ATR/ADX 等指标 |
| 筛选 | `core/scanner.py` | 全市场技术面初筛（MTR, Wedge, Climax）|
| AI | `hunter.py` | 调用 DeepSeek (Al Brooks Persona) 进行深度推演 |
| 展示 | Terminal / GUI | 结果显示并推送到微信 |

**运行时机**: 盘中 **14:30** 后（K线形态稳定）

---

## 🏗️ 系统架构设计原则

> **核心原则**: 数据极致纯净，分析完全离线。

```
┌─────────────────────────────────────────────────────────────┐
│  数据同步（慢速稳定）                                        │
│  scanner.py / gui_dashboard.py                              │
│  → fetcher.py → Baostock → 写入 baostock.db                 │
│  (Limit: 2 Workers)                                         │
└─────────────────────────────────────────────────────────────┘
                            │ 写入
                            ▼
                       [baostock.db]
                            ▲ 只读
                            │
┌─────────────────────────────────────────────────────────────┐
│  分析扫描（极速离线）                                        │
│  gui_dashboard.py / hunter.py                                 │
│  → 只读数据库，绝不发起网络请求                              │
└─────────────────────────────────────────────────────────────┘
```

**优点**:
- **绝对安全**: 彻底移除 AkShare/Tushare，规避封禁风险
- **数据完整**: 强制 3 年历史基准，EMA 计算更精准
- **极简维护**: 单一数据源，排查问题简单

---

## 🚀 快速开始 (Quick Start)

### 1. 首次运行（全量初始化）
```bash
python gui_dashboard.py
```
*点击"更新数据"，系统检测到新库，会自动开始拉取 2023-01-01 至今的所有数据。请耐心等待 1-2 小时。*

### 2. 常用操作
- **搜股**: 在左上角输入代码 (如 `600036`) 回车，离线秒开分析
- **扫描**: 盘后点击"猎手扫描"，快速筛选当日机会

### 3. 配置文件
| 文件 | 用途 |
|:---|:---|
| `.env` | **DeepSeek API Key** (仅此一项必填) |
| `config/settings.py` | 系统参数 (默认 MAX_WORKERS=2) |

---

## 📜 版本迭代记录

### 3K V2.0 (Breakout Gap → Measured Gap) - 2026-02-25
- **真实跳空缺口**: 缺口检测从 `Open>=Close` 升级为 `Low>=High`（影线级别的真实跳空）。
- **突破缺口确认**: 新增 `breakout_gap_open` 后验列，3K后回调波段低点 > K1高点 = 缺口保持开放。
- **测量缺口目标**: 缺口开放时自动计算 `measured_gap_target`（Al Brooks Measured Move 投射）。
- **缺口测试确认信号**: 新增 `signal_3k_gap_test`，回调测试缺口成功后自动生成 Buy Stop Order（Entry=测试K线High, SL=测试K线Low, TP=测量缺口目标）。
- **AI审核升级**: 提示词新增 Breakout Gap → Measured Gap 演进逻辑审计维度。

### V35.2 (Signal Bar Integration) - 2026-02-22
- **信号K线入场逻辑**: TL 后搜索强势阳线信号K，入场价 = 信号K的 high (buy stop order)。
- **SL/TP 修正**: SL = min(L1, TL)，TP = 2R。
- **H1 验证优化**: 25% bullish 替换为 EMA Gap Bar 检测 (low > EMA20)。
- **Lower Low 放宽**: 硬截断从 0.88/0.90 放宽至 0.80。
- **PA 理论对齐**: 删除 Volume 引用，趋势线突破升级为必要条件。

### V35.1 (Brooks Four-Element) - 2026-02-22
- **原文对照修复**: 基于 Al Brooks 原文 MTR 四大必要条件进行严格审计，修复 3 个 P0 + 2 个 P1 问题。
  - **[P0] AI Prompt 纠正**: 修复 H1/L1 概念描述错误，加入原文四要素审计框架和通道动能维度。
  - **[P0] 状态语义修正**: `SIGNAL_CONFIRMED` → `SETUP_READY`，`POTENTIAL_TESTING` → `SETUP_FORMING`。
  - **[P0] 趋势线突破集成**: `GeometricTrendlineEngine` 纳入 V35 引擎，检测 H1 是否突破下降趋势线。
  - **[P1] H1 验证强化**: 从 `np.any` 提升为 25%+ bullish + H1 收盘 > EMA。
  - **[P1] 通道动能评估**: 反弹/回调通道强弱程度纳入评分。
  - **评分重构**: 五维度 100 分满分体系（结构 30 + Setup 20 + 趋势线 15 + 动能 20 + 深度 15）。

### V35.0 (Strict Brooks) - 2026-02-11
- **策略引擎升级 (MTR V35.0)**:
  - **结构化重构**: 引入 `MTRStructuralEngineV35`，严格执行 Fibonacci 比例校验。
  - **趋势深度过滤**: 增加 `trend_depth` 指标，强制要求前置趋势深度 > 5.0 ATR。
  - **去噪优化**: 修复 Pandas Warning 报错，优化向量化计算性能。
- **AI 人格重塑**:
  - **Persona 升级**: 将 AI 分析师角色重置为 **"Al Brooks (Price Action Master)"** 本人。
  - **术语清洗**: 严禁 AI 使用“量价背离”、“MACD”等非 PA 术语，强制专注于 Context, Structure, Probability。
  - **中文强制**: 输出标签 `<PA_TAGS>` 强制使用标准中文 Brooks 术语（如：强趋势突破、二次测试失败）。
- **流程优化**:
  - **图表修正**: 推送图表增加 "Al Brooks 观点" 前缀，明确区分 AI 点评与硬指标。
  - **报错修复**: 解决了 Pandas `FutureWarning` 和索引对齐问题，确保长期运行稳定。

### V36.2 (3K Integration) - 2026-02-25
- **3K Climax Filter**: 过滤3K后不经回调直达MM的高潮行情，避免虚假盈亏比。
- **3K 跳过 AI**: 3K 信号不再走 DeepSeek 审计，直接生成图表。
- **全量图表推送**: 推送从 Top 3 改为全量信号，5 张一拼长图分批发送。
- **Gap Test 信息增强**: Scanner 为 3K 补充缺口测试确认的 Entry/SL/TP/R:R。

### V8.30 (Signal Focus) - 2026-02-08
- **策略工厂剥离**: 完成 3K 策略的物理隔离，精简策略注册表。
- **特化推送**: 定制 MTR 和 3K 的消息模版，自动计算 1R/2R。
- **图表直标**: K 线图自动标注 SL/TP 价格。

### V29.5 (MTR Master) - 2026-02-07
- **阿布正统逻辑回归**: 重构 MTR 核心逻辑，从机械参数转向物理结构。
- **战略性 MLH**: 识别“最后防守阵地”以降低假突破。
- **力量宣言**: 引入 1.5x ATR 强阳线作为趋势种子。

### V8.24 (Hunter Pro) - 2026-02-01
- **结构为王**: 转向基于 Swing Points 的结构引擎。
- **物理锚点锁定**: 基于波段低点锚定止损。

### V8.13 (Baostock Only) - 2026-01-28
- **架构重构**: 回归 Baostock 单数据源，全量离线分析。
