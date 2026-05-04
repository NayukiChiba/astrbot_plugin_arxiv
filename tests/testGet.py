"""/arxiv get 指令测试 — PDF 下载、视觉总结、合并转发。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from astrbot.api.message_components import File

from .. import arxiv_client, pdf_handler
from .testHelpers import collectTexts, collectYields, findComponent, makeEvent, makePlugin, paper


class TestGetCommand:
    """测试 /arxiv get 指令。"""

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_paper_by_id", new_callable=AsyncMock)
    async def test_byDirectId(self, mockGet: AsyncMock):
        """直接 arXiv ID 获取论文。"""
        mockGet.return_value = paper()
        plugin = makePlugin()
        event = makeEvent()

        results = await collectYields(plugin.cmd_get(event, "2501.00001"))

        mockGet.assert_called_once_with("2501.00001", timeout=30)
        assert "Test Paper Title" in collectTexts(results)

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_paper_by_id", new_callable=AsyncMock)
    async def test_byUrl(self, mockGet: AsyncMock):
        """arXiv URL 提取 ID 后获取。"""
        mockGet.return_value = paper()
        plugin = makePlugin()

        await collectYields(
            plugin.cmd_get(makeEvent(), "https://arxiv.org/abs/2501.00001")
        )

        mockGet.assert_called_once_with("2501.00001", timeout=30)

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_paper_by_id", new_callable=AsyncMock)
    async def test_notFound(self, mockGet: AsyncMock):
        """论文不存在时显示提示。"""
        mockGet.return_value = None
        plugin = makePlugin()

        results = await collectYields(plugin.cmd_get(makeEvent(), "9999.99999"))

        assert "未找到" in collectTexts(results)

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_paper_by_id", new_callable=AsyncMock)
    async def test_emptyId_showsError(self, mockGet: AsyncMock):
        """空 ID 应返回错误提示。"""
        plugin = makePlugin()

        results = await collectYields(plugin.cmd_get(makeEvent(), ""))

        assert "请提供" in collectTexts(results)
        mockGet.assert_not_called()

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_paper_by_id", new_callable=AsyncMock)
    async def test_timeout(self, mockGet: AsyncMock):
        """API 超时时显示错误。"""
        mockGet.side_effect = TimeoutError("timeout")
        plugin = makePlugin()

        results = await collectYields(plugin.cmd_get(makeEvent(), "2501.00001"))

        assert "超时" in collectTexts(results)


class TestGetLLM:
    """测试 get 指令与 LLM 功能的集成。"""

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_paper_by_id", new_callable=AsyncMock)
    async def test_translation_llmChinese(self, mockGet: AsyncMock):
        """get 时开启 LLM 翻译。"""
        mockGet.return_value = paper(abstract="English abstract for get test.")

        llmGen = AsyncMock(return_value=MagicMock(completion_text="获取测试的中文摘要。"))
        plugin = makePlugin({
            "llm_config": {"abstract_mode": "llm_chinese"},
            "send_config": {"send_abstract": True, "abstract_as_image": False},
        })
        plugin.context.llm_generate = llmGen

        results = await collectYields(plugin.cmd_get(makeEvent(), "2501.00001"))

        assert "获取测试的中文摘要" in collectTexts(results)
        assert llmGen.called

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_paper_by_id", new_callable=AsyncMock)
    @patch.object(pdf_handler, "download_pdf", new_callable=AsyncMock)
    @patch.object(pdf_handler, "screenshot_first_page")
    async def test_visionSummarize(
        self, mockScreenshot, mockDownload: AsyncMock, mockGet: AsyncMock
    ):
        """get 时开启视觉模型总结。"""
        mockGet.return_value = paper()

        pdfPath = Path(tempfile.mktemp(suffix=".pdf"))
        pdfPath.write_bytes(b"%PDF-1.4\nfake pdf content")
        mockDownload.return_value = pdfPath

        scrPath = Path(tempfile.mktemp(suffix=".png"))
        scrPath.touch()
        mockScreenshot.return_value = scrPath

        llmGen = AsyncMock(return_value=MagicMock(completion_text="视觉模型总结。"))
        plugin = makePlugin({
            "llm_config": {"llm_summarize": True},
            "send_config": {"attach_pdf": False, "screenshot_pdf": True, "use_forward": False},
        })
        plugin.context.llm_generate = llmGen

        try:
            results = await collectYields(plugin.cmd_get(makeEvent(), "2501.00001"))

            assert "视觉模型总结" in collectTexts(results)
            assert "image_urls" in llmGen.call_args.kwargs
        finally:
            if pdfPath.exists():
                pdfPath.unlink()
            if scrPath.exists():
                scrPath.unlink()

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_paper_by_id", new_callable=AsyncMock)
    @patch.object(pdf_handler, "download_pdf", new_callable=AsyncMock)
    @patch.object(pdf_handler, "screenshot_first_page")
    async def test_customSummaryPrompt(
        self, mockScreenshot, mockDownload: AsyncMock, mockGet: AsyncMock
    ):
        """自定义总结 prompt 注入验证。"""
        mockGet.return_value = paper()

        pdfPath = Path(tempfile.mktemp(suffix=".pdf"))
        pdfPath.write_bytes(b"%PDF-1.4\nfake")
        mockDownload.return_value = pdfPath

        scrPath = Path(tempfile.mktemp(suffix=".png"))
        scrPath.touch()
        mockScreenshot.return_value = scrPath

        llmGen = AsyncMock(return_value=MagicMock(completion_text="OK"))
        plugin = makePlugin({
            "llm_config": {"llm_summarize": True, "llm_summary_prompt": "INJECTED: summarize this."},
            "send_config": {"screenshot_pdf": True},
        })
        plugin.context.llm_generate = llmGen

        try:
            await collectYields(plugin.cmd_get(makeEvent(), "2501.00001"))
            assert "INJECTED" in llmGen.call_args.kwargs["prompt"]
        finally:
            if pdfPath.exists():
                pdfPath.unlink()
            if scrPath.exists():
                scrPath.unlink()


class TestGetPdf:
    """测试 get 指令的 PDF 相关功能。"""

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_paper_by_id", new_callable=AsyncMock)
    @patch.object(pdf_handler, "download_pdf", new_callable=AsyncMock)
    async def test_downloadAndAttach(self, mockDownload: AsyncMock, mockGet: AsyncMock):
        """PDF 下载成功 → File 组件出现在结果中。"""
        mockGet.return_value = paper()

        pdfPath = Path(tempfile.mktemp(suffix=".pdf"))
        pdfPath.touch()
        mockDownload.return_value = pdfPath

        plugin = makePlugin({
            "send_config": {"attach_pdf": True, "screenshot_pdf": False, "use_forward": False},
        })
        plugin.context.llm_generate = AsyncMock()

        try:
            results = await collectYields(plugin.cmd_get(makeEvent(), "2501.00001"))
            assert findComponent(results, File) is not None, "应包含 PDF File 附件"
        finally:
            if pdfPath.exists():
                pdfPath.unlink()

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_paper_by_id", new_callable=AsyncMock)
    @patch.object(pdf_handler, "download_pdf", new_callable=AsyncMock)
    async def test_downloadFailure_showsError(self, mockDownload: AsyncMock, mockGet: AsyncMock):
        """PDF 下载失败时消息中包含错误提示。"""
        mockGet.return_value = paper()
        mockDownload.return_value = None

        plugin = makePlugin({
            "send_config": {"attach_pdf": True, "screenshot_pdf": False, "use_forward": False},
        })
        plugin.context.llm_generate = AsyncMock()

        results = await collectYields(plugin.cmd_get(makeEvent(), "2501.00001"))

        text = collectTexts(results)
        assert "PDF 下载失败" in text or "跳过" in text

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_paper_by_id", new_callable=AsyncMock)
    @patch.object(pdf_handler, "download_pdf", new_callable=AsyncMock)
    async def test_sizeExceeded_showsError(self, mockDownload: AsyncMock, mockGet: AsyncMock):
        """PDF 超出大小限制时显示错误。"""
        mockGet.return_value = paper()
        mockDownload.side_effect = pdf_handler.PdfSizeExceededError(
            url="https://arxiv.org/pdf/2501.00001.pdf",
            actual_bytes=30 * 1024 * 1024,
            max_bytes=20 * 1024 * 1024,
        )

        plugin = makePlugin({
            "send_config": {"attach_pdf": True, "screenshot_pdf": False, "use_forward": False},
        })
        plugin.context.llm_generate = AsyncMock()

        results = await collectYields(plugin.cmd_get(makeEvent(), "2501.00001"))

        assert "大小超出限制" in collectTexts(results)

    @pytest.mark.asyncio
    @patch.object(arxiv_client, "get_paper_by_id", new_callable=AsyncMock)
    @patch.object(pdf_handler, "download_pdf", new_callable=AsyncMock)
    async def test_forwardMode_pdfSentSeparately(
        self, mockDownload: AsyncMock, mockGet: AsyncMock
    ):
        """合并转发模式下 PDF 作为独立 File 消息发送。"""
        mockGet.return_value = paper()

        pdfPath = Path(tempfile.mktemp(suffix=".pdf"))
        pdfPath.write_bytes(b"%PDF-1.4\ncontent")
        mockDownload.return_value = pdfPath

        plugin = makePlugin({
            "send_config": {
                "attach_pdf": True, "screenshot_pdf": False,
                "use_forward": True, "bot_name": "TestBot",
            },
        })
        plugin.context.llm_generate = AsyncMock()

        try:
            results = await collectYields(plugin.cmd_get(makeEvent(), "2501.00001"))
            assert findComponent(results, File) is not None, (
                "合并转发模式下 PDF 应作为独立 File 消息发送"
            )
        finally:
            if pdfPath.exists():
                pdfPath.unlink()
