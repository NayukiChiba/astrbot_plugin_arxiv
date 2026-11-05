"""AstrBot ArXiv 论文推送插件。

支持定时推送、搜索、LLM 摘要翻译、PDF 处理、合并转发等。
"""

from __future__ import annotations

import base64
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import File, Image, Plain
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.message.message_event_result import MessageEventResult
from astrbot.core.star.filter.command import GreedyStr

from . import (
    arxiv_client,
    formatter,
    image_render,
    llm_service,
    pdf_handler,
    text_render,
    web_api,
)
from .arxiv_client import extractArxivId
from .history import SentHistory
from .paper_store import PaperStore
from .send_options import (
    PaperArtifacts,
    PreparedPaper,
    ProcessedPaper,
    SendOptions,
)

# 定时任务在 AstrBot 数据库中的唯一名称，用于去重
_CRON_JOB_NAME = "arxiv_daily_push"


def _normalize_text_format(fmt: str) -> str:
    """规范化文本格式配置值。

    Args:
        fmt: 配置项 text_format 的值。

    Returns:
        "plain" 或 "markdown"，非法值回退为 "plain"。
    """
    return fmt if fmt in ("plain", "markdown") else "plain"


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


class ArxivPlugin(Star):
    """ArXiv 论文推送插件主类。"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self._data_dir: Path = Path()
        self._temp_dir: Path = Path()
        self._history: SentHistory | None = None
        self._paper_store: PaperStore | None = None
        self._cron_job_id: str = ""
        self._bestMirror: str = ""

    # ── 便捷配置访问 ──────────────────────────────────────────

    @property
    def _arxiv_cfg(self) -> dict:
        """获取 arXiv 论文配置。"""
        return self.config.get("arxiv_config", {})

    @property
    def _send_cfg(self) -> dict:
        """获取发送内容配置。"""
        return self.config.get("send_config", {})

    @property
    def _push_cfg(self) -> dict:
        """获取定时推送配置。"""
        return self.config.get("push_config", {})

    @property
    def _storage_cfg(self) -> dict:
        """获取论文库存储配置。"""
        return self.config.get("storage_config", {})

    @property
    def _llm_cfg(self) -> dict:
        """获取 LLM 赋能配置。"""
        return self.config.get("llm_config", {})

    @property
    def _platform_cfg(self) -> dict:
        """获取平台适配配置（含跨平台的按图发送设置与各平台配置块）。"""
        return self.config.get("platform_config", {})

    @property
    def _paper_store_enabled(self) -> bool:
        """是否持久化存储已发送论文与 PDF（供 WebUI 浏览）。"""
        return self._storage_cfg.get("store_papers", True)

    def _platform_settings(self, platform_name: str, platform_id: str = "") -> dict:
        """获取指定平台的发送设置。

        合并顺序：默认配置 < 平台类型配置 < 机器人实例条目（优先级最高）。
        qqofficial_webhook 与 qqofficial 共用 QQ 官方配置块。

        强制规则（不受配置影响）：
        - aiocqhttp 仅支持纯文本，text_format 始终为 plain；
        - 合并转发仅 aiocqhttp 支持，其他平台 use_forward 始终为 False。

        Args:
            platform_name: 平台类型名，如 aiocqhttp、qqofficial_webhook。
            platform_id: 平台实例 ID（UMO 第一段），用于匹配实例级适配条目。

        Returns:
            合并后的平台设置字典，含 text_format、use_forward 等键。
        """
        platform_cfg = self.config.get("platform_config", {})
        settings = dict(platform_cfg.get("default", {}))
        key = "qqofficial" if platform_name.startswith("qqofficial") else platform_name
        settings.update(platform_cfg.get(key, {}))

        # 实例级适配条目覆盖平台类型配置（取条目会话 UMO 首段匹配实例 ID）
        for entry in platform_cfg.get("instances", []):
            entry_session = entry.get("session", "")
            if platform_id and entry_session.split(":", 1)[0] == platform_id:
                settings.update(
                    {
                        k: v
                        for k, v in entry.items()
                        if k not in ("session", "__template_key")
                    }
                )

        if platform_name == "aiocqhttp":
            settings["text_format"] = "plain"
        else:
            settings["use_forward"] = False
        return settings

    def _settings_for_event(self, event: AstrMessageEvent) -> dict:
        """获取事件所属平台实例的生效发送设置。"""
        return self._platform_settings(
            event.get_platform_name(),
            event.unified_msg_origin.split(":", 1)[0],
        )

    def _settings_for_session(self, session: str) -> dict:
        """获取目标会话所属平台实例的生效发送设置。"""
        return self._platform_settings(
            self._resolve_platform_name(session),
            session.split(":", 1)[0],
        )

    # ── 配置迁移 ─────────────────────────────────────────────

    def _migrate_config(self) -> None:
        """将 v1.2.x 及更早的旧配置键迁移到 v1.3.0 的分组结构。

        AstrBot 启动时会用 schema 默认值补齐新配置组，因此旧键存在
        即代表未迁移的旧配置，直接覆盖对应新键并删除旧键。迁移成功
        后保存一次，后续启动不再触发（幂等）。
        """
        send_cfg = self.config.get("send_config", {})
        if not isinstance(send_cfg, dict):
            return

        moved = False
        # 平面迁移：定时推送与论文库存储配置组
        for key, group in (
            ("push_time", "push_config"),
            ("push_timezone", "push_config"),
            ("target_sessions", "push_config"),
            ("history_retention_days", "push_config"),
            ("store_papers", "storage_config"),
            ("paper_retention_days", "storage_config"),
        ):
            if key in send_cfg:
                self.config.setdefault(group, {})[key] = send_cfg.pop(key)
                moved = True

        # 合并转发仅 aiocqhttp 支持，迁入 aiocqhttp 平台配置块
        for key in ("use_forward", "bot_name"):
            if key in send_cfg:
                platform_cfg = self.config.setdefault("platform_config", {})
                platform_cfg.setdefault("aiocqhttp", {})[key] = send_cfg.pop(key)
                moved = True

        if "text_format" in send_cfg:
            old_fmt = send_cfg.pop("text_format")
            # 旧 auto 的效果与新结构默认值一致（QQ 官方 markdown、其他 plain），
            # 无需迁移；显式的 plain/markdown 迁移为全局默认格式
            if old_fmt in ("plain", "markdown"):
                platform_cfg = self.config.setdefault("platform_config", {})
                platform_cfg.setdefault("default", {})["text_format"] = old_fmt
            moved = True

        # 按图发送（发送形式）从发送内容组迁入平台适配组（v1.3.0 曾放在发送内容组）
        for key in ("send_as_image", "image_template"):
            if key in send_cfg:
                self.config.setdefault("platform_config", {})[key] = send_cfg.pop(key)
                moved = True

        if not moved:
            return

        self.config["send_config"] = send_cfg
        try:
            self.config.save_config()
        except AttributeError:
            pass  # 测试环境 config 为普通 dict，无 save_config
        except Exception:
            logger.exception("保存迁移后的插件配置失败。")
        logger.info("已将旧版配置迁移到 v1.3.0 的分组结构。")

    # ── 生命周期 ──────────────────────────────────────────────

    async def initialize(self) -> None:
        """插件初始化：迁移配置、加载历史、设置定时任务、测速选择最优镜像。"""
        self._migrate_config()

        # 初始化数据目录
        self._data_dir = StarTools.get_data_dir("astrbot_plugin_arxiv")
        self._temp_dir = self._data_dir / "temp"
        self._temp_dir.mkdir(parents=True, exist_ok=True)

        # 安装内置卡片模板到用户模板目录（不覆盖用户已有模板）
        image_render.install_builtin_templates(self._data_dir / "templates")

        # 初始化已发送历史
        retention = self._push_cfg.get("history_retention_days", 30)
        self._history = SentHistory(self._data_dir, retention_days=retention)

        # 初始化论文库（WebUI 浏览已发送论文与 PDF）。
        # 数据保存在 data/plugin_data/astrbot_plugin_arxiv/papers/ 下。
        paper_retention = self._storage_cfg.get("paper_retention_days", 7)
        self._paper_store = PaperStore(self._data_dir, retention_days=paper_retention)
        if self._paper_store_enabled:
            removed_papers = self._paper_store.cleanup_old()
            if removed_papers > 0:
                logger.info("已清理 %d 条过期的论文库记录。", removed_papers)
        else:
            # 关闭存储时清空已存储的论文与 PDF
            cleared = self._paper_store.clear_all()
            if cleared > 0:
                logger.info("论文存储已关闭，已清空 %d 条论文库记录。", cleared)

        # 清理过期记录
        removed = self._history.cleanup_old()
        if removed > 0:
            logger.info("已清理 %d 条过期的论文发送记录。", removed)

        # 注册 WebUI 页面 API
        web_api.register_web_routes(self)

        # 测速选择最优 PDF 镜像站
        mirrors = self._arxiv_cfg.get("pdf_mirrors", ["https://arxiv.org"])
        logger.info("正在测速 %d 个 PDF 镜像站...", len(mirrors))
        self._bestMirror = await pdf_handler.pickBestMirror(mirrors)

        # 注册定时任务
        await self._register_cron_job()

        logger.info("ArXiv 论文推送插件已初始化。")

    async def _cleanup_duplicate_jobs(self) -> None:
        """删除数据库中同名的历史推送任务，避免出现多个未来任务。

        AstrBot 每次 add_basic_job 都会新增一行记录（不按名称去重）。
        若上次进程异常退出导致 terminate() 未执行，旧记录会遗留在
        数据库中，WebUI 的「未来任务」页面会将其全部列出。
        """
        try:
            jobs = await self.context.cron_manager.list_jobs("basic")
        except Exception:
            logger.exception("查询历史定时任务失败，跳过同名任务清理。")
            return

        for job in jobs:
            if job.name != _CRON_JOB_NAME:
                continue
            try:
                await self.context.cron_manager.delete_job(job.job_id)
                logger.info("已清理旧的 ArXiv 定时任务 %s。", job.job_id)
            except Exception:
                logger.exception("清理旧的 ArXiv 定时任务 %s 失败。", job.job_id)

    async def _register_cron_job(self) -> None:
        """注册定时推送任务。

        注册前先清理同名历史任务，保证任何时刻全局只有一个推送任务。
        """
        await self._cleanup_duplicate_jobs()

        push_time = self._push_cfg.get("push_time", "09:00")
        timezone = self._push_cfg.get("push_timezone", "Asia/Shanghai")
        cron_expr = _time_to_cron(push_time)

        try:
            job = await self.context.cron_manager.add_basic_job(
                name=_CRON_JOB_NAME,
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
        """定时推送入口：获取最新论文并发送到所有目标会话。

        论文的平台无关产物（LLM 翻译/总结、PDF 下载、截图）只处理一次，
        跨会话复用，避免多会话时重复调用 LLM 与重复下载 PDF；各会话仅按
        其平台文本格式做格式化与投递。
        """
        target_sessions: list[str] = self._push_cfg.get("target_sessions", [])
        if not target_sessions:
            logger.info("ArXiv 定时推送：未配置目标会话，跳过。")
            return
        if not self._history:
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

        # 各会话未发论文的并集（去重按会话）
        union: dict[str, arxiv_client.ArxivPaper] = {}
        for session in target_sessions:
            for paper in papers:
                if not self._history.is_sent(session, paper.arxiv_id):
                    union.setdefault(paper.arxiv_id, paper)
        if not union:
            logger.info("ArXiv 定时推送：所有会话均无新论文。")
            return

        # 平台无关产物仅处理一次，跨会话复用
        base_options = SendOptions()
        prepared_map: dict[str, PreparedPaper] = {}
        for arxiv_id, paper in union.items():
            try:
                prepared_map[arxiv_id] = await self._prepare_paper(paper, base_options)
            except Exception:
                logger.exception("准备论文 %s 失败，跳过。", arxiv_id)

        # 每个会话仅按其文本格式格式化并投递
        for session in target_sessions:
            unsent = [
                p for p in papers if not self._history.is_sent(session, p.arxiv_id)
            ]
            if not unsent:
                continue
            options = SendOptions(text_format=self.text_format_for_session(session))
            results: list[ProcessedPaper] = []
            for i, paper in enumerate(unsent, 1):
                prepared = prepared_map.get(paper.arxiv_id)
                if not prepared:
                    continue
                try:
                    results.append(
                        await self._format_prepared(prepared, options, index=i)
                    )
                except Exception:
                    logger.exception("格式化论文 %s 失败，跳过。", paper.arxiv_id)
            await self._deliver_results(session, results)

    async def _deliver_results(
        self,
        session: str,
        results: list[ProcessedPaper],
    ) -> None:
        """投递处理结果，并按成功处理的论文标记去重与记录论文库。

        处理失败的论文不在 results 中，因此不会占用去重记录（下次推送
        会重试），也不会产生缺产物的库记录。
        """
        if not results or not self._history:
            return
        chains = [chain for r in results for chain in r.chains]
        await self._deliver_chains(session, chains)

        self._history.mark_sent_batch(session, [r.paper.arxiv_id for r in results])
        for r in results:
            self._store_record(r.paper, session, r.artifacts)
        logger.info("ArXiv 推送至 %s：成功发送 %d 篇论文。", session, len(results))

    async def _send_papers_to_session(
        self,
        session: str,
        papers: list[arxiv_client.ArxivPaper],
    ) -> None:
        """向指定会话发送论文（完整处理 + 投递，自动去重）。"""
        if not self._history:
            return

        # 过滤已发送的论文
        unsent_papers = [
            p for p in papers if not self._history.is_sent(session, p.arxiv_id)
        ]
        if not unsent_papers:
            logger.info("ArXiv 推送至 %s：无新论文。", session)
            return

        # 处理每篇论文（按目标平台配置决定文本格式）
        options = SendOptions(text_format=self.text_format_for_session(session))
        results = await self._process_papers(unsent_papers, options)
        await self._deliver_results(session, results)

    def _store_record(
        self,
        paper: arxiv_client.ArxivPaper,
        session: str,
        artifacts: PaperArtifacts,
    ) -> None:
        """把处理产物（截图/卡片）持久化到论文库并写入元数据（开启存储时）。

        Args:
            paper: 论文元数据。
            session: 目标会话 UMO。
            artifacts: 处理产物（本地文件路径与 LLM 结果文本）。
        """
        if not (self._paper_store and self._paper_store_enabled):
            return
        screenshot_file = (
            self._paper_store.save_screenshot(
                paper.arxiv_id, Path(artifacts.screenshot_path)
            )
            if artifacts.screenshot_path
            else ""
        )
        card_file = (
            self._paper_store.save_card(paper.arxiv_id, Path(artifacts.card_path))
            if artifacts.card_path
            else ""
        )
        self._paper_store.record(
            paper,
            session,
            screenshot_file=screenshot_file,
            card_file=card_file,
            abstract_zh=artifacts.abstract_zh,
            llm_summary=artifacts.llm_summary,
        )

    async def _deliver_chains(self, session: str, chains: list[MessageChain]) -> None:
        """按目标会话所属平台的配置（合并转发或逐条）发送消息链。"""
        platform_cfg = self._settings_for_session(session)
        use_forward = platform_cfg.get("use_forward", False)
        if use_forward:
            bot_name = platform_cfg.get("bot_name", "ArXiv Bot")
            forwardMsg, fileChains = formatter.build_forward_nodes(
                chains,
                bot_name=bot_name,
            )
            # 先发合并转发消息
            try:
                await self.context.send_message(session, forwardMsg)
            except Exception:
                logger.exception("ArXiv 合并转发消息发送失败。")
            # 再单独发送被提取出来的 File 链（PDF 附件）
            for fileChain in fileChains:
                try:
                    await self.context.send_message(session, fileChain)
                except Exception:
                    logger.exception("PDF 文件发送失败 (%s)。", session)
        else:
            for chain in chains:
                try:
                    await self.context.send_message(session, chain)
                except Exception:
                    logger.exception("论文消息发送失败 (%s)。", session)

    @staticmethod
    def _make_result(chain: MessageChain) -> MessageEventResult:
        """创建一个强制关闭 t2i 的 MessageEventResult。"""
        mer = MessageEventResult()
        mer.chain = chain.chain
        mer.use_t2i_ = False
        return mer

    def _resolve_platform_name(self, umo: str) -> str:
        """从 UMO 解析平台类型名（如 aiocqhttp、qqofficial_webhook）。

        UMO 第一段是平台实例 ID（可被用户改名），优先通过
        context.get_platform_inst 反查平台类型；反查失败时直接返回该 ID。
        """
        platform_id = umo.split(":", 1)[0]
        try:
            inst = self.context.get_platform_inst(platform_id)
            name = inst.meta().name if inst else ""
            if name:
                return name
        except Exception:
            logger.debug("反查平台类型失败: %s", platform_id)
        return platform_id

    def _text_format_for_event(self, event: AstrMessageEvent) -> str:
        """根据事件所属平台实例的配置决定文本格式（plain 或 markdown）。"""
        settings = self._settings_for_event(event)
        return _normalize_text_format(settings.get("text_format", "plain"))

    def text_format_for_session(self, session: str, override: str = "auto") -> str:
        """解析指定会话应使用的文本格式。

        Args:
            session: 目标 UMO 会话。
            override: WebUI 传入的覆盖值（plain / markdown）；
                "auto" 表示跟随会话所属平台实例的配置。

        Returns:
            "plain" 或 "markdown"。aiocqhttp 会话始终为 plain。
        """
        if self._resolve_platform_name(session) == "aiocqhttp":
            return "plain"
        if override != "auto":
            return _normalize_text_format(override)
        settings = self._settings_for_session(session)
        return _normalize_text_format(settings.get("text_format", "plain"))

    def list_card_templates(self) -> list[str]:
        """列出可用的卡片模板名（用户目录与内置目录的并集）。"""
        names: set[str] = set()
        for directory in (
            self._data_dir / "templates",
            image_render.BUILTIN_TEMPLATE_DIR,
        ):
            if directory.is_dir():
                names.update(p.stem for p in directory.glob("*.html"))
        return sorted(names)

    async def _process_papers(
        self,
        papers: list[arxiv_client.ArxivPaper],
        options: SendOptions,
    ) -> list[ProcessedPaper]:
        """处理论文列表，返回成功处理的论文（消息链 + 产物）。

        单篇失败仅记录日志并跳过，不影响其余论文。

        Args:
            papers: 论文列表。
            options: 发送覆盖选项。

        Returns:
            成功处理的论文列表，每项含消息链与处理产物。
        """
        logger.info("开始处理 %d 篇论文。", len(papers))
        results: list[ProcessedPaper] = []

        for i, paper in enumerate(papers, 1):
            try:
                logger.info("处理论文 [%d/%d]: %s", i, len(papers), paper.title)
                chains, artifacts = await self._process_single_paper(
                    paper, index=i, options=options
                )
                results.append(
                    ProcessedPaper(paper=paper, chains=chains, artifacts=artifacts)
                )
                logger.info(
                    "论文 %s 处理完成，生成 %d 条消息。",
                    paper.arxiv_id,
                    len(chains),
                )
            except Exception:
                logger.exception("处理论文 %s 失败，跳过。", paper.arxiv_id)

        logger.info("论文处理完毕，成功 %d/%d 篇。", len(results), len(papers))
        return results

    async def _process_single_paper(
        self,
        paper: arxiv_client.ArxivPaper,
        *,
        index: int = 0,
        options: SendOptions,
    ) -> tuple[list[MessageChain], PaperArtifacts]:
        """处理单篇论文：翻译摘要、下载 PDF、截图、总结、渲染输出。

        等价于 ``_prepare_paper``（平台无关产物）+ ``_format_prepared``
        （按选项格式化），供单篇处理（/arxiv get、WebUI 手动发送）使用。

        Returns:
            (消息链列表, 处理产物)。产物含截图/卡片本地路径与 LLM 结果文本。
        """
        prepared = await self._prepare_paper(paper, options)
        processed = await self._format_prepared(prepared, options, index=index)
        return processed.chains, processed.artifacts

    async def _prepare_paper(
        self,
        paper: arxiv_client.ArxivPaper,
        options: SendOptions,
    ) -> PreparedPaper:
        """准备论文的平台无关产物（LLM 翻译、PDF、截图、总结、摘要图片）。

        这些操作（尤其 LLM 调用与 PDF 下载）成本最高且与目标平台无关，
        多会话推送时只执行一次，各会话再用 ``_format_prepared`` 按需格式化。

        Args:
            paper: 论文元数据。
            options: 发送覆盖选项，值为 None 的字段跟随配置。

        Returns:
            平台无关产物，供 ``_format_prepared`` 使用。
        """
        logger.info("[%s] 开始处理: %s", paper.arxiv_id, paper.title)
        abstract_text = ""
        summary_text = ""
        screenshot_path = ""
        pdf_path_str = ""
        # 覆盖参数为 None 时跟随配置，否则使用调用方覆盖值（WebUI 测试用）
        send_as_image = options.send_as_image
        if send_as_image is None:
            send_as_image = self._platform_cfg.get("send_as_image", False)
        attach_pdf = options.attach_pdf
        if attach_pdf is None:
            attach_pdf = self._send_cfg.get("attach_pdf", False)
        screenshot_pdf = options.screenshot_pdf
        if screenshot_pdf is None:
            screenshot_pdf = self._send_cfg.get("screenshot_pdf", True)
        send_abstract = options.send_abstract
        if send_abstract is None:
            send_abstract = self._send_cfg.get("send_abstract", True)

        timeout = self._arxiv_cfg.get("timeout_seconds", 30)
        max_pdf_size = self._send_cfg.get("max_pdf_size_mb", 20)

        # 摘要处理
        abstract_zh = ""
        if send_abstract:
            abstract_mode = self._llm_cfg.get("abstract_mode", "original")
            if abstract_mode == "llm_chinese" and paper.abstract:
                provider_id = self._llm_cfg.get("translate_provider_id", "")
                logger.info("[%s] 使用 LLM 翻译摘要...", paper.arxiv_id)
                abstract_text = await llm_service.translate_abstract(
                    self.context,
                    paper.abstract,
                    provider_id=provider_id,
                )
                # 翻译成功（返回值与原文不同）时记入论文库，供 WebUI 展示
                if abstract_text != paper.abstract:
                    abstract_zh = abstract_text
                logger.info("[%s] 摘要翻译完成。", paper.arxiv_id)
            else:
                abstract_text = paper.abstract

        # 是否需要下载 PDF（截图、附件、LLM 总结都需要 PDF）
        need_pdf = (
            screenshot_pdf or attach_pdf or self._llm_cfg.get("llm_summarize", False)
        )

        downloaded_pdf: Path | None = None
        pdf_skip_reason: str = ""
        if need_pdf and paper.pdf_url:
            logger.info("[%s] 下载 PDF: %s", paper.arxiv_id, paper.pdf_url)
            # 开启存储时 PDF 下载到论文库目录持久化保存，供 WebUI 在线查看；
            # 关闭时下载到临时目录，随插件卸载清理
            download_dir = (
                self._paper_store.papers_dir
                if self._paper_store and self._paper_store_enabled
                else self._temp_dir
            )
            try:
                downloaded_pdf = await pdf_handler.download_pdf(
                    paper.pdf_url,
                    download_dir,
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
        need_screenshot = screenshot_pdf or self._llm_cfg.get("llm_summarize", False)
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

        # 摘要渲染为图片（或文本）。按图发送模式下摘要并入卡片，跳过 PIL 渲染。
        abstract_image_path = ""
        use_abstract_image = (
            self._send_cfg.get("abstract_as_image", True) and not send_as_image
        )

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
        if downloaded_pdf and attach_pdf:
            pdf_path_str = str(downloaded_pdf)

        return PreparedPaper(
            paper=paper,
            abstract_text=abstract_text,
            abstract_zh=abstract_zh,
            summary_text=summary_text,
            screenshot_path=screenshot_path,
            pdf_path_str=pdf_path_str,
            abstract_image_path=abstract_image_path,
            pdf_skip_reason=pdf_skip_reason,
        )

    async def _format_prepared(
        self,
        prepared: PreparedPaper,
        options: SendOptions,
        *,
        index: int = 0,
    ) -> ProcessedPaper:
        """把平台无关产物格式化为消息链（按图或文本），返回处理结果。

        仅文本格式（plain / markdown）随平台变化；按图渲染与产物复用
        ``_prepare_paper`` 的结果。

        Args:
            prepared: ``_prepare_paper`` 的产物。
            options: 发送覆盖选项，值为 None 的字段跟随配置。
            index: 论文序号（0 表示无序号）。

        Returns:
            处理结果（消息链 + 产物）。
        """
        paper = prepared.paper
        send_as_image = options.send_as_image
        if send_as_image is None:
            send_as_image = self._platform_cfg.get("send_as_image", False)
        send_abstract = options.send_abstract
        if send_abstract is None:
            send_abstract = self._send_cfg.get("send_abstract", True)
        text_format = options.text_format

        artifacts = PaperArtifacts(
            screenshot_path=prepared.screenshot_path,
            abstract_zh=prepared.abstract_zh,
            llm_summary=prepared.summary_text,
        )

        # 按图发送：整篇论文渲染为一张卡片图片
        if send_as_image:
            image_chains, card_path = await self._build_image_paper_chains(
                paper,
                index=index,
                abstract_text=prepared.abstract_text if send_abstract else "",
                summary_text=prepared.summary_text,
                screenshot_path=prepared.screenshot_path,
                pdf_path=prepared.pdf_path_str,
                extra_message=prepared.pdf_skip_reason,
                image_template=options.image_template,
            )
            if image_chains is not None:
                artifacts.card_path = card_path
                return ProcessedPaper(paper, image_chains, artifacts)
            logger.warning("[%s] 按图发送渲染失败，回退为文本模式。", paper.arxiv_id)

        chains = formatter.build_paper_chains(
            paper,
            index=index,
            show_abstract=send_abstract,
            abstract_text=prepared.abstract_text,
            summary_text=prepared.summary_text,
            screenshot_path=prepared.screenshot_path,
            pdf_path=prepared.pdf_path_str,
            abstract_image_path=prepared.abstract_image_path,
            extra_message=prepared.pdf_skip_reason,
            markdown=text_format == "markdown",
        )
        return ProcessedPaper(paper, chains, artifacts)

    async def _build_image_paper_chains(
        self,
        paper: arxiv_client.ArxivPaper,
        *,
        index: int = 0,
        abstract_text: str = "",
        summary_text: str = "",
        screenshot_path: str = "",
        pdf_path: str = "",
        extra_message: str = "",
        image_template: str | None = None,
    ) -> tuple[list[MessageChain] | None, str]:
        """将整篇论文渲染为一张卡片图片（按图发送模式）。

        元信息、摘要与 AI 总结合并渲染为一张 HTML 卡片图片；
        PDF 首页截图以 Base64 内嵌进卡片；PDF 附件仍单独发送。

        Args:
            image_template: 覆盖卡片模板名，None 时跟随平台配置。

        Returns:
            (消息链列表, 卡片图片本地路径)；渲染失败时返回 (None, "")，
            由调用方回退为文本模式。
        """
        screenshot_b64 = ""
        if screenshot_path:
            try:
                screenshot_b64 = base64.b64encode(
                    Path(screenshot_path).read_bytes()
                ).decode("ascii")
            except OSError:
                logger.warning(
                    "[%s] 读取 PDF 截图失败，卡片中不含截图。", paper.arxiv_id
                )

        data = image_render.build_paper_template_data(
            paper,
            index=index,
            abstract_text=abstract_text,
            summary_text=summary_text,
            screenshot_b64=screenshot_b64,
            extra_message=extra_message,
        )
        template_name = image_template or self._platform_cfg.get(
            "image_template", "default"
        )
        try:
            template_str = image_render.load_template(
                self._data_dir / "templates",
                template_name,
            )
            rendered_path = await self.html_render(
                template_str,
                data,
                return_url=False,
                options={"full_page": True, "type": "jpeg", "quality": 90},
            )
        except Exception:
            logger.exception("[%s] 论文卡片渲染失败。", paper.arxiv_id)
            return None, ""

        img_chain = MessageChain()
        img_chain.chain.append(Image.fromFileSystem(str(rendered_path)))
        chains = [img_chain]

        # PDF 附件无法并入图片，单独发送
        if pdf_path:
            pdf_chain = MessageChain()
            pdf_name = f"{paper.arxiv_id.replace('/', '_')}.pdf"
            pdf_chain.chain.append(File(name=pdf_name, file=pdf_path))
            chains.append(pdf_chain)

        return chains, str(rendered_path)

    async def _build_info_chains(
        self,
        papers: list[arxiv_client.ArxivPaper],
        *,
        text_format: str = "plain",
    ) -> list[MessageChain]:
        """为 search/latest 构建消息链列表，支持摘要渲染为图片和 LLM 翻译。

        每条论文生成 1~2 条消息：
        1. 基本信息（标题、作者、链接等）
        2. 摘要图片（如果 abstract_as_image 开启）

        按图发送模式下每篇论文渲染为一张卡片图片（元信息 + 摘要）。
        """
        chains: list[MessageChain] = []
        send_abstract = self._send_cfg.get("send_abstract", True)
        send_as_image = self._platform_cfg.get("send_as_image", False)
        abstract_as_image = (
            self._send_cfg.get("abstract_as_image", True) and not send_as_image
        )
        abstract_mode = self._llm_cfg.get("abstract_mode", "original")
        translate_provider_id = self._llm_cfg.get("translate_provider_id", "")
        markdown = text_format == "markdown"

        for i, paper in enumerate(papers, 1):
            # 摘要处理（支持 LLM 翻译）
            abstract_text = paper.abstract
            if send_abstract and abstract_mode == "llm_chinese" and paper.abstract:
                logger.info("[%s] 使用 LLM 翻译摘要...", paper.arxiv_id)
                abstract_text = await llm_service.translate_abstract(
                    self.context,
                    paper.abstract,
                    provider_id=translate_provider_id,
                )

            # 按图发送：元信息与摘要以一张卡片图片呈现
            if send_as_image:
                image_chains, _card_path = await self._build_image_paper_chains(
                    paper,
                    index=i,
                    abstract_text=abstract_text if send_abstract else "",
                )
                if image_chains is not None:
                    chains.extend(image_chains)
                    continue
                logger.warning(
                    "[%s] 按图发送渲染失败，回退为文本模式。", paper.arxiv_id
                )

            # 基本信息（摘要是否内嵌取决于是否以图片形式发送）
            show_in_text = send_abstract and not abstract_as_image
            info_chain = MessageChain()
            text = formatter.format_paper_text(
                paper,
                index=i,
                show_abstract=show_in_text,
                abstract_text=abstract_text,
                markdown=markdown,
            )
            text += f"\n\n💡 使用 /arxiv get {paper.arxiv_id} 获取完整内容（含 PDF）"
            info_chain.chain.append(Plain(text))
            chains.append(info_chain)

            # 摘要渲染为图片
            if send_abstract and abstract_as_image and abstract_text:
                img_name = f"abstract_{paper.arxiv_id.replace('/', '_')}.png"
                img_path = self._temp_dir / img_name
                rendered = text_render.render_abstract_image(abstract_text, img_path)
                if rendered:
                    img_chain = MessageChain()
                    img_chain.chain.append(Image.fromFileSystem(str(rendered)))
                    chains.append(img_chain)
                else:
                    # 渲染失败时回退为文本
                    fallback_chain = MessageChain()
                    heading = "### 📝 摘要\n\n" if markdown else "📝 摘要:\n"
                    fallback_chain.chain.append(Plain(f"{heading}{abstract_text}"))
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
        chains = await self._build_info_chains(
            papers,
            text_format=self._text_format_for_event(event),
        )

        platform_cfg = self._settings_for_event(event)
        if platform_cfg.get("use_forward", False):
            bot_name = platform_cfg.get("bot_name", "ArXiv Bot")
            forwardMsg, fileChains = formatter.build_forward_nodes(
                chains, bot_name=bot_name
            )
            yield self._make_result(forwardMsg)
            for fileChain in fileChains:
                yield self._make_result(fileChain)
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

        options = SendOptions(text_format=self._text_format_for_event(event))
        results = await self._process_papers([paper], options)
        chains = [chain for r in results for chain in r.chains]
        if not chains:
            yield event.plain_result("📭 处理论文时出错。")
            return

        platform_cfg = self._settings_for_event(event)
        if platform_cfg.get("use_forward", False):
            bot_name = platform_cfg.get("bot_name", "ArXiv Bot")
            forwardMsg, fileChains = formatter.build_forward_nodes(
                chains, bot_name=bot_name
            )
            yield self._make_result(forwardMsg)
            for fileChain in fileChains:
                yield self._make_result(fileChain)
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
        chains = await self._build_info_chains(
            papers,
            text_format=self._text_format_for_event(event),
        )

        platform_cfg = self._settings_for_event(event)
        if platform_cfg.get("use_forward", False):
            bot_name = platform_cfg.get("bot_name", "ArXiv Bot")
            forwardMsg, fileChains = formatter.build_forward_nodes(
                chains, bot_name=bot_name
            )
            yield self._make_result(forwardMsg)
            for fileChain in fileChains:
                yield self._make_result(fileChain)
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
        push_time = self._push_cfg.get("push_time", "09:00")
        timezone = self._push_cfg.get("push_timezone", "Asia/Shanghai")
        targets = self._push_cfg.get("target_sessions", [])
        platform_cfg = self._settings_for_event(event)
        use_forward = platform_cfg.get("use_forward", False)
        abstract_mode = self._llm_cfg.get("abstract_mode", "original")
        max_results = self._arxiv_cfg.get("max_results", 5)

        mode_display = "合并转发" if use_forward else "逐条发送"
        abstract_display = "原文" if abstract_mode == "original" else "LLM 中文翻译"
        text_format_display = self._text_format_for_event(event)

        lines = [
            "📊 ArXiv 插件状态",
            "",
            f"📚 学科分类: {', '.join(categories) or '未配置'}",
            f"🏷️ 关键词: {', '.join(tags) or '无'}",
            f"📄 每次推送: {max_results} 篇",
            f"⏰ 推送时间: {push_time} ({timezone})",
            f"🎯 目标会话: {len(targets)} 个",
            f"📨 发送模式: {mode_display}（当前平台）",
            f"🔤 文本格式: {text_format_display}（当前平台）",
            f"🌅 按图发送: {'开启' if self._platform_cfg.get('send_as_image') else '关闭'}",
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

        push_cfg = self.config.get("push_config", {})
        targets: list[str] = list(push_cfg.get("target_sessions", []))
        if umo in targets:
            yield event.plain_result("ℹ️ 当前会话已在推送列表中。")
            return

        targets.append(umo)
        push_cfg["target_sessions"] = targets
        self.config["push_config"] = push_cfg
        self.config.save_config()

        yield event.plain_result(f"✅ 已添加当前会话到推送列表。\n会话标识: {umo}")

    @arxiv_group.command("remove_session")
    async def cmd_remove_session(self, event: AstrMessageEvent):
        """将当前会话从推送目标中移除。用法: /arxiv remove_session"""
        umo = event.unified_msg_origin

        push_cfg = self.config.get("push_config", {})
        targets: list[str] = list(push_cfg.get("target_sessions", []))
        if umo not in targets:
            yield event.plain_result("ℹ️ 当前会话不在推送列表中。")
            return

        targets.remove(umo)
        push_cfg["target_sessions"] = targets
        self.config["push_config"] = push_cfg
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
