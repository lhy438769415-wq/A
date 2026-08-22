# 多 AI 同目录协作范式最佳实践研究报告

## 摘要

本报告源于一位真实用户场景：一名 A 股量化交易员（非程序员）正设计"多个 AI Agent 同本地目录协作、迭代同一工程"的工作流，却已发生过仓库被 Agent 改坏的事故。他的困境并非孤例，而是当前"多 Agent 同目录协作"这一新兴、尚未标准化场景下最典型的工程风险集合。

围绕用户的实际处境，本研究系统梳理了其明确提出的**三大痛点**：①说明书/上下文散落于多处（AGENTS.md、USER.md、SOUL.md、STATUS.md、CHANGELOG、`docs/` 等）且长期过期；②多个 Agent 同目录时，各自的推断与日志互相覆盖、碎片化；③协作模式悬而未决——究竟该采用角色分工（planner/executor/reviewer）还是红蓝对抗（proposer/critic）。

在双盲审稿过程中，本研究进一步识别出**六个超出用户原始痛点的结构性盲区**：B1 成本（多 Agent 系统 token 消耗约为聊天的 15×）；B2 可观测性（每次改动是否可回放、错误能否归因）；B3 错误传播与共享上下文污染（一个 Agent 的错误被其他 Agent 链式放大）；B4 安全与权限边界（N 个带权 Agent ≈ N 倍攻击面）；B5 收敛与终止、责任归属（迭代何时停、出事后谁负责）；B6 非专家监督缺口（无编程背景者如何"看 diff、拦命令"）。

研究方法上，报告由 7 章深度调研构成，覆盖范式演进、上下文治理、并发隔离、模式选型、安全监督、成本可观测与端到端落地，并经双盲审稿校验；全文事实性主张均附带可点击的真实来源超链接，主张与观点明确区隔，不迎合用户、主动补全其盲区。

**核心结论**：多 Agent 同目录协作的成败不在模型强弱，而在"结构性隔离 + 单一事实来源（SSOT）+ 护栏与守卫"三项工程纪律是否落地；对曾经历仓库损坏的非程序员，应优先采用 worktree 锁独立分支、对关键写入设人工审批闸、并要求 Agent 以通俗语言汇报改动与风险，切勿一开始就让多个 Agent 裸写同一目录。

## 目录

