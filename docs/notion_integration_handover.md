# Notion 数据库接入工作进展交接文档 (Notion Integration Handover)

## 1. 目标回顾
将用户在 Notion 中记录的结构化 PA 交易复盘日志（包括结构化字段、长文总结、以及截图附件）同步并集成到 Brooks-AI 系统本地的 `trade_reviews` 数据库中，以便为后续策略演进（Phase 2）提供人类感知和真实的复盘数据支持。

## 2. 已完成的工作 (Completed)

系统已成功实现并封装了通过 Notion API 全量拉取复盘数据的脚本：**`tools/sync_notion_reviews.py`**。
具体实现功能如下：

1.  **数据库结构探测**：实现了对 Notion Database schema 的嗅探(`--discover`模式)，可提取所有列属性及其类型，方便了解外部数据库结构。
2.  **全量属性解析**：`extract_property_value` 支持对各种复杂的 Notion 属性（Rich Text, Select, Multi-select, Formula, Rollup, Date, Checkbox 等）进行扁平化和字符串/数值提取。
3.  **富文本页面与版块提取**：提取每条复盘记录的内容页（Page blocks），包括文本段落、标题、列表项、引用，并整理为通用的 Markdown 格式文本。
4.  **附件（截图）下载**：提取页面内的图片链接，并支持自动下载至本地 `data/review_attachments/` 目录下（按照交易ID或Page ID分子目录）。
5.  **桥接 SQLite 数据库**：
    *   将从 Notion 提取的属性做中文映射匹配（如 `标的` -> `code`, `方向` -> `direction`, 等等）。
    *   富文本页面总结与本地图片路径拼接后统一存入 `notes` 字段。
    *   调用现有的 `core/review_bridge.py` 里的 `add_review` 函数无缝插入本地的 `trade_reviews` 表中。
6.  **JSON 本地存档**：每次同步会另外生出包含全部未过滤原始提取结果的完整 JSON 存档本 (`data/notion_pa_journal.json`)。

## 3. 使用说明与环境变量配置

要运行此脚本，需要在工程根目录的 **`.env`** 文件中配置以下两个环境变量：
```env
NOTION_TOKEN=secret_xxxxxxxxxxxxxxxxxxxxxxxxxxx
NOTION_DB_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

**命令行使用方法:**
*   仅探测数据库结构：`python tools/sync_notion_reviews.py --discover`
*   全量同步、拉取文本并下载截图写入数据库（默认）：`python tools/sync_notion_reviews.py`
*   纯拉属性表，不拉详细内容，不下载截图：`python tools/sync_notion_reviews.py --no-content --no-images`

## 4. 后续需继续完善的任务 (To-Do for Next AI)

下个接手的 AI 请注意以下待办事宜：

1.  **核对字段映射关系**：脚本中目前的字段映射是基于预期的中文字段名称设置的（如：`Code` 取 `标的` 或 `code`）。需与用户真实的 Notion Database 表头（通过 `--discover` 运行结果）核实，如果因用户 Notion 列名不同导致遗漏，需更新 `sync_notion_reviews.py` 的 `save_to_review_db` 函数中的映射逻辑。
2.  **Hunter/Dashboard 集成**：目前仅是一个独立的 tools 脚本，可考虑将 Notion 同步触发集成到 `hunter.py` 的主菜单或是数据更新流程中以便于自动化维护。
3.  **安全浮点数转换等异常处理**：已实装了 `safe_float` 进行带有千分位符等畸形字符串的处理，可当遇到新脏数据导致插入数据库失败时，重点排查 `save_to_review_db` 的类型转换环节。对于解析的容错处理可根据实际全量运行报错日志再去细分增强。
4.  **前端页面展示对接**：确认后续是否要在 Dashboard/GUI 中调出 SQLite 的 `notes` 字段，并在本地直接渲染那些存入本地的配套截图 `data/review_attachments/...`。

## 5. 核心关联文件
- **脚本入口**: `tools/sync_notion_reviews.py` (本次新增)
- **数据库中间层**: `core/review_bridge.py` (负责最终处理和录入 trade_reviews 表)
