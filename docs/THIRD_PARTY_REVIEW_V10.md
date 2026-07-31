# Brooks-AI Quant System V10.0 — 独立第三方评审报告

> **评审日期**：2026-07-26
> **评审方式**：只读静态评审。3 个独立评审代理并行深挖（架构 / 测试工程 / 策略数据安全），主评审员对所有 P0/P1 发现逐条人工核实（读源码 + 跑 SQL + grep 验证），未修改任何生产代码。
> **立场声明**：本报告不与任何既有内部评审对齐结论，全部发现以文件:行号证据为准。含对项目近期迭代（含 AI 协作者操作）的批判。

---

## 一、总体评级

| 维度 | 评分(/10) | 一句话评价 |
|:---|:---:|:---|
| 架构设计 | **5.5** | 注册表元数据驱动是真进步，但胖入口 + core→tools 层级倒置 + Gap 家族复制，"彻底收敛"名过其实 |
| 测试与工程实践 | **4.5** | 门禁/hook/CI 框架齐全且真跑，但 156 测试约 1/3 是元数据冒烟，核心数据层几乎裸奔 |
| 策略正确性 | **7.0** | 回测无未来函数、方法学框架专业、volume 铁律落实，但评级区分度未达标 |
| 数据完整性 | **5.0** | schema 冻结与 WAL 设计扎实，但周线回测取错数据、不完整周污染、缺运行时自检 |
| 安全性 | **8.0** | 密钥管理合格、无 SQL 注入面，Channel ID 硬编码默认值危害有限 |
| **综合** | **6.0** | **策略深度与工程意愿俱佳，但"宣称"与"实证"之间存在系统性差距** |

**核心结论**：这是一个"骨架专业、肌肉不足"的系统。护栏框架（门禁/CI/schema 冻结）搭得有模有样，但若干关键宣称（彻底单引擎、数据驱动评级、A+≥55% 胜率、测试守卫）经实证检验均打了折扣。最危险的不是代码烂，而是**仪表盘全绿时底层有三处指针是坏的**。

---

## 二、评审过程中发现并修复的生产事故（单独记录）

### 事故：生产数据库错位 12 小时（P0，已于评审当日修复）

- **现象**：评审发现 `data/baostock.db` 仅 94KB（空 schema + 5 行测试数据），真正的 570MB 生产库（273 万行 daily_bars、7629 条信号归档）躺在 `data/data_bak/` 子目录。
- **根因**：前一晚（07-25 22:23）AI 协作者做 CI 环境模拟时执行 `mv data data_bak` → 跑 pytest（期间代码自动创建了新的空 `data/baostock.db`）→ `mv data_bak data` 时因目标已存在，真库被移入成为 `data/data_bak/`。收尾验证只看了文件名存在，**未核对文件大小**，误判"还原成功"。
- **影响窗口**：07-25 22:23 ~ 07-26 10:58。期间若运行 hunter 扫描，将读到空库（扫描结果为空或报错）。
- **修复**：空库改名留证（`data/_empty_db_accident_20260726.db`），真库 24 个文件全部归位，`PRAGMA integrity_check` = ok，行数与数据新鲜度（max 2026-07-24）验证通过。零数据损失。
- **教训（转化为防线建议，见 §五 P0-4）**：系统缺少"启动时数据库自检"——hunter 启动不校验库文件大小/行数下限，空库也能静默跑。

---

## 三、亮点（实证，非客套）

1. **回测无未来函数**：评级严格在 `df.iloc[:idx+1]` 切片上计算（`tools/rating_calibration_backtest.py:155`），退出仅用信号后数据、次日 Buy-Stop 入场（`core/backtest_engine.py:74,110`）。这在个人量化项目里属于稀有纪律。
2. **方法学框架专业**：60/40 时间切分 + 年度 Walk-Forward CV + Wilson 置信区间 + 多重比较警示 + 生命周期三过滤（INVALIDATED/VOIDED/TIMEOUT 不入分母）。
3. **volume 铁律真实落地**：`core/rating_core.py` 全部因子带 sop_ref 溯源，无任何 volume 因子——不是注释摆设。
4. **密钥管理合格**：`.env` 从未入 git（git log 无记录），密钥全走环境变量；SQL 全部占位符参数化，无注入面。
5. **策略注册表元数据驱动**：6 策略声明式注册，消费方（hunter/scan_engine/scanner）走 `get_metadata()`，硬编码策略名判断基本消除。
6. **CI 假绿能自我修复**：07-25 CI 曾因 collect-only 替代真跑测试而"假绿"，当日被识别并修复（commit a520673）——说明质量文化是真的，不是装的。

---

## 四、问题清单（按严重度分级，全部含证据）

### P0 — 阻断级（结论不可信 / 系统不可用的风险）

