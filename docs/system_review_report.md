# 🔍 Brooks-AI Quant System V9.8 — 系统评审报告

> **评审日期**: 2026-05-26  
> **评审范围**: 架构合理性 · 用户高可用性 · 可扩展性 · 低冗余  
> **项目规模**: ~88 个 Python 文件，~16,100 行代码  
> **评审方式**: 纯只读，未修改任何文件

---

## 一、总体评价

| 维度 | 评分 | 说明 |
|:---|:---:|:---|
| 架构合理性 | ⭐⭐⭐☆☆ | 核心数据流清晰，但存在职责越界、重复机制、注册模式不一致等架构债务 |
| 用户高可用性 | ⭐⭐⭐⭐☆ | 离线架构设计优秀，容错与降级策略到位，但缺乏优雅退出与运行时监控 |
| 可扩展性 | ⭐⭐⭐☆☆ | 策略注册表与形态库框架已搭好，但新策略接入仍需手动同步多处硬编码 |
| 低冗余 | ⭐⭐☆☆☆ | DDL 重复、功能重叠、遗留代码、全局状态散布等多重冗余问题 |

**总评**: 项目历经 9+ 大版本迭代，功能完整性高、实战验证充分，但迭代过程中积累了可观的架构债务。以下逐维度展开详述。

---

## 二、架构合理性（⭐⭐⭐☆☆）

### 2.1 ✅ 做得好的

1. **数据流清晰**: `Baostock → data_provider → calculator → strategy → signal_tracker → notifier` 形成单向数据管道，层次分明。
2. **离线优先架构**: V7.1 起切换 Baostock 本地数据库，扫描完全不依赖实时网络，解决了 T+1 模式下的核心痛点。
3. **策略注册表模式**: `StrategyRegistry` 提供工厂方法统一策略实例化，为策略扩展提供了入口。
4. **形态库插件化**: `core/patterns/` 基类 + 注册表的设计，理论上支持独立形态插件的拔插。
5. **Phase2 流水线重构**: `hunter.py` 从 412 行 God Function 拆分为 `_scan_market → _classify_signals → _compose_report → _dispatch_charts` 四阶段流水线，显著改善了可读性。

### 2.2 ❌ 需要改善的

| # | 问题 | 严重度 | 位置 | 说明 |
|:--|:---|:---:|:---|:---|
| A1 | **Watchlist 与 SignalTracker 功能重叠** | 🔴 高 | `tools/watchlist.py` ↔ `core/signal_tracker.py` | 两者都实现了信号状态追踪逻辑（入场/止损判断、状态机转换），属于同一职责的重复实现。`WatchlistManager.update_status()` 与 `signal_tracker._track_pending()` 做的事情高度重叠，维护时需同步两处，否则行为不一致。 |
| A2 | **策略名判断逻辑三处重复** | 🔴 高 | `strategy_registry.py`, `scanner.py`, `notifier.py`, `formatter.py` | `if "MTR" in name.upper()` 形式的策略名判断散布在至少 4 个文件中，违反 DRY。新增策略时需手动同步所有判断点。应统一到 `StrategyRegistry.get_strategy_type()` 方法。 |
| A3 | **DDL 重复定义** | 🔴 高 | `core/database.py` ↔ `core/signal_tracker.py` | `signal_archive` 表的建表 DDL 存在两份，且索引定义不一致（`signal_tracker.py` 多了 `idx_sa_strategy` 索引）。建表应只在 `database.py` 维护。 |
| A4 | **StrategyRegistry 与 PatternRegistry 设计不一致** | 🟡 中 | `core/strategy_registry.py` ↔ `core/patterns/base.py` | 策略用工厂模式，形态用注册表模式，两套并存增加学习成本和接入复杂度。新开发者需理解两套机制。 |
| A5 | **scanner.py 承担了过多映射逻辑** | 🟡 中 | `core/scanner.py` 第 73-206 行 | `run_scanner()` 中策略列名映射（`sl_col_map`、`entry_price` 映射、`tp1_price` 映射）约 130 行 if-elif 逻辑，应下沉到各策略类自身。 |
| A6 | **formatter.py 引用不存在的策略名** | 🟡 中 | `core/formatter.py` 第 114 行 | `StrategyRegistry.get_strategy("HUNTER_V1")` 不在注册表中，通过模糊匹配 `if "MTR" in name` 回退到 MTRStrategy，这是一个隐式 bug——未来如果策略注册表去掉模糊匹配，此处会崩溃。 |
| A7 | **God Function 未完全消除** | 🟡 中 | `core/signal_tracker.py` | `run_tracker_dashboard()` ~230 行、`_push_dashboard_discord()` ~240 行，各自承担数据查询 + 业务逻辑 + 格式化 + 推送 4 种职责，应继续拆分。 |

