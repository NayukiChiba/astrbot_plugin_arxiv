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
- **Markdown 适配** — QQ 官方机器人平台自动使用 Markdown 语法（标题、加粗、超链接）发送
- **按图发送** — 论文元信息、摘要与 AI 总结可合并渲染为一张 HTML 卡片图片发送
- **WebUI 控制台** — 插件页面查看已发送论文、下载 PDF、手动获取 / 预览 / 发送论文
- **论文库存储** — 已发送论文与 PDF 可保存到插件数据目录，支持按保留天数自动清理
- **PDF 截图** — PDF 首页截图，DPI 可自由调整
- **PDF 附件** — 可选附带 PDF 文件
- **摘要处理** — 支持原文摘要或 LLM 翻译为中文
- **摘要渲染** — 摘要可渲染为图片发送
- **LLM 总结** — 使用 LLM 扫描 PDF 并总结论文，支持自定义 prompt；识图失败时仅显示“识图失败”

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
| text_format | string | `"auto"` | 文本格式：`auto`（QQ 官方平台自动用 Markdown）/ `plain` / `markdown` |
| send_as_image | bool | `false` | 按图发送：整篇论文渲染为一张卡片图片（依赖 t2i 文转图服务） |
| image_template | string | `"default"` | 卡片模板文件名（不含 .html 后缀），见下方「自定义卡片模板」 |
| send_abstract | bool | `true` | 是否发送摘要 |
| abstract_as_image | bool | `true` | 摘要是否渲染为图片 |
| attach_pdf | bool | `true` | 是否附带 PDF 文件 |
| screenshot_pdf | bool | `true` | 是否截图 PDF 首页 |
| screenshot_dpi | int | `150` | 截图 DPI（72~300） |
| max_pdf_size_mb | int | `20` | PDF 最大体积限制 (MB) |
| history_retention_days | int | `30` | 定时推送去重记录保留天数 |
| store_papers | bool | `true` | 是否保存已发送论文与 PDF 到论文库（WebUI 可查看）；关闭时启动自动清空 |
| paper_retention_days | int | `7` | 论文库保留天数，过期记录与 PDF 自动清理 |

### LLM 赋能配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| abstract_mode | string | `"original"` | `original` 或 `llm_chinese` |
| llm_summarize | bool | `false` | 是否使用 LLM 总结论文 |
| translate_provider_id | string | `""` | 摘要翻译 LLM 提供商（留空使用默认，可用小模型） |
| summarize_provider_id | string | `""` | 论文总结 LLM 提供商（需多模态视觉模型，留空使用默认） |
| llm_summary_prompt | text | `""` | 自定义 LLM 总结 prompt |

## WebUI 控制台

插件注册「ArXiv 控制台」页面，入口在 AstrBot Dashboard 侧边栏的扩展功能区（iframe 嵌入）。

**论文库** 标签页：

- 展示所有已发送论文（发送时间倒序），包括标题、作者、分区、发送时间与目标会话数
- 支持展开摘要、下载已保存的 PDF、跳转 arXiv 详情页
- 顶栏显示存储状态（保留天数、篇数、占用空间），「立即清理」按钮手动清理过期内容

**获取与发送** 标签页：

- 三种获取模式：最新论文（按配置分类）、关键词搜索、按 ID / 链接
- 选中论文后可预览纯文本 / Markdown / 卡片图片三种发送样式
- 可指定目标会话 UMO（自动识别平台类型）、文本格式、按图发送与是否包含摘要，点击「发送到会话」执行完整发送流程（含 PDF 下载与论文库记录）
- 「触发推送」按钮立即执行一次定时推送，便于测试

论文库内容保存在插件数据目录 `data/plugin_data/astrbot_plugin_arxiv/papers/`，由 `store_papers` 与 `paper_retention_days` 配置控制。

## 自定义卡片模板

按图发送（`send_as_image`）使用 HTML + Jinja2 模板渲染论文卡片。插件首次启动时会把内置模板复制到**插件数据目录**下的 `templates/` 文件夹（如 `data/plugin_data/astrbot_plugin_arxiv/templates/`），不会覆盖已有文件：

- 修改 `default.html` 可调整默认卡片样式
- 添加自己的 `.html` 模板文件后，将 `image_template` 配置为文件名（不含后缀）即可启用

模板中可用的 Jinja2 变量：

| 变量 | 说明 |
|------|------|
| `index` | 论文序号（0 表示无序号） |
| `category` | 主分类，如 `cs.AI / 人工智能` |
| `title_html` / `authors_html` | 标题 / 作者（已转义，需配合 `\| safe`） |
| `published` | 提交日期 |
| `tags` | 全部分类标签（单分类时为空） |
| `arxiv_id` / `abs_url` | arXiv ID / 详情页链接 |
| `abstract_html` | 摘要（已转义，可为空） |
| `summary_html` | AI 总结（已转义，可为空） |
| `summary_failure` | 是否识图失败（布尔值） |
| `screenshot_b64` | PDF 首页截图 Base64（可为空） |
| `extra_html` | 额外提示，如 PDF 跳过原因（已转义） |

## 依赖

- `aiohttp` — 异步 HTTP 请求
- `feedparser` — arXiv Atom XML 解析
- `pymupdf` — PDF 文本提取和截图（软依赖，未安装时相关功能自动禁用）
- `Pillow` — 摘要文本渲染为图片（软依赖）

## 许可证

[GPL-3.0](LICENSE)
