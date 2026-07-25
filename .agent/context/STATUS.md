# 项目状态快照

> 本文件由 Agent 在每次重大工作完成后更新。
> 最后更新: 2026-07-25

## 当前版本: V9.20

## 已注册策略
| 注册名 | 类 | 时间框架 | 状态 |
|:---|:---|:---:|:---:|
| MTR_MASTER | MTRStrategy | 日线 | ✅ |
| STRATEGY_3K | ThreeKStrategy | 日线 | ✅ |
| STRATEGY_STRUCTURAL_GAP | StructuralGapStrategy | 日线+周线 | ✅ |
| STRATEGY_GAP_PINBAR | GapPinbarStrategy | 日线+周线 | ✅ |
| STRATEGY_GAP_H2 | GapH2Strategy | 日线+周线 | ✅ |
| STRATEGY_AWIL | AWILStrategy | 日线 | ✅ |

## 最近迭代
| 版本 | 日期 | 摘要 |
|:---:|:---:|:---|
| V9.20 | 07-20 | 新增 AWIL 策略 (Always In Long H2 顺势入场) |
| V9.19 | 06-26 | 股票中文名本地持久化 (降低黑名单风险) |
| V9.18 | 06-25 | Baostock 黑名单防护 + 查询超时保护 |
| V9.17 | 06-04 | 图表历史缺口标注精简 (TradingView 风格) |
| V9.16 | 05-27 | Baostock 超时保护 + Discord 推送统一 |
| V9.15 | 05-24 | 缺口与 MTR 标注修复、日线去重 |

## Harness 建设进展
| 阶段 | 状态 | 日期 |
|:---:|:---:|:---|
| Phase 1: 建围栏 | ✅ 完成 | 06-06 ~ 06-12 |
| Phase 2: 基础设施 | 🔶 部分完成 | 质量门禁 (`.agent/quality_gate.py`) 已实现并绿通 (0 红线 / 156 测试守卫); CI 自动门禁 hook / 运行监控待建 |
| Phase 3: 持续进化 | ❌ 待执行 | — |

## 架构债务
| 编号 | 问题 | 优先级 | 状态 |
|:---:|:---|:---:|:---:|
| P4 | DDL 双重定义 | ★★★ | ✅ 已修复 (质量门禁 DDL 白名单约束, core/database.py + tools/journal.py 为唯一 schema 主人, 扫描 0 违例) |
| P5 | 裸 except | ★★ | ✅ 已修复 (门禁 0; 残留均在已归档 strategy_lab/) |
| P6 | 连接池无健康检查 | ★★ | ❌ 待修复 (无变化) |
| P7 | logging.basicConfig | ★★★★ | ✅ 已修复 (门禁 0; 统一 get_logger) |
| P8 | sys.path.insert | ★★★★ | ✅ 已修复 (门禁 0; 仅 core/paths.py ensure_importable 为唯一豁免注入点) |
| P9 | 废弃文件清理 | ★ | ✅ 已修复 (strategy_lab/ + 39 个 tools 研究脚本归档至 archive/, git mv 可逆) |
| P10 | signal_tracker 死代码 | ★★★★★ | ✅ 已修复 (commit 9487f73) |
| P11 | signal_tracker 职责拆分 (1649 行, 6 职责) | ★★★ | ✅ 已修复 (拆分为 8 子模块) |
| P12 | 信号洪流保护 (Signal Flood Guard) | ★★ | ❌ 待实现 (无变化) |

## 回归测试基线
- 命令: `.venv\Scripts\python.exe -m pytest tests/ -v --tb=short` (按文件分批跑，绕过 pytest capture bug)
- 测试函数数基线: 156 (由质量门禁 `.agent/quality_gate.py` 守卫，提交前自动比对，防删测试让检查变绿)
- 注意: pytest 框架本身的 capture I/O bug 会导致全量跑失败，非代码问题；部分用例需联网 (Baostock)，离线环境会跳过/失败，属预期
