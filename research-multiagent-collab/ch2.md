# 第2章：以 AGENTS.md 为 SSOT 的上下文与说明书治理（含 drift 检测）

> 对应痛点1：说明书 / 上下文散落多处且过期。
> 一句话主张：多 agent 同目录治理的第一性原理，是让同一事实只在**一处**被定义（SSOT），别处只引用不手抄；并用 CI / pre-commit 把"文档新鲜度"变成一等公民——因为过期文档比没有文档更糟，agent 会照着它执行。

## 2.1 论点：过期说明书不是"不整洁"，而是给 agent 灌错输入

对人类而言，一份三个月没动的文档只是不便——人会本能地怀疑它、去核对代码。对 AI agent 而言，过期文档是**被信任的错误输入**：它没有"这文档看着可疑"的直觉，会照着执行、照着生成代码。这正是"过期文档比没有文档更糟"的机制性原因——没有文档时 agent 会去读代码探索（慢但正确），有错文档时 agent 会跳过探索直接采信（快但错）。

学术界已把这个现象命名为 **context rot（上下文腐化）**。Treude 与 Baltes（2026）把一个原本用于 README/wiki 一致性检查的工具（DOCER）不加修改地套用到 AI 配置文件上，在 356 个统计代表性仓库样本中发现 **23.0% 的仓库存在失效的代码元素引用**——即 CLAUDE.md / AGENTS.md / .cursorrules 里写的函数、路径、模块在代码里已经不存在了（[Context Rot in AI-Assisted Software Development](https://arxiv.org/html/2606.09090)）。该文同时指出，context rot 远不止"引用失效"一类，还包括架构声明失效、工具使用指引失效、依赖与运行时假设失效、行为约定失效。**在多 agent 同目录场景下，这一风险被结构性放大**：N 个 agent 同时读同一份说明书，一处错误被 N 倍执行，而 agent 之间主要靠文档而非对话交接——这正是用户"仓库被改坏"事故的根因类型之一。

## 2.2 论据一：SSOT 的工程载体是 AGENTS.md，跨工具统一、CLAUDE.md 用 @ 引用桥接

**事实层面。** AGENTS.md 已被超过 6 万个非 fork 开源仓库采用，被 Codex、Cursor、Windsurf、Gemini CLI、Jules、Copilot coding agent、Zed、Aider、Amp、Factory、goose、VS Code 等二十余款工具原生读取；单仓库内支持嵌套，**agent 自动读取目录树中最近的那份，最近者优先**（[AGENTS.md 官方站](https://agents.md/)）。2025 年 12 月，该格式与 Anthropic 的 MCP、Block 的 goose 一并作为三个锚定项目捐赠给 Linux Foundation 旗下新成立的 Agentic AI Foundation（[Linux Foundation 公告](https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation)；[OpenAI 公告](https://openai.com/index/agentic-ai-foundation)）。需要精确限定的是：它目前是**受基金会托管的开放格式，尚无经批准的版本化规范**，"标准"一词更多是方向性描述（[What Is AGENTS.md?](http://llms-txt.io/blog/what-is-agents-md)）。

**桥接事实（关键）。** Claude Code 只读 CLAUDE.md、不读 AGENTS.md。官方推荐做法是在 CLAUDE.md 顶部写一行 `@AGENTS.md` 导入，Claude 专属内容写在其下方；符号链接 `ln -s AGENTS.md CLAUDE.md` 亦可，但在 Windows 需管理员/开发者模式、可能静默失败——有实测对比确认 `@import` 是跨平台更稳的路径（[三工具实测对照](https://techsy.io/en/blog/cursor-rules-vs-claude-md)）。由此可推出一条硬规则：**其他工具的入口文件只做薄壳（一行引用），绝不复制规则正文——复制即埋下 drift**。中文社区的最佳实践也明确："源 + 薄壳"三步法，各工具入口文件对照，但**绝不复制规则文本**（[如何写好 AGENTS.md](https://magicliang.github.io/2026/05/03/%E5%A6%82%E4%BD%95%E5%86%99%E5%A5%BD-agents-md/)）。

**决策沉淀用 ADR。** `decisions.md` / `docs/adr/` 用来沉淀关键决策与踩过的坑。ADR 的成熟做法是：一决策一文件，含 Status / Context / Decision / Consequences；**团队接受后的 ADR 即不可变**，需改变时新写一份并把旧的标记为 Superseded、保留在决策日志中（[AWS Prescriptive Guidance: ADR best practices](https://docs.aws.amazon.com/prescriptive-guidance/latest/architectural-decision-records/best-practices.html)）。其最常见失败模式是"写了但没人读"，根因是把 ADR 放在与代码分离的 wiki 里；放进代码仓库（通常 `docs/adr/`）能显著提高被检索引用的概率（[Architecture Decision Records: How Top Teams Document Decisions](https://sensussoft.com/blog/software-architecture-decision-records)）。对多 agent 场景尤其致命：agent 只会读它能在工作目录里找到的东西，所以 ADR 必须**在仓库内、被 AGENTS.md 显式导航到**，且把坑一并写入，否则不同 agent 会各自重复踩同一个坑。

**实证校准（务必并陈，避免一味鼓吹）。** "写了是否更好"在实证上并不一致：Lulla 等（2026, ICSE 2026 JAWs）在 10 仓库/124 PR 配对实验中测得 AGENTS.md 使**中位运行时间 −28.64%、输出 token −16.58%**（[On the Impact of AGENTS.md](https://arxiv.org/abs/2601.20404)）；但 Gloaguen 等（ETH Zurich & LogicStar.ai, 2026）在 4 款 agent、438 个任务上发现上下文文件**未普遍提升任务成功率、推理成本平均 +20% 以上**，并给出关键细节——**指令类内容被 agent 可靠遵守，"仓库概览"这类被厂商推荐的内容并无帮助**（[Evaluating AGENTS.md](https://arxiv.org/abs/2602.11988)）。第三方跨厂商消融研究给出有界零结论（正确性效应上界 ≤10pp/≤15pp）（[Two-Agent Ablation](https://arxiv.org/html/2607.27250v1)）。这组分歧不否定 SSOT，反而精确界定了它该装什么：**装"删掉就会让 agent 犯错"的可执行约定（命令、边界、禁区、非标准实践），不装项目介绍与架构散文。**

## 2.3 论据二：分层记忆有明确技术边界，"哪些常驻、哪些易失"是可判定的

Claude Code 的记忆机制提供了可对照的分层参考（官方文档事实）：作用域由宽到窄为 **托管策略 → 用户级 `~/.claude/CLAUDE.md` → 项目级 `./CLAUDE.md` → 本地级 `./CLAUDE.local.md`（应 gitignore）**；沿目录树向上**拼接而非覆盖**，越靠近工作目录者越后读、优先级越高；子目录 CLAUDE.md 为**懒加载**。`@path` 导入最大递归深度 **4 跳**，被反引号包裹的路径不解析。`.claude/rules/` 支持 YAML frontmatter 的 `paths` glob 作用域，仅在 agent 触及匹配文件时才载入，从而节省上下文。auto memory 存于 `~/.claude/projects/<project>/memory/`，每会话仅载入 `MEMORY.md` **前 200 行或 25KB**（先到为准）（[Claude Code Memory 官方文档](https://code.claude.com/docs/en/memory)）。

由官方机制可直接推出三态划分：**① 常驻入仓**（AGENTS.md、`.claude/rules/*`、`docs/adr/`——随版本控制、可 review、可 diff）；**② 可再生**（状态类信息应由脚本从 git/测试结果派生，不手写）；**③ 明确易失**（auto memory、MCP 会话记忆——只作加速器，不得成为团队决策的唯一存放地；一旦捕获真实决策，必须迁入版本化文件）。官方文档亦明确指出 CLAUDE.md / AGENTS.md 是以用户消息形式注入的**上下文、不具强制力**——必须硬性保证的事项应写成 hook / CI / 权限规则。

## 2.4 论据三：drift 检测是可工程化的，不靠自觉

已有公开工具链证明"文档 CI"完全可落地。[doc-drift-detector](https://lobehub.com/ar/skills/borghei-claude-skills-doc-drift-detector) 用 AST 解析源码提取函数/类签名并与文档比对（检出签名漂移、参数缺失、已删除功能仍被文档描述），做 Markdown 链接/锚点完整性审计（本地文件是否存在、相对路径 `../` 错误、Linux 下才暴露的大小写问题），并给出 **0–100 的 staleness 评分**（按"最近更新 20% / 代码-文档对齐 30% / 链接健康 15% / 完整性 20% / 准确性 15%"加权），支持非零退出码接入 GitHub Actions 与 pre-commit。同类工具 [docdrift (PyPI)](https://pypi.org/project/docdrift/2.0.0/) 走 pre-commit 路线：**ERROR 级陈旧文档阻断提交，WARNING 级只告警不阻断**（可 `--no-verify` 跳过）。一篇实践文章把根因说得很直白：文档腐烂不是开发者不在意，而是**文档缺少代码那样的反馈回路**——代码有 CI、有 lint、有测试，文档什么都没有（[Why Your Documentation Is Always Stale](https://dev.to/suhteevah/why-your-documentation-is-always-stale-and-how-to-fix-it-with-git-hooks-f7b)）。"过期文档比没有更糟，因为 agent 会信它"——这句实践社区的判断（属观点，[AGENTS.md, explained for teams that actually ship](https://dev.to/arpituppal2rgb/agentsmd-explained-for-teams-that-actually-ship-13c3)）正与 context rot 的量化结论相互印证。

## 2.5 分析：针对本项目现状的处方

用户当前状况：说明书散落于 AGENTS.md / USER.md / SOUL.md / STATUS.md / CHANGELOG_agent_handoff.md / `.workbuddy/memory/` / 一整套 docs/，且 STATUS.md 停在 18 天前、CHANGELOG 停在 80 天前、且把项目路径写成已冻结的备份目录——后者是 context rot 中 **referential rot（引用腐化）的教科书案例**，也是最危险的一类：agent 会照着那个错误路径去读写文件。处方如下：

1. **单源收口。** AGENTS.md 是唯一源，承载：项目一句话定位、五条精确命令（安装 / 测试含过滤参数 / lint / typecheck / 构建）、"完成"的定义、10 条以内架构地图、禁区清单（kill list）、以及指向 decisions.md 与 docs/ 的导航链接。USER.md / SOUL.md 内容要么并入 AGENTS.md 对应小节，要么保留为独立文件但**只由 AGENTS.md 用 `@` 引用**，绝不手抄。CLAUDE.md 只保留一行 `@AGENTS.md`（Windows 安全）。控制在 200–300 行以内。
2. **状态类文档一律不手写。** STATUS.md 与 CHANGELOG 之所以过期 18 天 / 80 天，根因是"手写状态"设计本身不可持续。改为由脚本从 `git log`、最近提交、测试结果派生生成（生成物入库但标注 generated，禁止手改），或**直接删除**——删掉比留着一份错的更安全。**任何绝对路径不写进文档**，改用相对路径或由脚本注入，从机制上杜绝"路径指向已冻结备份目录"这类事故复发。
3. **决策与坑点进 ADR。** `docs/adr/NNN-<slug>.md`，已接受者不可改、只能由新 ADR superseded；`decisions.md` 作索引与坑点清单。
4. **三层 freshness 守卫**（回应"文档缺反馈回路"这一根因）：
   - **pre-commit**：解析 AGENTS.md 中所有反引号命令与文件路径，校验"文件真实存在 / 命令可干跑"，失败即阻断提交；
   - **CI**：Markdown 链接/锚点完整性 + 状态类文件 mtime 超阈值告警 + "改了 `src/` 却未同步 rules/ADR"的提示式告警（预防式，不阻断）；
   - **Definition of Done 入 AGENTS.md**：agent 完成任务前必须同步 SSOT；但这是"提示"而非"强制"——硬约束须落到 hook / CI / 权限（见第 4 章权限沙箱）。

## 2.6 小结

第一，context rot 是已被量化的真实工程风险（356 仓库样本中 23.0% 存在失效引用），在多 agent 同目录下被 N 倍放大。第二，AGENTS.md 已具备成为 SSOT 的生态基础（6 万+ 仓库、20+ 工具、Linux Foundation 托管），但实证收益集中在**效率与约定遵守**（运行时间 −28.64%、输出 token −16.58%），对**正确性无普遍提升且抬高约 20% 成本**——故 SSOT 应精简到"可执行约定"，砍掉仓库概览类内容。第三，"哪些常驻、哪些易失"可依官方记忆机制清晰划分，跨工具入口一律做薄壳引用而非复制（复制即 drift）。第四，也是对本项目最关键的一条：**过期不是纪律问题而是缺回路问题**，唯一可持续的解法是把 freshness 变成 CI / pre-commit 的一等公民，并把状态类文档从"手写"改为"派生或删除"。

---

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
