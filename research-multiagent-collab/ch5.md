# 第5章：安全·权限·非专家监督——攻击面控制、hooks 护栏与可读看板

【论点】多 Agent 同目录协作，会把"单点失误"放大成"全局灾难"。当 N 个拥有文件读写、命令执行权限的智能体共享同一工作目录时，攻击面（attack surface，即可被误操作或恶意利用的入口总和）约等于单 Agent 的 N 倍——任一危险操作（删除、覆盖、外泄）都会被所有 Agent 共同承担。对本案用户——一位曾因仓库被改坏而高度敏感的交易员——而言，权限隔离与"非专家也能看懂、能喊停"的监督机制，不是锦上添花，而是"不出事"的底线。本章对应盲区 B4（安全/权限）与 B6（非专家监督）。

### 一、同目录攻击面放大：N 个带权 Agent = N 倍攻击面

事实表明，把多个 Claude Code 实例指向同一仓库会立刻变成一场"竞态条件（race condition，多进程争抢同一资源导致结果不可预测）"：作者在三个终端并行启动 Agent 后，"两分钟内两个 Agent 编辑了同一文件，一个覆盖了另一个的改动，第三个因工作目录处于意外状态而测试失败"（[Swarming the Codebase](https://blog.heliomedeiros.com/posts/2025-11-23-swarming-with-worktree)）。另一篇实战文章更直接：共享单目录时"文件和暂存区、分支都混在一起，你无法只提交一个智能体的工作而不不小心带上另一个的"（[Git Worktrees: From Running Multiple Agents to Real Multi-Agent Development](https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh)）。这正是用户"记忆/日志互相覆盖"痛点的工程化表述——攻击面随 Agent 数量线性放大，且任一 Agent 的越权或失误都会祸及全局。

### 二、最小权限：省略 tools 字段即为"隐式全授权"陷阱

权限最小化（least privilege，即每个主体只获得完成任务所必需的最小权限）是护栏的第一道闸门。PubNub 的工程实践指出，配置子 Agent 时若省略 `tools` 字段，等于"隐式授予对所有可用工具的访问权，包括 MCP 外部工具"（[Best practices for Claude Code subagents](https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/)）。因此应"刻意地"为每个 Agent 白名单化工具：规划/架构类只读搜索，实现类才给 Edit/Write/Bash。Anthropic 的官方工程建议同样强调，应在沙箱环境广泛测试 Agent 并设最大迭代次数等终止条件（[Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)）；其多 Agent 研究系统则通过"独立上下文窗口 + 显式护栏"防止 Agent 螺旋失控（[How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system)）。

### 三、hooks 护栏即代码：PreToolUse 阻断 + SubagentStop/Stop 审计

钩子（hooks，即挂载在 Agent 生命周期事件上的脚本）是把护栏"代码化"的关键。最前置的防线是 `PreToolUse` 钩子（每次工具执行前触发，返回退出码 2 即阻断）：社区方案可拦截 `git push --force`、`git reset --hard`、`git clean -f` 等高危命令（[Git Worktrees...](https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh)）。事后审计则靠 `SubagentStop` 与 `Stop`——PubNub 建议同时注册这两个在子 Agent 停止时触发的事件，"可靠地捕获任何运行的结束"，并在 `.claude/hooks/` 下像生产代码一样做版本管理、用 `jq` 校验、保持幂等（[Best practices for Claude Code subagents](https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/)）。这些钩子同时写入 `hooks.log`，构成可审计管线，使每一次危险动作都可追溯、可复盘。

### 四、行业标准收敛：AGENTS.md 与 Agentic AI Foundation

行业正在把"用一份可读说明约束 Agent 行为"变成共识。2025 年 12 月，OpenAI 联合 Anthropic、Block 在 Linux 基金会下共同发起 Agentic AI Foundation（AAIF；三位发起方各捐出核心项目——OpenAI 捐 AGENTS.md、Anthropic 捐 MCP、Block 捐 goose），并得到 Google、Microsoft、AWS、Bloomberg、Cloudflare 的支持（[OpenAI co-founds the Agentic AI Foundation](https://openai.com/index/agentic-ai-foundation)）。事实显示，AGENTS.md 自 2025 年 8 月发布后，至 AAIF 成立时（2025 年 12 月）已被 6 万多个开源项目采用。笔者认为，这一中性标准对用户同样有价值：一份写在仓库根目录、非程序员也能读懂的 AGENTS.md，本身就是最低成本的"权限与边界说明书"。

### 五、非专家监督盲区（B6）：审批闸、可读看板与通俗化报告

笔者认为，现有多 Agent 治理（AGENTS.md / worktree / hooks / CI）几乎全是程序员视角，对本案用户这样的非程序员是真实盲区——他无法在终端里"看 diff、拦命令"。好消息是，面向"不出命令也能监督"的工具已经出现：Rerun 这类无代码平台提供实时仪表板（每步干了什么、调了哪个 API、花了多少 token），并在敏感操作（如付款、发邮件）前暂停等待人工批准（[Rerun](https://aipure.ai/cn/products/rerun-2)）；OpenClaw Mission Control 支持用自然语言建仪表板，并对发邮件/改文件等敏感动作强制人工审批节点（[OpenClaw Mission Control](https://www.houdao.com/d/5314-OpenClaw-Mission-Control-kai-yuan-gao-bie-AI-hei-xiang-shi-xian-ke-shi-hua-bian-pai-yu-kong-zhi)）；LangGraph Approval Hub 则让"非技术审核人（CFO、法务、运营）直接在界面上批准，无需写代码"（[Human-in-the-loop approval dashboard](https://forum.langchain.com/t/human-in-the-loop-approval-dashboard-for-langgraph-agents-open-source-free-to-deploy/3616/1)）。有观点认为"自动化越深越好"，但笔者认为对高风险目录，事前审批优于事后回滚——据 jxxy.net 对 Rerun 的转述，其创始人 Clément 在 Product Hunt 评论区被追问"失败时怎么办"时即持此看法："与其事后回滚，不如事前审批"（[jxxy.net 转述](https://www.jxxy.net/ai/articles/rerun-no-code-ai-agents)）。

【分析】

把用户的真实事故代入：仓库被改坏，本质就是"无隔离 + 无审批闸"下多 Agent（或多轮操作）同目录协作的典型后果。若坚持"多 Agent 同目录"新法却不设权限隔离与审批闸，重演概率极高——这不是危言耸听，而是 Heliomedeiros 与 dev.to 两篇实战复盘共同证明的必然。

更深一层，B6 之所以危险，是因为用户不具备在命令行里"看 diff、拦命令"的能力。因此监督手段必须满足三条：①审批闸（approval gate，关键动作如 `git push`、删除、外发前必须由人确认）；②可读看板（用"谁在做什么、卡在哪、要不要我拍板"取代术语轰炸）；③通俗化报告（术语首次出现即注释，呼应全局硬约束）。这三条都不要求用户写代码，却能让他作为交易员真正"自己掌控"协作进程。

【小结】

多 Agent 同目录的"不出事"底线，是三层叠加：第一层隔离（worktree/独立分支，从结构上杜绝互相覆盖）；第二层最小权限 + hooks 审计（危险动作前置拦截、全程留痕）；第三层非专家可操作的审批闸与可读看板。前两层是工程护栏，第三层是用户唯一能独立掌控的部分——也是本报告最该补、却最易被通用教程忽略的一块。

---

### 关键发现
- 发现1：同目录多 Agent 会立即产生文件覆盖与竞态冲突，攻击面随 Agent 数近似线性放大，已有两篇实战复盘证实（[Heliomedeiros](https://blog.heliomedeiros.com/posts/2025-11-23-swarming-with-worktree)、[dev.to](https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh)）。
- 发现2：配置子 Agent 时省略 `tools` 字段等于隐式授予全部工具权限，必须显式白名单（[PubNub](https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/)）。
- 发现3：`PreToolUse` 钩子退出码 2 可阻断 force push/reset/clean，`SubagentStop`/`Stop` 钩子可把护栏"代码化"并生成可审计日志（[PubNub](https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/)、[dev.to](https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh)）。
- 发现4：面向非技术用户的"审批闸 + 可读看板"工具已存在（Rerun / OpenClaw / LangGraph Approval Hub），证明 B6 可被工程化解决（[Rerun](https://aipure.ai/cn/products/rerun-2)、[OpenClaw](https://www.houdao.com/d/5314-OpenClaw-Mission-Control-kai-yuan-gao-bie-AI-hei-xiang-shi-xian-ke-shi-hua-bian-pai-yu-kong-zhi)、[LangChain Forum](https://forum.langchain.com/t/human-in-the-loop-approval-dashboard-for-langgraph-agents-open-source-free-to-deploy/3616/1)）。
- 发现5：AAIF 由 OpenAI、Anthropic、Block 共同发起（Block 捐 goose），Google/Microsoft 等为支持方；AGENTS.md 自 2025-08 发布后至 2025-12 已被 6 万+ 项目采用，说明"一份可读说明约束 Agent"是共识（[OpenAI AAIF](https://openai.com/index/agentic-ai-foundation)）。

### 数据摘要
| 指标 | 数据 | 来源 |
|------|------|------|
| 多 Agent 同目录冲突发生时间 | 2 分钟内出现文件互相覆盖 | [Heliomedeiros](https://blog.heliomedeiros.com/posts/2025-11-23-swarming-with-worktree) |
| 子 Agent 配置风险 | 省略 `tools` = 隐式全权限 | [PubNub](https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/) |
| 高危命令拦截 | `PreToolUse` 退出码 2 阻断 force push/reset/clean | [dev.to](https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh) |
| AGENTS.md 采用项目数 | 60,000+ 开源项目（2025-08 发布，至 2025-12） | [OpenAI AAIF](https://openai.com/index/agentic-ai-foundation) |
| 非技术审批支持 | CFO/法务/运营可直接界面审批，无需代码 | [LangChain Forum](https://forum.langchain.com/t/human-in-the-loop-approval-dashboard-for-langgraph-agents-open-source-free-to-deploy/3616/1) |

---

## 本章新增来源（供主理人更新来源池，APA 格式）
1. Rerun. (2026). *Rerun: No-code platform to build, run and monitor AI agents* [Product page]. Retrieved from https://aipure.ai/cn/products/rerun-2 — 无代码、实时仪表板（每步动作/API/token）、敏感操作前暂停等审批（仅用于平台功能描述，非引语出处）。
2. OpenClaw. (2025). *OpenClaw Mission Control: From black box to command center* [Product/community page]. Retrieved from https://www.houdao.com/d/5314-OpenClaw-Mission-Control-kai-yuan-gao-bie-AI-hei-xiang-shi-xian-ke-shi-hua-bian-pai-yu-kong-zhi — 自然语言建仪表板、对发邮件/改文件强制人工审批节点。
3. surya_02. (2025, May 8). *Human-in-the-loop approval dashboard for LangGraph agents* [Forum post]. LangChain Forum. Retrieved from https://forum.langchain.com/t/human-in-the-loop-approval-dashboard-for-langgraph-agents-open-source-free-to-deploy/3616/1 — 非技术审核人（CFO/法务/运营）可直接界面审批，无需代码；含审计日志。
4. jxxy.net. (2025). *不写代码搭一个 24 小时干活的 AI 助手：每一步都能看见、能喊停* [Tech media article]. Retrieved from https://www.jxxy.net/ai/articles/rerun-no-code-ai-agents — 转述 Rerun 创始人 Clément 在 Product Hunt 评论区"与其事后回滚，不如事前审批"原话（本稿 Rerun 引语的唯一出处）。

（说明：本章共引用 10 个独立来源、4 种类型——官方工程博客 #1/#2、行业技术博客 #9/#10/#11、行业组织公告 #12、产品/社区页面与论坛 Rerun/OpenClaw/LangChain/jxxy.net，满足 ≥9 源、≥4 类要求；事实性陈述均带超链接，并显式区分"事实"与"观点"。Rerun 创始人引语已按要求单独加注真实出处并补全全名 Clément，不再链至 aipure 产品页。）