**P0-1 周线评级校准回测实为日线数据重跑，三个周线策略校准结论全部无效**
- `core/data_provider.py:528-561` `get_stock_data(timeframe='weekly')` 的 timeframe 参数**被完全忽略**——函数体只查 `daily_bars`，无任何 timeframe 分支。
- `tools/rating_calibration_backtest.py:117` 以 `timeframe='weekly'` 调用，拿到的却是日线数据。
- 铁证：`calibration_report_full_v2.1.txt` 中 `structural_gap_weekly` 与 `structural_gap_daily` 的年度分层数据**逐字相同**（2024: 39.07%/n=1121；2025: 49.08%/n=1461）。
- 后果：STRUCTURAL_GAP / GAP_PINBAR / GAP_H2 三个周线策略的"校准"从未真实发生，相关评级阈值无数据支撑。

**P0-2 数据驱动评级权重从未回灌生产**
- `config/rating_factors.json` 只有校准工具写入，**全库无运行时代码加载**（grep 证实，唯一引用是 `core/rating.py:99` 的一句注释）。
- 生产评级仍用手调阈值 80/65/50/30（`core/rating.py:24-27`）与手填权重。`awil_strategy.py:209` 标 `calibrated=True` 名不副实。
- 后果：Phase 2 评级校准的全部产出处于"练了不用"状态，评级字母在生产端的含义与校准报告脱节。

**P0-3 "A+ 档胜率 ≥55%" 的验收宣称不成立**
- `calibration_report_full_v2.1.txt:46`：跨年 Walk-Forward **A+ 净胜率 48.69%（n=534, CI 44.47~52.92%, EV -0.016R 负期望）**；2024 年仅 42.4%。
- 个别策略更差：某策略 A+ 跨年仅 38.64%（:89）。
- 测试集单点 A+ 样本量过小（n=17/6/19，CI 宽至 52.7~90.5），统计上不可读。
- 后果：评级字母的区分度未达到项目自定验收线，A+ 当前不等于"高胜率"。

**P0-4 系统无启动自检，空库可静默运行**（由 §二事故暴露）
- hunter 启动不校验 `data/baostock.db` 的存在性/大小/行数下限。本次事故中空库状态下系统"能跑"，只是扫不出东西。
- 建议：启动时校验 ①库文件存在 ②大小 > 100MB ③daily_bars 行数 > 100 万 ④max(trade_date) 距今 < 7 天，任一失败即拒绝运行并 Discord 告警。

### P1 — 应修级（结构债 / 结论偏差）

**架构**
- **A1 胖入口**：`hunter.py` 1151 行，`main()` 232 行(cc≈49)、`_classify_signals` 166 行(cc≈39)，报告排版/推送编排/绘图准备/市场时钟全混在入口层。
- **A2 层级倒置**：`core/scan_engine.py:30` 顶层 `from tools.notifier import ...`；`core/data_provider.py:15` import tools 层——L5 反向依赖 L4，"分层架构"名实不符。
- **A3 Gap 家族复制依旧**：突破检测/锚定 ffill/缺口存活/高潮规避/去重五段逐行雷同（structural:205-304 ≈ pinbar:195-275 ≈ h2:196-277），`parse_result` 三份逐字相同。只收敛了标注层，未抽 GapBase 信号骨架——"彻底收敛"宣称对策略层不成立。
- **A4 吞异常未清零**：`hunter.py:327-328` 主扫描循环 `except Exception: continue` 无日志吞错；scan_engine.py:791、notifier.py:80/336、database.py:70 同型。
- **A5 死代码**：`core/monitor.py` 零引用；`data_provider.py:1148 get_stock_data_hybrid`、`:80 preload_snapshots` 无调用方；`gap_h2_backtest.py:530` import 不存在的模块。

**测试与工程**
- **T1 关键模块裸奔**：156 测试中 98 个集中在 `test_p1_p2_regression.py` 且大半是 import/hasattr/callable 级断言。`data_provider.py`(1164行)、`backtest_engine.py`、`review_bridge.py`(623行)、`signal_tracker/dashboard.py`(539行) **零直接测试**；`scan_engine.py`(802行) 仅 2 个 `isinstance(list)` 冒烟测试；三 Gap 策略 + geometric_engine + mtr_structural_v35（约2450行）无信号级行为测试。
- **T2 假测试**：`test_bs_net.py` 全文无 `def test_`，模块级直接网络 I/O（收集即执行）；`test_mtr_flow.py:22-40` 把 hunter 逻辑复制进测试体自测，hunter 改坏它照样绿；`test_p1_p2_regression.py:570-580` 注释明言"写入失败不应抛异常"且断言仅 `isinstance(str)`——功能坏了也绿，还向真实库写 TEST_ 脏数据。
- **T3 门禁可绕过**：`EXCLUDE_DIRS` 豁免 tests/；红线正则只查 `sys.path.insert` 不查 `sys.path.append`；DDL 守卫不覆盖 `executescript`/外部 .sql/DROP TABLE；测试数守卫防删不防水（10 强测试换 10 冒烟照过）。
- **T4 CI 偏弱**：无 lint/format/type 检查；依赖手工列表与 requirements.txt 漂移（已实锤漏装 3 连）；push 直接进 main，CI 只是事后红叉，无分支保护。
- **T5 依赖失管**：`akshare`/`tushare` 全仓零 import 仍钉在 requirements.txt；无 lock 文件；numpy 钉死 2.4.1 但 CI 装 >=2.2,<2.5。

