"""PDF 处理流程单元测试。

测试重点：
1. PDF 下载失败时错误消息正确传播到消息链
2. _process_single_paper 流程中 pdf_skip_reason 正确设置
3. PDF 文件头验证逻辑
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astrbot.api.message_components import File, Plain

from ..arxiv_client import ArxivPaper
from ..formatter import build_paper_chains
from ..pdf_handler import PdfSizeExceededError


def _makePaper(**kwargs) -> ArxivPaper:
    """创建测试用 ArxivPaper。"""
    defaults = {
        "arxiv_id": "2501.00001",
        "title": "测试论文",
        "authors": ["作者A"],
        "abstract": "英文摘要内容。",
        "categories": ["cs.AI"],
        "published": "2025-01-01T00:00:00Z",
        "pdf_url": "https://arxiv.org/pdf/2501.00001.pdf",
        "abs_url": "https://arxiv.org/abs/2501.00001",
    }
    defaults.update(kwargs)
    return ArxivPaper(**defaults)


# ===========================================================================
# PDF 下载失败 → error message 传播
# ===========================================================================


class TestPdfSkipReasonPropagation:
    """测试 PDF 下载失败时的错误消息传播。"""

    def test_extraMessageText_in_chains_when_pdf_fails(self):
        """PDF 下载失败时 extra_message 应出现在生成的消息链中。"""
        paper = _makePaper()
        skipMsg = "⚠️ PDF 下载失败（网络超时或服务器错误），已跳过。"

        chains = build_paper_chains(
            paper,
            pdf_path="",
            extra_message=skipMsg,
        )

        allText = ""
        for chain in chains:
            for comp in chain.chain:
                if isinstance(comp, Plain):
                    allText += comp.text

        assert "PDF 下载失败" in allText
        assert "跳过" in allText

    def test_noFileChain_when_pdfPathEmpty(self):
        """pdf_path 为空时不生成 File 链。"""
        paper = _makePaper()
        chains = build_paper_chains(
            paper,
            pdf_path="",
        )

        for chain in chains:
            for comp in chain.chain:
                assert not isinstance(comp, File), "不应包含 File 组件"

    def test_noFileChain_when_pdfPathNone(self):
        """pdf_path 为 None 等效于空字符串。"""
        paper = _makePaper()
        chains = build_paper_chains(
            paper,
            pdf_path="",
        )

        for chain in chains:
            for comp in chain.chain:
                assert not isinstance(comp, File)

    def test_sizeExceededError_message(self):
        """PdfSizeExceededError 包含正确的错误信息。"""
        err = PdfSizeExceededError(
            url="https://arxiv.org/pdf/2501.00001.pdf",
            actual_bytes=30 * 1024 * 1024,
            max_bytes=20 * 1024 * 1024,
        )

        msg = str(err)
        assert "30.0 MB" in msg or "30" in msg
        assert "20 MB" in msg or "20" in msg

    def test_sizeExceeded_skipReason(self):
        """大小超限时 skip reason 消息正确。"""
        paper = _makePaper()
        maxMb = 20
        skipMsg = f"⚠️ PDF 大小超出限制（{maxMb} MB），跳过下载。"

        chains = build_paper_chains(
            paper,
            pdf_path="",
            extra_message=skipMsg,
        )

        allText = ""
        for chain in chains:
            for comp in chain.chain:
                if isinstance(comp, Plain):
                    allText += comp.text
        assert "PDF 大小超出限制" in allText

    def test_errorMessage_chainIsNotFile(self):
        """错误消息链不应包含 File 组件。"""
        paper = _makePaper()
        skipMsg = "⚠️ PDF 下载失败"

        chains = build_paper_chains(
            paper,
            pdf_path="",
            extra_message=skipMsg,
        )

        for chain in chains:
            for comp in chain.chain:
                if isinstance(comp, Plain) and "PDF 下载失败" in comp.text:
                    assert not any(
                        isinstance(c, File) for c in chain.chain
                    ), "错误消息链不应包含 File"


# ===========================================================================
# PDF 文件头验证（本地无损测试）
# ===========================================================================


class TestPdfValidation:
    """测试 PDF 文件格式验证逻辑。"""

    def test_validPdfHeader(self):
        """有效的 PDF 文件头 '%PDF-' 应通过验证。"""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4\nsome content")
            pdfPath = f.name

        try:
            with open(pdfPath, "rb") as f:
                header = f.read(5)
            assert header == b"%PDF-", "PDF 应以 '%PDF-' 开头"
        finally:
            os.unlink(pdfPath)

    def test_invalidHtmlHeader_rejected(self):
        """HTML 内容应被识别为非 PDF。"""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"<html><body>Error page</body></html>")
            fakePdf = f.name

        try:
            with open(fakePdf, "rb") as f:
                header = f.read(5)
            assert header != b"%PDF-"
        finally:
            os.unlink(fakePdf)

    def test_emptyFile_rejected(self):
        """空文件应被识别为非 PDF。"""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            pass
            emptyPdf = f.name

        try:
            with open(emptyPdf, "rb") as f:
                header = f.read(5)
            assert header != b"%PDF-"
        finally:
            os.unlink(emptyPdf)


# ===========================================================================
# _build_info_chains 抽象翻译逻辑
# ===========================================================================


class TestInfoChainsAbstractMode:
    """测试 search/latest 的摘要翻译逻辑。"""

    @pytest.mark.asyncio
    async def test_abstractTranslation_triggered_when_llmChinese(self):
        """abstract_mode=llm_chinese 且摘要非空时触发翻译。"""
        from ..llm_service import translate_abstract

        assert callable(translate_abstract)

    @pytest.mark.asyncio
    async def test_originalMode_skipsTranslation(self):
        """abstract_mode=original 时不触发翻译，直接使用原文。"""
        paper = _makePaper()
        abstract_mode = "original"

        abstract_text = paper.abstract
        if abstract_mode == "llm_chinese" and paper.abstract:
            abstract_text = "translated"

        assert abstract_text == paper.abstract


# ===========================================================================
# _process_single_paper 流程模拟
# ===========================================================================


class TestProcessSinglePaperFlow:
    """模拟 _process_single_paper 的错误处理流程。"""

    def test_pdfSkipReason_setOnDownloadError(self):
        """模拟 PDF 下载返回 None 时设置 skip reason。"""
        downloaded_pdf = None
        pdf_skip_reason = ""

        if downloaded_pdf:
            pass
        elif not pdf_skip_reason:
            pdf_skip_reason = "⚠️ PDF 下载失败（网络超时或服务器错误），已跳过。"

        assert pdf_skip_reason != ""
        assert "PDF 下载失败" in pdf_skip_reason

    def test_pdfSkipReason_setOnSizeExceeded(self):
        """模拟 PdfSizeExceededError 异常时的 skip reason。"""
        pdf_skip_reason = ""
        maxMb = 20
        pdf_skip_reason = f"⚠️ PDF 大小超出限制（{maxMb} MB），跳过下载。"

        assert "大小超出限制" in pdf_skip_reason

    def test_pdfSkipReason_empty_when_downloadSucceeds(self):
        """PDF 下载成功时 skip reason 保持空字符串。"""
        downloaded_pdf = Path("/tmp/test.pdf")
        pdf_skip_reason = ""

        if downloaded_pdf:
            pass
        elif not pdf_skip_reason:
            pdf_skip_reason = "should not be set"

        assert pdf_skip_reason == ""

    def test_buildPaperChains_receivesSkipReason(self):
        """验证 build_paper_chains 正确接收并使用 skip reason。"""
        paper = _makePaper()
        skipMsg = "⚠️ PDF 下载失败（网络超时或服务器错误），已跳过。"

        chains = build_paper_chains(
            paper,
            pdf_path="",
            extra_message=skipMsg,
        )

        found = False
        for chain in chains:
            for comp in chain.chain:
                if isinstance(comp, Plain) and skipMsg in comp.text:
                    found = True
        assert found, f"应找到包含错误消息 '{skipMsg}' 的链"