- [摘要](#摘要)
- [引言](#引言)
- [第1章 多 Agent 协作范式演进与主流框架图谱](#第1章-多-agent-协作范式演进与主流框架图谱)
- [第2章 以 AGENTS.md 为 SSOT 的上下文与说明书治理](#第2章-以-agentsmd-为-ssot-的上下文与说明书治理)
- [第3章 多 Agent 同目录的并发隔离与记忆/日志归属策略](#第3章-多-agent-同目录的并发隔离与记忆日志归属策略)
- [第4章 协作模式选型：角色分工与红蓝对抗的权衡及裁决机制](#第4章-协作模式选型角色分工与红蓝对抗的权衡及裁决机制)
- [第5章 安全·权限·非专家监督](#第5章-安全权限非专家监督)
- [第6章 成本·可观测性·收敛与责任](#第6章-成本可观测性收敛与责任)
- [第7章 多 Agent 协作端到端落地实践](#第7章-多-agent-协作端到端落地实践)
- [结论](#结论)
- [参考文献](#参考文献)
- [免责声明](#免责声明)

## 引言

**研究背景。** 2024—2025 年，AI 编程的工程范式正从"单 Agent 独立工作"快速迁移到"多 Agent 编排协作"，并进一步回归"人机协作"——Agent 只推送信号与建议，由人在环（HITL）裁决是否落地。在这场迁移中，出现了一类新兴但尚未标准化的场景：让多个 AI Agent 共享同一个本地项目目录，协作迭代同一份工程。它区别于经典 CI/CD 的"多分支并行"（后者由人类创建分支、由 PR 与 CI 门禁协调合并），因为自主 Agent 以机器速度并行改写，冲突解决常交由 Git 自动或模型决策，缺少稳定的人为门禁。本报告的用户正身处这一场景：一名 A 股量化交易员，非程序员，正设计"多 Agent 同目录协作"工作流，却已发生过仓库被 Agent 改坏的事故。

**用户明确提出的三大痛点。** ①说明书/上下文散落于多处（AGENTS.md、USER.md、SOUL.md、STATUS.md、CHANGELOG、`.workbuddy/memory/`、`docs/` 等）且长期过期——STATUS.md 停在 18 天前、CHANGELOG 停在 80 天前，甚至把项目路径写成已冻结的备份目录；②多个 Agent 同目录时，各自的推断与日志"往哪写"互相覆盖或碎片化；③协作模式悬而未决——究竟该采用角色分工（planner/executor/reviewer）还是红蓝对抗（proposer/critic）。

**六个超出用户原始痛点的结构性盲区。** 在双盲审稿中，本研究进一步识别出用户未直接提及、却决定方案成败的六类盲区：B1 成本（多 Agent 系统 token 消耗约为聊天的 15×，预算是硬天花板）；B2 可观测性（每次改动是否可回放、错误能否归因）；B3 错误传播与共享上下文污染（一个 Agent 的错误假设被其他 Agent 链式放大）；B4 安全与权限边界（N 个带权 Agent ≈ N 倍攻击面，省略工具白名单即隐式全授权）；B5 收敛与终止、责任归属（辩论/迭代何时停、出事后谁负责）；B6 非专家监督缺口（无编程背景者无法在终端"看 diff、拦命令"）。

**研究边界与时效。** 本报告资料以 2025—2026 年公开来源为主，覆盖官方工程博客、学术论文与预印本、行业技术博客及产品/社区页面；因该场景高度新兴，部分结论带有"量级参考"性质而非普适定律，读者应结合最新官方文档核验。

**客观性原则。** 本研究不迎合用户偏好，主动补全其盲区，并坚持"主张与观点区隔"：凡事实性陈述均附可点击超链接来源，凡属建议性框架（如模式选型次序、文件长度共识）均明确标注为观点或经验值，不以确定性口吻呈现分歧性证据（例如 AGENTS.md 在"效率提升"与"正确性无普遍收益"两类实证间存在明显分歧，本报告并陈而非择一）。

## 第1章 多 Agent 协作范式演进与主流框架图谱

### 一、论点（核心命题）

2024—2025 年，AI 编程的工程范式正在经历一条清晰的迁移链：**单 Agent 独立工作 → 多 Agent 编排协作 → 人机协作（Agent 只推信号/建议、人不下场代执行）**。这条链的本质，不是简单地堆叠更多 Agent，而是逐步沉淀出两类关键工程原语：其一是**结构化隔离**（让多个 Agent 互不踩踏），其二是**标准化指令源**（让所有 Agent 读取同一份可靠上下文）。然而必须指出：在当前阶段，"多个 Agent 同处一个本地目录协作迭代同一项目"仍是一个**新兴但尚未标准化**的场景——它与经典 CI/CD 中"多分支并行"有本质区别，也是本报告用户（一名非程序员 A 股量化交易员，正设计多 Agent 同本地目录协作、且曾发生仓库被改坏事故）最需要警惕的地带。Anthropic 的分类框架、主流框架与控制平台、以及 AGENTS.md 跨工具标准，共同构成了这条迁移脉络的"图谱"。

### 二、论据

#### 2.1 范式演进脉络：从单 Agent 到人机协作

- **第一阶段·单 Agent 独立工作**：早期做法常是"一 Agent 一目录 + 冻结"，即用物理隔离避免冲突。范式简单、可预测，但吞吐受限于单一上下文窗口，难以处理需同时追查多个方向的大任务。
- **第二阶段·多 Agent 编排**：实践表明，让多个 Agent 指向同一仓库而不加隔离，会迅速退化为竞争条件（race condition）：多个 Agent 同时编辑同一文件、互相覆盖、测试在混合状态下失败 ([Swarming with worktree](https://blog.heliomedeiros.com/posts/2025-11-23-swarming-with-worktree))。Git worktree 由此成为事实上的隔离原语——它为每个 Agent 分配独立的目录与分支，但共享同一个 .git 对象库，使 Agent 在结构上"无法"互相踩踏 ([Git Worktrees：从多 Agent 到真正的多 Agent 开发](https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh))。
- **第三阶段·人机协作**：最新的实践强调，Agent 不应"下场代执行"，而是**只推送信号与建议**，由人（或受控的集成层）决定是否落地。Claude Code 的子 Agent 架构即通过钩子（hooks）在阶段交接处保留人在环（HITL）审批，主 Agent 委托而非亲历 ([Claude Code 子 Agent 最佳实践](https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/))。对缺乏编程背景的使用者而言，这一层尤为关键——它把"是否允许 Agent 改动仓库"的最终裁决权保留在人手中。

#### 2.2 Anthropic 的 Workflow vs Agent 分类与五种可组合模式

Anthropic 将"智能体系统"明确分为两类：Workflow（由预定义代码路径编排，确定性高）与 Agent（由模型动态决策，自主性强）([Building effective agents](https://www.anthropic.com/engineering/building-effective-agents))。在 Workflow 一侧，它归纳出五种可组合模式：

1. **提示链（Prompt chaining）**：任务固定分解、逐步 gate，用延迟换准确度。
2. **路由（Routing）**：按类别将输入分流到专门后继。
3. **并行化（Parallelization）**：独立子任务并行，或多轮投票取多样输出。
4. **编排者-工作者（Orchestrator-workers）**：中央 LLM 动态拆解并委派，适合子任务无法预知的复杂场景。
5. **评估者-优化者（Evaluator-optimizer）**：生成与评估循环，适合有清晰评估标准、迭代可量化的任务。

Anthropic 同时给出明确边界：多数应用无需自主 Agent，**工作流优先于智能体**，仅当任务定义良好但需大规模灵活性时才选 Agent ([Building effective agents](https://www.anthropic.com/engineering/building-effective-agents))。这五种模式正是"多 Agent 同仓协作"的可复用设计积木。

#### 2.3 框架图谱与协作原语对比

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

#### 2.4 平台层协作原语：worktree 成为并发 Agent 的隔离底座

主流编码平台已将 worktree 内建为并发 Agent 原语：Claude Code 提供 `--worktree <name>` 一键隔离；Cursor 2.0（2025-10）以 git worktrees 驱动并行 Agent，最多 8 个；VS Code 1.107 后台代理启动即静默创建 worktree；OpenAI Codex 也已内置原生 worktree 支持 ([Git Worktrees](https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh))。worktree 的隔离机制在于：每个 Agent 拥有独立工作树、独立暂存区与独立分支 HEAD，但共享同一 .git 对象库，因此"架构上无法"互相覆盖文件，且合并冲突在合并时才暴露（而非编辑时）([Git Worktrees](https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh))。

#### 2.5 AGENTS.md 与 AAIF：跨工具"单一指令源"标准

上下文散落且过期是多 Agent 协作的头号痛点。OpenAI 于 2025-08 发布 AGENTS.md——一种基于 Markdown 的通用标准，为编码 Agent 提供跨仓库、跨工具链一致的"项目级指令源"，已被 6 万+ 开源项目及 Codex、Cursor、Gemini CLI、VS Code 等框架采纳 ([Linux Foundation 成立 Agentic AI Foundation 公告](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation))。在 Claude 生态中，同类文件 CLAUDE.md 作为系统提示的一部分自动加载，消除"每次对话重述架构决策"的重复，并以版本化保持时效 ([Using CLAUDE.md files](https://www.claude.com/blog/using-claude-md-files))。2025-12，Linux 基金会成立 Agentic AI Foundation（AAIF），将 MCP、goose、AGENTS.md 一并捐入中立开源治理，白金成员涵盖 AWS、Anthropic、Google、Microsoft、OpenAI 等 ([Linux Foundation 公告](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation))。AGENTS.md 成为跨工具"单一指令源"标准，直接回应"说明书散落、过期"之痛。

#### 2.6 关键数据：性能提升与成本信号

Anthropic 内部评测显示，以 Opus 4 为主、Sonnet 4 为子 Agent 的多 Agent 研究系统，较单 Agent Opus 4 在内部研究评测上提升 **90.2%**，且在"广度优先"查询优势显著 ([我们如何构建多 Agent 研究系统](https://www.anthropic.com/engineering/built-multi-agent-research-system))。但代价是成本：数据显示 Agent 通常比聊天多用约 4× token，而多 Agent 系统约为聊天的 **15×** ([我们如何构建多 Agent 研究系统](https://www.anthropic.com/engineering/built-multi-agent-research-system))。Anthropic 明确提示：多 Agent 系统仅在任务价值足够高、能覆盖额外性能成本时才具备经济可行性 ([我们如何构建多 Agent 研究系统](https://www.anthropic.com/engineering/built-multi-agent-research-system))。

#### 2.7 关键辨析："多 Agent 同本地目录" ≠ 经典 CI/CD 多分支并行

需要重点澄清一个常见误解：多 Agent 同本地目录协作，**并不等同于**经典 CI/CD 中的"多分支并行"。在 CI/CD 中，每个分支由人类创建、由 PR 与 CI 门禁协调，合并是受控、可审查、有人负责的；而"多 Agent 同工作树"场景下，多个自主 Agent 以机器速度并行改写，冲突解决往往交由 Git 自动或模型决策，缺少稳定的人为门禁 ([Swarming with worktree](https://blog.heliomedeiros.com/posts/2025-11-23-swarming-with-worktree))。因此有研究者直言：无隔离的并行不是加速，而是熵增；只有"带边界的并行"才有意义 ([Swarming with worktree](https://blog.heliomedeiros.com/posts/2025-11-23-swarming-with-worktree))。对用户这类非程序员而言，这意味着：**worktree 隔离 + 人在环审批 + AGENTS.md 单一指令源**，是把"新兴、未标准化"风险压到可控区间的三件套。

### 三、分析（主流观点与反方/局限/代价）

**主流观点**：多 Agent 编排在广度和吞吐上显著优于单 Agent，worktree 与子代理树已让"同仓并行"在工程上可行，AGENTS.md 正把协作上下文标准化 ([我们如何构建多 Agent 研究系统](https://www.anthropic.com/engineering/built-multi-agent-research-system)；[Linux Foundation 公告](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation))。

**反方与局限**（须同等重视）：
1. **成本信号**：15× token 用量意味着多 Agent 不是免费午餐，仅在任务价值高时划算 ([我们如何构建多 Agent 研究系统](https://www.anthropic.com/engineering/built-multi-agent-research-system))。
2. **错误复利**：长链路中微小失败会被灾难化放大，需断点恢复与确定性检查点 ([我们如何构建多 Agent 研究系统](https://www.anthropic.com/engineering/built-multi-agent-research-system))。
3. **非标准化风险**：同本地目录的自主并发仍缺统一治理，分支级自动合并可能掩盖语义冲突 ([Swarming with worktree](https://blog.heliomedeiros.com/posts/2025-11-23-swarming-with-worktree))。
4. **框架取舍**：LangGraph 控制力强但学习曲线陡；AutoGen 对话自然但结构化 DAG 下顺序难预测；CrewAI 快但规模化不稳 ([LangGraph vs AutoGen vs CrewAI](https://multiagentpro.ai/blog/langgraph-vs-autogen-vs-crewai))。
5. **Anthropic 自身的边界提醒**：多数应用其实不需要自主 Agent，简单工作流即可 ([Building effective agents](https://www.anthropic.com/engineering/building-effective-agents))。

### 四、小结

多 Agent 同仓/同目录协作已成为 2024—2025 年的主流方向，但其落地依赖三个支柱：**worktree 式物理隔离、AGENTS.md 式统一指令源、以及按场景选型的框架与模式**；并在最高阶段回归"人机协作"——Agent 推信号、人控落地。需谨记：范式演进带来 90.2% 的性能红利，也带来 15× 的成本与错误复利风险；对曾经历仓库被改坏的使用者，**"隔离优先于并发、人在环优先于全自动"**应作为不可逾越的底线。下一章将聚焦这些原语如何解决"记忆/日志互相覆盖"与"角色分工 vs 红蓝对抗"两大具体痛点。

## 第2章 以 AGENTS.md 为 SSOT 的上下文与说明书治理（含 drift 检测）

> 对应痛点1：说明书 / 上下文散落多处且过期。
> 一句话主张：多 agent 同目录治理的第一性原理，是让同一事实只在**一处**被定义（SSOT），别处只引用不手抄；并用 CI / pre-commit 把"文档新鲜度"变成一等公民——因为过期文档比没有文档更糟，agent 会照着它执行。

### 2.1 论点：过期说明书不是"不整洁"，而是给 agent 灌错输入

对人类而言，一份三个月没动的文档只是不便——人会本能地怀疑它、去核对代码。对 AI agent 而言，过期文档是**被信任的错误输入**：它没有"这文档看着可疑"的直觉，会照着执行、照着生成代码。这正是"过期文档比没有文档更糟"的机制性原因——没有文档时 agent 会去读代码探索（慢但正确），有错文档时 agent 会跳过探索直接采信（快但错）。

学术界已把这个现象命名为 **context rot（上下文腐化）**。Treude 与 Baltes（2026）把一个原本用于 README/wiki 一致性检查的工具（DOCER）不加修改地套用到 AI 配置文件上，在 356 个统计代表性仓库样本中发现 **23.0% 的仓库存在失效的代码元素引用**——即 CLAUDE.md / AGENTS.md / .cursorrules 里写的函数、路径、模块在代码里已经不存在了（[Context Rot in AI-Assisted Software Development](https://arxiv.org/html/2606.09090)）。该文同时指出，context rot 远不止"引用失效"一类，还包括架构声明失效、工具使用指引失效、依赖与运行时假设失效、行为约定失效。**在多 agent 同目录场景下，这一风险被结构性放大**：N 个 agent 同时读同一份说明书，一处错误被 N 倍执行，而 agent 之间主要靠文档而非对话交接——这正是用户"仓库被改坏"事故的根因类型之一。

### 2.2 论据一：SSOT 的工程载体是 AGENTS.md，跨工具统一、CLAUDE.md 用 @ 引用桥接

**事实层面。** AGENTS.md 已被超过 6 万个非 fork 开源仓库采用，被 Codex、Cursor、Windsurf、Gemini CLI、Jules、Copilot coding agent、Zed、Aider、Amp、Factory、goose、VS Code 等二十余款工具原生读取；单仓库内支持嵌套，**agent 自动读取目录树中最近的那份，最近者优先**（[AGENTS.md 官方站](https://agents.md/)）。2025 年 12 月，该格式与 Anthropic 的 MCP、Block 的 goose 一并作为三个锚定项目捐赠给 Linux Foundation 旗下新成立的 Agentic AI Foundation（[Linux Foundation 公告](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation)；[OpenAI 公告](https://openai.com/index/agentic-ai-foundation)）。需要精确限定的是：它目前是**受基金会托管的开放格式，尚无经批准的版本化规范**，"标准"一词更多是方向性描述（[What Is AGENTS.md?](http://llms-txt.io/blog/what-is-agents-md)）。

**桥接事实（关键）。** Claude Code 只读 CLAUDE.md、不读 AGENTS.md。官方推荐做法是在 CLAUDE.md 顶部写一行 `@AGENTS.md` 导入，Claude 专属内容写在其下方；符号链接 `ln -s AGENTS.md CLAUDE.md` 亦可，但在 Windows 需管理员/开发者模式、可能静默失败——有实测对比确认 `@import` 是跨平台更稳的路径（[三工具实测对照](https://techsy.io/en/blog/cursor-rules-vs-claude-md)）。由此可推出一条硬规则：**其他工具的入口文件只做薄壳（一行引用），绝不复制规则正文——复制即埋下 drift**。中文社区的最佳实践也明确："源 + 薄壳"三步法，各工具入口文件对照，但**绝不复制规则文本**（[如何写好 AGENTS.md](https://magicliang.github.io/2026/05/03/%E5%A6%82%E4%BD%95%E5%86%99%E5%A5%BD-agents-md/)）。

**决策沉淀用 ADR。** `decisions.md` / `docs/adr/` 用来沉淀关键决策与踩过的坑。ADR 的成熟做法是：一决策一文件，含 Status / Context / Decision / Consequences；**团队接受后的 ADR 即不可变**，需改变时新写一份并把旧的标记为 Superseded、保留在决策日志中（[AWS Prescriptive Guidance: ADR best practices](https://docs.aws.amazon.com/prescriptive-guidance/latest/architectural-decision-records/best-practices.html)）。其最常见失败模式是"写了但没人读"，根因是把 ADR 放在与代码分离的 wiki 里；放进代码仓库（通常 `docs/adr/`）能显著提高被检索引用的概率（[Architecture Decision Records: How Top Teams Document Decisions](https://sensussoft.com/blog/software-architecture-decision-records)）。对多 agent 场景尤其致命：agent 只会读它能在工作目录里找到的东西，所以 ADR 必须**在仓库内、被 AGENTS.md 显式导航到**，且把坑一并写入，否则不同 agent 会各自重复踩同一个坑。

**实证校准（务必并陈，避免一味鼓吹）。** "写了是否更好"在实证上并不一致：Lulla 等（2026, ICSE 2026 JAWs）在 10 仓库/124 PR 配对实验中测得 AGENTS.md 使**中位运行时间 −28.64%、输出 token −16.58%**（[On the Impact of AGENTS.md](https://arxiv.org/abs/2601.20404)）；但 Gloaguen 等（ETH Zurich & LogicStar.ai, 2026）在 4 款 agent、438 个任务上发现上下文文件**未普遍提升任务成功率、推理成本平均 +20% 以上**，并给出关键细节——**指令类内容被 agent 可靠遵守，"仓库概览"这类被厂商推荐的内容并无帮助**（[Evaluating AGENTS.md](https://arxiv.org/abs/2602.11988)）。第三方跨厂商消融研究给出有界零结论（正确性效应上界 ≤10pp/≤15pp）（[Two-Agent Ablation](https://arxiv.org/html/2607.27250v1)）。这组分歧不否定 SSOT，反而精确界定了它该装什么：**装"删掉就会让 agent 犯错"的可执行约定（命令、边界、禁区、非标准实践），不装项目介绍与架构散文。**

### 2.3 论据二：分层记忆有明确技术边界，"哪些常驻、哪些易失"是可判定的

Claude Code 的记忆机制提供了可对照的分层参考（官方文档事实）：作用域由宽到窄为 **托管策略 → 用户级 `~/.claude/CLAUDE.md` → 项目级 `./CLAUDE.md` → 本地级 `./CLAUDE.local.md`（应 gitignore）**；沿目录树向上**拼接而非覆盖**，越靠近工作目录者越后读、优先级越高；子目录 CLAUDE.md 为**懒加载**。`@path` 导入最大递归深度 **4 跳**，被反引号包裹的路径不解析。`.claude/rules/` 支持 YAML frontmatter 的 `paths` glob 作用域，仅在 agent 触及匹配文件时才载入，从而节省上下文。auto memory 存于 `~/.claude/projects/<project>/memory/`，每会话仅载入 `MEMORY.md` **前 200 行或 25KB**（先到为准）（[Claude Code Memory 官方文档](https://code.claude.com/docs/en/memory)）。

由官方机制可直接推出三态划分：**① 常驻入仓**（AGENTS.md、`.claude/rules/*`、`docs/adr/`——随版本控制、可 review、可 diff）；**② 可再生**（状态类信息应由脚本从 git/测试结果派生，不手写）；**③ 明确易失**（auto memory、MCP 会话记忆——只作加速器，不得成为团队决策的唯一存放地；一旦捕获真实决策，必须迁入版本化文件）。官方文档亦明确指出 CLAUDE.md / AGENTS.md 是以用户消息形式注入的**上下文、不具强制力**——必须硬性保证的事项应写成 hook / CI / 权限规则。

### 2.4 论据三：drift 检测是可工程化的，不靠自觉

已有公开工具链证明"文档 CI"完全可落地。[doc-drift-detector](https://lobehub.com/ar/skills/borghei-claude-skills-doc-drift-detector) 用 AST 解析源码提取函数/类签名并与文档比对（检出签名漂移、参数缺失、已删除功能仍被文档描述），做 Markdown 链接/锚点完整性审计（本地文件是否存在、相对路径 `../` 错误、Linux 下才暴露的大小写问题），并给出 **0–100 的 staleness 评分**（按"最近更新 20% / 代码-文档对齐 30% / 链接健康 15% / 完整性 20% / 准确性 15%"加权），支持非零退出码接入 GitHub Actions 与 pre-commit。同类工具 [docdrift (PyPI)](https://pypi.org/project/docdrift/2.0.0/) 走 pre-commit 路线：**ERROR 级陈旧文档阻断提交，WARNING 级只告警不阻断**（可 `--no-verify` 跳过）。一篇实践文章把根因说得很直白：文档腐烂不是开发者不在意，而是**文档缺少代码那样的反馈回路**——代码有 CI、有 lint、有测试，文档什么都没有（[Why Your Documentation Is Always Stale](https://dev.to/suhteevah/why-your-documentation-is-always-stale-and-how-to-fix-it-with-git-hooks-f7b)）。"过期文档比没有更糟，因为 agent 会信它"——这句实践社区的判断（属观点，[AGENTS.md, explained for teams that actually ship](https://dev.to/arpituppal2rgb/agentsmd-explained-for-teams-that-actually-ship-13c3)）正与 context rot 的量化结论相互印证。

### 2.5 分析：针对本项目现状的处方

用户当前状况：说明书散落于 AGENTS.md / USER.md / SOUL.md / STATUS.md / CHANGELOG_agent_handoff.md / `.workbuddy/memory/` / 一整套 docs/，且 STATUS.md 停在 18 天前、CHANGELOG 停在 80 天前、且把项目路径写成已冻结的备份目录——后者是 context rot 中 **referential rot（引用腐化）的教科书案例**，也是最危险的一类：agent 会照着那个错误路径去读写文件。处方如下：

1. **单源收口。** AGENTS.md 是唯一源，承载：项目一句话定位、五条精确命令（安装 / 测试含过滤参数 / lint / typecheck / 构建）、"完成"的定义、10 条以内架构地图、禁区清单（kill list）、以及指向 decisions.md 与 docs/ 的导航链接。USER.md / SOUL.md 内容要么并入 AGENTS.md 对应小节，要么保留为独立文件但**只由 AGENTS.md 用 `@` 引用**，绝不手抄。CLAUDE.md 只保留一行 `@AGENTS.md`（Windows 安全）。控制在 200–300 行以内。
2. **状态类文档一律不手写。** STATUS.md 与 CHANGELOG 之所以过期 18 天 / 80 天，根因是"手写状态"设计本身不可持续。改为由脚本从 `git log`、最近提交、测试结果派生生成（生成物入库但标注 generated，禁止手改），或**直接删除**——删掉比留着一份错的更安全。**任何绝对路径不写进文档**，改用相对路径或由脚本注入，从机制上杜绝"路径指向已冻结备份目录"这类事故复发。
3. **决策与坑点进 ADR。** `docs/adr/NNN-<slug>.md`，已接受者不可改、只能由新 ADR superseded；`decisions.md` 作索引与坑点清单。
4. **三层 freshness 守卫**（回应"文档缺反馈回路"这一根因）：
   - **pre-commit**：解析 AGENTS.md 中所有反引号命令与文件路径，校验"文件真实存在 / 命令可干跑"，失败即阻断提交；
   - **CI**：Markdown 链接/锚点完整性 + 状态类文件 mtime 超阈值告警 + "改了 `src/` 却未同步 rules/ADR"的提示式告警（预防式，不阻断）；
   - **Definition of Done 入 AGENTS.md**：agent 完成任务前必须同步 SSOT；但这是"提示"而非"强制"——硬约束须落到 hook / CI / 权限（见第 4 章权限沙箱）。

### 2.6 小结

第一，context rot 是已被量化的真实工程风险（356 仓库样本中 23.0% 存在失效引用），在多 agent 同目录下被 N 倍放大。第二，AGENTS.md 已具备成为 SSOT 的生态基础（6 万+ 仓库、20+ 工具、Linux Foundation 托管），但实证收益集中在**效率与约定遵守**（运行时间 −28.64%、输出 token −16.58%），对**正确性无普遍提升且抬高约 20% 成本**——故 SSOT 应精简到"可执行约定"，砍掉仓库概览类内容。第三，"哪些常驻、哪些易失"可依官方记忆机制清晰划分，跨工具入口一律做薄壳引用而非复制（复制即 drift）。第四，也是对本项目最关键的一条：**过期不是纪律问题而是缺回路问题**，唯一可持续的解法是把 freshness 变成 CI / pre-commit 的一等公民，并把状态类文档从"手写"改为"派生或删除"。

### 关键发现

- **发现1（事实）**：356 个代表性仓库样本中 **23.0%** 的 AI 配置文件存在失效代码元素引用；context rot 还含架构声明、工具指引、依赖假设、行为约定四类腐化（[Treude & Baltes, 2026](https://arxiv.org/html/2606.09090)）。
- **发现2（事实）**：AGENTS.md 在 10 仓库/124 PR 配对实验中使中位运行时间 **−28.64%**、输出 token **−16.58%**（[Lulla et al., 2026](https://arxiv.org/abs/2601.20404)）。
- **发现3（事实，反向证据）**：4 agent/438 任务评估显示上下文文件**未普遍提升成功率、推理成本 +20% 以上**；指令被可靠遵守，仓库概览无帮助（[Gloaguen et al., 2026](https://arxiv.org/abs/2602.11988)）；跨厂商消融给出正确性效应上界 ≤10pp/≤15pp（[arXiv 2607.27250](https://arxiv.org/html/2607.27250v1)）。
- **发现4（事实）**：Claude Code 不读 AGENTS.md；官方桥接为 CLAUDE.md 顶部 `@AGENTS.md`，`@` 导入最大 4 跳；`.claude/rules/` 可用 `paths` frontmatter 做 glob 作用域按需加载；auto memory 每会话仅载 MEMORY.md 前 200 行或 25KB（[Claude Code Memory 官方文档](https://code.claude.com/docs/en/memory)）。
- **发现5（事实）**：AGENTS.md 已被 6 万+ 非 fork 仓库采用，嵌套"最近者优先"；2025-12 捐给 Linux Foundation 旗下 AAIF，但**尚无批准版本化规范**（[agents.md](https://agents.md/)；[Linux Foundation](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation)；[llms-txt.io](http://llms-txt.io/blog/what-is-agents-md)）。
- **发现6（事实）**：ADR 最常见失败模式是"写了没人读"，根因为存放于与代码分离的 wiki；已接受 ADR 应不可变、以 Superseded 迭代（[AWS Prescriptive Guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/architectural-decision-records/best-practices.html)；[Sensussoft](https://sensussoft.com/blog/software-architecture-decision-records)）。
- **发现7（可落地工程）**：doc drift 检测已有成熟形态——AST 签名比对、链接/锚点审计、0–100 staleness 加权评分、非零退出码接入 CI 与 pre-commit（ERROR 阻断 / WARNING 告警）（[doc-drift-detector](https://lobehub.com/ar/skills/borghei-claude-skills-doc-drift-detector)；[docdrift](https://pypi.org/project/docdrift/2.0.0/)）。
- **发现8（观点，非实证）**：实践社区普遍主张 AGENTS.md 应 <200–300 行、每行须通过"删掉会不会让 agent 犯错"检验，且"半真的 AGENTS.md 比没有更糟"（[dev.to](https://dev.to/arpituppal2rgb/agentsmd-explained-for-teams-that-actually-ship-13c3)）。

### 数据摘要

| 指标 | 数据 | 来源 |
|------|------|------|
| AGENTS.md 采纳规模 | 60,000+ 非 fork 开源仓库；20+ 工具原生支持 | [agents.md](https://agents.md/) |
| 治理归属 | 2025-12 捐赠给 Linux Foundation 旗下 Agentic AI Foundation | [Linux Foundation](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation) |
| 配置文件陈旧引用比例 | 356 仓库中 23.0% | [arXiv 2606.09090](https://arxiv.org/html/2606.09090) |
| 有 AGENTS.md 的中位运行时间变化 | −28.64% | [arXiv 2601.20404](https://arxiv.org/abs/2601.20404) |
| 有 AGENTS.md 的输出 token 变化 | −16.58% | [arXiv 2601.20404](https://arxiv.org/abs/2601.20404) |
| 上下文文件对任务成功率 | 无普遍提升；推理成本 +20% 以上 | [arXiv 2602.11988](https://arxiv.org/abs/2602.11988) |
| 正确性效应上界（消融） | ≤10pp (Claude) / ≤15pp (Codex)，288 次评测 | [arXiv 2607.27250](https://arxiv.org/html/2607.27250v1) |
| `@import` 最大递归深度 | 4 跳 | [Claude Code Memory docs](https://code.claude.com/docs/en/memory) |
| auto memory 每会话载入上限 | MEMORY.md 前 200 行或 25KB（先到为准） | [Claude Code Memory docs](https://code.claude.com/docs/en/memory) |
| staleness 评分权重 | 最近更新 20% / 代码-文档对齐 30% / 链接健康 15% / 完整性 20% / 准确性 15% | [doc-drift-detector](https://lobehub.com/ar/skills/borghei-claude-skills-doc-drift-detector) |
| 建议文件长度 | <200–300 行（实践共识，非实证） | [dev.to](https://dev.to/arpituppal2rgb/agentsmd-explained-for-teams-that-actually-ship-13c3) |

## 第3章 多 Agent 同目录的并发隔离与记忆/日志归属策略

### 论点

当用户把多个 AI Agent 指向同一个本地项目目录时，最先崩掉的往往不是模型能力，而是并发安全与上下文归属。本章的核心论点是：多 Agent 协作的并发安全，不能依赖 Agent 之间的"运行时协调"（聊天、约定、人工盯屏），而必须依赖"结构性隔离"；而当业务上确实必须多 Agent 同目录时，应以"共享只读层 + 带 Agent 标识的结构化个人子目录"来替代旧物理隔离（副本）所提供的安全边界。这一命题同时回应用户的第二大痛点——多 Agent 同目录时，各自跑出的推断与日志"往哪写"会互相覆盖或碎片化。

### 论据

#### 3.1 Git Worktree 已成为主流编码 Agent 的内置隔离原语

事实层面，Git worktree 已从"人类少用的 git 特性"转变为多 Agent 并行开发的默认隔离基元。Claude Code 提供 `--worktree <name>` 标志，自动在 `.claude/worktrees/<name>/` 创建目录并切到名为 `worktree-<name>` 的分支；Cursor 2.0 底层使用 worktree，最多支持 8 个并行 Agent；VS Code 自 1.107 起，其 Copilot 后台 Agent 在启动时自动创建 worktree；OpenAI Codex 也内置了 worktree 支持（[Git Worktrees: From Running Multiple Agents to Real Multi-Agent Development](https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh)）。

其机制是"共享同一 git 对象数据库、但各自拥有独立工作目录、暂存区与分支"。一篇工程解析强调：Agent 1 改 `package.json` 并提交、Agent 2 在自己的 worktree 做同样的事，"它们不可能碰撞，因为根本没碰同一批文件——不是'大概不会撞'，而是结构上不能撞"（同上）。换言之，worktree 把"文件互斥"从"靠约定"升级为"靠 git 强制"。另有实践者将其总结为"三层隔离模型"：SubAgent 的上下文窗口隔离（各自对话历史）、worktree 的文件隔离、以及可共享的只读层——worktree 补齐了"第四面墙：文件隔离"（[Claude Code Worktrees: File Isolation for Parallel Agents](https://dev.to/aijasonz/claude-code-worktrees-file-isolation-for-parallel-agents-57d)）。OpenClaw 等框架进一步把"工作空间隔离"推广到记忆层：每个 Agent 拥有独立的 `MEMORY.md` 与 `sessions/`，成为"互不相识的独立个体"才不会"串台"（[Multi-Agent Teams](https://azin.run/blog/multi-agent-teams-openclaw)）。

#### 3.2 运行期互斥 vs 合并期冲突：分层表述

需要厘清一个常见误解：worktree 解决的是"运行期"文件覆盖，并不消灭"合并期"冲突。若两个 Agent 在不同分支都改了 `package.json`，冲突会在合并时出现——"区别在于你只解决一次，干净地解决，而不是在两个 Agent 还在跑的中途才发现"（[Git Worktrees...](https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh)）。因此正确的表述是：worktree 让运行期"结构上不能撞"，但合并期"可能撞"——二者分层，并不矛盾。

更隐蔽的是"语义冲突"：Agent A 改了某函数返回类型，Agent B 照旧签名写调用方；两文件在 git 层面干净合并，运行时却崩。这类冲突只有 Agent 间显式通信或接口契约才能捕获（[Claude Code Worktrees...](https://dev.to/aijasonz/claude-code-worktrees-file-isolation-for-parallel-agents-57d)）。此外，worktree 隔离了文件系统，但端口、本地数据库等仍是共享态：两个 Agent 同时起开发服务器会抢 3000 端口，共享本地数据库守护进程会让并行迁移互相污染（[Zylos Research](https://zylos.ai/en/research/2026-02-22-git-worktree-parallel-ai-development/)）。

#### 3.3 同目录降级方案：per-agent 分片子目录

当业务约束要求多 Agent 必须共享同一目录（而非各自 worktree）时，应降级为"按 Agent 名分片"的目录策略。PubNub 团队用 kebab-case 的 `slug` 标识每个任务，相关工件分散存于 `docs/claude/working-notes/<slug>.md`、`decisions/ADR-<slug>.md`，钩子运行写入带时间戳的 `hooks.log`；并规定"仅对不相交 slug 并行"，由钩子在两任务触碰同目录时告警（[Best Practices for Claude Code Subagents](https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/)）。若采用 `<agent-name>` 子目录（如 `.claude/agents/<name>/`、`.workbuddy/memory/<agent-name>/`、`.history/<date>-<agent-name>.md`），文件名自带 Agent 标识，从根本上消除"互相覆盖"与"读错他人记忆"。

共享态冲突的反面教材来自 sudocode 的隔离架构：若多个 Agent 共享同一本地数据库（如 `.cache.db`），会因读取同一计数而生成重复主键、互相覆盖提交；改为"每 worktree 一份隔离副本"后冲突消失（[Worktree Isolation](https://deepwiki.com/sudocode-ai/sudocode/7.2-rest-api)）。端口争抢（3000/5432/8080）与 `package.json` 合并冲突，则分别用"按 worktree 序号偏移端口"与"rebase-before-PR"等约定缓解（[Zylos Research](https://zylos.ai/en/research/2026-02-22-git-worktree-parallel-ai-development/)）。

#### 3.4 记忆/日志归属：共享只读层 + 个人结构化子目录

上下文管理被业内视为 AI 辅助编码 90% 的功力所在，关键在于分层（[Context Management Is 90% of the Skill](https://fazm.ai/blog/context-management-90-percent-ai-coding-skill)）。其"三层上下文栈"为：第 1 层项目级 `CLAUDE.md`（单一可信源，建议 <200 行，承载架构、约定、已否决方案）；第 2 层目录级 `rules/`；第 3 层会话/历史层（决策日志 `decisions.md`、记忆服务器）。`CLAUDE.md` 可置于仓库根、父目录或用户 home，自动并入系统提示，充当团队与 AI 的"指令单一可信源"，从机制上避免上下文碎片化（[Using CLAUDE.md Files](https://www.claude.com/blog/using-claude-md-files)）。

Anthropic 的研究系统印证了"共享层 + 轻量回传"的范式：Lead Agent 把计划存入外部 Memory 以防上下文截断；subagent 把成果写入外部文件系统、只回传轻量引用，避免所有信息经主 Agent 中转造成丢失（[How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system)）。

**反方与局限**：值得指出，worktree 并非无代价。每次 worktree 是近完整的文件副本（`.git` 共享），一个 2GB 仓库开 5 个 worktree 可达 10GB+，需主动清理以免孤儿积累（[Claude Code Worktrees...](https://dev.to/aijasonz/claude-code-worktrees-file-isolation-for-parallel-agents-57d)）。另一方面，纯粹的"每 Agent 完全隔离"也会带来知识碎片化——这正是旧物理副本方案的代价；因此共享只读层必须承载 SSOT，个人层仅存"推断/日志"，并通过回传摘要收敛，而非各写各的。更有观点提醒：swarming（群体并行）不是"堆更多 Agent"，而是"更多有边界的 Agent"；若任务分解错误，并行只会放大混乱（[Swarming the Codebase](https://blog.heliomedeiros.com/posts/2025-11-23-swarming-with-worktree)）。

### 分析：为什么"结构隔离 + 分层归属"是用户痛点 2 的解药

用户痛点 2 的症结是：旧法用"物理隔离（副本）"解决了记忆覆盖，新法放弃隔离后，多 Agent 同目录时"推断/日志往哪写"成了真空。两种错误极端都不可取——都写 `.workbuddy/memory/` 会并发覆盖；各写各的又会知识碎片化。

遵循上述证据，对用户的具体处方可凝练为：

1. **优先用 worktree 做目录隔离**。若业务允许，每个 Agent 一个 worktree+分支，从结构上杜绝互相覆盖（对应 Claude/Cursor/VS Code/Codex 内置能力）。
2. **必须同目录时，降级为"共享只读层 + 个人结构化子目录"**：
   - **共享层（只读事实源）**：`CLAUDE.md` / SSOT 说明书，所有人只读，承载架构、约定、已否决方案；
   - **个人层（隔离写）**：`.workbuddy/memory/<agent-name>/` 存该 Agent 的推断/日志；`.history/<date>-<agent-name>.md` 存带标识的会话历史。文件名自带 agent 标识，从根本上消除"互相覆盖"与"读错他人记忆"。
3. **收敛碎片化**：各 Agent 写完后回传摘要/决策到共享层（如 `decisions.md` 或任务队列），使知识既归属清晰又可被他人检索，复制 Anthropic "轻量引用回传"的做法。
4. **并发写防护**：用 PreToolUse 钩子封杀危险写（如 `git push --force`、删除他人目录），并在两任务触碰同目录时告警（呼应 PubNub 的 slug 不相交原则）。
5. **语义冲突兜底**：通过显式接口契约 + 变更返回类型时同步更新调用方约定，把"语义冲突"暴露在合并前而非运行时。

### 小结

多 Agent 同目录的并发安全，本质是"隔离"与"归属"两件事：隔离靠 worktree 这一结构性原语，归属靠"共享只读 SSOT + 带 Agent 标识的个人子目录"。前者保证运行期不互相覆盖，后者保证记忆/日志既不覆盖也不碎片化。对用户而言，痛点 2 的具体处方可凝练为一句话——**共享层只读、个人层按 `<agent-name>` 分片子目录写入、写完回传摘要到共享层**，并以 worktree 为首选、同目录降级方案为兜底。

## 第4章 协作模式选型：角色分工与红蓝对抗的权衡及裁决机制

### 论点

面对"多个 AI Agent 在同一本地目录协作迭代同一项目"这一场景（对应您的痛点③：协作模式尚未确定），首要事实是：**不存在一种对所有任务都最优的协作范式**。角色分工模式（如 planner/executor/reviewer 或 pm-spec→architect→implementer 管线）擅长确定性、可审计的工程任务，产物可逐环节追溯；红蓝对抗／辩论模式（proposer/critic）则在事实核查、降低幻觉方面更具优势。但辩论并非免费午餐——多 Agent 辩论存在"越辩越错"的稳定性风险。因此，选型应基于"任务的可审计性需求"与"容错/安全性约束"两条主线，并配套明确的裁决机制与迭代上限。

### 论据

#### 一、角色分工：可审计的工程流水线

角色化子代理（subagent）将任务拆解为带明确输入/输出与交接规则的单一职责环节。PubNub 工程团队提出基线三阶流水线：pm-spec（写规格）→ architect-review（出架构决策记录 ADR）→ implementer-tester（实现并测试），通过状态机（BACKLOG→READY_FOR_ARCH→READY_FOR_BUILD→DONE）串联，并主张"单一职责＋权限卫生（PM/Architect 偏只读，Implementer 才获写权限）"（[Best practices for Claude Code subagents](https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/)）。Anthropic 的多智能体研究系统采用 orchestrator-worker 模式，主导代理并行派生子代理、回收结果再综合，其内部评测量显示多代理相对单代理在复杂研究任务上提升约 90.2%，并强调"关注点分离"能减少路径依赖（[How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system)）。Anthropic 亦将"评估器—优化器（evaluator-optimizer）"列为标准工作流：一个 LLM 生成响应、另一个在循环中给出反馈并迭代修订，适用于"有明确评价标准且迭代可度量收益"的任务（[Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)）。

#### 二、红蓝对抗／辩论：降幻觉但需约束

Yang 等（2025）提出融合"对抗性辩论＋角色化投票＋重复询问＋错误日志"的多智能体框架，在 MMLU 等基准上复合准确率随批次稳步上升，消融实验显示移除加权与一致性机制后性能下降，证明对抗辩论配合投票可显著降低幻觉（[Minimizing Hallucinations and Communication Costs](https://www.mdpi.com/2076-3417/15/7/3676)）。框架层面，AutoGen 的"对话/群聊"心智模型被业界认为最契合辩论、批判与头脑风暴类任务（[LangGraph vs AutoGen vs CrewAI](https://multiagentpro.ai/blog/langgraph-vs-autogen-vs-crewai)）。

#### 三、稳定性风险：辩论可能把对的改成错的

Ji 等（2025）在测试时计算综述中明确指出："多智能体辩论可能不稳定，因为 LLM 易受对抗性信息影响，并可能在误导性输入下将正确答案修正为错误答案"（[Test-Time Compute](https://arxiv.org/html/2501.02497v2)）。这表明辩论是双刃剑——错误反馈或误导性能使错误率不降反升。

#### 四、裁决机制：投票与裁判 Agent

当多个声音冲突时需第三方仲裁。Yang 等（2025）采用按历史错误率动态加权的可靠性投票（w=ln((1−ε)/ε)），让高可靠模型更大程度影响共识。更系统化的方案是 Agent-as-a-Judge：让一个具备工具使用与多步推理能力的"评判智能体"检查被评智能体的完整行动与决策链，而非仅看最终答案。Yu（2025）报告其在代码任务上的判断与人类多数投票（5 位专家基准）仅相差约 0.3%，而单一 LLM 评判者的分歧高达约 31%，且与人类评估者达到同等水平（[When AIs Judge AIs](https://arxiv.org/html/2508.02994v1)）。不过，该范式计算开销大（最坏单任务达数十分钟）、同模型家族可能偏袒同方，需人类校准。

### 分析

将两类范式并置，权衡清晰：**角色分工以"可预测、可审计、低幻觉风险"换"创造性/纠错力较弱"**，适合您"仓库被改坏"后最在意的确定性工程交付；**红蓝对抗以"更强的事实核查与纠错"换"稳定性与成本风险"**，适合需求模糊、错误代价高但可逆的核查环节。

对您这位非程序员、且对"多 agent 同目录安全性"高度敏感的交易员，三条落地建议尤为关键：

1. **默认选角色分工**——把"写规格/定架构"的代理设为只读，仅"实现"代理获写权限，从源头避免两 agent 同时改写同一文件（即您仓库被改坏的事故根因）。
2. **把红蓝对抗降级为子环节**，仅用于对关键改动（如交易策略参数、风险阈值）做事实核查与挑刺，而非贯穿全程。
3. **用评估器—优化器作为轻量折中**：当任务有明确对错标准（如"这段代码能否通过回测"），让一 agent 生成、另一 agent 批判修订，比全程辩论更安全可控。

### 小结：协作模式决策框架

- **何时用角色分工**：任务边界清晰、需可审计交付物、出错代价高（工程构建、配置、策略落地）→ 采用 pm-spec→architect→implementer 管线，强交接、弱并发。
- **何时用红蓝对抗**：任务存在事实不确定性、需降低幻觉或交叉验证（需求澄清、参数合理性、合规核查）→ 采用 proposer/critic，但限定范围与轮次。
- **如何裁决**：优先用加权投票（w=ln((1−ε)/ε)，按历史错误率）；关键产物引入 Agent-as-a-Judge 做过程级审查，并以人类抽检校准，避免同家族模型偏袒。
- **如何设迭代上限**：鉴于"越辩越错"风险，辩论/评估—优化循环建议设硬上限（2–3 轮）；一旦某轮未提升即通过"正确答案锁定"或裁判仲裁收口，禁止无限自我修订。

> 说明：以上选型次序为基于公开工程实践与研究的建议性框架（观点），具体阈值应结合您的项目规模与风险偏好在试点中校准（事实依据见各引用来源）。

## 第5章 安全·权限·非专家监督——攻击面控制、hooks 护栏与可读看板

### 论点

多 Agent 同目录协作，会把"单点失误"放大成"全局灾难"。当 N 个拥有文件读写、命令执行权限的智能体共享同一工作目录时，攻击面（attack surface，即可被误操作或恶意利用的入口总和）约等于单 Agent 的 N 倍——任一危险操作（删除、覆盖、外泄）都会被所有 Agent 共同承担。对本案用户——一位曾因仓库被改坏而高度敏感的交易员——而言，权限隔离与"非专家也能看懂、能喊停"的监督机制，不是锦上添花，而是"不出事"的底线。本章对应盲区 B4（安全/权限）与 B6（非专家监督）。

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

### 分析

把用户的真实事故代入：仓库被改坏，本质就是"无隔离 + 无审批闸"下多 Agent（或多轮操作）同目录协作的典型后果。若坚持"多 Agent 同目录"新法却不设权限隔离与审批闸，重演概率极高——这不是危言耸听，而是 Heliomedeiros 与 dev.to 两篇实战复盘共同证明的必然。

更深一层，B6 之所以危险，是因为用户不具备在命令行里"看 diff、拦命令"的能力。因此监督手段必须满足三条：①审批闸（approval gate，关键动作如 `git push`、删除、外发前必须由人确认）；②可读看板（用"谁在做什么、卡在哪、要不要我拍板"取代术语轰炸）；③通俗化报告（术语首次出现即注释，呼应全局硬约束）。这三条都不要求用户写代码，却能让他作为交易员真正"自己掌控"协作进程。

### 小结

多 Agent 同目录的"不出事"底线，是三层叠加：第一层隔离（worktree/独立分支，从结构上杜绝互相覆盖）；第二层最小权限 + hooks 审计（危险动作前置拦截、全程留痕）；第三层非专家可操作的审批闸与可读看板。前两层是工程护栏，第三层是用户唯一能独立掌控的部分——也是本报告最该补、却最易被通用教程忽略的一块。

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

## 第6章 成本·可观测性·收敛与责任：token 核算、回放调试与审计链

**论点**：当多个 AI Agent 在同一本地目录协作迭代同一项目时，最易被忽视的不是"它们能不能干活"，而是四项隐性约束——成本是否可控、行为是否可回放、错误是否会被放大、出事后能否追责。对一名曾因仓库被改坏而高度敏感的独立用户，这四点直接决定多 Agent 同目录方案是否值得采用。以下逐一论证。

### 一、成本（B1）：token 是硬约束，而"上下文该花多少"尚存实证分歧

多 Agent 系统的 token 消耗远高于直觉。Anthropic 在其多 Agent 研究系统复盘中实测，普通 agent 比聊天交互多耗约 4× token，而多 Agent 系统消耗量约为聊天的 15×；同一研究还显示 token 用量单独解释了 80% 的性能差异——多花 token 多数情况下确实换来了更好结果，但账单线性放大（[How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system)）。该 4×/15× 倍率来自 Anthropic 特定研究架构的实测，应视为"量级参考"而非普适定律；对个人用户，API 速率限制与月度预算仍是硬天花板，节流因此必须前置设计而非事后补救。

节流实践在工程社区已较成熟。多位从业者总结，多 Agent 成本治理需三层：请求级 per-agent 日志、实时聚合、编排层硬性上限，触顶即节流或停摆（[Latenode 社区讨论，非同行评审](https://community.latenode.com/t/when-you-run-multiple-autonomous-ai-agents-how-do-you-actually-track-costs-and-prevent-runaway-spend/54626/9)）；另有工程博客给出量化经验值：动态上下文剪枝约 30%、多 Agent 通信压缩约 40%、模型路由降级最高约 60%（[CSDN 工程博客，经验性数据，未经复核](https://blog.csdn.net/2501_91930600/article/details/161525323)）。

值得注意的是，关于"上下文/说明书该投入多少"近期出现**实证分歧**，应客观并陈：Lulla 等（2026）在 10 个仓库、124 个 PR 上实测，配置得当的 AGENTS.md 使中位运行时间降低约 28.64%、输出 token 降低约 16.58%，且任务完成率持平（[Lulla et al., 2026](https://arxiv.org/abs/2601.20404)）；而 Gloaguen 等（2026，ETH Zurich）在 SWE-bench Lite 与 AGENTbench 上发现，LLM 生成的上下文文件使成功率下降约 3%、推理成本上升超 20%，即便开发者手写的文件也仅小幅提升成功率（约 +4%）却仍使成本上升约 19%（[Gloaguen et al., 2026](https://arxiv.org/abs/2602.11988)）。两文测度不同（一测效率、一测成功率），但共同提示：上下文不是越多越好，冗余说明反而推高成本。这对用户痛点①（说明书散落、过期）是直接警示——与其堆砌上下文，不如保持精简、可测试。

### 二、可观测性（B2）：没有回放，诡异行为就是悬案

多 Agent 同目录最大调试难题：谁在何时改了什么、依据是什么，传统日志无法回答。横向框架对比显示，LangGraph 借助 LangSmith 提供"每一步、每个 token、每次工具调用都可追踪"的能力，被评价为最适合需要细粒度控制与回滚的生产级工作流（[LangGraph vs AutoGen vs CrewAI](https://multiagentpro.ai/blog/langgraph-vs-autogen-vs-crewai)）；其本质是把工作流建模为有向图、状态以类型化字典流转，使执行路径显式化。

更深一层，可观测性目标是把"黑盒"变"玻璃盒"。技术分析指出，分层追踪（hierarchical tracing，如 Langfuse/Logfire）能捕获完整嵌套执行轨迹，开发者可下钻到每个 span 查看输入输出，从而"在脑中重放"agent 逻辑（[OpenAI Agents + Logfire + Langfuse 可观测栈](http://thinhdanggroup.github.io/agent-observability/)）。多 Agent 场景中，单一 agent 错误会级联到 others，统一 trace 能立刻标出错误起源与传播路径（[Datadog LLM Observability](https://www.datadoghq.com/blog/llm-aws-strands/)）。对独立用户：每次运行前应开启结构化日志与全局 Trace ID，使每次改动可回放、可归因。

### 三、错误传播 / 共享上下文污染（B3）：错的会被接着建

多 Agent 辩论并不天然稳定。综述性研究明确指出，LLM 易受对抗性信息影响，在误导性输入下"可能把正确答案改成错误答案"（[Test-Time Compute: from System-1 to System-2 Thinking](https://arxiv.org/html/2501.02497v2)）。这正是共享上下文污染的核心风险：一旦某 agent 把错误假设写入共享记忆/上下文，其他 agent 在其上继续构建，错误便链式放大；即便红蓝对抗，也可能因一方被误导而在辩论中把对的辩错。

对抗之道是隔离与交叉校验。学术论文提出，让多个不同背景的 LLM 通过"对抗辩论 + 投票 + 错误日志"交叉验证，并由汇总模型维护各 agent 历史错误率以实现贡献透明（[Minimizing Hallucinations and Communication Costs](https://www.mdpi.com/2076-3417/15/7/3676)）。对独立用户可落地建议：避免所有 agent 共享同一份可变上下文，改用只读共享说明书 + 各自隔离工作区/分支；关键改动要求第二 agent 独立复核。

### 四、收敛与责任（B5）：给循环设上限，给决策留痕迹

多 Agent 辩论"持续到达成共识或由 judge 模型总结为止"，但稳定性非保证，可能因误导输入而振荡或发散（[Test-Time Compute](https://arxiv.org/html/2501.02497v2)）。故必须设迭代上限与明确停止条件，防无限辩论烧光预算。Anthropic 实践也强调 agent 有状态、错误会累积，系统须能从断点恢复（定期 checkpoint + 重试）（[How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system)）。

责任归属上，业内观点主张"零信任"架构：每条决策都应留可审计记录，说明所依据信息、评估的替代方案与置信度（[Agent architecture: How AI decision-making drives business impact](https://www.retool.com/blog/agent-architecture)）。这正是多 Agent 下难归因问题的解方——当多个 agent 都动过同一文件，若无审计链便无法判定"谁该负责"。若每次文件改动都关联到具体 agent、其推理依据与审批状态，出事后即可定位责任、回滚到任意状态，直接回应用户的仓库损坏史。

**小结**：对独立交易员的同目录多 Agent 方案，成本、可观测、收敛、责任须前置设计而非事后补救。落地清单：①单任务 token 熔断 + 并行度上限，且上下文保持精简可测（勿堆砌）；②开启结构化日志与全局 Trace ID 实现回放；③隔离共享上下文、关键改动双 agent 复核；④设迭代上限与 checkpoint，并为每次改动保留"谁+依据+审批"审计链。如此多 Agent 同目录才从"高风险实验"变为"可控、可查、可回滚"的协作范式。

## 第7章 多 Agent 协作端到端落地实践：目录结构、说明书写法与 CI 集成 Playbook

### 论点

前六章分别闭合了六个能力盲区——范式认知（B1）、说明书治理（B2）、并发隔离（B3）、模式选型（B4）、安全监督（B5）、成本可观测（B6）。本章作为收口章，不引入新理论，而是把前六章能力拼成一个**从零搭建"多 Agent 同目录协作环境"**的可执行 playbook，并直接回应用户的三个工程痛点：①说明书/上下文散落且过期；②多 Agent 同目录时记忆/日志互相覆盖；③协作模式（角色分工 vs 红蓝对抗）悬而未决。本章只落地三件工程纪律：**定目录结构（隔离）、写说明书（SSOT）、接 CI 守卫（防漂移与越权）**；B5 安全监督与 B6 成本可观测已前置，本章仅做集成钩子，不重复展开。

### 一、目录结构模板：worktree 优先，同目录分片降级

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

### 二、说明书写法：SSOT + when-to-use + @import 约束

CLAUDE.md/AGENTS.md 本质是"人与 AI 共享的唯一可信文档"，应简洁、人类可读、提交版本控制且不内含 API key 等敏感信息（[Using CLAUDE.md files](https://www.claude.com/blog/using-claude-md-files)）。行业共识进一步主张 AGENTS.md 作为"单一事实来源（SSOT）"便携基线，放仓库根、随 PR 更新，避免"仓库外上下文会腐烂"；稳定与易变内容分离，通用基线与工具专用薄层解耦（[the hidden truth about cross-tool AI coding context management](https://dredyson.com/the-hidden-truth-about-cross-tool-ai-coding-context-management-what-every-developer-needs-to-know-about-agents-md-mcp-memory-and-proxy-layer-architectures-in-2025)）。

写法三原则：

1. **SSOT 单点定义**——命令只链接到脚本文件而非内联，从根上消灭漂移（"link, don't duplicate"，[How to Handle AI Coding Tool Context](https://dredyson.com/how-to-handle-ai-coding-tool-context-across-claude-code-cursor-codex-and-windsurf-a-complete-beginners-step-by-step-guide-to-managing-project-memory-rules-and-cross-tool-drift-in-2025/)）。
2. **when-to-use 触发条件**——`devprompts/` 下每个模块文件须写明"When to use"，让路由 Agent 精确委派、避免重复劳动（Anthropic 早期教训：简短指令导致子代理重复或留白，[How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system)）。
3. **控制 `@` 引用深度（经验值不超过 4 跳）**——主文件用 `@` 导入 devprompts/ 细节，建议将引用深度控制在 4 跳以内：过深会稀释上下文窗口、放大断链与漂移，且难以审阅；此为启发式经验上限而非硬性铁律（Claude Code 对 `@path` 导入亦约定最大递归深度为 4 跳，参见其 Memory 官方文档 [Claude Code Memory](https://code.claude.com/docs/en/memory)）。

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

### 三、CI 集成：drift 守卫 + 质量门禁

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

### 四、模式选择落点：角色分工 vs 红蓝对抗

何时用角色分工、何时用红蓝对抗，是本章与 B4 模式选型的集成点，直接回应痛点③。框架对比显示三种心智模型：CrewAI 的**角色制**（Agent 带 role/goal/backstory）适合线性内容流水线；AutoGen 的**会话/辩论制**（GroupChat 多 Agent 轮流发言、互相 critique）天然适配审查、辩论、迭代精炼；LangGraph 的**图控**适合需人工中断与可回滚的生产流程（[LangGraph vs AutoGen vs CrewAI](https://multiagentpro.ai/blog/langgraph-vs-autogen-vs-crewai)）。

落地建议：

- **确定性强的编码/构建任务用角色分工**——PM 提问、Architect 校验、Implementer 构建、QA 验证，各 Agent 工具白名单隔离（[Best practices for Claude Code subagents](https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/)）。
- **质量风险高的产出（研究结论、策略报告）用红蓝对抗**——以 verifier/critic 型 Agent 末端校验；Anthropic 研究系统即由 CitationAgent 确保所有主张正确归因（[How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system)）。
- **审批闸与迭代上限**：关键写入点设人工 checkpoint，并给自主 Agent 设最大迭代次数防止失控（[Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)）。

### 五、工具链接线：从零搭建的一份清单

把前六章能力串成可执行顺序，逐项闭合盲区：

1. **认知范式（B1）**→ 明确"多 Agent 同目录"的定位与边界；
2. **建 SSOT 说明书（B2）**→ 写 AGENTS.md/CLAUDE.md，@ 下沉细节，坑点入 ADR；
3. **并发隔离（B3 隔离）**→ 能用 worktree 就一 agent 一分支，否则按 agent/slug 分片子目录；
4. **模式选型（B4）**→ 线性任务走角色分工、高风险产出走红蓝对抗；
5. **安全监督（B5）**→ 写入点加审批闸、工具白名单限权、权限边界进 CI；
6. **成本可观测（B6）**→ CI 接 drift 守卫、成本埋点、迭代上限兜底；
7. **收口复盘**→ 定期 `decisions.md` 回顾，刷新"last verified"日期。

### 六、收口：痛点与盲区落点映射

| 前文问题 | 落点步骤 | 对应能力盲区 |
|---|---|---|
| 痛点① 说明书散落且过期 | SSOT 单点 + @import + docdrift ERROR 阻断 + ADR 入册 | B2 说明书治理 |
| 痛点② 记忆/日志互相覆盖 | worktree 优先 + working-notes/decisions 按 slug/agent 分片 | B3 并发隔离 |
| 痛点③ 协作模式未定 | 角色分工 / 红蓝对抗落点 + 审批闸 + 迭代上限 | B4 模式选型 |
| 盲区 B1–B6 | 上述七步清单逐章闭合，B5/B6 经 CI 与权限钩子集成 | B1–B6 全闭合 |

### 小结

多 Agent 同目录协作的成败不在模型强弱，而在"隔离、单源、守卫"三项工程纪律是否落地：目录上 worktree 优先、分片降级；说明书上 SSOT 单点、@ 桥接（建议深度上限，经验值 ≤4 跳）、坑点入册；集成上 drift 与权限双守卫。守住这三条，前六章能力才真正可用。

**给本用户的落地建议（一句级）**：作为有仓库损坏史的非程序员，优先用 worktree 把每个 Agent 锁在独立分支 + 关键写入设人工审批闸 + 要 Agent 用通俗语言汇报"它改了什么、风险几何"，切勿一上来就让多个 Agent 裸写同一目录。

## 结论

综合 7 章研究，多 Agent 同目录协作的可靠性可被三根支柱支撑，三者缺一不可。

**第一支柱·结构性隔离。** 并发安全不能依赖 Agent 间的运行时协调（聊天、约定、盯屏），而必须依赖 worktree 这类结构性原语——每个 Agent 拥有独立工作树、暂存区与分支，从架构上"无法"互相覆盖文件（参见第 1、3、5、7 章）。当业务约束强制同目录时，降级为"共享只读层 + 带 Agent 标识的个人子目录"，使记忆/日志既不覆盖也不碎片化。

**第二支柱·单一事实来源（SSOT）。** "同一事实只在一处定义、别处只引用不手抄"是治理过期说明书的第一性原理。AGENTS.md 已成跨工具标准（6 万+ 仓库、20+ 工具、Linux Foundation 托管），但实证收益集中在效率与约定遵守（运行时间 −28.64%、输出 token −16.58%），对正确性无普遍提升且抬高约 20% 成本——故 SSOT 应精简到"删掉就会让 Agent 犯错"的可执行约定，并以 CI/pre-commit 把新鲜度变成一等公民（第 2 章）。

**第三支柱·护栏与守卫。** 权限最小化（显式工具白名单，杜绝隐式全授权）、hooks 审计（`PreToolUse` 阻断高危命令、`SubagentStop`/`Stop` 留痕）、以及非专家可操作的审批闸与可读看板，共同构成"不出事"的底线（第 4、5、6 章）。

**客观盲区小结。** 本研究坦诚六类盲区均未被完全解决：成本随 Agent 数线性放大、可观测性依赖框架选型、共享上下文污染仍可能链式放大、安全边界需人工配置、收敛与责任依赖审计链沉淀、非专家监督仍依赖第三方无代码工具。其中 B6（非专家监督）是通用教程最易忽略、对本案用户却最关键的一块。

**给该用户的一句级落地建议。** 作为有仓库损坏史的非程序员：优先用 worktree 把每个 Agent 锁在独立分支 + 对关键写入（如 `git push`、依赖安装、交易策略参数）设人工审批闸 + 要求 Agent 用通俗语言汇报"它改了什么、风险几何"，切勿一上来就让多个 Agent 裸写同一目录。

**局限声明。** 本场景新兴未标准化，结论具"方向性"而非"规范级"；AGENTS.md 的效能存在实证分歧（效率提升 vs 正确性无普遍收益），落地阈值须结合用户风险偏好在试点中校准；所有数据与引用请读者在生产使用前自行核验。

## 参考文献

> 以下按类型分组，已对 7 章全部内联链接与"关键发现/数据摘要/来源清单"中的 URL 去重汇总，共 44 条。每条均保留可点击链接。

### A. 学术文献（期刊 / 预印本）

- Treude, C., & Baltes, S. (2026). Context rot in AI-assisted software development. 检索自 https://arxiv.org/html/2606.09090
- Lulla, J. L., Mohsenimofidi, S., Galster, M., Zhang, J. M., Baltes, S., & Treude, C. (2026). On the impact of AGENTS.md files on the efficiency of AI coding agents. 检索自 https://arxiv.org/abs/2601.20404
- Gloaguen, T., Mündler, N., Müller, M., Raychev, V., & Vechev, M. (2026). Evaluating AGENTS.md: Are repository-level context files helpful for coding agents? 检索自 https://arxiv.org/abs/2602.11988
- arXiv. (2026). Two-agent ablation: Bounded effects of AGENTS.md on coding agent correctness (arXiv:2607.27250v1). 检索自 https://arxiv.org/html/2607.27250v1
- Yang, Y., Ma, Y., Feng, H., Cheng, Y., & Han, Z. (2025). Minimizing hallucinations and communication costs: Adversarial debate and voting mechanisms in LLM-based multi-agents. *Applied Sciences, 15*(7), 3676. 检索自 https://www.mdpi.com/2076-3417/15/7/3676
- Ji, Y., Li, J., Ye, H., Wu, K., Yao, K., Xu, J., Mo, L., & Zhang, M. (2025). Test-time compute: From System-1 thinking to System-2 thinking (arXiv:2501.02497v2). 检索自 https://arxiv.org/html/2501.02497v2
- Yu, F. (2025). When AIs judge AIs: The rise of Agent-as-a-Judge evaluation for LLMs (arXiv:2508.02994v1). 检索自 https://arxiv.org/html/2508.02994v1

### B. 官方工程博客与产品文档

- Anthropic. (2024, December 19). Building effective agents. 检索自 https://www.anthropic.com/engineering/building-effective-agents
- Anthropic. (2025, June 13). How we built our multi-agent research system. 检索自 https://www.anthropic.com/engineering/built-multi-agent-research-system
- Claude Code. (n.d.). Memory. 检索自 https://code.claude.com/docs/en/memory
- AWS Prescriptive Guidance. (n.d.). Architectural decision records (ADR) best practices. 检索自 https://docs.aws.amazon.com/prescriptive-guidance/latest/architectural-decision-records/best-practices.html
- OpenAI. (2025, December). OpenAI co-founds the Agentic AI Foundation. 检索自 https://openai.com/index/agentic-ai-foundation
- Linux Foundation. (2025, December). Linux Foundation announces the formation of the Agentic AI Foundation. 检索自 https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation
- AGENTS.md. (n.d.). AGENTS.md — the agent-native standard for project instructions. 检索自 https://agents.md/
- Anthropic. (n.d.). Using CLAUDE.md files. 检索自 https://www.claude.com/blog/using-claude-md-files

### C. 行业 / 社区技术博客

- Heliomedeiros. (2025, November 23). Swarming the codebase: Git worktrees for parallel AI agents. 检索自 https://blog.heliomedeiros.com/posts/2025-11-23-swarming-with-worktree
- Vibehackers. (2025). Git worktrees: From running multiple agents to real multi-agent development. 检索自 https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh
- aijasonz. (n.d.). Claude Code worktrees: File isolation for parallel agents. 检索自 https://dev.to/aijasonz/claude-code-worktrees-file-isolation-for-parallel-agents-57d
- Azin. (n.d.). Multi-agent teams (OpenClaw). 检索自 https://azin.run/blog/multi-agent-teams-openclaw
- SudoCode. (n.d.). Worktree isolation (rest API). 检索自 https://deepwiki.com/sudocode-ai/sudocode/7.2-rest-api
- Zylos. (2026, February 22). Git worktree for parallel AI development. 检索自 https://zylos.ai/en/research/2026-02-22-git-worktree-parallel-ai-development/
- Fazm. (n.d.). Context management is 90% of the skill. 检索自 https://fazm.ai/blog/context-management-90-percent-ai-coding-skill
- PubNub. (2025). Best practices for Claude Code subagents. 检索自 https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/
- MultiAgentPro. (2025, March 8). LangGraph vs AutoGen vs CrewAI: Which framework should you use? 检索自 https://multiagentpro.ai/blog/langgraph-vs-autogen-vs-crewai
- Sensussoft. (n.d.). Architecture decision records: How top teams document decisions. 检索自 https://sensussoft.com/blog/software-architecture-decision-records
- Magic Liang. (2026, May 3). 如何写好 AGENTS.md. 检索自 https://magicliang.github.io/2026/05/03/%E5%A6%82%E4%BD%95%E5%86%99%E5%A5%BD-agents-md/
- Techsy.io. (n.d.). Cursor rules vs Claude.md: Three-tool practical comparison. 检索自 https://techsy.io/en/blog/cursor-rules-vs-claude-md
- llms-txt.io. (n.d.). What is AGENTS.md? 检索自 http://llms-txt.io/blog/what-is-agents-md
- suhteevah. (n.d.). Why your documentation is always stale (and how to fix it with git hooks). 检索自 https://dev.to/suhteevah/why-your-documentation-is-always-stale-and-how-to-fix-it-with-git-hooks-f7b
- arpituppal2rgb. (n.d.). AGENTS.md, explained for teams that actually ship. 检索自 https://dev.to/arpituppal2rgb/agentsmd-explained-for-teams-that-actually-ship-13c3
- Dredyson. (2025). The hidden truth about cross-tool AI coding context management. 检索自 https://dredyson.com/the-hidden-truth-about-cross-tool-ai-coding-context-management-what-every-developer-needs-to-know-about-agents-md-mcp-memory-and-proxy-layer-architectures-in-2025
- Dredyson. (2025). How to handle AI coding tool context across Claude Code, Cursor, Codex and Windsurf. 检索自 https://dredyson.com/how-to-handle-ai-coding-tool-context-across-claude-code-cursor-codex-and-windsurf-a-complete-beginners-step-by-step-guide-to-managing-project-memory-rules-and-cross-tool-drift-in-2025/
- agentsurface.dev. (n.d.). Drift detection for context files. 检索自 https://agentsurface.dev/docs/context-files/drift-detection
- LobeHub. (n.d.). doc-drift-detector skill. 检索自 https://lobehub.com/ar/skills/borghei-claude-skills-doc-drift-detector
- docdrift. (n.d.). docdrift (Version 2.0.0). 检索自 https://pypi.org/project/docdrift/2.0.0/
- Dang, T. (2025). A strategic analysis of the OpenAI Agents, Logfire, and Langfuse observability stack. 检索自 http://thinhdanggroup.github.io/agent-observability/
- Datadog. (2025). Gain visibility into Strands Agents workflows with Datadog LLM Observability. 检索自 https://www.datadoghq.com/blog/llm-aws-strands/
- Retool. (n.d.). Agent architecture: How AI decision-making drives business impact. 检索自 https://www.retool.com/blog/agent-architecture
- Latenode Community. (n.d.). Tracking costs and preventing runaway spend across multiple autonomous AI agents. 检索自 https://community.latenode.com/t/when-you-run-multiple-autonomous-ai-agents-how-do-you-actually-track-costs-and-prevent-runaway-spend/54626/9
- CSDN. (n.d.). Multi-agent cost governance: Context pruning, communication compression, model routing. 检索自 https://blog.csdn.net/2501_91930600/article/details/161525323

### D. 产品 / 社区页面与媒体

- Rerun. (2026). Rerun: No-code platform to build, run and monitor AI agents. 检索自 https://aipure.ai/cn/products/rerun-2
- OpenClaw. (2025). OpenClaw Mission Control: From black box to command center. 检索自 https://www.houdao.com/d/5314-OpenClaw-Mission-Control-kai-yuan-gao-bie-AI-hei-xiang-shi-xian-ke-shi-hua-bian-pai-yu-kong-zhi
- surya_02. (2025, May 8). Human-in-the-loop approval dashboard for LangGraph agents. LangChain Forum. 检索自 https://forum.langchain.com/t/human-in-the-loop-approval-dashboard-for-langgraph-agents-open-source-free-to-deploy/3616/1
- jxxy.net. (2025). 不写代码搭一个 24 小时干活的 AI 助手. 检索自 https://www.jxxy.net/ai/articles/rerun-no-code-ai-agents

## 免责声明

本报告基于 2025—2026 年公开资料综合撰写，属于工程实践调研与建议性框架，非同行评审学术成果，亦不构成对任何具体工具、框架或商业产品的背书。文中引用的性能数据（如多 Agent 系统约为聊天 15× token、AGENTS.md 使运行时间 −28.64%）来自特定研究架构或样本，应视为"量级参考"而非普适定律；不同框架、任务与配置下的实测结果可能存在显著差异。所有超链接来源均按原文呈现，链接有效性随时间变化，请读者在生产使用前自行核验数据、日期与适用性。用户应结合自身风险承受力、技术背景与项目规模采纳本报告建议，对关键生产环境（如交易策略、资金操作）的任何自动化改动，务必保留人工审批闸与可回滚机制。本报告作者不对因依此报告操作导致的任何损失承担责任。
