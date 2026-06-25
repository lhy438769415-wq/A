# 项目状态快照

> 本文件由 Agent 在每次重大工作完成后更新。
> 最后更新: 2026-06-25

## 当前版本: V9.18

## 已注册策略
| 注册名 | 类 | 时间框架 | 状态 |
|:---|:---|:---:|:---:|
| MTR_MASTER | MTRStrategy | 日线 | ✅ |
| STRATEGY_3K | ThreeKStrategy | 日线 | ✅ |
| STRATEGY_STRUCTURAL_GAP | StructuralGapStrategy | 日线+周线 | ✅ |
| STRATEGY_GAP_PINBAR | GapPinbarStrategy | 日线+周线 | ✅ |
| STRATEGY_GAP_H2 | GapH2Strategy | 日线+周线 | ✅ |

## 最近迭代
| 版本 | 日期 | 摘要 |
|:---:|:---:|:---|
| V9.18 | 06-25 | Baostock 黑名单防护 + 查询超时保护 |
| V9.17 | 06-04 | 图表历史缺口标注精简 (TradingView 风格) |
| V9.16 | 05-27 | Baostock 超时保护 + Discord 推送统一 |
| V9.15 | 05-24 | 缺口与 MTR 标注修复、日线去重 |

## Harness 建设进展
| 阶段 | 状态 | 日期 |
|:---:|:---:|:---|
| Phase 1: 建围栏 | ✅ 完成 | 06-06 ~ 06-12 |
| Phase 2: 基础设施 | ❌ 待执行 | — |
| Phase 3: 持续进化 | ❌ 待执行 | — |

## 架构债务
| 编号 | 问题 | 优先级 | 状态 |
|:---:|:---|:---:|:---:|
| P4 | DDL 双重定义 (signal_archive 表在多文件重复) | ★★★ | 待修复 |
| P5 | 35 处裸 except (已修复 7 处新增违规) | ★★ | 修复中 |
| P6 | 连接池无健康检查 | ★★ | 待修复 |
| P7 | 38 处 logging.basicConfig | ★★★★ | 待修复 |
| P8 | 41 处 sys.path.insert | ★★★★ | 待修复 |
| P9 | 废弃文件清理 | ★ | 待修复 |
| P10 | signal_tracker 死代码 (5 组同名函数覆盖) | ★★★★★ | 待修复 |
| P11 | signal_tracker 职责拆分 (1649 行, 6 职责) | ★★★ | 待修复 |
| P12 | 信号洪流保护 (Signal Flood Guard) | ★★ | 待实现 |

## 回归测试基线
- 命令: `.venv\Scripts\python.exe -m pytest tests/ -v --tb=short`
- 基线: 25/25 PASS (按文件分批跑，pytest capture bug 需绕过)
- 注意: pytest 框架本身的 capture I/O bug 会导致全量跑失败，非代码问题
