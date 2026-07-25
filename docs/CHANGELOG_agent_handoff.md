# 🤝 Brooks-AI Agent 交接文档

> **生成日期**: 2026-05-26
> **适用对象**: 继任 Agent（了解项目现状、已完成改动、待执行任务）
> **项目代号**: debugV7.1_for_antigravity
> **项目路径**: `D:\life\Trading view\_Project_A\Data_from_Akshare\debugV7.1_for_antigravity`

---

## 一、项目概况

| 项目 | 说明 |
|------|------|
| **项目名称** | Brooks-AI Quant System V9.20 |
| **核心功能** | A股量化扫描系统：基于 Al Brooks PA 理论 + DeepSeek AI 二次审计 |
| **产品定位** | **人机协作** — 系统负责 数据获取 → 标的筛选 → 机会推送 → 统计回测；真正交易由人工操作 |
| **数据源** | Baostock 本地 SQLite（离线优先，T+1 模式） |
| **推送渠道** | Discord |
| **技术栈** | Python 3.13 / SQLite WAL / pandas+numpy 向量化 / DeepSeek API |
| **规模** | ~88 个 Python 文件，~16,100 行代码 |

---

## 二、系统架构（5 层）

```
L1 入口层    → hunter.py / gui_dashboard.py
L2 核心流水线 → scanner / signal_tracker / formatter
L3 策略系统   → BaseStrategy + 5个具体策略 + StrategyRegistry
L4 输出追踪   → notifier / watchlist(Facade)
L5 基础设施   → database / data_provider / config
```

**4 阶段流水线**:
```
_scan_market() → _classify_signals() → _compose_report() → _dispatch_charts()
   数据层             策略层             AI审计层            推送层
```

**5 个已注册策略**:
| 策略 | 注册名 | 时间框架 | 核心逻辑 |
|------|--------|---------|---------|
| MTR 反转 | `MTR_MASTER` | 日线 | 七维度评分体系 (V35/V36)，五点结构序列 |
| 三K突破 | `STRATEGY_3K` | 日线 | 八步信号流水线，动量微通道突破 |
| 结构缺口 | `STRATEGY_STRUCTURAL_GAP` | 日线+周线 | 突破识别+缺口存活+EV评级 |
| 缺口Pinbar | `STRATEGY_GAP_PINBAR` | 日线+周线 | 突破识别+Pinbar确认 |
| 缺口H2 | `STRATEGY_GAP_H2` | 日线+周线 | 两腿回调状态机 |

---

## 三、已完成的改动（2026-05-26）

### ✅ P1: 策略自描述机制（★★★★★ 决策影响级）

**问题**: 新策略接入需修改 7 处代码，极易漏配导致信号遗漏。

**解决方案**: 在 `BaseStrategy` 上新增 3 个自描述方法，策略接入从 7 处修改降至 3 处。

**改动文件清单**:

| 文件 | 改动内容 |
|------|---------|
| `core/strategies/base.py` | 新增 `get_metadata()` / `get_signal_info(df)` / `annotate_chart(fig, df)` 三个方法 |
| `core/strategies/mtr_strategy.py` | 实现 `get_metadata()` 返回 display_name/timeframes/tp_multiplier 等 |
| `core/strategies/three_k_strategy.py` | 同上 |
| `core/strategies/structural_gap_strategy.py` | 同上 |
| `core/strategies/gap_pinbar_strategy.py` | 同上 |
| `core/strategies/gap_h2_strategy.py` | 同上 |
| `core/strategy_registry.py` | 新增 `_resolve_class()` / `get_metadata()` / `get_strategies_by_timeframe()` |
| `core/scanner.py` | `sl_col_map` 硬编码 → `strat.get_signal_info()` 动态获取；策略名 → `get_metadata().display_name` |
| `tools/notifier.py` | 策略名翻译字典 → `get_metadata().display_name`；图表注解 → `annotate_chart()` |
| `hunter.py` | `weekly_supported` 硬编码列表 → `StrategyRegistry.get_strategies_by_timeframe('weekly')` |
| `tools/scanner_weekly_gap.py` | `STRATEGY_COLS` 硬编码 → `_get_strategy_cols()` 动态获取 |

