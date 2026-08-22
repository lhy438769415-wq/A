# 第3章 多 Agent 同目录的并发隔离与记忆/日志归属策略

## 论点

当用户把多个 AI Agent 指向同一个本地项目目录时，最先崩掉的往往不是模型能力，而是并发安全与上下文归属。本章的核心论点是：多 Agent 协作的并发安全，不能依赖 Agent 之间的"运行时协调"（聊天、约定、人工盯屏），而必须依赖"结构性隔离"；而当业务上确实必须多 Agent 同目录时，应以"共享只读层 + 带 Agent 标识的结构化个人子目录"来替代旧物理隔离（副本）所提供的安全边界。这一命题同时回应用户的第二大痛点——多 Agent 同目录时，各自跑出的推断与日志"往哪写"会互相覆盖或碎片化。

## 论据

### 3.1 Git Worktree 已成为主流编码 Agent 的内置隔离原语

事实层面，Git worktree 已从"人类少用的 git 特性"转变为多 Agent 并行开发的默认隔离基元。Claude Code 提供 `--worktree <name>` 标志，自动在 `.claude/worktrees/<name>/` 创建目录并切到名为 `worktree-<name>` 的分支；Cursor 2.0 底层使用 worktree，最多支持 8 个并行 Agent；VS Code 自 1.107 起，其 Copilot 后台 Agent 在启动时自动创建 worktree；OpenAI Codex 也内置了 worktree 支持（[Git Worktrees: From Running Multiple Agents to Real Multi-Agent Development](https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh)）。

