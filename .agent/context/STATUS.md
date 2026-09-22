# 项目状态快照

> 本文件由 Agent 在每次重大工作完成后更新。
> 最后更新: 2026-09-22

## 当前版本: V10.0（2026-07-25 发布，后续为持续迭代，未再升版本号）

## 已注册策略
| 注册名 | 类 | 时间框架 | 状态 |
|:---|:---|:---:|:---:|
| MTR_MASTER | MTRStrategy | 日线 | ✅ |
| STRATEGY_3K | ThreeKStrategy | 日线 | ✅ |
| STRATEGY_STRUCTURAL_GAP | StructuralGapStrategy | 日线+周线 | ✅ |
| STRATEGY_GAP_PINBAR | GapPinbarStrategy | 日线+周线 | ✅ |
| STRATEGY_GAP_H2 | GapH2Strategy | 日线+周线 | ✅ |
| STRATEGY_GAP_H2_ENHANCED | GapH2EnhancedStrategy | backtest（回测专用） | ✅ 已注册，不在官方扫描池 |
| STRATEGY_AWIL | AWILStrategy | 日线 | ✅ |
| STRATEGY_MONTHLY_RANGE_BREAK | MonthlyRangeBreakStrategy | 月线（仅快照工具） | ✅ 已注册，不在官方扫描池 |

- `_strategies` 共 **8 项**；`_OFFICIAL_LIST`（日/周扫描实际遍历池，strategy_registry.py:62）含前 6 项（不含 H2_ENHANCED 与 MONTHLY）。
- 周线只跑缺口三家族（STRUCTURAL_GAP / GAP_PINBAR / GAP_H2）；MTR / AWIL / 3K 仅日线。

## 最近迭代
| 节点 | 日期 | 摘要 |
|:---:|:---:|:---|
| V10.0 | 07-25 | 架构收敛（彻底单引擎）+ 自动门禁 + 运行监控（崩溃告警/心跳） |
| V9.20 | 07-20 | 新增 AWIL 策略 |
| 项目自述 | 09-05 | 新增 `docs/项目自述_面向Agent.md`（面向多 Agent 接手的自述，§5/§12/§13 演进后须同步） |
| Web 操盘台 v2 原型 | 09-05~09-08 | 需求规格 + 高保真原型 + UX/场景评审 + 纠偏重评估（Tk 操盘台最终退役方向，属二期事项，未动生产代码） |
| Baostock 限流 | 09-09 | 夜间高峰分时段限流 + 黑名单专项说明 |
| GAP H2 动态入场研究 | 09-20~09-21 | 生产“活跃投影”（日线持续标注当前挂单价，顺回调下移、内包 K 也下移、HH 收口才成交）+ 回测自算动态入场；4 组配置回测（A 原版出场 EV 最高 / B 新出场胜率最高）；HH-only 判据经 Al Brooks 核实 |
| 策略卡 + 示意图 | 09-22 | 新增 `docs/gap_h2_strategy_card.md` / `docs/mtr_strategy_card.md`（六类交易员视角，逐条对照生产代码核实、纠正旧 spec 漂移）；利旧 `notifier` 出图工具生成两张典型形态示意图 PNG 嵌入卡内 |

## Harness 建设进展
| 阶段 | 状态 | 日期 |
|:---:|:---:|:---:|
| Phase 1: 建围栏 | ✅ 完成 | 06-06 ~ 06-12 |
| Phase 2: 基础设施 | ✅ 完成 | 质量门禁（`.agent/quality_gate.py`）绿通 + pre-commit hook + 测试基线 |
| Phase 3: 持续进化 | ✅ 完成 | 运行监控（崩溃告警+心跳）+ **GitHub Actions CI（`.github/workflows/ci.yml`，07-25 加，push/PR 触发）** |

## 架构债务
| 编号 | 问题 | 优先级 | 状态 |
|:---:|:---|:---:|:---:|
| P4 | DDL 双重定义 | ★★★ | ✅ 已修复（DDL 白名单，仅 core/database.py + tools/journal.py 为 schema 主人） |
| P5 | 裸 except | ★★ | ✅ 已修复（门禁 0） |
| P6 | 连接池无健康检查 | ★★ | ✅ 已修复 |
| P7 | logging.basicConfig | ★★★★ | ✅ 已修复 |
| P8 | sys.path.insert | ★★★★ | ✅ 已修复（仅 core/paths.py 为豁免注入点） |
| P9 | 废弃文件清理 | ★ | ✅ 已修复 |
| P10 | signal_tracker 死代码 | ★★★★★ | ✅ 已修复 |
| P11 | signal_tracker 职责拆分 | ★★★ | ✅ 已修复（拆 8 子模块） |
| P12 | 信号洪流保护 | ★★ | ✅ 已修复 |
| #136 | core→tools 层级倒置（scan_engine.py:30） | ★ | 已知债，不阻塞 |

## 回归测试基线
- 命令: `.venv\Scripts\python.exe -m pytest tests/ -v --tb=short`（按文件分批跑，绕过 pytest capture bug）
- 测试函数数: **当前实测 199**（grep `def test_` 于 tests/）；门禁基线文件 `.agent/test_baseline.txt` 仍记 **156（已过时）**——质量门禁仅校验"不低于基线"，故 199≥156 仍通过。本轮已 re-init 基线为 199。
- 注意: pytest 框架本身 capture I/O bug 会导致全量跑失败，非代码问题；部分用例需联网（Baostock），离线环境会跳过/失败，属预期

## 数据规模（2026-09-22 实测）
- core 41 py / 12,042 行；core/strategies 12 py；tools 15 py / 4,316 行；tests 27 py；docs 78 md
- 库（2026-09-22 只读重数）: daily_bars 2,868,090 行（≈287 万）、weekly_bars 2,049,030 行（≈205 万）、signal_archive 14,696 条（daily 11,531 / weekly 3,154 / monthly 11；含 6,162 条 BT_ 回测污染，见发现 6）

## 当前待办 / 未根治
- 🔴 发现 6: signal_archive.status 列不可信（周线状态机 04-11 停摆），待用户批准写库修复
- 🔴 track_signals 一次只推进一步（已绕过跑两轮），未根治
- 周线接入第 8 步（用户实跑验收）未做
- backlog: 漏扫日提醒 / 回测 backfill / watchlist 每日定点推送