---

## 三、用户高可用性（⭐⭐⭐⭐☆）

### 3.1 ✅ 做得好的

1. **离线扫描零网络依赖**: 扫描流程完全基于本地 SQLite，不受网络波动影响。
2. **盘中时空错位警告**: `hunter.py` 第 675-680 行的 T-1 数据警示，防止用户在盘中误用延迟数据做实盘决策。
3. **Discord 推送容错**: 重试机制（3 次）、超时放宽（150s）、DPI 降级，解决大批量推送断连问题。
4. **AI 审计旁路**: `--no-ai` 模式允许纯技术面直通，在 DeepSeek API 不可用时保障系统可用。
5. **数据新鲜度检查**: 启动时自动检测数据滞后天数并提示。
6. **数据完整性校验**: `validate_integrity()` 对 OHLCV 做严格逻辑检查，拒绝脏数据入库。
7. **周线极速本地聚合**: `_fast_local_weekly_aggregation()` 从日线本地转换周线，5 秒完成全市场更新，极大提升了周线扫描的可用性。

### 3.2 ❌ 需要改善的

| # | 问题 | 严重度 | 位置 | 说明 |
|:--|:---|:---:|:---|:---|
| H1 | **裸 `except: pass` 吞关键异常** | 🔴 高 | 23 处 | 全项目共 23 处裸 `except:` 或 `except Exception: pass`，会吞掉 `KeyboardInterrupt`、`SystemExit` 等关键异常。最危险的是 `database.py` 第 79/100 行（连接池归还时），以及 `signal_tracker.py` 第 876 行（价格获取失败静默返回 None）。 |
| H2 | **连接池无健康检查与泄漏检测** | 🔴 高 | `core/database.py` | `Queue` 实现的连接池缺乏：① 连接健康检查（归还后 SELECT 1 但不阻断不健康连接的取出）；② 连接泄漏检测（无超时回收机制）；③ 连接数上限控制（`DB_POOL_SIZE=10` 但 `Queue` 无 maxsize 限制）。 |
| H3 | **Watchlist JSON 无并发保护** | 🟡 中 | `tools/watchlist.py` | JSON 文件读写无文件锁，多进程/多线程同时操作可能导致数据丢失。 |
| H4 | **日志配置互相覆盖** | 🟡 中 | 35 处 | 35 个文件在模块级别调用 `logging.basicConfig()`，后加载的模块会覆盖先加载的配置。应只在主入口调用一次。 |
| H5 | **无优雅退出机制** | 🟡 中 | `hunter.py` | 扫描过程中 Ctrl+C 可能导致 SQLite WAL 锁未释放、DB Writer 线程未收到 Poison Pill。应注册 `signal.SIGINT` 处理器，确保资源清理。 |
| H6 | **GUI 仪表盘引用过期模块** | 🟡 中 | `gui_dashboard.py` 第 43-46 行 | `from tools import data_manager` 和 `from core.monitor import monitor` 引用了旧版薄代理层和已弃用的监控模块，导入失败时仅打印错误但不降级提示。 |

---

## 四、可扩展性（⭐⭐⭐☆☆）

### 4.1 ✅ 做得好的

1. **策略基类设计**: `BaseStrategy` ABC 定义了 `calculate_signals()`, `signal_column`, `format_prompt()`, `parse_result()` 等标准接口，新策略只需继承并实现。
2. **策略注册表**: `StrategyRegistry` 通过类映射 + 工厂方法，新增策略只需注册即可自动出现在菜单中。
3. **周线策略反射映射**: `scanner_weekly_gap.py` 的 `STRATEGY_COLS` 字典实现策略与输出列的解耦，避免了硬编码公式。
4. **配置外部化**: `config/settings.py` 集中管理策略参数、API 密钥、系统参数，支持 `.env` 覆盖。

### 4.2 ❌ 需要改善的