**新策略接入流程（修改后）**:
1. `core/strategies/` 新建策略文件，继承 `BaseStrategy`
2. `strategy_registry.py` 的 `_STRATEGY_MAP` 注册映射
3. 策略类实现 `get_metadata()` + `get_signal_info()` + `annotate_chart()`

**无需再改**: scanner.py / notifier.py / hunter.py / scanner_weekly_gap.py

---

### ✅ P2: Watchlist/SignalTracker 合并（★★★★☆ 决策影响级）

**问题**: WatchlistManager（JSON后端）和 SignalTracker（SQLite后端）功能高度重叠，状态不同步风险高。

**解决方案**: `WatchlistManager` 重构为 Facade，委托到 `SignalTracker`（SQLite 后端），消除双状态追踪。

**改动文件清单**:

| 文件 | 改动内容 |
|------|---------|
| `core/signal_tracker.py` | 新增 5 个兼容函数：`add_signal_entry` / `check_signal_exists` / `get_signal_status` / `get_signals_by_status` / `update_signal_entry` |
| `tools/watchlist.py` | 整体重写为 Facade，内部委托到 `SignalTracker`，消除 JSON 状态追踪 |

**兼容性**: `WatchlistManager` 的公共接口保持不变，调用方无需修改。

---

### ✅ P3: 优雅退出机制 / WAL 锁卡重启（★★★★☆ 使用效率级）

**问题**: Ctrl+C 强杀后 SQLite WAL 临时文件未合并，导致下次启动 5-30 秒恢复等待。

**解决方案**: WAL checkpoint + 连接池排空 + AI Worker 线程 join + KeyboardInterrupt 中断扫描。

**改动文件清单**:

| 文件 | 改动内容 |
|------|---------|
| `core/database.py` | 新增 `close_all_connections()` — WAL checkpoint + 连接池排空 |
| `hunter.py` Edit 1 | `_scan_market()` 的 `as_completed` 循环中新增 `except KeyboardInterrupt: break` |
| `hunter.py` Edit 2 | `run_pipeline_once()` 四阶段包裹在 `try/finally` 中，finally 确保 `stop_event.set()` + `t.join(timeout=3)` |
| `hunter.py` Edit 3 | `main()` 的 `finally` 块中调用 `close_all_connections()` |

**原则**: 最小改动，仅 2 文件 4 处编辑，未触碰不相关代码。

---

### 测试验证状态

| 测试项 | 结果 |
|--------|------|
| 语法检查 | ✅ PASS |
| 导入测试 | ✅ PASS |
| P3 专项测试（5 项） | ✅ 全 PASS |
| P1/P2/P3 回归测试 | ✅ 98/98 PASS |

测试文件: `tests/test_p1_p2_regression.py`（98 个用例覆盖 P1/P2/P3 所有改动）

**运行测试命令**:
```bash
cd "D:\life\Trading view\_Project_A\Data_from_Akshare\debugV7.1_for_antigravity"
.venv\Scripts\python.exe -m pytest tests/test_p1_p2_regression.py -v --tb=short
```

---

## 四、待修复任务（按优先级排序）

| 编号 | 问题 | 优先级 | 状态 | 简要说明 |
|------|------|:------:|:----:|---------|
| P4 | DDL 双重定义 | ★★★☆☆ | 待修复 | `signal_archive` 建表语句同时存在于 `database.py`(L169) 和 `signal_tracker.py`(L37)，后者多了 `idx_sa_strategy` 索引 |
| P5 | 裸 except: pass | ★★☆☆☆ | 待修复 | 23 处裸 except，人机协作下 0 处危险，~17 处合理忽略，~4 处烦人但不危险，影响调试 |
| P6 | 连接池优化 | ★★☆☆☆ | 待修复 | Queue 连接池无健康检查、无泄漏检测、无 maxsize 限制 |
| P7 | logging.basicConfig 覆盖 | ★★☆☆☆ | 待修复 | 35 处模块级 basicConfig 互相覆盖，格式/级别不统一 |
| P8 | sys.path.insert 散布 | ★☆☆☆☆ | 待修复 | 38 处各自计算项目根路径，写法不统一 |
| P9 | 废弃文件清理 | ★☆☆☆☆ | 待修复 | ~9 个废弃文件，约 500 行死代码 |

