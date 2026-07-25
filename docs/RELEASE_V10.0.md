# Brooks-AI Quant System V10.0 发布总结

> 发布日期: 2026-07-25
> 版本定位: 架构收敛 + 工程守门 + 高可用（质变版本）
> 前序版本: V9.20（AWIL 策略）

## 一、为什么是 V10.0（而非 V9.21）

V9.x 系列以"新增策略 / 功能"为主轴。V10.0 不做新策略，而是把过去散落、重复、靠人肉守门的工程状态，**收敛成一条清晰、可自动拦截、不易静默死**的系统。这是从"脚本能跑"到"工程可靠"的质变，故跳版。

## 二、核心变更

### 1. 彻底单引擎（消除重复编排）
- 周线 / 3K 扫描入口统一到 `hunter.py` 单一 CLI（`--timeframe weekly [--strategy STRATEGY_3K]`）+ `core/scan_engine` 共享编排核心。
- 删除两个重复 scanner 脚本（`tools/scanner_weekly_gap.py` / `tools/scanner_weekly_3k.py`），零外部 import，迁移 2 个测试 + `web_viewer` 文案。
- 等价交叉 diff 证明**零逻辑漂移**（gap 177 行仅函数名 / docstring 差异；3K 仅 import 位置差异）。
- 刻意保留：3K 不归档 signal_tracker、独立 JSON / MD / Discord 形状、日线热路径零变化。

### 2. 自动门禁 hook（质量守门从手动 → 自动拦截）
- 装 `pre-commit` hook（`hooks/pre-commit` 跟踪 + `.git/hooks/pre-commit` 生效）：每次 commit 前自动跑 `.agent/quality_gate.py`，红线 / DDL 违规或测试数跌破基线**直接阻断提交**。
- 测试基线刷新 143 → 156（真实测试数，守卫才真有意义）。
- 端到端验证：提交 `bc5d73f` 过程中 hook 自动跑通（89 .py / 156 测试 / 0 红线）。

### 3. 运行监控 / 崩溃告警（避免静默死，高可用第一性）
- `hunter.py` 入口最外层兜底：未捕获异常 → Discord 告警 + 写 `data/crash_log.txt`，然后 re-raise（外部调度可感知失败）。
- 正常运行写 `data/last_run.json` 心跳；新增 `tools/check_heartbeat.py` 供定时任务检查运行过期（`--alert` 过期发 Discord）。

### 4. 配套清理（降低熵）
- 死代码归档：`git mv` 32 个 tools 研究 / 回测脚本 + 整个 `strategy_lab/` 到 `archive/`（可逆）。
- 《项目代码全书》`docs/PROJECT_CODEBOOK.md`：归档后实测规模与模块字典。
- #23 文档 / 低优收尾：门禁加固、calculator 告警、stdout 互踩修复、文档矛盾校正。
- 全流程验证：质量门禁 0 红线 / 156 测试全绿 / 等价 diff 零漂移 / 安全冒烟真实 Discord 0 次。

## 三、质量基线（V10.0）

| 指标 | 值 |
|:---|:---|
| 红线（sys.path.insert / basicConfig / 裸except / import*） | 0（全清零） |
| 测试 | 156 passed（39s；其中 2 个真打 570MB 库的 schema 测试占 33.5s，属已知可优化项） |
| 扫描 .py 数 | 88 |
| 远程库 | `origin → github.com/lhy438769415-wq/A`（本地优先，未 push） |
| 当前版本标识 | README / CHANGELOG / LOGIC_AUDIT_RULES / ENGINEERING_MATURITY_PLAN / code_review_report / STATUS / MEMORY 统一刷新为 V10.0 |

> 注：代码内 `V9.x` 历史注释属"改动标记"，非当前版本，全部保留。

## 四、已知待办（下阶段本地优化，本次未动）

- **测试提速**：2 个 schema 测试独占 33.5s，可改 fixture / mock 或拆慢测标签（低风险、见效直接）。
- **停牌边界保护 ④b**：复牌 / 新股上市首日无涨跌停限制的 bar 进入指标计算（中风险，动日线计算层）。
- **pending 命中 rating=None 形状统一**：需谨慎，避免破坏已验证的零漂移等价性。
- **扫描运行时性能勘察**：数据层 IO / 周线 5s 聚合瓶颈定位。

## 五、升级记录

- 当前版本标识统一刷新为 V10.0：README（标题 + 迭代表新增 V10.0 行）、CHANGELOG_agent_handoff、LOGIC_AUDIT_RULES、ENGINEERING_MATURITY_PLAN、code_review_report、STATUS（当前版本 + 最近迭代表）、MEMORY（项目头 + 版本演进链 + 规模注记）。
- 新增本发布总结 `docs/RELEASE_V10.0.md`。
- 代码内 `V9.x` 历史注释保留。
