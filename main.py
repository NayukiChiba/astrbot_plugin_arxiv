"""AstrBot ArXiv 论文推送插件。

支持定时推送、搜索、LLM 摘要翻译、PDF 处理、合并转发等。
"""

from __future__ import annotations

from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.message.message_event_result import MessageEventResult
from astrbot.core.star.filter.command import GreedyStr

from . import arxiv_client, formatter, llm_service, pdf_handler, text_render
from .arxiv_client import extractArxivId
from .history import SentHistory

def _time_to_cron(time_str: str) -> str:
    """将 HH:MM 格式的时间字符串转换为 cron 表达式。

    例如 '09:00' -> '0 9 * * *', '14:30' -> '30 14 * * *'
    """
    try:
        parts = time_str.strip().split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return f"{minute} {hour} * * *"
    except (ValueError, IndexError):
        logger.warning("无法解析推送时间 '%s'，使用默认 09:00。", time_str)
        return "0 9 * * *"


@register(
    "astrbot_plugin_arxiv",
    "NayukiChiba",
    "ArXiv 论文搜索与定时推送插件",
    "1.0.2",
)
class ArxivPlugin(Star):
    """ArXiv 论文推送插件主类。"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self._data_dir: Path = Path()
        self._temp_dir: Path = Path()
        self._history: SentHistory | None = None
        self._cron_job_id: str = ""
        self._bestMirror: str = ""

    # ── 便捷配置访问 ──────────────────────────────────────────

    @property
    def _arxiv_cfg(self) -> dict:
        """获取 arXiv 论文配置。"""
        return self.config.get("arxiv_config", {})

    @property
    def _send_cfg(self) -> dict:
        """获取发送配置。"""
        return self.config.get("send_config", {})

    @property
    def _llm_cfg(self) -> dict:
        """获取 LLM 赋能配置。"""
        return self.config.get("llm_config", {})

    # ── 生命周期 ──────────────────────────────────────────────

    async def initialize(self) -> None:
        """插件初始化：加载历史、设置定时任务、测速选择最优镜像。"""
        # 初始化数据目录
        self._data_dir = StarTools.get_data_dir("astrbot_plugin_arxiv")
        self._temp_dir = self._data_dir / "temp"
        self._temp_dir.mkdir(parents=True, exist_ok=True)

        # 初始化已发送历史
        retention = self._send_cfg.get("history_retention_days", 30)
        self._history = SentHistory(self._data_dir, retention_days=retention)

        # 清理过期记录
        removed = self._history.cleanup_old()
        if removed > 0:
            logger.info("已清理 %d 条过期的论文发送记录。", removed)

        # 测速选择最优 PDF 镜像站
        mirrors = self._arxiv_cfg.get("pdf_mirrors", ["https://arxiv.org"])
        logger.info("正在测速 %d 个 PDF 镜像站...", len(mirrors))
        self._bestMirror = await pdf_handler.pickBestMirror(mirrors)

        # 注册定时任务
        await self._register_cron_job()

        logger.info("ArXiv 论文推送插件已初始化。")

    async def _register_cron_job(self) -> None:
        """注册定时推送任务。"""
        push_time = self._send_cfg.get("push_time", "09:00")
        timezone = self._send_cfg.get("push_timezone", "Asia/Shanghai")
        cron_expr = _time_to_cron(push_time)

        try:
            job = await self.context.cron_manager.add_basic_job(
                name="arxiv_daily_push",
                cron_expression=cron_expr,
                handler=self._scheduled_push,
                description=f"ArXiv 每日论文推送 ({push_time})",
                timezone=timezone,
                enabled=True,
                persistent=False,
            )
            self._cron_job_id = job.job_id
            logger.info(
                "ArXiv 定时推送已注册: %s (%s)",
                push_time,
                timezone,
            )
        except Exception:
            logger.exception("注册 ArXiv 定时任务失败。")

    # ── 定时推送逻辑 ─────────────────────────────────────────────

    async def _scheduled_push(self) -> None:
        """定时推送入口：获取最新论文并发送到所有目标会话。"""
        target_sessions: list[str] = self._send_cfg.get("target_sessions", [])
        if not target_sessions:
            logger.info("ArXiv 定时推送：未配置目标会话，跳过。")
            return

        categories = self._arxiv_cfg.get("categories", ["cs.AI"])
        tags = self._arxiv_cfg.get("tags", [])
        max_results = self._arxiv_cfg.get("max_results", 5)
        timeout = self._arxiv_cfg.get("timeout_seconds", 30)

        try:
            papers = await arxiv_client.get_latest_papers(
                categories=categories,
                tags=tags,
                max_results=max_results,
                timeout=timeout,
            )
        except Exception:
            logger.exception("ArXiv 定时推送：获取论文失败。")
            return

        # 对每个目标会话发送
        for session in target_sessions:
            await self._send_papers_to_session(session, papers)

    async def _send_papers_to_session(
        self,
        session: str,
        papers: list[arxiv_client.ArxivPaper],
    ) -> None:
        """向指定会话发送论文（定时推送，自动去重）。"""
        if not self._history:
            return

        # 过滤已发送的论文
        unsent_papers = [
            p for p in papers if not self._history.is_sent(session, p.arxiv_id)
        ]

        if not unsent_papers:
            logger.info("ArXiv 推送至 %s：无新论文。", session)
            return

        # 处理每篇论文
        chains = await self._process_papers(unsent_papers)

        if not chains:
            return

        # 根据配置的模式发送
        use_forward = self._send_cfg.get("use_forward", True)
        try:
            if use_forward:
                bot_name = self._send_cfg.get("bot_name", "ArXiv Bot")
                msg = formatter.build_forward_nodes(
                    chains,
                    bot_name=bot_name,
                )
                await self.context.send_message(session, msg)
            else:
                for chain in chains:
                    await self.context.send_message(session, chain)
        except Exception:
            logger.exception("ArXiv 推送至 %s 失败。", session)
            return

        # 标记为已发送
        self._history.mark_sent_batch(
            session,
            [p.arxiv_id for p in unsent_papers],
        )
        logger.info(
            "ArXiv 推送至 %s：成功发送 %d 篇论文。",
            session,
            len(unsent_papers),
        )

    @staticmethod
    def _make_result(chain: MessageChain) -> MessageEventResult:
        """创建一个强制关闭 t2i 的 MessageEventResult。"""
        mer = MessageEventResult()
        mer.chain = chain.chain
        mer.use_t2i_ = False
        return mer

    async def _process_papers(
        self,
        papers: list[arxiv_client.ArxivPaper],
    ) -> list[MessageChain]:
        """处理论文列表，生成消息链。"""
        logger.info("开始处理 %d 篇论文。", len(papers))
        chains: list[MessageChain] = []

        for i, paper in enumerate(papers, 1):
            try:
                logger.info("处理论文 [%d/%d]: %s", i, len(papers), paper.title)
                paper_chains = await self._process_single_paper(paper, index=i)
                chains.extend(paper_chains)
                logger.info(
                    "论文 %s 处理完成，生成 %d 条消息。",
                    paper.arxiv_id,
                    len(paper_chains),
                )
            except Exception:
                logger.exception("处理论文 %s 失败，跳过。", paper.arxiv_id)

        logger.info("论文处理完毕，成功生成 %d 条消息。", len(chains))
        return chains

    async def _process_single_paper(
        self,
        paper: arxiv_client.ArxivPaper,
        *,
        index: int = 0,
    ) -> list[MessageChain]:
        """处理单篇论文：翻译摘要、下载 PDF、截图、总结、渲染摘要图片。"""
        logger.info("[%s] 开始处理: %s", paper.arxiv_id, paper.title)
        abstract_text = ""
        summary_text = ""
        screenshot_path = ""
        pdf_path_str = ""

        timeout = self._arxiv_cfg.get("timeout_seconds", 30)
        max_pdf_size = self._send_cfg.get("max_pdf_size_mb", 20)

        # 摘要处理
        if self._send_cfg.get("send_abstract", True):
            abstract_mode = self._llm_cfg.get("abstract_mode", "original")
            if abstract_mode == "llm_chinese" and paper.abstract:
                provider_id = self._llm_cfg.get("translate_provider_id", "")
                logger.info("[%s] 使用 LLM 翻译摘要...", paper.arxiv_id)
                abstract_text = await llm_service.translate_abstract(
                    self.context,
                    paper.abstract,
                    provider_id=provider_id,
                )
                logger.info("[%s] 摘要翻译完成。", paper.arxiv_id)
            else:
                abstract_text = paper.abstract

        # 是否需要下载 PDF（截图、附件、LLM 总结都需要 PDF）
        need_pdf = (
            self._send_cfg.get("screenshot_pdf", True)
            or self._send_cfg.get("attach_pdf", False)
            or self._llm_cfg.get("llm_summarize", False)
        )

        downloaded_pdf: Path | None = None
        pdf_skip_reason: str = ""
        if need_pdf and paper.pdf_url:
            logger.info("[%s] 下载 PDF: %s", paper.arxiv_id, paper.pdf_url)
            try:
                downloaded_pdf = await pdf_handler.download_pdf(
                    paper.pdf_url,
                    self._temp_dir,
                    timeout=timeout,
                    max_size_mb=max_pdf_size,
                    best_mirror=self._bestMirror,
                )
            except pdf_handler.PdfSizeExceededError as e:
                logger.warning("[%s] %s", paper.arxiv_id, e)
                pdf_skip_reason = f"⚠️ PDF 大小超出限制（{max_pdf_size} MB），跳过下载。"
            if downloaded_pdf:
                logger.info("[%s] PDF 下载成功: %s", paper.arxiv_id, downloaded_pdf)
            elif not pdf_skip_reason:
                logger.warning("[%s] PDF 下载失败。", paper.arxiv_id)
                pdf_skip_reason = "⚠️ PDF 下载失败（网络超时或服务器错误），已跳过。"

        # PDF 首页截图（screenshot_pdf 或 LLM 总结开启时需要）
        need_screenshot = (
            self._send_cfg.get("screenshot_pdf", True)
            or self._llm_cfg.get("llm_summarize", False)
        )
        if downloaded_pdf and need_screenshot:
            dpi = self._send_cfg.get("screenshot_dpi", 150)
            screenshot = pdf_handler.screenshot_first_page(
                downloaded_pdf,
                self._temp_dir,
                dpi=dpi,
            )
            if screenshot:
                screenshot_path = str(screenshot)
                logger.info("[%s] PDF 首页截图成功。", paper.arxiv_id)
            else:
                logger.warning("[%s] PDF 首页截图失败。", paper.arxiv_id)

        # LLM 总结（优先使用视觉模型 + PDF 截图）
        if downloaded_pdf and self._llm_cfg.get("llm_summarize", False):
            provider_id = self._llm_cfg.get("summarize_provider_id", "")
            custom_prompt = self._llm_cfg.get("llm_summary_prompt", "")

            if screenshot_path:
                summary_text = await llm_service.summarize_paper_vision(
                    self.context,
                    screenshot_path,
                    provider_id=provider_id,
                    custom_prompt=custom_prompt,
                )
            else:
                # 截图失败时回退文本模式
                pdf_text = pdf_handler.extract_text(downloaded_pdf)
                if pdf_text:
                    summary_text = await llm_service.summarize_paper(
                        self.context,
                        pdf_text,
                        provider_id=provider_id,
                        custom_prompt=custom_prompt,
                    )

        # 摘要渲染为图片（或文本）
        abstract_image_path = ""
        use_abstract_image = self._send_cfg.get("abstract_as_image", True)
        send_abstract = self._send_cfg.get("send_abstract", True)

        if send_abstract and abstract_text and use_abstract_image:
            logger.info("[%s] 渲染摘要为图片...", paper.arxiv_id)
            img_name = f"abstract_{paper.arxiv_id.replace('/', '_')}.png"
            img_path = self._temp_dir / img_name
            rendered = text_render.render_abstract_image(
                abstract_text,
                img_path,
            )
            if rendered:
                abstract_image_path = str(rendered)
                logger.info("[%s] 摘要图片渲染成功: %s", paper.arxiv_id, rendered)
            else:
                logger.warning(
                    "[%s] 摘要图片渲染失败，将以文本形式发送。", paper.arxiv_id
                )

        # PDF 附件
        if downloaded_pdf and self._send_cfg.get("attach_pdf", False):
            pdf_path_str = str(downloaded_pdf)

        return formatter.build_paper_chains(
            paper,
            index=index,
            show_abstract=send_abstract,
            abstract_text=abstract_text,
            summary_text=summary_text,
            screenshot_path=screenshot_path,
            pdf_path=pdf_path_str,
            abstract_image_path=abstract_image_path,
            extra_message=pdf_skip_reason,
        )

    def _build_info_chains(
        self,
        papers: list[arxiv_client.ArxivPaper],
    ) -> list[MessageChain]:
        """为 search/latest 构建消息链列表，支持摘要渲染为图片。

        每条论文生成 1~2 条消息：
        1. 基本信息（标题、作者、链接等）
        2. 摘要图片（如果 abstract_as_image 开启）
        """
        chains: list[MessageChain] = []
        send_abstract = self._send_cfg.get("send_abstract", True)
        abstract_as_image = self._send_cfg.get("abstract_as_image", True)

        for i, paper in enumerate(papers, 1):
            # 基本信息（摘要是否内嵌取决于是否以图片形式发送）
            show_in_text = send_abstract and not abstract_as_image
            info_chain = MessageChain()
            text = formatter.format_paper_text(
                paper,
                index=i,
                show_abstract=show_in_text,
                abstract_text=paper.abstract,
            )
            text += f"\n\n💡 使用 /arxiv get {paper.arxiv_id} 获取完整内容（含 PDF）"
            info_chain.chain.append(Plain(text))
            chains.append(info_chain)

            # 摘要渲染为图片
            if send_abstract and abstract_as_image and paper.abstract:
                img_name = f"abstract_{paper.arxiv_id.replace('/', '_')}.png"
                img_path = self._temp_dir / img_name
                rendered = text_render.render_abstract_image(paper.abstract, img_path)
                if rendered:
                    img_chain = MessageChain()
                    img_chain.chain.append(Image.fromFileSystem(str(rendered)))
                    chains.append(img_chain)
                else:
                    # 渲染失败时回退为文本
                    fallback_chain = MessageChain()
                    fallback_chain.chain.append(
                        Plain(f"📝 摘要:\n{paper.abstract}")
                    )
                    chains.append(fallback_chain)

        return chains

    # ── 指令处理 ──────────────────────────────────────────────

    @filter.command_group("arxiv")
    def arxiv_group(self):
        """ArXiv 论文相关指令组。"""

    @arxiv_group.command("help")
    async def cmd_help(self, event: AstrMessageEvent):
        """显示帮助信息。"""
        lines = [
            "📖 ArXiv 插件帮助",
            "",
            "可用指令：",
            "  /arxiv help — 显示本帮助信息",
            "  /arxiv search <关键词> [数量] — 搜索论文（仅显示摘要信息，数量默认取配置值）",
            "  /arxiv get <arxiv_id|url> — 获取指定论文完整内容（含 PDF/截图），支持 ID 或链接",
            "  /arxiv latest — 获取最新论文（按配置的分类）",
            "  /arxiv categories — 列出所有支持的学科分类",
            "  /arxiv status — 查看插件当前配置和状态",
            "  /arxiv add_session — 将当前会话加入定时推送列表",
            "  /arxiv remove_session — 将当前会话移出推送列表",
        ]
        yield event.plain_result("\n".join(lines))

    @arxiv_group.command("search")
    async def cmd_search(
        self, event: AstrMessageEvent, query: GreedyStr = GreedyStr("")
    ):
        """搜索 arXiv 论文（仅显示信息，不下载 PDF）。用法: /arxiv search <关键词> [数量]"""
        if not query.strip():
            yield event.plain_result(
                "❌ 请提供搜索关键词。用法: /arxiv search <关键词> [数量]\n"
                "例如: /arxiv search attention mechanism 3"
            )
            return

        query = query.strip()

        # 从末尾解析可选的结果数量参数，如 "attention mechanism 3"
        default_max = self._arxiv_cfg.get("max_results", 5)
        max_results = default_max
        parts = query.rsplit(maxsplit=1)
        if len(parts) == 2 and parts[-1].isdigit():
            num = int(parts[-1])
            if 1 <= num <= 20:
                max_results = num
                query = parts[0]

        logger.info("收到搜索请求: '%s'，数量: %d", query, max_results)

        timeout = self._arxiv_cfg.get("timeout_seconds", 30)

        try:
            papers = await arxiv_client.search_papers(
                query,
                max_results=max_results,
                timeout=timeout,
            )
        except TimeoutError:
            logger.exception("ArXiv 搜索 '%s' 超时。", query)
            yield event.plain_result("❌ 请求超时，请检查网络连接后重试。")
            return
        except Exception:
            logger.exception("ArXiv 搜索 '%s' 失败。", query)
            yield event.plain_result("❌ 搜索失败，请稍后重试。")
            return

        logger.info("搜索 '%s' 返回 %d 篇论文。", query, len(papers))

        if not papers:
            yield event.plain_result("📭 未找到匹配的论文。")
            return

        # search 只推信息，不走 PDF 流程
        chains = self._build_info_chains(papers)

        use_forward = self._send_cfg.get("use_forward", True)
        if use_forward:
            bot_name = self._send_cfg.get("bot_name", "ArXiv Bot")
            msg = formatter.build_forward_nodes(chains, bot_name=bot_name)
            yield self._make_result(msg)
        else:
            for chain in chains:
                yield self._make_result(chain)
        logger.info("搜索 '%s' 结果已发送。", query)

    @arxiv_group.command("get")
    async def cmd_get(
        self, event: AstrMessageEvent, arxiv_id: GreedyStr = GreedyStr("")
    ):
        """通过 arXiv ID 或链接获取单篇论文完整内容（含 PDF/截图/摘要）。

        用法: /arxiv get 2501.12345
              /arxiv get https://arxiv.org/abs/2501.12345
        """
        arxiv_id = arxiv_id.strip()
        if not arxiv_id:
            yield event.plain_result(
                "❌ 请提供论文 ID 或链接。用法: /arxiv get <arxiv_id|url>\n"
                "例如: /arxiv get 2501.12345\n"
                "      /arxiv get https://arxiv.org/abs/2501.12345"
            )
            return

        # 从用户输入中提取 arXiv ID（支持直接 ID 和 URL 两种形式）
        arxiv_id = extractArxivId(arxiv_id)
        logger.info("收到 get 请求: '%s'", arxiv_id)
        timeout = self._arxiv_cfg.get("timeout_seconds", 30)

        yield event.plain_result(f"🔍 开始获取 {arxiv_id}，请稍候...")

        try:
            paper = await arxiv_client.get_paper_by_id(arxiv_id, timeout=timeout)
        except TimeoutError:
            logger.exception("获取论文 '%s' 超时。", arxiv_id)
            yield event.plain_result("❌ 请求超时，请检查网络连接后重试。")
            return
        except Exception:
            logger.exception("获取论文 '%s' 失败。", arxiv_id)
            yield event.plain_result("❌ 获取论文失败，请稍后重试。")
            return

        if paper is None:
            yield event.plain_result(
                f"📭 未找到 ID 为 {arxiv_id} 的论文，请确认 ID 是否正确。"
            )
            return

        logger.info("获取论文成功: %s", paper.title)

        chains = await self._process_papers([paper])
        if not chains:
            yield event.plain_result("📭 处理论文时出错。")
            return

        use_forward = self._send_cfg.get("use_forward", True)
        if use_forward:
            bot_name = self._send_cfg.get("bot_name", "ArXiv Bot")
            msg = formatter.build_forward_nodes(chains, bot_name=bot_name)
            yield self._make_result(msg)
        else:
            for chain in chains:
                yield self._make_result(chain)
        logger.info("论文 '%s' 已发送。", arxiv_id)

    @arxiv_group.command("latest")
    async def cmd_latest(self, event: AstrMessageEvent):
        """手动获取最新论文。用法: /arxiv latest"""
        categories = self._arxiv_cfg.get("categories", ["cs.AI"])
        tags = self._arxiv_cfg.get("tags", [])
        max_results = self._arxiv_cfg.get("max_results", 5)
        timeout = self._arxiv_cfg.get("timeout_seconds", 30)

        logger.info("收到 latest 请求，分类: %s", categories)

        try:
            papers = await arxiv_client.get_latest_papers(
                categories=categories,
                tags=tags,
                max_results=max_results,
                timeout=timeout,
            )
        except TimeoutError:
            logger.exception("ArXiv 获取最新论文超时。")
            yield event.plain_result("❌ 请求超时，请检查网络连接后重试。")
            return
        except Exception:
            logger.exception("ArXiv 获取最新论文失败。")
            yield event.plain_result("❌ 获取最新论文失败，请稍后重试。")
            return

        logger.info("latest 返回 %d 篇论文。", len(papers))

        if not papers:
            yield event.plain_result("📭 当前分类下没有找到论文。")
            return

        # latest 只展示论文信息，不下载 PDF（PDF 仅通过 /arxiv get 获取）
        chains = self._build_info_chains(papers)

        use_forward = self._send_cfg.get("use_forward", True)
        if use_forward:
            bot_name = self._send_cfg.get("bot_name", "ArXiv Bot")
            msg = formatter.build_forward_nodes(chains, bot_name=bot_name)
            yield self._make_result(msg)
        else:
            for chain in chains:
                yield self._make_result(chain)
        logger.info("latest 结果已发送。")

    @arxiv_group.command("categories")
    async def cmd_categories(self, event: AstrMessageEvent):
        """列出所有支持的 arXiv 学科分类。"""
        msg = formatter.build_categories_chain()
        yield self._make_result(msg)

    @arxiv_group.command("status")
    async def cmd_status(self, event: AstrMessageEvent):
        """显示插件当前配置和状态。"""
        categories = self._arxiv_cfg.get("categories", [])
        tags = self._arxiv_cfg.get("tags", [])
        push_time = self._send_cfg.get("push_time", "09:00")
        timezone = self._send_cfg.get("push_timezone", "Asia/Shanghai")
        targets = self._send_cfg.get("target_sessions", [])
        use_forward = self._send_cfg.get("use_forward", True)
        abstract_mode = self._llm_cfg.get("abstract_mode", "original")
        max_results = self._arxiv_cfg.get("max_results", 5)

        mode_display = "合并转发" if use_forward else "逐条发送"
        abstract_display = "原文" if abstract_mode == "original" else "LLM 中文翻译"

        lines = [
            "📊 ArXiv 插件状态",
            "",
            f"📚 学科分类: {', '.join(categories) or '未配置'}",
            f"🏷️ 关键词: {', '.join(tags) or '无'}",
            f"📄 每次推送: {max_results} 篇",
            f"⏰ 推送时间: {push_time} ({timezone})",
            f"🎯 目标会话: {len(targets)} 个",
            f"📨 发送模式: {mode_display}",
            f"📝 摘要模式: {abstract_display}",
            f"🖼️ PDF 截图: {'开启' if self._send_cfg.get('screenshot_pdf') else '关闭'}",
            f"📎 附带 PDF: {'开启' if self._send_cfg.get('attach_pdf') else '关闭'}",
            f"🤖 LLM 总结: {'开启' if self._llm_cfg.get('llm_summarize') else '关闭'}",
        ]

        yield event.plain_result("\n".join(lines))

    @arxiv_group.command("add_session")
    async def cmd_add_session(self, event: AstrMessageEvent):
        """将当前会话添加为推送目标。用法: /arxiv add_session"""
        umo = event.unified_msg_origin

        send_cfg = self.config.get("send_config", {})
        targets: list[str] = list(send_cfg.get("target_sessions", []))
        if umo in targets:
            yield event.plain_result("ℹ️ 当前会话已在推送列表中。")
            return

        targets.append(umo)
        send_cfg["target_sessions"] = targets
        self.config["send_config"] = send_cfg
        self.config.save_config()

        yield event.plain_result(f"✅ 已添加当前会话到推送列表。\n会话标识: {umo}")

    @arxiv_group.command("remove_session")
    async def cmd_remove_session(self, event: AstrMessageEvent):
        """将当前会话从推送目标中移除。用法: /arxiv remove_session"""
        umo = event.unified_msg_origin

        send_cfg = self.config.get("send_config", {})
        targets: list[str] = list(send_cfg.get("target_sessions", []))
        if umo not in targets:
            yield event.plain_result("ℹ️ 当前会话不在推送列表中。")
            return

        targets.remove(umo)
        send_cfg["target_sessions"] = targets
        self.config["send_config"] = send_cfg
        self.config.save_config()

        yield event.plain_result("✅ 已从推送列表中移除当前会话。")

    async def terminate(self) -> None:
        """插件卸载时清理定时任务和临时文件。"""
        if self._cron_job_id:
            try:
                await self.context.cron_manager.delete_job(self._cron_job_id)
                logger.info("ArXiv 定时任务已清理。")
            except Exception:
                logger.exception("清理 ArXiv 定时任务失败。")

        # 清理临时文件
        if self._temp_dir.exists():
            import shutil

            try:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
            except Exception:
                pass
