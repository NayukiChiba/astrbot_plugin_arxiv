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

if TYPE_CHECKING:
    from .main import ArxivPlugin

PLUGIN_NAME = "astrbot_plugin_arxiv"


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
                platforms.append({"id": meta.id, "name": meta.name})
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
            return json_response(
                {"data_url": _file_to_data_url(path, "image/png")}
            )
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
        if ":" not in session:
            return error_response(
                "session 必须是合法的 UMO（如 aiocqhttp:GroupMessage:123）"
            )

        plugin = self.plugin
        paper = paper_from_dict(paper_data)
        text_format = plugin.text_format_for_session(
            session,
            str(body.get("text_format", "auto")),
        )
        send_as_image = body.get("send_as_image")
        if send_as_image is not None:
            send_as_image = bool(send_as_image)

        chains = await plugin._process_papers(
            [paper],
            text_format=text_format,
            send_as_image=send_as_image,
        )
        if not chains:
            return error_response("论文处理失败，未生成任何消息", status_code=500)

        await plugin._deliver_chains(session, chains)

        # 记录去重历史与论文库（论文库仅在开启存储时写入）
        if plugin._history:
            plugin._history.mark_sent_batch(session, [paper.arxiv_id])
        if plugin._paper_store and plugin._paper_store_enabled:
            artifacts = plugin._pending_artifacts.pop(paper.arxiv_id, {})
            plugin._paper_store.record(
                paper,
                session,
                screenshot_file=artifacts.get("screenshot_file", ""),
                card_file=artifacts.get("card_file", ""),
            )

        logger.info("WebUI 手动发送论文 %s 至 %s。", paper.arxiv_id, session)
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
