# astrbot_plugin_arxiv

ArXiv 论文搜索与定时推送插件，适用于 [AstrBot](https://github.com/AstrBotDevs/AstrBot)。

## 功能特性

- **学科分类筛选** — 支持选择 arXiv 学科分类（cs.AI、cs.LG、math.CO 等）
- **关键词标签** — 支持自定义关键词标签进行模糊匹配
- **论文搜索** — `/arxiv search <关键词>` 搜索论文
- **最新论文** — `/arxiv latest` 获取配置分类下的最新论文
- **定时推送** — 通过 cron 表达式定时推送（默认每天 9:00 上海时间）
- **目标会话** — 支持 UMO 会话列表，可通过指令快捷添加/移除
- **发送模式** — 支持合并转发和逐条发送两种模式
- **PDF 附件** — 可选附带 PDF 文件
- **PDF 截图** — PDF 首页截图，DPI 可自由调整
- **体积限制** — 可配置 PDF 最大处理体积
- **超时控制** — 可配置 HTTP 请求超时时间
- **摘要处理** — 支持原文摘要或 LLM 翻译为中文
- **LLM 总结** — 使用 LLM 扫描 PDF 并总结论文，支持自定义 prompt
- **去重推送** — 自动记录已发送论文，避免重复推送

## 指令列表

| 指令 | 说明 |
|------|------|
| `/arxiv search <关键词>` | 搜索 arXiv 论文 |
| `/arxiv latest` | 获取已配置分类的最新论文 |
| `/arxiv categories` | 列出所有支持的学科分类 |
| `/arxiv status` | 查看插件配置和状态 |
| `/arxiv add_session` | 将当前会话添加为定时推送目标 |
| `/arxiv remove_session` | 将当前会话从推送目标中移除 |
| `/arxiv push_now` | 立即向当前会话推送最新论文（去重） |

## 配置项

所有配置项都可以在 AstrBot WebUI 的插件管理面板中修改：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| categories | list | `["cs.AI"]` | arXiv 学科分类代码列表 |
| tags | list | `[]` | 关键词标签 |
| max_results | int | `5` | 每次推送的最大论文数 |
| cron_expression | str | `"0 9 * * *"` | 定时表达式 |
| cron_timezone | str | `"Asia/Shanghai"` | 时区 |
| target_sessions | list | `[]` | 目标 UMO 会话列表 |
| send_mode | str | `"forward"` | `forward` 或 `individual` |
| attach_pdf | bool | `false` | 是否附带 PDF 文件 |
| screenshot_pdf | bool | `true` | 是否截图 PDF 首页 |
| screenshot_dpi | int | `150` | 截图 DPI（72~300） |
| max_pdf_size_mb | int | `20` | PDF 最大体积限制 (MB) |
| timeout_seconds | int | `30` | HTTP 请求超时 (秒) |
| send_abstract | bool | `true` | 是否发送摘要 |
| abstract_mode | str | `"original"` | `original` 或 `llm_chinese` |
| llm_summarize | bool | `false` | 是否使用 LLM 总结论文 |
| llm_provider_id | str | `""` | LLM 提供商 ID |
| llm_summary_prompt | str | `""` | 自定义 LLM 总结 prompt |
| bot_name | str | `"ArXiv Bot"` | 合并转发中的机器人名称 |
| history_retention_days | int | `30` | 已发送记录保留天数 |

## 依赖

- `aiohttp` — 异步 HTTP 请求
- `feedparser` — arXiv Atom XML 解析
- `pymupdf` — PDF 文本提取和截图（软依赖，未安装时相关功能自动禁用）

## 许可证

[GPL-3.0](LICENSE)
