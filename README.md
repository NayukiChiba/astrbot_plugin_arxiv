# astrbot_plugin_arxiv

ArXiv 论文搜索与定时推送插件，适用于 [AstrBot](https://github.com/AstrBotDevs/AstrBot)。

## 功能特性

- **论文搜索** — `/arxiv search <关键词> [数量]` 快速搜索论文，仅返回基本信息，不下载 PDF
- **精确获取** — `/arxiv get <arxiv_id|url>` 通过 ID 或链接获取单篇论文完整内容（摘要、PDF 截图、LLM 总结）
- **最新论文** — `/arxiv latest` 获取配置分类下的最新论文（含完整内容）
- **定时推送** — 每日定时推送，自动去重已发送论文
- **学科分类筛选** — 支持选择 arXiv 学科分类（cs.AI、cs.LG、math.CO 等）
- **关键词标签** — 支持自定义关键词标签进行模糊匹配
- **目标会话** — 支持 UMO 会话列表，可通过指令快捷添加/移除
- **发送模式** — 支持合并转发和逐条发送两种模式
- **PDF 截图** — PDF 首页截图，DPI 可自由调整
- **PDF 附件** — 可选附带 PDF 文件
- **摘要处理** — 支持原文摘要或 LLM 翻译为中文
- **摘要渲染** — 摘要可渲染为图片发送
- **LLM 总结** — 使用 LLM 扫描 PDF 并总结论文，支持自定义 prompt

## 指令列表

| 指令 | 说明 |
|------|------|
| `/arxiv help` | 显示帮助信息 |
| `/arxiv search <关键词> [数量]` | 搜索 arXiv 论文（仅显示信息，数量可选，默认取配置值，最多 20） |
| `/arxiv get <arxiv_id or url>` | 通过 arXiv ID 或链接获取单篇论文完整内容（含 PDF 截图/LLM 总结） |
| `/arxiv latest` | 获取已配置分类的最新论文 |
| `/arxiv categories` | 列出所有支持的学科分类 |
| `/arxiv status` | 查看插件配置和状态 |
| `/arxiv add_session` | 将当前会话添加为定时推送目标 |
| `/arxiv remove_session` | 将当前会话从推送目标中移除 |

> **提示：** 合并转发模式需平台支持（如 QQ），WebUI 内置聊天不支持显示合并转发消息，请关闭 `use_forward` 选项。

## 使用示例

```
# 搜索论文（快速，不下载 PDF）
/arxiv search diffusion model
/arxiv search attention transformer 5

# 通过 ID 获取论文完整内容（含 PDF 截图）
/arxiv get 2501.12345

# 获取最新论文
/arxiv latest
```

## PDF 镜像站

插件在启动时并发测速所有配置的镜像站，自动选择最快的节点下载 PDF。默认使用官方站和中科院镜像：

- `https://arxiv.org` — arXiv 官方
- `https://cn.arxiv.org` — 中科院镜像（国内推荐）

可在 WebUI 的 `pdf_mirrors` 配置项中自定义镜像列表。下载失败时会在消息中显示跳过原因（超限/网络错误）。

## 配置说明

所有配置项都可以在 AstrBot WebUI 的插件管理面板中修改，分为三大组：

### ArXiv 论文配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| categories | list | `["cs.AI"]` | arXiv 学科分类代码列表 |
| tags | list | `[]` | 关键词标签（模糊匹配） |
| max_results | int | `1` | 每次推送/搜索的默认最大论文数 |
| pdf_mirrors | list | `["https://arxiv.org", "https://cn.arxiv.org"]` | PDF 下载镜像站列表，启动时自动测速选最快 |
| timeout_seconds | int | `30` | HTTP 请求超时 (秒) |

### 发送配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| push_time | string | `"09:00"` | 每日推送时间（HH:MM） |
| push_timezone | string | `"Asia/Shanghai"` | 时区 |
| target_sessions | list | `[]` | 目标 UMO 会话列表 |
| use_forward | bool | `false` | 是否使用合并转发 |
| bot_name | string | `"ArXiv Bot"` | 合并转发中的机器人名称 |
| send_abstract | bool | `true` | 是否发送摘要 |
| abstract_as_image | bool | `true` | 摘要是否渲染为图片 |
| attach_pdf | bool | `true` | 是否附带 PDF 文件 |
| screenshot_pdf | bool | `true` | 是否截图 PDF 首页 |
| screenshot_dpi | int | `150` | 截图 DPI（72~300） |
| max_pdf_size_mb | int | `20` | PDF 最大体积限制 (MB) |
| history_retention_days | int | `30` | 定时推送去重记录保留天数 |

### LLM 赋能配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| abstract_mode | string | `"original"` | `original` 或 `llm_chinese` |
| llm_summarize | bool | `false` | 是否使用 LLM 总结论文 |
| translate_provider_id | string | `""` | 摘要翻译 LLM 提供商（留空使用默认，可用小模型） |
| summarize_provider_id | string | `""` | 论文总结 LLM 提供商（需多模态视觉模型，留空使用默认） |
| llm_summary_prompt | text | `""` | 自定义 LLM 总结 prompt |

## 依赖

- `aiohttp` — 异步 HTTP 请求
- `feedparser` — arXiv Atom XML 解析
- `pymupdf` — PDF 文本提取和截图（软依赖，未安装时相关功能自动禁用）
- `Pillow` — 摘要文本渲染为图片（软依赖）

## 许可证

[GPL-3.0](LICENSE)
