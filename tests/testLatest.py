"""/arxiv latest 指令测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .. import arxiv_client
from .testHelpers import (
    DEFAULT_CATEGORIES,
    collectTexts,
    collectYields,
    makeEvent,
    makePlugin,
    paper,
    papers,
)


class TestLatestCommand:
    """测试 /arxiv latest 指令。"""

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_latest_papers", new_callable=AsyncMock)
    async def test_basic(self, mockLatest: AsyncMock):
        """获取最新论文并展示全部。"""
        mockLatest.return_value = papers(3)
        plugin = makePlugin()

        results = await collectYields(plugin.cmd_latest(makeEvent()))

        mockLatest.assert_called_once_with(
            categories=DEFAULT_CATEGORIES, tags=[], max_results=5, timeout=30
        )
        text = collectTexts(results)
        for i in (1, 2, 3):
            assert f"Test Paper {i}" in text

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_latest_papers", new_callable=AsyncMock)
    async def test_withLLMtranslation(self, mockLatest: AsyncMock):
        """开启 LLM 中文翻译。"""
        mockLatest.return_value = [paper(abstract="Latest abstract in English.")]

        llmGen = AsyncMock(return_value=MagicMock(completion_text="中文摘要。"))
        plugin = makePlugin({"llm_config": {"abstract_mode": "llm_chinese"}})
        plugin.context.llm_generate = llmGen

        results = await collectYields(plugin.cmd_latest(makeEvent()))

        assert "中文摘要" in collectTexts(results)

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_latest_papers", new_callable=AsyncMock)
    async def test_noPapers(self, mockLatest: AsyncMock):
        """无论文时显示提示。"""
        mockLatest.return_value = []
        plugin = makePlugin()

        results = await collectYields(plugin.cmd_latest(makeEvent()))

        assert "📭" in collectTexts(results)

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_latest_papers", new_callable=AsyncMock)
    async def test_timeout(self, mockLatest: AsyncMock):
        """API 超时时显示错误。"""
        mockLatest.side_effect = TimeoutError("timeout")
        plugin = makePlugin()

        results = await collectYields(plugin.cmd_latest(makeEvent()))

        assert "超时" in collectTexts(results)

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_latest_papers", new_callable=AsyncMock)
    async def test_forwardMode(self, mockLatest: AsyncMock):
        """合并转发模式下正常工作。"""
        mockLatest.return_value = papers(2)
        plugin = makePlugin({"send_config": {"use_forward": True, "bot_name": "TestBot"}})

        results = await collectYields(plugin.cmd_latest(makeEvent()))

        assert len(results) >= 1
