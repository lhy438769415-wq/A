# 第7章 多 Agent 协作端到端落地实践：目录结构、说明书写法与 CI 集成 Playbook

## 论点
前六章分别闭合了六个能力盲区——范式认知（B1）、说明书治理（B2）、并发隔离（B3）、模式选型（B4）、安全监督（B5）、成本可观测（B6）。本章作为收口章，不引入新理论，而是把前六章能力拼成一个**从零搭建"多 Agent 同目录协作环境"**的可执行 playbook，并直接回应用户的三个工程痛点：①说明书/上下文散落且过期；②多 Agent 同目录时记忆/日志互相覆盖；③协作模式（角色分工 vs 红蓝对抗）悬而未决。本章只落地三件工程纪律：**定目录结构（隔离）、写说明书（SSOT）、接 CI 守卫（防漂移与越权）**；B5 安全监督与 B6 成本可观测已前置，本章仅做集成钩子，不重复展开。

## 一、目录结构模板：worktree 优先，同目录分片降级
多 Agent 同目录最危险的不是"写错"而是"互相覆盖"——这正是许多仓库被改坏事故的根因，直接对应痛点②。结构上最干净的隔离手段是 git worktree：每个 Agent 各占一个独立工作树与分支，"结构上不可能冲突（structurally cannot）"，提交实时共享于同一 .git 对象库，无需 push/fetch（[Git worktrees: From Running Multiple Agents](https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh)）。Anthropic 实测亦印证：Lead 编排 + 多子 Agent 并行可让复杂查询耗时最多降 90%（[How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system)）。

当环境不允许 worktree（如本地单目录限制），则进入**同目录分片降级**：把记忆与日志按 Agent 身份切到各自子目录，直接消解痛点②。PubNub 工程实践给出可落地布局：`.claude/agents/` 放子代理定义，`docs/claude/working-notes/<slug>.md` 与 `docs/claude/decisions/ADR-<slug>.md` 按 slug 串联跨代理痕迹（[Best practices for Claude Code subagents](https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/)）。降级态再切出 `logs/<agent-name>.log` 与 `.claude/agents/<name>/memory.md`，确保各 Agent 上下文与运行日志互不触碰。

推荐根模板：

```
repo-root/
├── AGENTS.md              # 通用 SSOT（根，可提交，随 PR 更新）
├── CLAUDE.md              # Claude 专用薄层，@ 导入 AGENTS.md
├── devprompts/            # 模块化提示（每文件带 when-to-use 触发）
├── .claude/
│   ├── agents/            # 子代理定义（pm-spec / architect / implementer / qa）
│   ├── hooks/             # 生命周期钩子（审批建议 / 日志）
│   ├── settings.json      # 共享权限白名单（可提交）
│   └── worktrees/         # worktree 模式：各 agent 独立分支
└── docs/claude/
    ├── working-notes/     # 按 slug 分片记忆（同目录降级关键）
    └── decisions/         # ADR-<slug>.md 沉淀坑点
```

权限白名单示例（呼应 B5 安全监督，[Best practices for Claude Code subagents](https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/)）：

```json
// .claude/settings.json（共享、可提交）
{
  "permissions": {
    "allow": ["Read", "Grep", "Glob", "WebSearch"],
    "ask":   ["Edit", "Write", "Bash(*)"],
    "deny":  ["Bash(git push:*)", "Bash(rm -rf:*)", "Bash(:sudo*)"]
  }
}
```

> 注意：省略 `tools` 字段的子代理会继承线程全部工具（含 MCP），等于隐式全授权——必须显式白名单。