**详细修复方案**: 见 `docs/optimization_fix_plan.md`

---

## 五、关键文件速查

### 核心代码

| 文件 | 职责 | 行数 | 注意事项 |
|------|------|:----:|---------|
| `hunter.py` | 主入口 + 4阶段流水线 | ~1000 | P1/P3 已改；`main()` finally 中调 `close_all_connections()` |
| `core/scanner.py` | 扫描引擎 | ~300 | P1 已改；使用 `strat.get_signal_info()` 替代硬编码映射 |
| `core/strategy_registry.py` | 策略注册表 | ~150 | P1 已改；新增 `_resolve_class` / `get_metadata` / `get_strategies_by_timeframe` |
| `core/strategies/base.py` | 策略基类 | ~100 | P1 已改；新增 3 个自描述方法 |
| `core/signal_tracker.py` | 信号追踪核心 | ~1200 | P2 已改；新增 5 个兼容函数；**仍有 DDL 重复（P4）** |
| `tools/watchlist.py` | Watchlist Facade | ~120 | P2 已重写；委托到 SignalTracker |
| `tools/notifier.py` | Discord 推送+图表 | ~500 | P1 已改；使用 `get_metadata().display_name` 和 `annotate_chart()` |
| `core/database.py` | 数据库管理 | ~250 | P3 已改；新增 `close_all_connections()` |
| `tools/scanner_weekly_gap.py` | 周线扫描器 | ~520 | P1 已改；`STRATEGY_COLS` → `_get_strategy_cols()` |
| `core/data_provider.py` | 数据获取 | ~400 | 未改；WAL 模式 + 批量写入 + 日线→周线聚合 |
| `core/formatter.py` | AI 审计格式化 | ~250 | **未改**；L114 引用不存在策略名 "HUNTER_V1"（P10 次要 bug） |

### 配置与文档

| 文件 | 说明 |
|------|------|
| `config/settings.py` | 集中配置管理，API Key 通过 .env 注入 |
| `config/gap_optimized_rules.json` | Gap 策略阈值配置，支持自演进 |
| `docs/system_review_report.md` | 4 维系统评审报告（架构/可用性/可扩展性/低冗余） |
| `docs/optimization_fix_plan.md` | 修订版修复计划（含测试验证和回退保护） |
| `docs/SYSTEM_MANUAL.md` | 系统使用手册 |
| `docs/MTR_V35_0_STRATEGY.md` | MTR 策略规范文档 |
| `code_review_report.md` | 原始代码评审报告（V7.1 时代） |

### 测试

| 文件 | 说明 |
|------|------|
| `tests/test_p1_p2_regression.py` | 98 个回归测试，覆盖 P1/P2/P3 全部改动 |
| `tests/test_calculator.py` | calculator 向量化计算测试 |
| `tests/test_three_k_strategy.py` | 3K 策略测试 |
| `tests/test_mtr_flow.py` | MTR 流程测试 |
| `tests/test_phase1_regression.py` | Phase1 回归测试 |
| `tests/test_weekly_and_noai_flow.py` | 周线+无AI模式流程测试 |
| `tests/test_bs_net.py` | 网络连接测试 |
| `tests/test_split_msg.py` | 消息拆分测试 |

**运行测试注意**: 项目使用 `.venv/Scripts/python.exe`（非系统 Python），pytest 安装在 venv 中。

---

## 六、产品定位对技术决策的影响

本系统的核心定位是**人机协作**，这直接影响了以下技术判断：

