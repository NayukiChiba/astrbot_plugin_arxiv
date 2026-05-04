"""ArXiv 插件测试共享工具函数。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import MessageEventResult

from ..arxiv_client import ArxivPaper
from ..main import ArxivPlugin

DEFAULT_CATEGORIES = ["cs.AI", "cs.LG"]


def paper(**kw: Any) -> ArxivPaper:
    """快速构造测试论文。"""
    d: dict[str, Any] = {
        "arxiv_id": "2501.00001",
        "title": "Test Paper Title",
        "authors": ["Alice", "Bob"],
        "abstract": "This is an English abstract for testing.",
        "categories": DEFAULT_CATEGORIES[:1],
        "published": "2025-01-01T00:00:00Z",
        "updated": "2025-01-02T00:00:00Z",
        "pdf_url": "https://arxiv.org/pdf/2501.00001.pdf",
        "abs_url": "https://arxiv.org/abs/2501.00001",
    }
    d.update(kw)
    return ArxivPaper(**d)


def papers(n: int, **kw: Any) -> list[ArxivPaper]:
    """快速构造 n 篇测试论文。"""
    out: list[ArxivPaper] = []
    for i in range(n):
        idx = i + 1
        out.append(
            paper(
                arxiv_id=f"2501.0000{idx}",
                title=f"Test Paper {idx}",
                pdf_url=f"https://arxiv.org/pdf/2501.0000{idx}.pdf",
                abs_url=f"https://arxiv.org/abs/2501.0000{idx}",
                **kw,
            )
        )
    return out


def collectTexts(results: list[MessageEventResult]) -> str:
    """从 MessageEventResult 列表中提取所有 Plain 文本。"""
    parts: list[str] = []
    for mer in results:
        if not hasattr(mer, "chain") or not mer.chain:
            continue
        for comp in mer.chain:
            if isinstance(comp, Plain):
                parts.append(comp.text)
    return "\n".join(parts)


def collectComponents(results: list[MessageEventResult]) -> list[Any]:
    """从 MessageEventResult 列表中提取所有组件。"""
    comps: list[Any] = []
    for mer in results:
        if not hasattr(mer, "chain") or not mer.chain:
            continue
        comps.extend(mer.chain)
    return comps


def findComponent(results: list[MessageEventResult], compType: type) -> Any | None:
    """从结果列表中查找第一个指定类型的组件。"""
    for comp in collectComponents(results):
        if isinstance(comp, compType):
            return comp
    return None


def makePlugin(configOverride: dict | None = None) -> ArxivPlugin:
    """构造一个最小化可用的 ArxivPlugin 实例。"""
    plugin = ArxivPlugin.__new__(ArxivPlugin)
    plugin.config = MagicMock()
    plugin.config.get = MagicMock()

    _cfg: dict[str, Any] = {
        "arxiv_config": {
            "categories": DEFAULT_CATEGORIES,
            "tags": [],
            "max_results": 5,
            "timeout_seconds": 30,
            "pdf_mirrors": ["https://arxiv.org"],
        },
        "send_config": {
            "push_time": "09:00",
            "push_timezone": "Asia/Shanghai",
            "target_sessions": [],
            "use_forward": False,
            "bot_name": "ArXiv Bot",
            "send_abstract": True,
            "abstract_as_image": False,
            "attach_pdf": False,
            "screenshot_pdf": False,
            "screenshot_dpi": 150,
            "max_pdf_size_mb": 20,
            "history_retention_days": 30,
        },
        "llm_config": {
            "abstract_mode": "original",
            "llm_summarize": False,
            "translate_provider_id": "",
            "summarize_provider_id": "",
            "llm_summary_prompt": "",
        },
    }

    if configOverride:
        for section, values in configOverride.items():
            _cfg[section].update(values)

    def _get(section: str, default: Any = None) -> Any:
        return _cfg.get(section, default)

    plugin.config.get = MagicMock(side_effect=_get)
    plugin._data_dir = Path(tempfile.mkdtemp())
    plugin._temp_dir = plugin._data_dir / "temp"
    plugin._temp_dir.mkdir(parents=True, exist_ok=True)
    plugin._bestMirror = "https://arxiv.org"
    plugin._history = None
    plugin._cron_job_id = ""

    plugin.context = MagicMock()
    plugin.context.llm_generate = AsyncMock()
    plugin.context.send_message = AsyncMock()
    plugin.context.cron_manager = MagicMock()
    plugin.context.cron_manager.add_basic_job = AsyncMock()
    plugin.context.cron_manager.add_basic_job.return_value = MagicMock()
    plugin.context.cron_manager.add_basic_job.return_value.job_id = "test_job_id"

    return plugin


def makeEvent(msgStr: str = "") -> MagicMock:
    """构造 mock 的 AstrMessageEvent。"""
    event = MagicMock(spec=AstrMessageEvent)
    event.unified_msg_origin = "test_session_001"
    event.session_id = "test_session_001"
    event.message_str = msgStr
    event.plain_result = MagicMock()

    def _plainResult(text: str) -> MessageEventResult:
        mer = MessageEventResult()
        mer.chain = [Plain(text)]
        mer.use_t2i_ = False
        return mer

    event.plain_result.side_effect = _plainResult
    return event


async def collectYields(gen) -> list[MessageEventResult]:
    """收集异步生成器的所有 yield 结果。"""
    results: list[MessageEventResult] = []
    async for result in gen:
        results.append(result)
    return results