| # | 问题 | 严重度 | 位置 | 说明 |
|:--|:---|:---:|:---|:---|
| E1 | **新策略接入需修改 N 处** | 🔴 高 | 全局 | 新增一个策略（如"Gap+H3"）需要同步修改：① `strategies/` 新文件；② `StrategyRegistry._strategies` 注册；③ `StrategyRegistry._OFFICIAL_LIST` 列表；④ `scanner.py` 的 `sl_col_map` 映射和 entry/tp 映射；⑤ `notifier.py` 的策略名判断和绘图逻辑；⑥ `scanner_weekly_gap.py` 的 `STRATEGY_COLS`；⑦ `hunter.py` 的 `weekly_supported` 列表和策略分流逻辑。至少 7 处手动同步，极易遗漏。 |
| E2 | **映射逻辑硬编码在 scanner** | 🔴 高 | `core/scanner.py` 第 73-106 行 | 策略列名到标准列名的映射（sl_col_map, entry, tp）硬编码在 scanner 中，而非由策略类自身提供。应让每个策略类定义 `get_signal_info(df)` 方法返回标准化的 info 字典。 |
| E3 | **周线策略列表硬编码** | 🟡 中 | `hunter.py` 第 828/904 行 | `weekly_supported = ['STRATEGY_STRUCTURAL_GAP', 'STRATEGY_GAP_PINBAR', 'STRATEGY_GAP_H2']` 硬编码，应从 `StrategyRegistry` 动态查询支持周线的策略。 |
| E4 | **绘图逻辑与策略耦合** | 🟡 中 | `tools/notifier.py` | `generate_chart_bytes()` 内约 200 行绘图逻辑中，策略名判断决定了标注方式，每新增一种策略形态就需修改此函数。应将 PA 标注逻辑下沉到策略类。 |
| E5 | **__init__.py 未充分利用** | 🟢 低 | `core/`, `tools/`, `config/` | 这些包的 `__init__.py` 均为空文件，未利用包初始化简化导入路径（如 `from core import scanner` vs `from core.scanner import run_scanner`）。 |

---

## 五、低冗余（⭐⭐☆☆☆）

### 5.1 代码冗余清单

| # | 冗余类型 | 严重度 | 涉及文件 | 行数估算 |
|:--|:---|:---:|:---|:---|
| R1 | **Watchlist ↔ SignalTracker 功能重叠** | 🔴 | `tools/watchlist.py` ↔ `core/signal_tracker.py` | ~80 行重复逻辑 |
| R2 | **DDL 双重定义** | 🔴 | `core/database.py` ↔ `core/signal_tracker.py` | ~20 行重复 DDL |
| R3 | **策略名判断 N 处重复** | 🔴 | scanner/notifier/formatter/hunter | ~30 行重复判断 |
| R4 | **logging.basicConfig 35 处重复** | 🟡 | 全局 35 个文件 | ~35 行重复配置 |
| R5 | **sys.path.insert 38 处重复** | 🟡 | 全局 38 个文件 | ~38 行重复路径修补 |
| R6 | **遗留别名未清理** | 🟡 | `formatter.py` (format_for_ai, _get_common_context) | ~4 行 |
| R7 | **死代码** | 🟡 | `signal_db.py` (旧版)、`backtest.py` (Placeholder)、`data_manager.py` (薄代理) | ~140 行 |
| R8 | **R 倍数计算重复** | 🟢 | `signal_tracker.py` 第 468-478 行 vs 第 504-509 行 | ~10 行 |
| R9 | **导入重复** | 🟢 | `fetcher_baostock.py` (threading/sys 双重导入) | ~2 行 |

**冗余估算总计**: 约 **360 行** 可消除的重复代码，占项目总量 ~2.2%。

### 5.2 文件冗余清单

| 文件 | 状态 | 说明 |
|:---|:---|:---|
| `core/signal_db.py` | 🗑️ 废弃 | 旧版 JSON 信号去重，已被 `signal_archive` 完全替代 |
| `core/backtest.py` | 🗑️ 废弃 | 49 行 Placeholder 实现，从未使用 |
| `tools/data_manager.py` | 🗑️ 废弃 | 57 行薄代理层，仅转发 `data_provider` 的函数 |
| `tools/scan_three_k.py` | 🗑️ 废弃 | 78 行旧版 3K 扫描器，已被 `scanner.py` 统一 |
| `tools/scan_v34.py` | 🗑️ 废弃 | 124 行 V34 版本扫描器，历史遗留 |
| `tools/test_signals.py` | 🗑️ 废弃 | 33 行简易测试脚本，已被 `tests/` 正式测试替代 |
| `tools/test_weekly_history.py` | 🗑️ 废弃 | 72 行手动测试脚本，同上 |
| `tools/read_stats.py` | 🗑️ 废弃 | 36 行统计读取脚本，一次性用途 |
| `gui_dashboard.py` | ⚠️ 过期 | 引用旧版模块（data_manager/monitor），与新架构脱节 |

---

## 六、架构改进建议路线图

按优先级排列，建议分三阶段执行：

### 🚀 阶段一：消除冗余（预计影响 ~360 行，风险低）