| 技术问题 | 通用服务标准判断 | 人机协作定位下的判断 | 原因 |
|---------|:-------------:|:----------------:|------|
| 裸 except | ★★★★★ 生产安全 | ★★☆☆☆ 代码卫生 | 系统不自动下单，~17处合理忽略，0处导致错误决策 |
| 连接池 | ★★★★★ 需要完善 | ★★☆☆☆ 按需改进 | 单用户、按需启停，非 7×24 服务 |
| 优雅退出 | ★★★☆☆ 一般 | ★★★★☆ 重要 | 用户频繁启停，WAL 锁卡直接影响体验 |
| 策略扩展性 | ★★★★☆ 重要 | ★★★★★ 最重要 | 信号遗漏 = 错失机会，直接影响决策 |

---

## 七、架构债务全景

```
┌──────────────────────────────────────────────────────────┐
│                    已修复 ✅                               │
├──────────────────────────────────────────────────────────┤
│  P1 策略自描述   → 接入从7处降至3处                        │
│  P2 信号追踪合并 → Watchlist→Facade，消除双状态            │
│  P3 优雅退出     → WAL checkpoint + 连接池排空             │
├──────────────────────────────────────────────────────────┤
│                    待修复 ⏳                               │
├──────────────────────────────────────────────────────────┤
│  P4 DDL双重定义  → database.py + signal_tracker.py        │
│  P5 裸except     → 23处，0处危险，影响调试                  │
│  P6 连接池       → 无健康检查/泄漏检测/maxsize              │
│  P7 日志覆盖     → 35处 logging.basicConfig 互相覆盖       │
│  P8 sys.path     → 38处散布                               │
│  P9 废弃文件     → ~9个，~500行死代码                       │
├──────────────────────────────────────────────────────────┤
│                    评审参考 📋                             │
├──────────────────────────────────────────────────────────┤
│  详细评审: docs/system_review_report.md                   │
│  修复计划: docs/optimization_fix_plan.md                  │
│  原始评审: code_review_report.md                          │
└──────────────────────────────────────────────────────────┘
```

---

## 八、4 维评审评分

| 维度 | 评分 | 一句话 |
|------|:----:|-------|
| 架构合理性 | ⭐⭐⭐☆☆ | 核心数据流清晰，但存在职责越界、重复机制等架构债务 |
| 用户高可用性 | ⭐⭐⭐⭐☆ | 离线架构优秀，容错降级到位，优雅退出已补上 |
| 可扩展性 | ⭐⭐⭐☆☆ | 策略注册表+自描述机制已搭好，但 DDL/日志/路径仍有技术债 |
| 低冗余 | ⭐⭐☆☆☆ | DDL重复、功能重叠已解决一部分，日志/路径/废弃文件待清理 |

---

## 九、继任 Agent 注意事项

1. **运行环境**: 项目有自己的 `.venv`，使用 `.venv/Scripts/python.exe` 运行脚本和测试，**不要用系统 Python 或 workbuddy Python**
2. **数据库**: SQLite WAL 模式，连接通过 `core/database.py` 的连接池管理；已有 `close_all_connections()` 用于优雅退出
3. **策略系统**: 所有策略必须实现 `get_metadata()` / `get_signal_info()` / `annotate_chart()`，通过 `StrategyRegistry` 注册
4. **信号追踪**: `WatchlistManager` 已是 Facade，真正的后端是 `SignalTracker`（SQLite），不要再引入独立的状态追踪
5. **DDL 位置**: `signal_archive` 的建表 DDL 应只维护在 `core/database.py`，`signal_tracker.py` 中的重复 DDL 是 P4 待清理项
6. **人机协作定位**: 评估优先级时始终考虑——系统只推送信号，不下单。对"交易决策"有直接影响的问题优先级最高
7. **测试基线**: 当前回归测试 98/98 通过，任何修改后必须重新跑 `test_p1_p2_regression.py`
8. **项目记忆**: `.workbuddy/memory/MEMORY.md` 记录项目长期状态，`2026-05-26.md` 记录当日工作日志

---

*本文档为 Agent 交接专用，包含项目现状、已完成改动、待执行任务的完整快照。*
