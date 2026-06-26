# AGENTS.md — Brooks-AI 量化扫描系统

## 项目身份
- **名称**: Brooks-AI Quant System
- **版本**: → 见 `.agent/context/STATUS.md`
- **定位**: A 股全自动量化扫描 (人机协作, 只推信号不下单)
- **理论基础**: Al Brooks Price Action

## 系统架构
```
L1 入口 → hunter.py (交互菜单 + CLI)
L2 流水线 → _scan_market → _classify_signals → _compose_report → _dispatch_charts
L3 策略 → BaseStrategy + StrategyRegistry (插件化, 自描述)
L4 输出 → notifier (Discord+图表) / watchlist (Facade→SignalTracker)
L5 基础 → database (SQLite WAL) / data_provider (Baostock 离线)
```

## 技术栈
- Python 3.13 / SQLite WAL / pandas+numpy 向量化 / DeepSeek API
- 虚拟环境: `.venv/Scripts/python.exe`
- 测试: `.venv/Scripts/python.exe -m pytest --maxfail=2`
- 格式化: 计划引入 Ruff (尚未配置)

## 路由表 (按需深入时读取)
| 需要了解... | 读取... |
|:---|:---|
| 当前版本/最近改动/待办 | `.agent/context/STATUS.md` |
| 策略接入 SOP | `.agents/skills/strategy-onboarding/SKILL.md` |
| Al Brooks PA 理论参考 | `config/sop_rules.md` |
| GAP 策略规范 | `docs/gap_h2_strategy_spec.md` |
| MTR 策略规范 | `docs/MTR_V35_0_STRATEGY.md` |
| 完整架构交接文档 | `docs/CHANGELOG_agent_handoff.md` |

## 绝对禁止
- ❌ 禁止使用系统 Python (必须用 .venv)
- ❌ 禁止修改 data/*.db 数据库文件结构
- ❌ 禁止引入独立于 SignalTracker 的新状态追踪
- ❌ 禁止新增 sys.path.insert (使用 core/paths.py)
- ❌ 禁止新增 logging.basicConfig (使用 core/log_config.py)