其机制是"共享同一 git 对象数据库、但各自拥有独立工作目录、暂存区与分支"。一篇工程解析强调：Agent 1 改 `package.json` 并提交、Agent 2 在自己的 worktree 做同样的事，"它们不可能碰撞，因为根本没碰同一批文件——不是'大概不会撞'，而是结构上不能撞"（同上）。换言之，worktree 把"文件互斥"从"靠约定"升级为"靠 git 强制"。另有实践者将其总结为"三层隔离模型"：SubAgent 的上下文窗口隔离（各自对话历史）、worktree 的文件隔离、以及可共享的只读层——worktree 补齐了"第四面墙：文件隔离"（[Claude Code Worktrees: File Isolation for Parallel Agents](https://dev.to/aijasonz/claude-code-worktrees-file-isolation-for-parallel-agents-57d)）。OpenClaw 等框架进一步把"工作空间隔离"推广到记忆层：每个 Agent 拥有独立的 `MEMORY.md` 与 `sessions/`，成为"互不相识的独立个体"才不会"串台"（[Multi-Agent Teams](https://azin.run/blog/multi-agent-teams-openclaw)）。

### 3.2 运行期互斥 vs 合并期冲突：分层表述

需要厘清一个常见误解：worktree 解决的是"运行期"文件覆盖，并不消灭"合并期"冲突。若两个 Agent 在不同分支都改了 `package.json`，冲突会在合并时出现——"区别在于你只解决一次，干净地解决，而不是在两个 Agent 还在跑的中途才发现"（[Git Worktrees...](https://dev.to/vibehackers/git-worktrees-from-running-multiple-agents-to-real-multi-agent-development-7dh)）。因此正确的表述是：worktree 让运行期"结构上不能撞"，但合并期"可能撞"——二者分层，并不矛盾。

更隐蔽的是"语义冲突"：Agent A 改了某函数返回类型，Agent B 照旧签名写调用方；两文件在 git 层面干净合并，运行时却崩。这类冲突只有 Agent 间显式通信或接口契约才能捕获（[Claude Code Worktrees...](https://dev.to/aijasonz/claude-code-worktrees-file-isolation-for-parallel-agents-57d)）。此外，worktree 隔离了文件系统，但端口、本地数据库等仍是共享态：两个 Agent 同时起开发服务器会抢 3000 端口，共享本地数据库守护进程会让并行迁移互相污染（[Zylos Research](https://zylos.ai/en/research/2026-02-22-git-worktree-parallel-ai-development/)）。

### 3.3 同目录降级方案：per-agent 分片子目录

当业务约束要求多 Agent 必须共享同一目录（而非各自 worktree）时，应降级为"按 Agent 名分片"的目录策略。PubNub 团队用 kebab-case 的 `slug` 标识每个任务，相关工件分散存于 `docs/claude/working-notes/<slug>.md`、`decisions/ADR-<slug>.md`，钩子运行写入带时间戳的 `hooks.log`；并规定"仅对不相交 slug 并行"，由钩子在两任务触碰同目录时告警（[Best Practices for Claude Code Subagents](https://www.pubnub.com/blog/best-practices-for-claude-code-sub-agents/)）。若采用 `<agent-name>` 子目录（如 `.claude/agents/<name>/`、`.workbuddy/memory/<agent-name>/`、`.history/<date>-<agent-name>.md`），文件名自带 Agent 标识，从根本上消除"互相覆盖"与"读错他人记忆"。

共享态冲突的反面教材来自 sudocode 的隔离架构：若多个 Agent 共享同一本地数据库（如 `.cache.db`），会因读取同一计数而生成重复主键、互相覆盖提交；改为"每 worktree 一份隔离副本"后冲突消失（[Worktree Isolation](https://deepwiki.com/sudocode-ai/sudocode/7.2-rest-api)）。端口争抢（3000/5432/8080）与 `package.json` 合并冲突，则分别用"按 worktree 序号偏移端口"与"rebase-before-PR"等约定缓解（[Zylos Research](https://zylos.ai/en/research/2026-02-22-git-worktree-parallel-ai-development/)）。

### 3.4 记忆/日志归属：共享只读层 + 个人结构化子目录

上下文管理被业内视为 AI 辅助编码 90% 的功力所在，关键在于分层（[Context Management Is 90% of the Skill](https://fazm.ai/blog/context-management-90-percent-ai-coding-skill)）。其"三层上下文栈"为：第 1 层项目级 `CLAUDE.md`（单一可信源，建议 <200 行，承载架构、约定、已否决方案）；第 2 层目录级 `rules/`；第 3 层会话/历史层（决策日志 `decisions.md`、记忆服务器）。`CLAUDE.md` 可置于仓库根、父目录或用户 home，自动并入系统提示，充当团队与 AI 的"指令单一可信源"，从机制上避免上下文碎片化（[Using CLAUDE.md Files](https://www.claude.com/blog/using-claude-md-files)）。

Anthropic 的研究系统印证了"共享层 + 轻量回传"的范式：Lead Agent 把计划存入外部 Memory 以防上下文截断；subagent 把成果写入外部文件系统、只回传轻量引用，避免所有信息经主 Agent 中转造成丢失（[How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system)）。

**反方与局限**：值得指出，worktree 并非无代价。每次 worktree 是近完整的文件副本（`.git` 共享），一个 2GB 仓库开 5 个 worktree 可达 10GB+，需主动清理以免孤儿积累（[Claude Code Worktrees...](https://dev.to/aijasonz/claude-code-worktrees-file-isolation-for-parallel-agents-57d)）。另一方面，纯粹的"每 Agent 完全隔离"也会带来知识碎片化——这正是旧物理副本方案的代价；因此共享只读层必须承载 SSOT，个人层仅存"推断/日志"，并通过回传摘要收敛，而非各写各的。更有观点提醒：swarming（群体并行）不是"堆更多 Agent"，而是"更多有边界的 Agent"；若任务分解错误，并行只会放大混乱（[Swarming the Codebase](https://blog.heliomedeiros.com/posts/2025-11-23-swarming-with-worktree)）。

## 分析：为什么"结构隔离 + 分层归属"是用户痛点 2 的解药

用户痛点 2 的症结是：旧法用"物理隔离（副本）"解决了记忆覆盖，新法放弃隔离后，多 Agent 同目录时"推断/日志往哪写"成了真空。两种错误极端都不可取——都写 `.workbuddy/memory/` 会并发覆盖；各写各的又会知识碎片化。

遵循上述证据，对用户的具体处方可凝练为：

1. **优先用 worktree 做目录隔离**。若业务允许，每个 Agent 一个 worktree+分支，从结构上杜绝互相覆盖（对应 Claude/Cursor/VS Code/Codex 内置能力）。
2. **必须同目录时，降级为"共享只读层 + 个人结构化子目录"**：
   - **共享层（只读事实源）**：`CLAUDE.md` / SSOT 说明书，所有人只读，承载架构、约定、已否决方案；
   - **个人层（隔离写）**：`.workbuddy/memory/<agent-name>/` 存该 Agent 的推断/日志；`.history/<date>-<agent-name>.md` 存带标识的会话历史。文件名自带 agent 标识，从根本上消除"互相覆盖"与"读错他人记忆"。
3. **收敛碎片化**：各 Agent 写完后回传摘要/决策到共享层（如 `decisions.md` 或任务队列），使知识既归属清晰又可被他人检索，复制 Anthropic "轻量引用回传"的做法。
4. **并发写防护**：用 PreToolUse 钩子封杀危险写（如 `git push --force`、删除他人目录），并在两任务触碰同目录时告警（呼应 PubNub 的 slug 不相交原则）。
5. **语义冲突兜底**：通过显式接口契约 + 变更返回类型时同步更新调用方约定，把"语义冲突"暴露在合并前而非运行时。

## 小结

多 Agent 同目录的并发安全，本质是"隔离"与"归属"两件事：隔离靠 worktree 这一结构性原语，归属靠"共享只读 SSOT + 带 Agent 标识的个人子目录"。前者保证运行期不互相覆盖，后者保证记忆/日志既不覆盖也不碎片化。对用户而言，痛点 2 的具体处方可凝练为一句话——**共享层只读、个人层按 `<agent-name>` 分片子目录写入、写完回传摘要到共享层**，并以 worktree 为首选、同目录降级方案为兜底。
