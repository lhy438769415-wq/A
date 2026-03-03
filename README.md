# Brooks-AI Quant System V9.0

> 基于 Al Brooks 价格行为理论的 A 股全自动量化扫描系统。
> 支持日线/周线双周期扫描、多策略信号检测、AI 二次筛选、Discord 实时推送。

---

## 系统架构

```
hunter.py (主入口)
    │
    ├── [日线模式] → core/scanner.py → 策略信号 → AI 筛选 → Discord 推送
    │
    └── [周线模式] → tools/scanner_weekly_gap.py → V9.0 四因子评级 → Discord 推送
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
│   └── strategies/          策略实现
│       ├── mtr_strategy.py          MTR 主趋势反转
│       ├── three_k_strategy.py      3K 动量突破
│       └── structural_gap_strategy.py  结构性测量缺口
│
├── config/                  ← 配置
│   ├── settings.py          全局设置 (DB路径/字体/参数)
│   └── fonts/               字体文件
│
├── tools/                   ← 工具集
│   ├── notifier.py          Discord 推送 + K线图绘制
│   ├── scanner_weekly_gap.py  周线缺口扫描器 (V9.0 四因子评级)
│   ├── watchlist.py         信号生命周期管理
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
| **MTR** (Major Trend Reversal) | 日线 | 主趋势反转信号，AI 二次筛选 |
| **3K** (Three-K Momentum) | 日线 | 三K动量突破 + 缺口测试确认 |
| **Structural Gap** | 周线 | 结构性测量缺口，V9.0 四因子积分评级 |

## 迭代版本记录

| 版本 | 日期 | 主要变更 |
|:---:|:---:|:---|
| V9.1 | 2026-03-02 | Signal Tracker 信号追踪器；交互式三选主菜单；个股仪表盘 |
| V9.0 | 2026-03-01 | 周线 Structural Gap 四因子积分评级 (经 4988 样本鲁棒性验证) |
| V8.8 | 2026-02-28 | Hunter 日线/周线统一入口；系统文件整理 |
| V8.5 | 2026-02-25 | 3K 策略回测框架；信号生命周期管理 |
| V8.0 | 2026-02-22 | MTR 信号首次触发机制；观察名单三级推送 |
| V7.1 | 2026-02-20 | Baostock 本地数据库；离线扫描架构 |
