# 🧪 Strategy Lab (高胜率形态实验室)

## 🎯 当前焦点形态: 强趋势/支撑位长下影阳线 (Bullish Pinbar with Context)

### 1. 核心定义 (User Specs)
用户指定的高胜率入场形态，核心在于 **"强有力的拒绝下跌"** (Strong Rejection) 配合 **"关键支撑确认"** (Confluence)。

**A. 信号棒特征 (Signal Bar)**:
1.  **长下影线**: 下影线长度占比较大 (例如 > 50% Range)。
2.  **高位收盘**: 收盘在顶部 (High Close)，最好是光头 (Shaved Head)。
3.  **阳线**: 必须是阳线 (Bull Body)，代表买方最终获胜。

**B. 环境背景 (Context)**:
应该出现在以下支撑区域之一：
1.  **趋势起点**: EMA20 上方，开启新一轮趋势。
2.  **区间底部**: 前低附近 (Previous Low)，双底/三重底潜力。
3.  **缺口回踩**: 突破后回踩但不补缺口 (Breakout Pullback)。
4.  **密集区突破**: 突破密集交易区 (TTR) 后有好的跟随。
5.  **共振**: 近期多次出现相同位置的下影线 (Tweezer Bottoms / Micro Double Bottom)。

---

## ⚠️ 安全原则 (Safety Rule)

> **本实验室所有程序仅从本地数据库读取数据，禁止联网！**
> 
> 使用 `data_manager.get_stock_data_offline()` 函数确保零网络请求。

---

## 📂 目录结构
- `pattern_bullish_pinbar.py`: 核心量化代码
- `run_analysis.py`: 验证脚本 (🔒 Offline Only)
- `full_backtest.py`: 全量回测脚本 (🔒 Offline Only)
- `generate_gallery.py`: K线图画廊生成
- `visualize_results.py`: 结果可视化
- `pattern_evaluation.md`: Al Brooks 理论评估
- `EXPERIMENT_LOG.md`: 📊 实验日志 (含胜率统计结果)
