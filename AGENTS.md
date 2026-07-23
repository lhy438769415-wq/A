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

## 数据库结构冻结（Schema 保护）— 最高优先级

本系统的第一性是**数据完整性 / 可用性**：数据层（SQLite）是经过多版迭代沉淀的
稳定地基，任何对其结构的随意改动都会直接污染信号质量、甚至让整套扫描失效。
因此库结构被**冻结**，由护栏强制执行（不是君子协定）：

- **单一 schema 主人（白名单）**：每个数据库有且仅有一个文件拥有其 DDL。
  - `data/baostock.db` ← `core/database.py`（daily_bars / weekly_bars / abu_indicators / signal_archive / trade_reviews）
  - `data/ai_journal.db` ← `tools/journal.py`（hunter_journal / guardian_journal）
- ❌ 禁止在白名单之外的任何文件写 `CREATE TABLE` / `ALTER TABLE`。quality_gate 会**阻断**此类提交。
- ❌ 禁止绕过代码、直接用 DB 工具/SQL 手改线上库文件结构。
- ✅ 新增列 / 改列的正确流程：
  1. 只在对应 schema 主人文件改 DDL；
  2. 同步更新 `core/schema_guard.py` 的解析来源（自动从源码解析，通常无需手改）；
  3. 跑 `core/schema_guard.py` 与 `tests/test_schema_integrity.py` 确认通过；
  4. 经评审后提交。
- **运营级守护**：`core/schema_guard.py` 直接打开真实库文件，做
  `PRAGMA integrity_check`（抓损坏）+ 实际表/列与声明 schema 比对（抓带外漂移）；
  它的"期望 schema"实时解析自两个主人源码，天然单一来源、不会脱节。
  建议纳入 CI / 数据同步前自检。
