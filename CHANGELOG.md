# Changelog

## v1.0.1 (2026-03-22)

### 新功能

- **论文精确获取** — 新增 `/arxiv get <arxiv_id>` 指令，通过 arXiv ID（如 `2501.12345`）直接获取单篇论文的完整内容，包含摘要、PDF 截图及 LLM 总结

### 改进

- **搜索结果轻量化** — `/arxiv search` 不再下载 PDF，仅返回论文标题、作者、摘要、链接等基本信息，并在每条结果末尾提示使用 `get` 指令获取完整内容，大幅加快搜索响应速度
- **搜索数量可控** — `/arxiv search` 支持在关键词末尾附加数量参数（1~20），如 `/arxiv search diffusion model 3`，不填则使用配置中的默认值

### 修复

- 将各模块中违规使用的 `import logging` / `logging.getLogger("astrbot")` 全部替换为框架提供的 `from astrbot.api import logger`
- 移除 `main.py` 中 `event_filter` 别名，改为直接从 `astrbot.api.event` 导入 `filter`

## v1.0.0 (2026-03-20)

首个正式版本发布。

### 功能

- **论文搜索** — `/arxiv search <关键词>` 搜索 arXiv 论文，支持多词查询
- **最新论文** — `/arxiv latest` 获取已配置分类下的最新论文
- **帮助信息** — `/arxiv help` 显示所有可用指令
- **学科分类** — `/arxiv categories` 列出所有支持的 arXiv 学科分类
- **插件状态** — `/arxiv status` 查看当前配置和状态
- **定时推送** — 每日定时推送最新论文，支持自定义推送时间和时区
- **推送会话管理** — `/arxiv add_session` 和 `/arxiv remove_session` 快捷添加/移除推送目标
- **定时推送去重** — 自动记录已发送论文，定时推送不重复发送
- **摘要处理** — 支持原文摘要或使用 LLM 翻译为中文
- **摘要渲染为图片** — 摘要可渲染为长图片发送，通过 `abstract_as_image` 配置切换图片/文本模式
- **PDF 首页截图** — 使用 PyMuPDF 渲染 PDF 首页为 PNG 图片，DPI 可调
- **PDF 附件** — 可选附带 PDF 文件
- **LLM 总结** — 使用 LLM 扫描 PDF 并生成中文总结，支持自定义 prompt
- **LLM 提供商自动回退** — 未配置 LLM 提供商时自动使用当前对话的默认 LLM
- **合并转发模式** — 支持合并转发和逐条发送两种模式（合并转发需平台支持）
- **强制关闭 t2i** — 插件响应强制关闭系统文本转图片，避免重复渲染

### 消息发送顺序

每篇论文按以下顺序发送独立消息：

1. 📚 论文信息（分区 / 标题 / 作者 / 提交时间 / 详情链接）
2. 📝 摘要（图片或文本）
3. 🖼️ PDF 首页截图
4. 📎 PDF 文件
5. 🤖 AI 总结

### 配置

- 三大配置组：ArXiv 论文配置、发送配置、LLM 赋能配置
- 所有配置项均可通过 AstrBot WebUI 插件管理面板修改
- 详见 [README.md](README.md)

### 依赖

- `aiohttp` — 异步 HTTP 请求
- `feedparser` — arXiv Atom XML 解析
- `pymupdf` — PDF 文本提取和截图（软依赖）
- `Pillow` — 摘要文本渲染为图片（软依赖）
