# 第1章 多 Agent 协作范式演进与主流框架图谱

## 一、论点（核心命题）

2024—2025 年，AI 编程的工程范式正在经历一条清晰的迁移链：**单 Agent 独立工作 → 多 Agent 编排协作 → 人机协作（Agent 只推信号/建议、人不下场代执行）**。这条链的本质，不是简单地堆叠更多 Agent，而是逐步沉淀出两类关键工程原语：其一是**结构化隔离**（让多个 Agent 互不踩踏），其二是**标准化指令源**（让所有 Agent 读取同一份可靠上下文）。然而必须指出：在当前阶段，"多个 Agent 同处一个本地目录协作迭代同一项目"仍是一个**新兴但尚未标准化**的场景——它与经典 CI/CD 中"多分支并行"有本质区别，也是本报告用户（一名非程序员 A 股量化交易员，正设计多 Agent 同本地目录协作、且曾发生仓库被改坏事故）最需要警惕的地带。Anthropic 的分类框架、主流框架与控制平台、以及 AGENTS.md 跨工具标准，共同构成了这条迁移脉络的"图谱"。

## 二、论据

### 2.1 范式演进脉络：从单 Agent 到人机协作

- **第一阶段·单 Agent 独立工作**：早期做法常是"一 Agent 一目录 + 冻结"，即用物理隔离避免冲突。范式简单、可预测，但吞吐受限于单一上下文窗口，难以处理需同时追查多个方向的大任务。
- **第二阶段·多 Agent 编排**：实践表明，让多个 Agent 指向同一仓库而不加隔离，会迅速退化为竞争条件（race condition）：多个 Agent 同时编辑同一文件、互相覆盖、测试在混合状态下失败 ([Swarming with worktree](https://blog.heliomedeiros.com/posts/2025-11-23-swarming-with-worktree))。Git worktree 由此成为事实上的隔离原语——它为每个 Agent 分配独立的目录与分支，但共享同一个 .git 对象库，使 Agent 在结构上"无法"互相踩踏 ([Git Worktrees：从多 Agent 到真正的多 Agent 开发](https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh))。
- **第三阶段·人机协作**：最新的实践强调，Agent 不应"下场代执行"，而是**只推送信号与建议**，由人（或受控的集成层）决定是否落地。Claude Code 的子 Agent 架构即通过钩子（hooks）在阶段交接处保留人在环（HITL）审批，主 Agent 委托而非亲历 ([Claude Code 子 Agent 最佳实践](https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/))。对缺乏编程背景的使用者而言，这一层尤为关键——它把"是否允许 Agent 改动仓库"的最终裁决权保留在人手中。

### 2.2 Anthropic 的 Workflow vs Agent 分类与五种可组合模式

Anthropic 将"智能体系统"明确分为两类：Workflow（由预定义代码路径编排，确定性高）与 Agent（由模型动态决策，自主性强）([Building effective agents](https://www.anthropic.com/engineering/building-effective-agents))。在 Workflow 一侧，它归纳出五种可组合模式：

1. **提示链（Prompt chaining）**：任务固定分解、逐步 gate，用延迟换准确度。
2. **路由（Routing）**：按类别将输入分流到专门后继。
3. **并行化（Parallelization）**：独立子任务并行，或多轮投票取多样输出。
4. **编排者-工作者（Orchestrator-workers）**：中央 LLM 动态拆解并委派，适合子任务无法预知的复杂场景。
5. **评估者-优化者（Evaluator-optimizer）**：生成与评估循环，适合有清晰评估标准、迭代可量化的任务。

Anthropic 同时给出明确边界：多数应用无需自主 Agent，**工作流优先于智能体**，仅当任务定义良好但需大规模灵活性时才选 Agent ([Building effective agents](https://www.anthropic.com/engineering/building-effective-agents))。这五种模式正是"多 Agent 同仓协作"的可复用设计积木。

### 2.3 框架图谱与协作原语对比

主流框架各代表一种"协作原语"心智模型，选择取决于任务拓扑：

| 框架 / 平台 | 协作原语 | 核心特征 | 最佳场景 |
|---|---|---|---|
| **AutoGen**（Microsoft） | 消息传递（对话） | Agent 以群聊会话协作，GroupChatManager 决定谁发言；内置沙箱代码执行 | 辩论、批判、头脑风暴、迭代润色 ([LangGraph vs AutoGen vs CrewAI](https://multiagentpro.ai/blog/langgraph-vs-autogen-vs-crewai)) |
| **CrewAI** | 角色分工（声明式） | 以 role/goal/backstory 定义 Agent，API 最直观、50 行内可原型 | 快速原型、内容流水线 ([LangGraph vs AutoGen vs CrewAI](https://multiagentpro.ai/blog/langgraph-vs-autogen-vs-crewai)) |
| **LangGraph** | 共享状态（有向图） | 节点为函数/LLM 调用，typed state 在图中流转；可观测性（LangSmith）最强，支持 human-in-the-loop 中断 | 复杂生产级、需精细控制与回滚 ([LangGraph vs AutoGen vs CrewAI](https://multiagentpro.ai/blog/langgraph-vs-autogen-vs-crewai)) |
| **Claude Code Agent / 子 Agent** | 子代理树（树状委托） | 主 Agent 派生独立上下文窗口的子 Agent，仅以摘要回传；独立工具权限 | 长任务分阶段、隔离噪音 ([Claude Code 子 Agent 最佳实践](https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/)) |
| **Cursor 2.0** | worktree + 并行代理 | 以 git worktrees 驱动并行 Agent（最多 8 个） | 同任务多代理比对输出 ([Git Worktrees](https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh)) |
| **OpenAI Codex** | worktree 原生支持 | 内置原生 worktree，端到端自主 | 端到端任务代理 ([Git Worktrees](https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh)) |

业界形成较一致的选型观：生产优先选 LangGraph（控制力/可观测性最高），研究/对话优先选 AutoGen（最契合对话/辩论），快速交付选 CrewAI（上手最快）([LangGraph vs AutoGen vs CrewAI](https://multiagentpro.ai/blog/langgraph-vs-autogen-vs-crewai))。

### 2.4 平台层协作原语：worktree 成为并发 Agent 的隔离底座

主流编码平台已将 worktree 内建为并发 Agent 原语：Claude Code 提供 `--worktree <name>` 一键隔离；Cursor 2.0（2025-10）以 git worktrees 驱动并行 Agent，最多 8 个；VS Code 1.107 后台代理启动即静默创建 worktree；OpenAI Codex 也已内置原生 worktree 支持 ([Git Worktrees](https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh))。worktree 的隔离机制在于：每个 Agent 拥有独立工作树、独立暂存区与独立分支 HEAD，但共享同一 .git 对象库，因此"架构上无法"互相覆盖文件，且合并冲突在合并时才暴露（而非编辑时）([Git Worktrees](https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh))。

### 2.5 AGENTS.md 与 AAIF：跨工具"单一指令源"标准

上下文散落且过期是多 Agent 协作的头号痛点。OpenAI 于 2025-08 发布 AGENTS.md——一种基于 Markdown 的通用标准，为编码 Agent 提供跨仓库、跨工具链一致的"项目级指令源"，已被 6 万+ 开源项目及 Codex、Cursor、Gemini CLI、VS Code 等框架采纳 ([Linux Foundation 成立 Agentic AI Foundation 公告](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation))。在 Claude 生态中，同类文件 CLAUDE.md 作为系统提示的一部分自动加载，消除"每次对话重述架构决策"的重复，并以版本化保持时效 ([Using CLAUDE.md files](https://www.claude.com/blog/using-claude-md-files))。2025-12，Linux 基金会成立 Agentic AI Foundation（AAIF），将 MCP、goose、AGENTS.md 一并捐入中立开源治理，白金成员涵盖 AWS、Anthropic、Google、Microsoft、OpenAI 等 ([Linux Foundation 公告](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation))。AGENTS.md 成为跨工具"单一指令源"标准，直接回应"说明书散落、过期"之痛。

### 2.6 关键数据：性能提升与成本信号

Anthropic 内部评测显示，以 Opus 4 为主、Sonnet 4 为子 Agent 的多 Agent 研究系统，较单 Agent Opus 4 在内部研究评测上提升 **90.2%**，且在"广度优先"查询优势显著 ([我们如何构建多 Agent 研究系统](https://www.anthropic.com/engineering/built-multi-agent-research-system))。但代价是成本：数据显示 Agent 通常比聊天多用约 4× token，而多 Agent 系统约为聊天的 **15×** ([我们如何构建多 Agent 研究系统](https://www.anthropic.com/engineering/built-multi-agent-research-system))。Anthropic 明确提示：多 Agent 系统仅在任务价值足够高、能覆盖额外性能成本时才具备经济可行性 ([我们如何构建多 Agent 研究系统](https://www.anthropic.com/engineering/built-multi-agent-research-system))。

### 2.7 关键辨析："多 Agent 同本地目录" ≠ 经典 CI/CD 多分支并行

需要重点澄清一个常见误解：多 Agent 同本地目录协作，**并不等同于**经典 CI/CD 中的"多分支并行"。在 CI/CD 中，每个分支由人类创建、由 PR 与 CI 门禁协调，合并是受控、可审查、有人负责的；而"多 Agent 同工作树"场景下，多个自主 Agent 以机器速度并行改写，冲突解决往往交由 Git 自动或模型决策，缺少稳定的人为门禁 ([Swarming with worktree](https://blog.heliomedeiros.com/posts/2025-11-23-swarming-with-worktree))。因此有研究者直言：无隔离的并行不是加速，而是熵增；只有"带边界的并行"才有意义 ([Swarming with worktree](https://blog.heliomedeiros.com/posts/2025-11-23-swarming-with-worktree))。对用户这类非程序员而言，这意味着：**worktree 隔离 + 人在环审批 + AGENTS.md 单一指令源**，是把"新兴、未标准化"风险压到可控区间的三件套。

## 三、分析（主流观点与反方/局限/代价）

**主流观点**：多 Agent 编排在广度和吞吐上显著优于单 Agent，worktree 与子代理树已让"同仓并行"在工程上可行，AGENTS.md 正把协作上下文标准化 ([我们如何构建多 Agent 研究系统](https://www.anthropic.com/engineering/built-multi-agent-research-system)；[Linux Foundation 公告](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation))。

**反方与局限**（须同等重视）：
1. **成本信号**：15× token 用量意味着多 Agent 不是免费午餐，仅在任务价值高时划算 ([我们如何构建多 Agent 研究系统](https://www.anthropic.com/engineering/built-multi-agent-research-system))。
2. **错误复利**：长链路中微小失败会被灾难化放大，需断点恢复与确定性检查点 ([我们如何构建多 Agent 研究系统](https://www.anthropic.com/engineering/built-multi-agent-research-system))。
3. **非标准化风险**：同本地目录的自主并发仍缺统一治理，分支级自动合并可能掩盖语义冲突 ([Swarming with worktree](https://blog.heliomedeiros.com/posts/2025-11-23-swarming-with-worktree))。
4. **框架取舍**：LangGraph 控制力强但学习曲线陡；AutoGen 对话自然但结构化 DAG 下顺序难预测；CrewAI 快但规模化不稳 ([LangGraph vs AutoGen vs CrewAI](https://multiagentpro.ai/blog/langgraph-vs-autogen-vs-crewai))。
5. **Anthropic 自身的边界提醒**：多数应用其实不需要自主 Agent，简单工作流即可 ([Building effective agents](https://www.anthropic.com/engineering/building-effective-agents))。

## 四、小结

多 Agent 同仓/同目录协作已成为 2024—2025 年的主流方向，但其落地依赖三个支柱：**worktree 式物理隔离、AGENTS.md 式统一指令源、以及按场景选型的框架与模式**；并在最高阶段回归"人机协作"——Agent 推信号、人控落地。需谨记：范式演进带来 90.2% 的性能红利，也带来 15× 的成本与错误复利风险；对曾经历仓库被改坏的使用者，**"隔离优先于并发、人在环优先于全自动"**应作为不可逾越的底线。下一章将聚焦这些原语如何解决"记忆/日志互相覆盖"与"角色分工 vs 红蓝对抗"两大具体痛点。

---

## 来源清单（本章引用，均来自已收集来源池，共 8 条，覆盖 3 种类型）

1. [我们如何构建多 Agent 研究系统](https://www.anthropic.com/engineering/built-multi-agent-research-system) — 官方博客
2. [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) — 官方博客
3. [Using CLAUDE.md files](https://www.claude.com/blog/using-claude-md-files) — 官方博客
4. [LangGraph vs AutoGen vs CrewAI](https://multiagentpro.ai/blog/langgraph-vs-autogen-vs-crewai) — 实践博客
5. [Claude Code 子 Agent 最佳实践](https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/) — 实践博客
6. [Swarming with worktree](https://blog.heliomedeiros.com/posts/2025-11-23-swarming-with-worktree) — 实践博客
7. [Git Worktrees：从多 Agent 到真正的多 Agent 开发](https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh) — 实践博客
8. [Linux Foundation 成立 Agentic AI Foundation 公告](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation) — 基金会公告