| 序号 | 动作 | 预期收益 |
|:---|:---|:---|
| 1 | 删除废弃文件：`signal_db.py`, `backtest.py`, `data_manager.py`, `scan_three_k.py`, `scan_v34.py`, `test_signals.py`, `test_weekly_history.py`, `read_stats.py` | 减少 ~500 行死代码 |
| 2 | 统一 DDL 到 `database.py`，删除 `signal_tracker.py` 中的重复 DDL | 消除建表不一致风险 |
| 3 | 清理 `formatter.py` 中的遗留别名 (`format_for_ai`, `_get_common_context`) | 消除混淆 |
| 4 | 统一日志配置：删除所有模块级 `logging.basicConfig()`，仅在 `hunter.py` 主入口配置一次 | 消除 35 处重复 |
| 5 | 用 `pyproject.toml` 的 `[tool.pytest.ini_options] pythonpath` 替代所有 `sys.path.insert` | 消除 38 处重复 |

### 🔧 阶段二：架构收敛（预计影响 ~500 行，风险中）

| 序号 | 动作 | 预期收益 |
|:---|:---|:---|
| 6 | **合并 Watchlist 到 SignalTracker**：将 `WatchlistManager` 的去重拦截功能统一到 `signal_tracker`，消除重叠的状态追踪逻辑 | 核心架构简化 |
| 7 | **策略映射下沉**：将 `scanner.py` 中的列名映射逻辑移入各策略类，每个策略定义 `get_signal_info(df) -> dict` 标准方法 | 新策略接入从 7 处→3 处 |
| 8 | **统一注册模式**：将 `PatternRegistry` 合并到 `StrategyRegistry`，形态作为策略的子类型注册 | 减少概念数量 |
| 9 | **StrategyRegistry 增加元数据**：每个策略注册时声明 `supported_timeframes`, `display_name`, `sl_column`, `entry_column` 等元数据，消除 scanner/notifier 中的硬编码判断 | 彻底消灭策略名 if-elif 链 |
| 10 | **修复 formatter.py 的 "HUNTER_V1" 引用**：改为正确的策略名或通过 StrategyRegistry 获取 | 消除隐式 bug |

### 🏗️ 阶段三：质量提升（预计影响 ~300 行，风险中高）

| 序号 | 动作 | 预期收益 |
|:---|:---|:---|
| 11 | **拆分 signal_tracker God Function**：将 `run_tracker_dashboard()` 和 `_push_dashboard_discord()` 拆分为 查询/计算/格式化/推送 四个独立函数 | 可维护性 |
| 12 | **连接池升级**：使用 `contextlib` + 超时回收 + 健康检查替代裸 `Queue` | 运行时稳定性 |
| 13 | **裸 except 全量替换**：`except:` → `except Exception:`，关键位置添加日志 | 可观测性 |
| 14 | **优雅退出**：注册 `signal.SIGINT/SIGTERM` 处理器，确保 DB Writer 收到 Poison Pill、SQLite WAL 释放 | 数据安全 |
| 15 | **绘图标注下沉到策略类**：每个策略定义 `annotate_chart(ax, df)` 方法，notifier 只负责渲染框架 | 可扩展性 |

---

## 七、关键指标总结

```
┌──────────────────────────────────────────────────┐
│            项目健康度仪表盘                        │
├──────────────────────┬───────────┬────────────────┤
│ 指标                  │ 当前值     │ 目标值         │
├──────────────────────┼───────────┼────────────────┤
│ Python 文件数         │ 88        │ ≤ 70           │
│ 总代码行数            │ ~16,100   │ ≤ 15,000       │
│ 裸 except 数量        │ 23        │ 0              │
│ logging.basicConfig  │ 35        │ 1              │
│ sys.path.insert      │ 38        │ 0              │
│ 重复 DDL             │ 2         │ 1              │
│ 功能重叠模块          │ 2(WS+ST)  │ 1              │
│ 废弃文件              │ 9         │ 0              │
│ 策略接入修改点        │ 7         │ 3              │
│ 测试覆盖率            │ ~5%       │ ≥ 30%          │
│ 文档与代码同步率       │ 低        │ 高             │
└──────────────────────┴───────────┴────────────────┘
```

---

## 八、风险提示

1. **最优先修复**: `H1`（裸 except 吞异常）和 `H2`（连接池无健康检查）是生产环境中最高风险的两个问题，可能导致数据静默丢失或服务挂死。
2. **V10 升级窗口**: 建议在下次大版本迭代（如 V10.0）时集中执行阶段一和阶段二，避免架构债务继续累积。
3. **gui_dashboard.py**: 当前已与新架构脱节，建议明确其定位——要么升级适配新模块，要么标记废弃并引导用户使用 CLI 模式。

---

*本报告为只读评审，未对项目做任何修改。所有建议仅供参考，实施前请在独立分支验证。*
