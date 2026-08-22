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

---

### 参考文献（APA）

- Anthropic. (2025, June 13). *How we built our multi-agent research system*. https://www.anthropic.com/engineering/built-multi-agent-research-system
- MultiAgentPro. (2025, March 8). *LangGraph vs AutoGen vs CrewAI: Which framework should you use?* https://multiagentpro.ai/blog/langgraph-vs-autogen-vs-crewai
- Lulla, J. L., Mohsenimofidi, S., Galster, M., Zhang, J. M., Baltes, S., & Treude, C. (2026). *On the impact of AGENTS.md files on the efficiency of AI coding agents*. arXiv:2601.20404. https://arxiv.org/abs/2601.20404
- Gloaguen, T., Mündler, N., Müller, M., Raychev, V., & Vechev, M. (2026). *Evaluating AGENTS.md: Are repository-level context files helpful for coding agents?* arXiv:2602.11988. https://arxiv.org/abs/2602.11988
- Dang, T. (2025). *A strategic analysis of the OpenAI Agents, Logfire, and Langfuse observability stack*. http://thinhdanggroup.github.io/agent-observability/
- Datadog. (2025). *Gain visibility into Strands Agents workflows with Datadog LLM Observability*. https://www.datadoghq.com/blog/llm-aws-strands/
- Retool. (n.d.). *Agent architecture: How AI decision-making drives business impact*. https://www.retool.com/blog/agent-architecture
- *Minimizing hallucinations and communication costs: Adversarial debate and voting mechanisms in LLM-based multi-agents*. (2025). *Applied Sciences*, 15(7), 3676. https://www.mdpi.com/2076-3417/15/7/3676
- *Test-time compute: From System-1 thinking to System-2 thinking*. (2025). arXiv:2501.02497v2. https://arxiv.org/html/2501.02497v2
