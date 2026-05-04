"""LLM 服务测试 — 翻译、文本总结、视觉总结。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from .. import llm_service


def _makeCtx():
    """构造 mock Context。"""
    ctx = MagicMock()
    ctx.llm_generate = AsyncMock()
    ctx.get_using_provider = MagicMock()
    prov = MagicMock()
    prov.meta.return_value.id = "test_provider"
    ctx.get_using_provider.return_value = prov
    return ctx


class TestTranslateAbstract:
    """测试 translate_abstract。"""

    @pytest.mark.asyncio
    async def test_success(self):
        ctx = _makeCtx()
        ctx.llm_generate.return_value = MagicMock(completion_text="中文摘要。")
        result = await llm_service.translate_abstract(ctx, "English", provider_id="p")
        assert result == "中文摘要。"

    @pytest.mark.asyncio
    async def test_emptyAbstract_returnsEmpty(self):
        ctx = MagicMock()
        result = await llm_service.translate_abstract(ctx, "")
        assert result == ""

    @pytest.mark.asyncio
    async def test_fallback_onError(self):
        ctx = _makeCtx()
        ctx.llm_generate = AsyncMock(side_effect=RuntimeError("fail"))
        result = await llm_service.translate_abstract(ctx, "Original", provider_id="p")
        assert result == "Original"

    @pytest.mark.asyncio
    async def test_fallback_onEmptyResponse(self):
        ctx = _makeCtx()
        ctx.llm_generate.return_value = MagicMock(completion_text="")
        result = await llm_service.translate_abstract(ctx, "Original", provider_id="p")
        assert result == "Original"


class TestSummarizePaper:
    """测试 summarize_paper。"""

    @pytest.mark.asyncio
    async def test_textMode_success(self):
        ctx = _makeCtx()
        ctx.llm_generate.return_value = MagicMock(completion_text="总结内容。")
        result = await llm_service.summarize_paper(ctx, "Paper content", provider_id="p")
        assert result == "总结内容。"

    @pytest.mark.asyncio
    async def test_customPrompt(self):
        ctx = _makeCtx()
        ctx.llm_generate.return_value = MagicMock(completion_text="OK")
        await llm_service.summarize_paper(ctx, "C", provider_id="p", custom_prompt="CP:{content}")
        assert "CP:" in ctx.llm_generate.call_args.kwargs["prompt"]

    @pytest.mark.asyncio
    async def test_invalidCustomPrompt_ignored(self):
        ctx = _makeCtx()
        ctx.llm_generate.return_value = MagicMock(completion_text="OK")
        await llm_service.summarize_paper(ctx, "C", provider_id="p", custom_prompt="bad")
        assert "学术论文总结助手" in ctx.llm_generate.call_args.kwargs["prompt"]

    @pytest.mark.asyncio
    async def test_emptyContent_returnsEmpty(self):
        ctx = MagicMock()
        result = await llm_service.summarize_paper(ctx, "")
        assert result == ""

    @pytest.mark.asyncio
    async def test_longContent_truncated(self):
        ctx = _makeCtx()
        ctx.llm_generate.return_value = MagicMock(completion_text="OK")
        await llm_service.summarize_paper(ctx, "x" * 20000, provider_id="p")
        assert "[... 内容已截断 ...]" in ctx.llm_generate.call_args.kwargs["prompt"]


class TestSummarizeVision:
    """测试 summarize_paper_vision。"""

    @pytest.mark.asyncio
    async def test_vision_success(self):
        ctx = _makeCtx()
        ctx.llm_generate.return_value = MagicMock(completion_text="Vision summary.")
        result = await llm_service.summarize_paper_vision(ctx, "/tmp/s.png", provider_id="p")
        assert result == "Vision summary."
        kw = ctx.llm_generate.call_args.kwargs
        assert "image_urls" in kw
        assert "/tmp/s.png" in kw["image_urls"]

    @pytest.mark.asyncio
    async def test_emptyPath_returnsEmpty(self):
        ctx = MagicMock()
        result = await llm_service.summarize_paper_vision(ctx, "")
        assert result == ""