## 二、说明书写法：SSOT + when-to-use + @import 约束
CLAUDE.md/AGENTS.md 本质是"人与 AI 共享的唯一可信文档"，应简洁、人类可读、提交版本控制且不内含 API key 等敏感信息（[Using CLAUDE.md files](https://www.claude.com/blog/using-claude-md-files)）。行业共识进一步主张 AGENTS.md 作为"单一事实来源（SSOT）"便携基线，放仓库根、随 PR 更新，避免"仓库外上下文会腐烂"；稳定与易变内容分离，通用基线与工具专用薄层解耦（[the hidden truth about cross-tool AI coding context management](https://dredyson.com/the-hidden-truth-about-cross-tool-ai-coding-context-management-what-every-developer-needs-to-know-about-agents-md-mcp-memory-and-proxy-layer-architectures-in-2025)）。

写法三原则：
1. **SSOT 单点定义**——命令只链接到脚本文件而非内联，从根上消灭漂移（"link, don't duplicate"，[How to Handle AI Coding Tool Context](https://dredyson.com/how-to-handle-ai-coding-tool-context-across-claude-code-cursor-codex-and-windsurf-a-complete-beginners-step-by-step-guide-to-managing-project-memory-rules-and-cross-tool-drift-in-2025/)）。
2. **when-to-use 触发条件**——`devprompts/` 下每个模块文件须写明"When to use"，让路由 Agent 精确委派、避免重复劳动（Anthropic 早期教训：简短指令导致子代理重复或留白，[How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system)）。
3. **@import 嵌套不超过 4 跳**——主文件用 `@` 导入 devprompts/ 细节，但业内模块化实践建议引用深度不超过 4 跳：过深会稀释上下文窗口、放大断链漂移且难以审阅（呼应 B2 说明书治理与 B6 成本可观测）。

`devprompts/ci-cd-guide.md` 示例：

```markdown
# CI/CD Guide
## When to use
- 修改 pipeline 配置、部署触发、回滚流程时
- Agent 需要理解 CI/CD 语义以定位失败阶段时
## Summary
本仓库 CI 由 GitHub Actions 驱动，stage 顺序为 lint → test → build → deploy。
## Details
@ci-stages.md | @rollback-procedure.md
```

把每次"踩过的坑"写成 `docs/claude/decisions/ADR-<slug>.md`，呼应第2章说明书治理（痛点①的"过期"由"坑点入册 + 定期复核"解决）。

## 三、CI 集成：drift 守卫 + 质量门禁
说明书会随代码演进而过期，是静默失败主因——Agent 照着已删除的命令执行（[Drift Detection](https://agentsurface.dev/docs/context-files/drift-detection)）。CI 守卫分两层：

**文档漂移守卫（pre-commit，ERROR 阻断）**：在每次提交校验 AGENTS.md/CLAUDE.md 引用的脚本/命令是否真实存在、@import 链接是否断链。做法是正则提取反引号命令比对 package.json 脚本、提取 `@` 引用比对文件系统。实测一个 bash 脚本约 10 秒即可在每次 PR 抓出过期指令（[How to Handle AI Coding Tool Context](https://dredyson.com/how-to-handle-ai-coding-tool-context-across-claude-code-cursor-codex-and-windsurf-a-complete-beginners-step-by-step-guide-to-managing-project-memory-rules-and-cross-tool-drift-in-2025/)）。本 playbook 将校验脚本命名为 `docdrift`，遇断链以 `ERROR` 退出码 1 阻断合并：

```bash
#!/usr/bin/env bash
# scripts/docdrift.sh  —— 接 pre-commit 或 CI
set -euo pipefail
echo "docdrift: validating AGENTS.md / CLAUDE.md references..."
for f in AGENTS.md CLAUDE.md; do
  [ -f "$f" ] || continue
  # 校验反引号命令是否存在于 package.json
  grep -oP '`\K(npm|pnpm|bun|yarn) run [a-z-]+' "$f" | while read -r cmd; do
    script="${cmd##* }"
    grep -q "\"$script\"" package.json || { echo "ERROR: '$cmd' in $f missing in package.json"; exit 1; }
  done
  # 校验 @import 链接文件存在
  grep -oP '(?<=@)[A-Za-z0-9_./-]+\.md' "$f" | while read -r ref; do
    [ -f "$ref" ] || { echo "ERROR: broken @import '$ref' in $f"; exit 1; }
  done
done
echo "docdrift: OK"
```

**质量门禁**：呼应 B5 安全监督，把 `.claude/settings.json` 权限边界纳入 CI——确认子代理未隐式继承全部工具（省略 `tools` 字段会继承线程所有工具含 MCP，等于隐式全授权）（[Best practices for Claude Code subagents](https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/)）。同时建议对高风险写入（如 `git push`、依赖安装）设人工审批闸（human checkpoint），并给自主 Agent 配置最大迭代次数防止失控（[Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)）。

## 四、模式选择落点：角色分工 vs 红蓝对抗
何时用角色分工、何时用红蓝对抗，是本章与 B4 模式选型的集成点，直接回应痛点③。框架对比显示三种心智模型：CrewAI 的**角色制**（Agent 带 role/goal/backstory）适合线性内容流水线；AutoGen 的**会话/辩论制**（GroupChat 多 Agent 轮流发言、互相 critique）天然适配审查、辩论、迭代精炼；LangGraph 的**图控**适合需人工中断与可回滚的生产流程（[LangGraph vs AutoGen vs CrewAI](https://multiagentpro.ai/blog/langgraph-vs-autogen-vs-crewai)）。

落地建议：
- **确定性强的编码/构建任务用角色分工**——PM 提问、Architect 校验、Implementer 构建、QA 验证，各 Agent 工具白名单隔离（[Best practices for Claude Code subagents](https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/)）。
- **质量风险高的产出（研究结论、策略报告）用红蓝对抗**——以 verifier/critic 型 Agent 末端校验；Anthropic 研究系统即由 CitationAgent 确保所有主张正确归因（[How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system)）。
- **审批闸与迭代上限**：关键写入点设人工 checkpoint，并给自主 Agent 设最大迭代次数防止失控（[Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)）。

## 五、工具链接线：从零搭建的一份清单
把前六章能力串成可执行顺序，逐项闭合盲区：
1. **认知范式（B1）**→ 明确"多 Agent 同目录"的定位与边界；
2. **建 SSOT 说明书（B2）**→ 写 AGENTS.md/CLAUDE.md，@ 下沉细节，坑点入 ADR；
3. **并发隔离（B3 隔离）**→ 能用 worktree 就一 agent 一分支，否则按 agent/slug 分片子目录；
4. **模式选型（B4）**→ 线性任务走角色分工、高风险产出走红蓝对抗；
5. **安全监督（B5）**→ 写入点加审批闸、工具白名单限权、权限边界进 CI；
6. **成本可观测（B6）**→ CI 接 drift 守卫、成本埋点、迭代上限兜底；
7. **收口复盘**→ 定期 `decisions.md` 回顾，刷新"last verified"日期。

## 六、收口：痛点与盲区落点映射
| 前文问题 | 落点步骤 | 对应能力盲区 |
|---|---|---|
| 痛点① 说明书散落且过期 | SSOT 单点 + @import + docdrift ERROR 阻断 + ADR 入册 | B2 说明书治理 |
| 痛点② 记忆/日志互相覆盖 | worktree 优先 + working-notes/decisions 按 slug/agent 分片 | B3 并发隔离 |
| 痛点③ 协作模式未定 | 角色分工 / 红蓝对抗落点 + 审批闸 + 迭代上限 | B4 模式选型 |
| 盲区 B1–B6 | 上述七步清单逐章闭合，B5/B6 经 CI 与权限钩子集成 | B1–B6 全闭合 |

## 小结
多 Agent 同目录协作的成败不在模型强弱，而在"隔离、单源、守卫"三项工程纪律是否落地：目录上 worktree 优先、分片降级；说明书上 SSOT 单点、@ 桥接（≤4 跳）、坑点入册；集成上 drift 与权限双守卫。守住这三条，前六章能力才真正可用。

**给本用户的落地建议（一句级）**：作为有仓库损坏史的非程序员，优先用 worktree 把每个 Agent 锁在独立分支 + 关键写入设人工审批闸 + 要 Agent 用通俗语言汇报"它改了什么、风险几何"，切勿一上来就让多个 Agent 裸写同一目录。
