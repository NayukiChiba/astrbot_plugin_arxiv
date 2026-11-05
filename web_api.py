"""ArXiv 插件 WebUI 页面的后端 Web API。

通过 ``context.register_web_api`` 注册 REST 端点，供 ``pages/arxiv/``
下的插件页面（iframe）调用。前端经 Bridge SDK 访问时，请求会被
Dashboard 转发到 ``/api/plug/astrbot_plugin_arxiv/<route>``。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING, Any

from astrbot.api import logger
from astrbot.api.web import error_response, file_response, json_response, request

from . import arxiv_client, formatter, image_render
from .send_options import SendOptions

if TYPE_CHECKING:
    from .main import ArxivPlugin

PLUGIN_NAME = "astrbot_plugin_arxiv"


def _opt_bool(value: Any) -> bool | None:
    """规范化请求体中的可选布尔值：None 保持 None，其余转 bool。"""
    return None if value is None else bool(value)


def paper_to_dict(paper: arxiv_client.ArxivPaper) -> dict[str, Any]:
    """将 ArxivPaper 序列化为可 JSON 传输的字典。"""
    return {
        "arxiv_id": paper.arxiv_id,
        "title": paper.title,
        "authors": paper.authors,
        "abstract": paper.abstract,
        "categories": paper.categories,
        "published": paper.published,
        "abs_url": paper.abs_url,
        "pdf_url": paper.pdf_url,
    }


def paper_from_dict(data: dict[str, Any]) -> arxiv_client.ArxivPaper:
    """从字典构造 ArxivPaper，缺失字段使用默认值。"""
    return arxiv_client.ArxivPaper(
        arxiv_id=str(data.get("arxiv_id", "")),
        title=str(data.get("title", "")),
        authors=[str(a) for a in data.get("authors", [])],
        abstract=str(data.get("abstract", "")),
        categories=[str(c) for c in data.get("categories", [])],
        published=str(data.get("published", "")),
        pdf_url=str(data.get("pdf_url", "")),
        abs_url=str(data.get("abs_url", "")),
    )


def _file_to_data_url(path: Path, mime: str) -> str:
    """把本地图片文件读为 base64 data URL（用于 WebUI iframe 内经鉴权展示）。"""
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


class ArxivWebAPI:
    """ArXiv 插件页面的 Web API 处理器集合。"""

    def __init__(self, plugin: ArxivPlugin) -> None:
        self.plugin = plugin

    # ── 概览 ────────────────────────────────────────────────

    async def api_overview(self):
        """返回插件配置摘要、平台列表、模板列表与存储状态。"""
        plugin = self.plugin
        send_cfg = plugin._send_cfg
        push_cfg = plugin._push_cfg

        platforms = []
        try:
            for inst in plugin.context.platform_manager.platform_insts:
                meta = inst.meta()
                # 后端直接算好该实例的生效设置，前端无需重复合并规则
                eff = plugin._platform_settings(meta.name, meta.id)
                platforms.append(
                    {
                        "id": meta.id,
                        "name": meta.name,
                        "effective": {
                            "text_format": eff.get("text_format", "plain"),
                            "use_forward": eff.get("use_forward", False),
                            "bot_name": eff.get("bot_name", "ArXiv Bot"),
                        },
                    }
                )
        except Exception:
            logger.debug("获取平台列表失败。", exc_info=True)

        storage: dict[str, Any] = {
            "enabled": plugin._paper_store_enabled,
            "retention_days": plugin._storage_cfg.get("paper_retention_days", 7),
            "count": 0,
            "total_size_bytes": 0,
        }
        if plugin._paper_store:
            _, records = plugin._paper_store.list_papers(limit=100000)
            storage["count"] = len(records)
            storage["total_size_bytes"] = plugin._paper_store.total_size_bytes()

        return json_response(
            {
                "config": {
                    "categories": plugin._arxiv_cfg.get("categories", []),
                    "tags": plugin._arxiv_cfg.get("tags", []),
                    "max_results": plugin._arxiv_cfg.get("max_results", 5),
                    "push_time": push_cfg.get("push_time", "09:00"),
                    "push_timezone": push_cfg.get("push_timezone", "Asia/Shanghai"),
                    "target_sessions": push_cfg.get("target_sessions", []),
                    "platform_config": plugin.config.get("platform_config", {}),
                    "send_as_image": plugin._platform_cfg.get("send_as_image", False),
                    "image_template": plugin._platform_cfg.get(
                        "image_template", "default"
                    ),
                    "send_abstract": send_cfg.get("send_abstract", True),
                    "attach_pdf": send_cfg.get("attach_pdf", False),
                    "screenshot_pdf": send_cfg.get("screenshot_pdf", True),
                    "abstract_mode": plugin._llm_cfg.get("abstract_mode", "original"),
                    "llm_summarize": plugin._llm_cfg.get("llm_summarize", False),
                },
                "templates": plugin.list_card_templates(),
                "platforms": platforms,
                "storage": storage,
            }
        )

    # ── 配置读写 ─────────────────────────────────────────────

    # 允许在线编辑的配置组（键 -> 该组内允许更新的字段）
    _EDITABLE_CONFIG = {
        "arxiv_config": None,  # None 表示整组可改
        "push_config": None,
        "send_config": None,
        "platform_config": None,
        "storage_config": None,
        "llm_config": None,
    }

    async def api_config_get(self):
        """返回当前完整插件配置（六分组）。"""
        cfg = self.plugin.config
        return json_response(
            {group: cfg.get(group, {}) for group in self._EDITABLE_CONFIG}
        )

    async def api_config_update(self):
        """更新指定配置组并保存。仅接受已知的六个配置组。"""
        body = await request.json(default={}) or {}
        group = str(body.get("group", ""))
        values = body.get("values")
        if group not in self._EDITABLE_CONFIG:
            return error_response(f"不允许修改的配置组: {group}")
        if not isinstance(values, dict):
            return error_response("values 必须是对象")

        plugin = self.plugin
        plugin.config[group] = values
        try:
            plugin.config.save_config()
        except Exception as e:
            logger.exception("WebUI 保存配置失败。")
            return error_response(f"保存配置失败: {e}", status_code=500)

        # 定时推送配置变更后重注册定时任务
        if group == "push_config":
            try:
                await plugin._register_cron_job()
            except Exception:
                logger.exception("重注册定时任务失败。")
        # 关闭存储时清空论文库
        if group == "storage_config" and not values.get("store_papers", True):
            if plugin._paper_store:
                plugin._paper_store.clear_all()

        logger.info("WebUI 更新了配置组 %s。", group)
        return json_response({"ok": True})

    # ── 论文库 ───────────────────────────────────────────────

    async def api_papers(self):
        """列出已发送的论文（发送时间倒序，支持分页与会话过滤）。"""
        store = self.plugin._paper_store
        if not store:
            return error_response("论文库未初始化", status_code=503)

        query = request.query
        try:
            limit = min(int(query.get("limit", "50")), 200)
            offset = max(int(query.get("offset", "0")), 0)
        except ValueError:
            return error_response("limit/offset 参数必须是整数")
        session = query.get("session", "")

        total, records = store.list_papers(
            limit=limit,
            offset=offset,
            session=session,
        )
        # 列表页不返回摘要全文，减少传输体积
        items = [{**r, "abstract": ""} for r in records]
        return json_response(
            {
                "total": total,
                "items": items,
                "storage_enabled": self.plugin._paper_store_enabled,
            }
        )

    async def api_paper_detail(self):
        """返回单篇论文的完整元数据（含摘要）。"""
        store = self.plugin._paper_store
        arxiv_id = request.query.get("arxiv_id", "")
        if not store or not arxiv_id:
            return error_response("缺少 arxiv_id 参数")

        record = store.get(arxiv_id)
        if not record:
            return error_response("论文不存在", status_code=404)
        return json_response(
            {
                **record,
                "has_pdf": store.pdf_path(arxiv_id) is not None,
                "has_screenshot": store.screenshot_path(arxiv_id) is not None,
                "has_card": store.card_path(arxiv_id) is not None,
            }
        )

    async def api_paper_pdf(self):
        """返回论文对应的 PDF 文件。"""
        store = self.plugin._paper_store
        arxiv_id = request.query.get("arxiv_id", "")
        if not store or not arxiv_id:
            return error_response("缺少 arxiv_id 参数")

        pdf_path = store.pdf_path(arxiv_id)
        if not pdf_path:
            return error_response("该论文没有可用的 PDF 文件", status_code=404)
        return file_response(
            pdf_path,
            filename=pdf_path.name,
            content_type="application/pdf",
        )

    async def api_paper_screenshot(self):
        """返回论文对应的 PDF 首页截图（format=base64 时返回 JSON data URL）。"""
        store = self.plugin._paper_store
        arxiv_id = request.query.get("arxiv_id", "")
        if not store or not arxiv_id:
            return error_response("缺少 arxiv_id 参数")

        path = store.screenshot_path(arxiv_id)
        if not path:
            return error_response("该论文没有可用的首页截图", status_code=404)
        if request.query.get("format") == "base64":
            return json_response({"data_url": _file_to_data_url(path, "image/png")})
        return file_response(path, filename=path.name, content_type="image/png")

    async def api_paper_card(self):
        """返回论文对应的按图发送卡片图片（format=base64 时返回 JSON data URL）。"""
        store = self.plugin._paper_store
        arxiv_id = request.query.get("arxiv_id", "")
        if not store or not arxiv_id:
            return error_response("缺少 arxiv_id 参数")

        path = store.card_path(arxiv_id)
        if not path:
            return error_response("该论文没有可用的卡片图片", status_code=404)
        suffix = path.suffix.lstrip(".").lower() or "jpeg"
        mime = "image/jpeg" if suffix in ("jpg", "jpeg") else f"image/{suffix}"
        if request.query.get("format") == "base64":
            return json_response({"data_url": _file_to_data_url(path, mime)})
        return file_response(path, filename=path.name, content_type=mime)

    async def api_paper_preview(self):
        """用论文库存储的数据按当前配置渲染发送预览（纯文本 / Markdown / 卡片图片）。

        复用已保存的中文摘要、AI 总结与 PDF 首页截图，不重新调用 LLM；
        渲染出的卡片会持久化到论文库，作为该论文的最新卡片样式。
        """
        body = await request.json(default={}) or {}
        arxiv_id = str(body.get("arxiv_id", ""))
        store = self.plugin._paper_store
        if not store or not arxiv_id:
            return error_response("缺少 arxiv_id 参数")
        record = store.get(arxiv_id)
        if not record:
            return error_response("论文不存在", status_code=404)

        plugin = self.plugin
        paper = paper_from_dict(record)
        # 摘要：当前配置为中文翻译且有已存译文时用译文，否则用原文
        show_abstract = plugin._send_cfg.get("send_abstract", True)
        abstract_text = ""
        if show_abstract:
            use_zh = plugin._llm_cfg.get(
                "abstract_mode", "original"
            ) == "llm_chinese" and record.get("abstract_zh")
            abstract_text = record["abstract_zh"] if use_zh else paper.abstract
        # AI 总结：仅在当前配置开启总结时随消息发送，预览保持一致
        summary_text = (
            record.get("llm_summary", "")
            if plugin._llm_cfg.get("llm_summarize", False)
            else ""
        )

        result: dict[str, Any] = {
            "plain": formatter.format_paper_text(
                paper,
                index=1,
                show_abstract=show_abstract,
                abstract_text=abstract_text,
                summary_text=summary_text,
                markdown=False,
            ),
            "markdown": formatter.format_paper_text(
                paper,
                index=1,
                show_abstract=show_abstract,
                abstract_text=abstract_text,
                summary_text=summary_text,
                markdown=True,
            ),
        }

        # 卡片预览：用库存截图与 LLM 结果重新渲染，并持久化为最新卡片
        send_as_image = body.get("send_as_image")
        if send_as_image is None:
            send_as_image = plugin._platform_cfg.get("send_as_image", False)
        if send_as_image:
            screenshot_b64 = ""
            shot = store.screenshot_path(arxiv_id)
            if shot:
                screenshot_b64 = base64.b64encode(shot.read_bytes()).decode("ascii")
            template_name = str(
                body.get("image_template")
                or plugin._platform_cfg.get("image_template", "default")
            )
            try:
                data = image_render.build_paper_template_data(
                    paper,
                    index=1,
                    abstract_text=abstract_text,
                    summary_text=summary_text,
                    screenshot_b64=screenshot_b64,
                )
                template_str = image_render.load_template(
                    plugin._data_dir / "templates",
                    template_name,
                )
                rendered = await plugin.html_render(
                    template_str,
                    data,
                    return_url=False,
                    options={"full_page": True, "type": "jpeg", "quality": 90},
                )
                rendered_path = Path(str(rendered))
                result["image_base64"] = base64.b64encode(
                    rendered_path.read_bytes()
                ).decode("ascii")
                store.update_card(arxiv_id, rendered_path)
                rendered_path.unlink(missing_ok=True)
            except Exception as e:
                logger.exception("WebUI 论文库卡片预览渲染失败。")
                result["image_error"] = str(e)

        return json_response(result)

    async def api_papers_delete(self):
        """批量删除论文记录及其关联文件。"""
        store = self.plugin._paper_store
        if not store:
            return error_response("论文库未初始化", status_code=503)

        body = await request.json(default={}) or {}
        arxiv_ids = body.get("arxiv_ids", [])
        if not isinstance(arxiv_ids, list) or not arxiv_ids:
            return error_response("缺少 arxiv_ids 列表")
        arxiv_ids = [str(i) for i in arxiv_ids]

        removed = store.delete_papers(arxiv_ids)
        return json_response(
            {
                "removed": removed,
                "count": len(store.list_papers(limit=100000)[1]),
                "total_size_bytes": store.total_size_bytes(),
            }
        )

    async def api_papers_cleanup(self):
        """立即清理过期与孤立的论文库内容，返回清理统计。"""
        store = self.plugin._paper_store
        if not store:
            return error_response("论文库未初始化", status_code=503)

        removed = store.cleanup_old()
        return json_response(
            {
                "removed": removed,
                "count": len(store.list_papers(limit=100000)[1]),
                "total_size_bytes": store.total_size_bytes(),
            }
        )

    # ── 获取 / 预览 / 发送 ──────────────────────────────────

    async def api_fetch(self):
        """从 arXiv 获取论文（不发送），用于 WebUI 预览与手动发送。"""
        body = await request.json(default={}) or {}
        mode = body.get("mode", "latest")
        timeout = self.plugin._arxiv_cfg.get("timeout_seconds", 30)
        max_results = body.get("max_results") or self.plugin._arxiv_cfg.get(
            "max_results", 5
        )
        try:
            max_results = max(1, min(int(max_results), 20))
        except (TypeError, ValueError):
            max_results = 5

        try:
            if mode == "search":
                query = str(body.get("query", "")).strip()
                if not query:
                    return error_response("搜索模式需要 query 参数")
                papers = await arxiv_client.search_papers(
                    query,
                    max_results=max_results,
                    timeout=timeout,
                )
            elif mode == "id":
                arxiv_id = arxiv_client.extractArxivId(str(body.get("query", "")))
                if not arxiv_id:
                    return error_response("ID 模式需要 query 参数（arxiv ID 或链接）")
                paper = await arxiv_client.get_paper_by_id(arxiv_id, timeout=timeout)
                papers = [paper] if paper else []
            else:
                papers = await arxiv_client.get_latest_papers(
                    categories=self.plugin._arxiv_cfg.get("categories", ["cs.AI"]),
                    tags=self.plugin._arxiv_cfg.get("tags", []),
                    max_results=max_results,
                    timeout=timeout,
                )
        except TimeoutError:
            return error_response("请求 arXiv 超时，请稍后重试", status_code=504)
        except Exception as e:
            logger.exception("WebUI 获取论文失败。")
            return error_response(f"获取论文失败: {e}", status_code=502)

        return json_response({"papers": [paper_to_dict(p) for p in papers]})

    async def api_preview(self):
        """预览一篇论文在各种输出设置下的内容（纯文本 / Markdown / 卡片图片）。"""
        body = await request.json(default={}) or {}
        paper_data = body.get("paper")
        if not isinstance(paper_data, dict) or not paper_data.get("title"):
            return error_response("缺少 paper 字段")

        paper = paper_from_dict(paper_data)
        show_abstract = bool(body.get("send_abstract", True))
        abstract_text = paper.abstract if show_abstract else ""

        result: dict[str, Any] = {
            "plain": formatter.format_paper_text(
                paper,
                index=1,
                show_abstract=show_abstract,
                abstract_text=abstract_text,
                markdown=False,
            ),
            "markdown": formatter.format_paper_text(
                paper,
                index=1,
                show_abstract=show_abstract,
                abstract_text=abstract_text,
                markdown=True,
            ),
        }

        # 按图发送预览：渲染卡片并返回 base64 图片
        if body.get("send_as_image"):
            template_name = body.get("image_template") or self.plugin._platform_cfg.get(
                "image_template", "default"
            )
            try:
                data = image_render.build_paper_template_data(
                    paper,
                    index=1,
                    abstract_text=abstract_text,
                )
                template_str = image_render.load_template(
                    self.plugin._data_dir / "templates",
                    template_name,
                )
                rendered = await self.plugin.html_render(
                    template_str,
                    data,
                    return_url=False,
                    options={"full_page": True, "type": "jpeg", "quality": 90},
                )
                rendered_path = Path(str(rendered))
                result["image_base64"] = base64.b64encode(
                    rendered_path.read_bytes()
                ).decode("ascii")
                rendered_path.unlink(missing_ok=True)
            except Exception as e:
                logger.exception("WebUI 卡片预览渲染失败。")
                result["image_error"] = str(e)

        return json_response(result)

    async def api_send(self):
        """将一篇论文发送到指定会话（完整处理流程，含 PDF 下载）。"""
        body = await request.json(default={}) or {}
        paper_data = body.get("paper")
        session = str(body.get("session", "")).strip()
        if not isinstance(paper_data, dict) or not paper_data.get("arxiv_id"):
            return error_response("缺少 paper 字段")
        paper = paper_from_dict(paper_data)
        return await self._send_and_record(paper, session, body, mark_sent=True)

    async def api_resend(self):
        """把论文库中已推送的论文重新发送到指定会话。"""
        body = await request.json(default={}) or {}
        arxiv_id = str(body.get("arxiv_id", ""))
        session = str(body.get("session", "")).strip()
        store = self.plugin._paper_store
        if not store or not arxiv_id:
            return error_response("缺少 arxiv_id 参数")

        record = store.get(arxiv_id)
        if not record:
            return error_response("论文不存在", status_code=404)
        paper = paper_from_dict(record)
        return await self._send_and_record(paper, session, body, mark_sent=False)

    async def _send_and_record(
        self,
        paper: arxiv_client.ArxivPaper,
        session: str,
        body: dict[str, Any],
        *,
        mark_sent: bool,
    ):
        """处理单篇论文并发送到会话，按需记录去重历史与论文库。

        send 与 resend 的公共逻辑，仅论文来源与是否标记去重历史不同。

        Args:
            paper: 待发送论文。
            session: 目标会话 UMO。
            body: 原始请求体（读取 text_format 与覆盖参数）。
            mark_sent: 是否在去重历史中标记（重发不占用去重记录）。
        """
        session = session.strip()
        if ":" not in session:
            return error_response(
                "session 必须是合法的 UMO（如 aiocqhttp:GroupMessage:123）"
            )

        plugin = self.plugin
        options = SendOptions(
            text_format=plugin.text_format_for_session(
                session, str(body.get("text_format", "auto"))
            ),
            send_as_image=_opt_bool(body.get("send_as_image")),
            attach_pdf=_opt_bool(body.get("attach_pdf")),
            screenshot_pdf=_opt_bool(body.get("screenshot_pdf")),
            send_abstract=_opt_bool(body.get("send_abstract")),
            image_template=str(body.get("image_template") or "").strip() or None,
        )
        results = await plugin._process_papers([paper], options)
        if not results:
            return error_response("论文处理失败，未生成任何消息", status_code=500)

        chains = [c for r in results for c in r.chains]
        await plugin._deliver_chains(session, chains)

        # 去重历史与论文库（论文库仅在开启存储时写入；重发不占用去重记录）
        if mark_sent and plugin._history:
            plugin._history.mark_sent_batch(session, [paper.arxiv_id])
        for r in results:
            plugin._store_record(r.paper, session, r.artifacts)

        logger.info("WebUI 发送论文 %s 至 %s。", paper.arxiv_id, session)
        return json_response({"ok": True, "chains": len(chains)})

    async def api_trigger_push(self):
        """手动触发一次定时推送（用于测试）。"""
        try:
            await self.plugin._scheduled_push()
        except Exception as e:
            logger.exception("WebUI 手动触发推送失败。")
            return error_response(f"推送失败: {e}", status_code=500)
        return json_response({"ok": True})


def register_web_routes(plugin: ArxivPlugin) -> None:
    """注册 ArXiv 插件页面的全部 Web API 路由。"""
    api = ArxivWebAPI(plugin)
    routes = [
        (f"/{PLUGIN_NAME}/overview", api.api_overview, ["GET"], "插件配置与状态概览"),
        (f"/{PLUGIN_NAME}/config", api.api_config_get, ["GET"], "读取插件配置"),
        (f"/{PLUGIN_NAME}/config", api.api_config_update, ["POST"], "更新插件配置"),
        (f"/{PLUGIN_NAME}/papers", api.api_papers, ["GET"], "已发送论文列表"),
        (f"/{PLUGIN_NAME}/papers/detail", api.api_paper_detail, ["GET"], "论文详情"),
        (f"/{PLUGIN_NAME}/papers/pdf", api.api_paper_pdf, ["GET"], "论文 PDF 文件"),
        (
            f"/{PLUGIN_NAME}/papers/screenshot",
            api.api_paper_screenshot,
            ["GET"],
            "论文 PDF 首页截图",
        ),
        (
            f"/{PLUGIN_NAME}/papers/card",
            api.api_paper_card,
            ["GET"],
            "论文按图发送卡片图片",
        ),
        (
            f"/{PLUGIN_NAME}/papers/preview",
            api.api_paper_preview,
            ["POST"],
            "按当前配置预览已存论文",
        ),
        (
            f"/{PLUGIN_NAME}/papers/delete",
            api.api_papers_delete,
            ["POST"],
            "批量删除论文记录",
        ),
        (
            f"/{PLUGIN_NAME}/papers/cleanup",
            api.api_papers_cleanup,
            ["POST"],
            "清理过期论文库内容",
        ),
        (f"/{PLUGIN_NAME}/fetch", api.api_fetch, ["POST"], "获取论文（不发送）"),
        (f"/{PLUGIN_NAME}/preview", api.api_preview, ["POST"], "预览发送样式"),
        (f"/{PLUGIN_NAME}/send", api.api_send, ["POST"], "发送论文到会话"),
        (f"/{PLUGIN_NAME}/resend", api.api_resend, ["POST"], "重发已推送论文"),
        (
            f"/{PLUGIN_NAME}/push/trigger",
            api.api_trigger_push,
            ["POST"],
            "手动触发定时推送",
        ),
    ]
    for path, handler, methods, desc in routes:
        try:
            plugin.context.register_web_api(path, handler, methods, desc)
        except Exception:
            logger.exception("注册 Web API 路由 %s 失败。", path)
