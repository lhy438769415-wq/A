# Brooks-AI Structural Gap Radar (周线缺口雷达) 🚀

## 📌 项目概述
本项目是基于 **Al Brooks 价格行为 (Price Action) 理论** 的周线级别“结构性缺口 (Structural Gap)”自动化扫描与可视化系统。

系统的核心目标是识别 A 股市场中强力的突破缺口，并计算其 **Measure Move (等长测量目标)**。通过交互式仪表盘，交易者可以直观地查看信号形态、盈亏比分级以及 AI 生成的测绘标注。

---

## 📁 核心架构 (Gap Only)
本仓库仅包含与“结构性缺口”策略及 Web 可视化相关的核心组件：

- **`tools/web_viewer.py`**: 基于 Streamlit 的交互式看盘仪表盘 (GitHub Pages / Streamlit Cloud 入口)。
- **`tools/scanner_weekly_gap.py`**: 周线级别缺口扫描引擎。
- **`core/strategies/structural_gap_strategy.py`**: 核心策略逻辑 - 识别突破、回调确认及目标计算。
- **`data/weekly_gap_watchlist.json`**: 扫描生成的信号数据库。
- **`SimHei.ttf`**: 中文图表支持字体。

---

## 🛠️ 工作流 (Workflow)
1. **本地扫描**: 在本地环境运行 `python tools/scanner_weekly_gap.py` 完成全市场扫描。
2. **数据同步**: 运行 `python tools/deploy_dashboard.py` 自动将结果推送到本仓库。
3. **云端展示**: Streamlit Cloud 监控本仓库变动，自动更新线上仪表盘网址。

---

## ⚖️ 免责声明
本系统生成的信号仅供技术研究分享，不构成任何投资建议。量化策略基于历史数据，不保证未来收益。
