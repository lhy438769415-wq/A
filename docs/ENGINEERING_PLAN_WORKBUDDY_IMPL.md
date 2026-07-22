# 工程成熟度方案 · WorkBuddy 实施能力评估

> 目的：把 `ENGINEERING_MATURITY_PLAN.md` 里的五大支柱，逐一映射到 WorkBuddy 真实可用的能力上，
> 明确"哪些现在就能做、哪些要等外接、先用哪种能力动手"。
> 评估依据：WorkBuddy 官方总览（自主执行/本地文件操作/多步任务/可交付结果）+ 本机真实工具箱
> （automation_update 定时任务、Skill 可复用技能、code-reviewer 评审技能、Task 任务追踪、Bash/Write 等）。

---

## 0. 先说结论（一句话）

对"一个人 + AI"的项目，WorkBuddy 的正确用法是：
**用 Skill 把规矩变成"每次动手前自动跑的检查"，用 Automation 让机器"定期自己体检"**——
这能在不依赖任何外部服务器的情况下，替掉团队才有的"第二双眼睛"和"有人盯构建"。

真正"合并前卡死"的硬门禁需要外接代码仓库（GitHub 等），但**那部分配置现在就写好备着**，连上连接器即可生效。

---

## 1. 方案支柱 → WorkBuddy 能力映射

| 方案支柱 | 对应的 WorkBuddy 能力 | 纯原生？(无需外接) | 外部依赖 | 怎么落地 |
|---|---|---|---|---|
| **A. 单一事实源 + 活文档** | • Write 写 ADR 文档<br>• Skill「生成模块调用图」跑 `pydeps` 出图<br>• Automation 定时重新生成文档/图 | ✅ 是 | 无（若要"随 PR 自动更新"需 GitHub） | 先建 ADR 模板 + 一个出图 Skill；Automation 每周重跑 |
| **B. 变更护栏（红线变代码）** | • Write 写 `.pre-commit-config.yaml` + 自定义钩子脚本<br>• **Skill「质量门禁」** 把 ruff + 红线 grep + pytest 固化为每次必跑<br>• code-reviewer 技能当"第二双眼睛" | ✅ 是（本地 git 钩子即可） | 仅"合并前强制卡死"需 GitHub | **P0 首选**：写 pre-commit + 建质量门禁 Skill |
| **C. 测试即规格** | • Write 写 pytest 契约测试<br>• Bash 跑 `vulture` 扫死代码<br>• 接入质量门禁 Skill | ✅ 是 | 无 | 补契约测试 + 死代码扫描并进 Skill/Automation |
| **D. 边界 + 架构适应度函数** | • Write 写一段"检查依赖方向/禁循环依赖"的 Python 脚本<br>• 接入质量门禁 Skill（改动前自动跑） | ✅ 是 | 无 | 写 fitness-check 脚本，挂到质量门禁 Skill | 
| **E. DORA / 健康仪表盘** | • **Automation（定时任务）** 汇总跑 ruff/vulture/pytest/fitness，产出报告<br>• 可经 Discord 连接器推送（当前断开，可落文件） | ✅ 是 | 推送需 Discord 连接器（项目自身 notifier 已有 Discord 能力） | 建一个"每日收盘后体检"Automation | 

---

## 2. WorkBuddy 能做到 vs 做不到（诚实边界）

### ✅ 现在就能做（纯原生，零外部依赖）
1. **写所有护栏配置文件**：pre-commit 配置、CI 的 yaml、CODEOWNERS、适应度函数脚本、ADR 模板——一次性写好。
2. **本地跑所有检查**：ruff（规范）、pytest（测试）、vulture（死代码）、自定义边界检查——通过 Bash。
3. **定时自审（Automation）**：让项目每天/每周自己跑一遍体检并出报告。这正是一个人项目最缺的"有人盯"的替代品。
4. **可复用的质量门禁（Skill）**：把"改动前必须先过检查"做成一个站立规则，以后每次动手自动触发，AI 没法"猜完就交差"。
5. **AI 互审（code-reviewer 技能）**：任何改动都可调用它当"第二双眼睛"，直接对应评审门禁。
6. **任务追踪（Task）**：把 P0–P5 路线当任务清单盯进度。

### ⚠️ 做不到（需外接，当前连接器全断开）
- **真正"合并前卡死"的 CI 门禁**：需要 GitHub Actions 等 CI 服务。WorkBuddy 能写 yaml，但触发/卡死要靠仓库侧。
- **PR 强制人工/AI 评审**：需要 GitHub PR + CODEOWNERS。WorkBuddy 能写 CODEOWNERS、能跑 code-reviewer，但"不批不准合"要靠仓库侧。
- **健康报告自动推 Discord**：需要 Discord 连接器；但本项目自己的 `notifier.py` 本就有 Discord 推送能力，可复用。

> 关键判断：**外接部分现在就写好配置"备着"，但价值大头（自动检查 + 定时体检 + AI 互审）WorkBuddy 现在就能单独交付**。对单人+AI 项目，这就够用了。

---

## 3. 推荐的 WorkBuddy 实施顺序

| 步骤 | 用 WorkBuddy 的哪种能力 | 做什么 | 外部依赖 | 风险 |
|---|---|---|---|---|
| **P0（首选）** | Write + **建用户级 Skill「质量门禁」** + 写 pre-commit | 把"禁止 sys.path.insert / basicConfig"等红线写成 pre-commit 钩子 + Skill（跑 ruff + 红线 grep + 现有 pytest） | 无 | 极低，纯功能外 |
| **P1** | **Automation（定时）** | "每日收盘后体检"：跑 ruff/vulture/pytest/fitness，出 Markdown 报告 | 无 | 极低 |
| **P2** | Write(ADR) + Skill(出图) | 补前 5 个关键决策 ADR；Skill 用 pydeps 出模块图 | 无 | 低 |
| **P3** | Write(测试) + Bash(vulture) | 为策略自描述接口/信号状态机补契约测试；死代码扫进 Automation | 无 | 中（补测试需理解行为） |
| **P4** | Write(fitness 脚本) + 挂 Skill | 写架构适应度函数（依赖方向/共用元数据断言） | 无 | 中 |
| **P5** | Automation(汇总) | 技术债仪表盘周报 | 无（推送待连 Discord） | 低 |

---

## 4. 对你之前"AI 没读全就建议"的直接根治机制

- **质量门禁 Skill**：任何改动前自动跑检查，猜错立刻红 → 杜绝"蒙混过关"。
- **code-reviewer 技能**：AI 产出要交付，先过一遍互审 → 强制"第二双眼睛"，正是之前缺失的环节。
- **Automation 定时体检**：你不在时机器自己查，问题不积累到下次人工发现。
- **适应度函数脚本**：模块边界被破坏，合并前就报警 → 杜绝双引擎式隐性漂移。

> 一句话：WorkBuddy 用"Skill（每次动手先自检）+ Automation（定期自审）+ code-reviewer（互审）"三件套，
> 把团队的"CI 门禁 + 评审 + 监控"在**没有 CI 服务器**的情况下，先替你跑起来。

---

## 5. 待你拍板

1. 是否认可"以 WorkBuddy 原生三件套（Skill + Automation + code-reviewer）为主、外接配置备着"的路线？
2. 若认可，建议**从 P0 动手**：由我创建用户级 Skill「质量门禁」+ 写 pre-commit 配置（纯原生、零功能风险）。
3. 是否要我把 GitHub 连接器连上，以便让"合并前硬卡死"也生效？（当前所有连接器断开）