**策略与数据**
- **S1 不完整周污染**：`data_provider.py:949-996` 周中运行会把 1-3 天的半成品周 K 写入 weekly_bars，`scan_engine.py` 读取端无"当周完整性"过滤 → 周中跑周线扫描会产生幻影信号。（周五收盘后运行不受影响。）
- **S2 幸存者偏差**：库仅 3310 股、日线起于 2023-01-03，名单来自当前在市列表（`data_provider.py:789`），2023 前退市股缺席 → 长期回测胜率系统性虚高。
- **S3 成本模型偏乐观**：滑点 0（`backtest_engine.py:31`）、无最低佣金、一字涨停仍按 max(entry,open) 成交。
- **S4 schema_guard 仅测试期调用**：运行时不阻断带外漂移（`tests/test_schema_integrity.py:24` 是唯一调用点之一）。

### P2 — 可缓级（卫生与一致性）

- **D1 文档名实不符**：根目录 `code_review_report.md` 标 V10.0 实为 2026-05-25 旧评审（仍写"测试覆盖 1.0"）；`docs/PROJECT_CODEBOOK.md:14` 版本仍 V9.20；`STATUS.md:34` 称"CI 因无远程仓库暂缓"与 CI 已上线矛盾。
- **D2 仓库卫生**：`gap_h2_*.csv/json`、`backtest_report.txt`、`hold_list.txt`、`index.html` 等产物被 git 追踪；`pytest-ci-log.zip`（CI 调试残留）在工作区；`.agent/` 被 gitignore 却 -f 强纳部分文件，新增文件将静默漏管。
- **D3 命名不一致**：`MTR_MASTER` vs `STRATEGY_*` 前缀不一；display_name 中英文混排（'GAP H1'/'GAP Pinbar'/'MTR反转'）。
- **D4 重复实现**：`_letter_to_ev_text` 在 hunter.py:66 与 scan_engine.py:40 逐字重复；hunter 周线 CLI 与交互菜单两分支重复；`gap_h2_backtest.py:95-156` 重写 H2 逻辑未复用策略类。
- **D5 单一数据源**：Baostock 停服仅降级无校验；无坏 tick/合法大波动（新股上市）检测；DeepSeek 调用无 max_tokens 上限。

---

## 五、建议行动清单（按性价比排序）

| # | 动作 | 解决 | 工作量 |
|:---:|:---|:---|:---:|
| 1 | hunter 启动加数据库自检（存在/大小/行数/新鲜度），失败即拒跑+Discord 告警 | P0-4 + 本次事故类 | 小 |
| 2 | 修 `get_stock_data` timeframe 分支（weekly 走 weekly_bars/聚合），重跑三周线策略校准 | P0-1 | 中 |
| 3 | 生产评级加载 `rating_factors.json`（带文件缺失兜底），或反向：明确废弃校准产物并改回"经验阈值"文档表述 | P0-2 | 中 |
| 4 | 重定 A+ 验收口径：以跨年 Walk-Forward 净胜率+EV 为准（而非单测试集），或下调宣称 | P0-3 | 小（改口径） |
| 5 | 周线扫描加"当周完整性"过滤（trade_date 须为周五或周最后交易日） | S1 | 小 |
| 6 | 抽 `GapBaseStrategy` 信号骨架，三 Gap 只保留差异参数 | A3 | 大 |
| 7 | hunter.py 拆分：报告排版/推送编排/市场时钟移至 core/formatter、tools/notifier | A1+A2 | 大 |
| 8 | 测试补强：data_provider 周线聚合、scan_engine 路由、rating 阈值映射补行为级测试；删除/改造 T2 三个假测试 | T1+T2 | 中 |
| 9 | CI 加分支保护（PR 必过 CI 才能合 main）+ ruff；requirements.txt 删 akshare/tushare、对齐 CI 依赖 | T4+T5 | 小 |
| 10 | 文档刷新：code_review_report 标注"历史评审(2026-05)"、CODEBOOK 版本号、STATUS Harness 行 | D1 | 小 |

---

## 六、结语

V10.0 的迭代方向是对的——收敛、门禁、监控、CI 全是该做的事，且质量文化（能发现假绿、能自我修正）是真的。但本次第三方评审的核心提醒是：**这个项目的风险已从"代码混乱"转移到"宣称与实证脱节"**——"彻底单引擎"对策略层不成立、"数据驱动评级"未接入生产、"A+≥55%"未被跨年验证支持、"156 测试守卫"里三分之一是冒烟。

下一阶段建议少做新功能，优先做"宣称对齐"：要么把宣称落到实处（修 P0-1/2），要么把宣称降到实证水平（改 P0-3 口径）。一个不敢全信仪表盘的系统，比没有仪表盘更危险。

---

*评审方法声明：本报告由 AI 评审代理执行只读分析，全部 P0/P1 发现经人工复核源码/SQL/grep 证据。评审未涉及：实盘推送端对端演练、DeepSeek 审计质量抽样、GUI 可用性。*
