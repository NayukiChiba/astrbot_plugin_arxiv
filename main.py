"""AstrBot ArXiv 论文推送插件。

支持定时推送、搜索、LLM 摘要翻译、PDF 处理、合并转发等。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.event import filter as event_filter
from astrbot.api.star import Context, Star, StarTools, register

from . import arxiv_client, formatter, llm_service, pdf_handler
from .history import SentHistory

if TYPE_CHECKING:
    pass

logger = logging.getLogger("astrbot")

# 插件命名空间，用于配置存储
PLUGIN_NAME = "astrbot_plugin_arxiv"

# 默认 LLM 总结 prompt
_DEFAULT_SUMMARY_PROMPT = (
    "你是一个学术论文总结助手。请用中文总结以下论文内容。"
    "重点关注: 1) 主要贡献 2) 核心方法 3) 关键结果和结论。"
    "总结应简洁专业，不超过 300 字。\n\n"
    "论文内容:\n{content}"
)


def _init_config(plugin_name: str) -> None:
    """注册所有插件配置项到 WebUI 面板。"""
    from astrbot.core.star.config import put_config

    put_config(
        plugin_name,
        "学科分类",
        "categories",
        ["cs.AI"],
        "arXiv 学科分类代码列表，例如 cs.AI, cs.LG, math.CO",
    )
    put_config(
        plugin_name,
        "关键词标签",
        "tags",
        [],
        "额外的关键词标签，用于模糊匹配（留空则仅按分类筛选）",
    )
    put_config(
        plugin_name,
        "每次推送数量",
        "max_results",
        5,
        "每次推送或搜索的最大论文数量",
    )
    put_config(
        plugin_name,
        "定时表达式",
        "cron_expression",
        "0 9 * * *",
        "Cron 定时表达式（默认每天早上 9 点）",
    )
    put_config(
        plugin_name,
        "时区",
        "cron_timezone",
        "Asia/Shanghai",
        "定时任务时区",
    )
    put_config(
        plugin_name,
        "目标会话列表",
        "target_sessions",
        [],
        "自动推送的目标 UMO 会话列表（从消息事件中获取 unified_msg_origin）",
    )
    put_config(
        plugin_name,
        "发送模式",
        "send_mode",
        "forward",
        "发送方式: forward (合并转发) 或 individual (逐条发送)",
    )
    put_config(
        plugin_name,
        "附带 PDF",
        "attach_pdf",
        False,
        "是否附带 PDF 文件",
    )
    put_config(
        plugin_name,
        "截图 PDF 首页",
        "screenshot_pdf",
        True,
        "是否截取 PDF 第一页作为图片发送",
    )
    put_config(
        plugin_name,
        "截图 DPI",
        "screenshot_dpi",
        150,
        "PDF 首页截图的渲染精度（DPI），建议 72~300",
    )
    put_config(
        plugin_name,
        "PDF 最大体积 (MB)",
        "max_pdf_size_mb",
        20,
        "允许处理的 PDF 最大文件大小（MB）",
    )
    put_config(
        plugin_name,
        "超时时间 (秒)",
        "timeout_seconds",
        30,
        "HTTP 请求超时时间（秒）",
    )
    put_config(
        plugin_name,
        "发送摘要",
        "send_abstract",
        True,
        "是否在消息中包含论文摘要",
    )
    put_config(
        plugin_name,
        "摘要模式",
        "abstract_mode",
        "original",
        "摘要处理方式: original (原文) 或 llm_chinese (LLM 翻译为中文)",
    )
    put_config(
        plugin_name,
        "LLM 总结论文",
        "llm_summarize",
        False,
        "是否使用 LLM 扫描 PDF 并生成论文总结",
    )
    put_config(
        plugin_name,
        "LLM 提供商 ID",
        "llm_provider_id",
        "",
        "用于 LLM 功能的提供商 ID（留空则使用默认提供商）",
    )
    put_config(
        plugin_name,
        "LLM 总结 Prompt",
        "llm_summary_prompt",
        "",
        "自定义 LLM 总结 prompt，需包含 {content} 占位符（留空使用默认）",
    )
    put_config(
        plugin_name,
        "机器人名称",
        "bot_name",
        "ArXiv Bot",
        "合并转发消息中显示的机器人昵称",
    )
    put_config(
        plugin_name,
        "历史保留天数",
        "history_retention_days",
        30,
        "已发送论文记录的保留天数（用于去重）",
    )


@register(
    "astrbot_plugin_arxiv",
    "NayukiChiba",
    "ArXiv 论文搜索与定时推送插件",
    "1.0.0",
)
class ArxivPlugin(Star):
    """ArXiv 论文推送插件主类。"""

    def __init__(self, context: Context) -> None:
        super().__init__(context)
        self._data_dir: Path = Path()
        self._temp_dir: Path = Path()
        self._history: SentHistory | None = None
        self._config: dict = {}
        self._cron_job_id: str = ""

    async def initialize(self) -> None:
        """插件初始化：注册配置、加载历史、设置定时任务。"""
        # 注册配置项
        _init_config(PLUGIN_NAME)

        # 加载配置
        self._load_config()

        # 初始化数据目录
        self._data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self._temp_dir = self._data_dir / "temp"
        self._temp_dir.mkdir(parents=True, exist_ok=True)

        # 初始化已发送历史
        retention = self._config.get("history_retention_days", 30)
        self._history = SentHistory(self._data_dir, retention_days=retention)

        # 清理过期记录
        removed = self._history.cleanup_old()
        if removed > 0:
            logger.info("已清理 %d 条过期的论文发送记录。", removed)

        # 注册定时任务
        await self._register_cron_job()

        logger.info("ArXiv 论文推送插件已初始化。")

    def _load_config(self) -> None:
        """从配置文件加载插件配置。"""
        from astrbot.core.star.config import load_config

        cfg = load_config(PLUGIN_NAME)
        if isinstance(cfg, dict):
            self._config = cfg
        else:
            self._config = {}

    async def _register_cron_job(self) -> None:
        """注册定时推送任务。"""
        cron_expr = self._config.get("cron_expression", "0 9 * * *")
        timezone = self._config.get("cron_timezone", "Asia/Shanghai")

        try:
            job = await self.context.cron_manager.add_basic_job(
                name="arxiv_daily_push",
                cron_expression=cron_expr,
                handler=self._scheduled_push,
                description="ArXiv 每日论文定时推送",
                timezone=timezone,
                enabled=True,
                persistent=False,
            )
            self._cron_job_id = job.job_id
            logger.info(
                "ArXiv 定时推送已注册: %s (%s)",
                cron_expr,
                timezone,
            )
        except Exception:
            logger.exception("注册 ArXiv 定时任务失败。")

    # ── 定时推送逻辑 ─────────────────────────────────────────────

    async def _scheduled_push(self) -> None:
        """定时推送入口：获取最新论文并发送到所有目标会话。"""
        self._load_config()  # 重新加载配置以获取最新设置

        target_sessions: list[str] = self._config.get("target_sessions", [])
        if not target_sessions:
            logger.info("ArXiv 定时推送：未配置目标会话，跳过。")
            return

        categories = self._config.get("categories", ["cs.AI"])
        tags = self._config.get("tags", [])
        max_results = self._config.get("max_results", 5)
        timeout = self._config.get("timeout_seconds", 30)

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
        """向指定会话发送论文（去重后）。"""
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

        # 根据模式发送
        send_mode = self._config.get("send_mode", "forward")
        try:
            if send_mode == "forward":
                bot_name = self._config.get("bot_name", "ArXiv Bot")
                msg = formatter.build_forward_nodes(
                    chains,
                    bot_name=bot_name,
                )
                await self.context.send_message(session, msg)
            else:
                # 逐条发送
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

    async def _process_papers(
        self,
        papers: list[arxiv_client.ArxivPaper],
    ) -> list[MessageChain]:
        """处理论文列表，生成消息链。"""
        chains: list[MessageChain] = []

        for i, paper in enumerate(papers, 1):
            try:
                chain = await self._process_single_paper(paper, index=i)
                chains.append(chain)
            except Exception:
                logger.exception("处理论文 %s 失败，跳过。", paper.arxiv_id)

        return chains

    async def _process_single_paper(
        self,
        paper: arxiv_client.ArxivPaper,
        *,
        index: int = 0,
    ) -> MessageChain:
        """处理单篇论文：翻译摘要、下载 PDF、截图、总结。"""
        abstract_text = ""
        summary_text = ""
        screenshot_path = ""
        pdf_path_str = ""

        timeout = self._config.get("timeout_seconds", 30)
        max_pdf_size = self._config.get("max_pdf_size_mb", 20)

        # 摘要处理
        if self._config.get("send_abstract", True):
            abstract_mode = self._config.get("abstract_mode", "original")
            if abstract_mode == "llm_chinese" and paper.abstract:
                provider_id = self._config.get("llm_provider_id", "")
                abstract_text = await llm_service.translate_abstract(
                    self.context,
                    paper.abstract,
                    provider_id=provider_id,
                )
            else:
                abstract_text = paper.abstract

        # 是否需要下载 PDF（截图或附件或 LLM 总结都需要）
        need_pdf = (
            self._config.get("screenshot_pdf", True)
            or self._config.get("attach_pdf", False)
            or self._config.get("llm_summarize", False)
        )

        downloaded_pdf: Path | None = None
        if need_pdf and paper.pdf_url:
            downloaded_pdf = await pdf_handler.download_pdf(
                paper.pdf_url,
                self._temp_dir,
                timeout=timeout,
                max_size_mb=max_pdf_size,
            )

        # PDF 首页截图
        if downloaded_pdf and self._config.get("screenshot_pdf", True):
            dpi = self._config.get("screenshot_dpi", 150)
            screenshot = pdf_handler.screenshot_first_page(
                downloaded_pdf,
                self._temp_dir,
                dpi=dpi,
            )
            if screenshot:
                screenshot_path = str(screenshot)

        # LLM 总结
        if downloaded_pdf and self._config.get("llm_summarize", False):
            pdf_text = pdf_handler.extract_text(downloaded_pdf)
            if pdf_text:
                provider_id = self._config.get("llm_provider_id", "")
                custom_prompt = (
                    self._config.get(
                        "llm_summary_prompt",
                        "",
                    )
                    or _DEFAULT_SUMMARY_PROMPT
                )
                summary_text = await llm_service.summarize_paper(
                    self.context,
                    pdf_text,
                    provider_id=provider_id,
                    custom_prompt=custom_prompt,
                )

        # PDF 附件
        if downloaded_pdf and self._config.get("attach_pdf", False):
            pdf_path_str = str(downloaded_pdf)

        return formatter.build_paper_chain(
            paper,
            index=index,
            show_abstract=self._config.get("send_abstract", True),
            abstract_text=abstract_text,
            summary_text=summary_text,
            screenshot_path=screenshot_path,
            pdf_path=pdf_path_str,
        )

    # ── 指令处理 ──────────────────────────────────────────────

    @event_filter.command_group("arxiv")
    def arxiv_group(self):
        """ArXiv 论文相关指令组。"""

    @arxiv_group.command("search")
    async def cmd_search(self, event: AstrMessageEvent):
        """搜索 arXiv 论文。用法: /arxiv search <关键词>"""
        # 提取搜索关键词（去掉指令本身）
        query = event.message_str.strip()
        # 移除指令前缀
        for prefix in ["/arxiv search ", "arxiv search "]:
            if query.lower().startswith(prefix):
                query = query[len(prefix) :].strip()
                break

        if not query:
            yield event.plain_result(
                "❌ 请提供搜索关键词。用法: /arxiv search <关键词>"
            )
            return

        self._load_config()
        max_results = self._config.get("max_results", 5)
        timeout = self._config.get("timeout_seconds", 30)

        try:
            papers = await arxiv_client.search_papers(
                query,
                max_results=max_results,
                timeout=timeout,
            )
        except Exception:
            logger.exception("ArXiv 搜索失败。")
            yield event.plain_result("❌ 搜索失败，请稍后重试。")
            return

        if not papers:
            yield event.plain_result("📭 未找到匹配的论文。")
            return

        chains = await self._process_papers(papers)
        if not chains:
            yield event.plain_result("📭 处理论文时出错。")
            return

        send_mode = self._config.get("send_mode", "forward")
        if send_mode == "forward":
            bot_name = self._config.get("bot_name", "ArXiv Bot")
            msg = formatter.build_forward_nodes(chains, bot_name=bot_name)
            yield event.result_message(msg)
        else:
            for chain in chains:
                yield event.result_message(chain)

    @arxiv_group.command("latest")
    async def cmd_latest(self, event: AstrMessageEvent):
        """手动获取最新论文。用法: /arxiv latest"""
        self._load_config()

        categories = self._config.get("categories", ["cs.AI"])
        tags = self._config.get("tags", [])
        max_results = self._config.get("max_results", 5)
        timeout = self._config.get("timeout_seconds", 30)

        try:
            papers = await arxiv_client.get_latest_papers(
                categories=categories,
                tags=tags,
                max_results=max_results,
                timeout=timeout,
            )
        except Exception:
            logger.exception("ArXiv 获取最新论文失败。")
            yield event.plain_result("❌ 获取最新论文失败，请稍后重试。")
            return

        if not papers:
            yield event.plain_result("📭 当前分类下没有找到论文。")
            return

        chains = await self._process_papers(papers)
        if not chains:
            yield event.plain_result("📭 处理论文时出错。")
            return

        send_mode = self._config.get("send_mode", "forward")
        if send_mode == "forward":
            bot_name = self._config.get("bot_name", "ArXiv Bot")
            msg = formatter.build_forward_nodes(chains, bot_name=bot_name)
            yield event.result_message(msg)
        else:
            for chain in chains:
                yield event.result_message(chain)

    @arxiv_group.command("categories")
    async def cmd_categories(self, event: AstrMessageEvent):
        """列出所有支持的 arXiv 学科分类。"""
        msg = formatter.build_categories_chain()
        yield event.result_message(msg)

    @arxiv_group.command("status")
    async def cmd_status(self, event: AstrMessageEvent):
        """显示插件当前配置和状态。"""
        self._load_config()

        categories = self._config.get("categories", [])
        tags = self._config.get("tags", [])
        cron_expr = self._config.get("cron_expression", "0 9 * * *")
        timezone = self._config.get("cron_timezone", "Asia/Shanghai")
        targets = self._config.get("target_sessions", [])
        send_mode = self._config.get("send_mode", "forward")
        abstract_mode = self._config.get("abstract_mode", "original")
        max_results = self._config.get("max_results", 5)

        mode_display = "合并转发" if send_mode == "forward" else "逐条发送"
        abstract_display = "原文" if abstract_mode == "original" else "LLM 中文翻译"

        lines = [
            "📊 ArXiv 插件状态",
            "",
            f"📚 学科分类: {', '.join(categories) or '未配置'}",
            f"🏷️ 关键词: {', '.join(tags) or '无'}",
            f"📄 每次推送: {max_results} 篇",
            f"⏰ 定时表达式: {cron_expr} ({timezone})",
            f"🎯 目标会话: {len(targets)} 个",
            f"📨 发送模式: {mode_display}",
            f"📝 摘要模式: {abstract_display}",
            f"🖼️ PDF 截图: {'开启' if self._config.get('screenshot_pdf') else '关闭'}",
            f"📎 附带 PDF: {'开启' if self._config.get('attach_pdf') else '关闭'}",
            f"🤖 LLM 总结: {'开启' if self._config.get('llm_summarize') else '关闭'}",
        ]

        yield event.plain_result("\n".join(lines))

    @arxiv_group.command("add_session")
    async def cmd_add_session(self, event: AstrMessageEvent):
        """将当前会话添加为推送目标。用法: /arxiv add_session"""
        self._load_config()
        umo = event.unified_msg_origin

        targets: list[str] = self._config.get("target_sessions", [])
        if umo in targets:
            yield event.plain_result("ℹ️ 当前会话已在推送列表中。")
            return

        targets.append(umo)
        from astrbot.core.star.config import update_config

        update_config(PLUGIN_NAME, "target_sessions", targets)
        self._config["target_sessions"] = targets

        yield event.plain_result(f"✅ 已添加当前会话到推送列表。\n会话标识: {umo}")

    @arxiv_group.command("remove_session")
    async def cmd_remove_session(self, event: AstrMessageEvent):
        """将当前会话从推送目标中移除。用法: /arxiv remove_session"""
        self._load_config()
        umo = event.unified_msg_origin

        targets: list[str] = self._config.get("target_sessions", [])
        if umo not in targets:
            yield event.plain_result("ℹ️ 当前会话不在推送列表中。")
            return

        targets.remove(umo)
        from astrbot.core.star.config import update_config

        update_config(PLUGIN_NAME, "target_sessions", targets)
        self._config["target_sessions"] = targets

        yield event.plain_result("✅ 已从推送列表中移除当前会话。")

    @arxiv_group.command("push_now")
    async def cmd_push_now(self, event: AstrMessageEvent):
        """立即向当前会话推送最新论文（不影响定时推送）。用法: /arxiv push_now"""
        self._load_config()
        umo = event.unified_msg_origin

        categories = self._config.get("categories", ["cs.AI"])
        tags = self._config.get("tags", [])
        max_results = self._config.get("max_results", 5)
        timeout = self._config.get("timeout_seconds", 30)

        yield event.plain_result("⏳ 正在获取最新论文...")

        try:
            papers = await arxiv_client.get_latest_papers(
                categories=categories,
                tags=tags,
                max_results=max_results,
                timeout=timeout,
            )
        except Exception:
            logger.exception("ArXiv push_now 获取论文失败。")
            yield event.plain_result("❌ 获取论文失败，请稍后重试。")
            return

        if not papers:
            yield event.plain_result("📭 当前分类下没有找到论文。")
            return

        # 过滤已发送
        if self._history:
            papers = [p for p in papers if not self._history.is_sent(umo, p.arxiv_id)]

        if not papers:
            yield event.plain_result("📭 当前没有新的论文可推送（均已发送过）。")
            return

        chains = await self._process_papers(papers)
        if not chains:
            yield event.plain_result("📭 处理论文时出错。")
            return

        send_mode = self._config.get("send_mode", "forward")
        if send_mode == "forward":
            bot_name = self._config.get("bot_name", "ArXiv Bot")
            msg = formatter.build_forward_nodes(chains, bot_name=bot_name)
            yield event.result_message(msg)
        else:
            for chain in chains:
                yield event.result_message(chain)

        # 标记已发送
        if self._history:
            self._history.mark_sent_batch(
                umo,
                [p.arxiv_id for p in papers],
            )

    async def terminate(self) -> None:
        """插件卸载时清理定时任务。"""
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
