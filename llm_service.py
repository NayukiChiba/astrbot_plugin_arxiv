"""LLM-powered abstract translation and paper summarization.

Uses AstrBot's Context.llm_generate() to call the configured LLM provider
for translating abstracts and summarizing PDF content.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrbot.core.star.context import Context

logger = logging.getLogger("astrbot")

DEFAULT_SUMMARY_PROMPT = (
    "You are a research paper summarization assistant. "
    "Please summarize the following paper in Chinese. "
    "Focus on: 1) Main contributions 2) Core methodology "
    "3) Key results and conclusions. "
    "Keep the summary concise (within 300 words) and use "
    "clear, professional language.\n\n"
    "Paper content:\n{content}"
)

ABSTRACT_TRANSLATE_PROMPT = (
    "Please translate the following academic paper abstract into Chinese. "
    "Use accurate, professional academic language. "
    "Do not add any extra commentary — output only the translated abstract.\n\n"
    "Abstract:\n{abstract}"
)


async def translate_abstract(
    context: Context,
    abstract: str,
    *,
    provider_id: str = "",
) -> str:
    """Translate a paper abstract to Chinese using LLM.

    Args:
        context: AstrBot context for LLM access.
        abstract: Original English abstract.
        provider_id: LLM provider ID. Empty string uses the default provider.

    Returns:
        Translated abstract, or the original if LLM call fails.
    """
    if not abstract:
        return abstract

    pid = provider_id or _get_default_provider_id(context)
    if not pid:
        logger.warning("No LLM provider available for abstract translation.")
        return abstract

    prompt = ABSTRACT_TRANSLATE_PROMPT.format(abstract=abstract)

    try:
        resp = await context.llm_generate(
            chat_provider_id=pid,
            prompt=prompt,
        )
        if resp and resp.completion_text:
            return resp.completion_text.strip()
    except Exception:
        logger.exception("LLM abstract translation failed.")

    return abstract


async def summarize_paper(
    context: Context,
    content: str,
    *,
    provider_id: str = "",
    custom_prompt: str = "",
) -> str:
    """Summarize paper content using LLM.

    Args:
        context: AstrBot context for LLM access.
        content: Paper text content (from PDF extraction).
        provider_id: LLM provider ID. Empty string uses the default provider.
        custom_prompt: Custom prompt template. Must contain ``{content}``
            placeholder. Falls back to the default prompt if empty.

    Returns:
        Summary text, or empty string if LLM call fails.
    """
    if not content:
        return ""

    pid = provider_id or _get_default_provider_id(context)
    if not pid:
        logger.warning("No LLM provider available for paper summarization.")
        return ""

    template = custom_prompt if custom_prompt and "{content}" in custom_prompt else DEFAULT_SUMMARY_PROMPT
    # Truncate content to avoid exceeding context window
    max_chars = 15000
    truncated = content[:max_chars]
    if len(content) > max_chars:
        truncated += "\n\n[... content truncated ...]"

    prompt = template.format(content=truncated)

    try:
        resp = await context.llm_generate(
            chat_provider_id=pid,
            prompt=prompt,
        )
        if resp and resp.completion_text:
            return resp.completion_text.strip()
    except Exception:
        logger.exception("LLM paper summarization failed.")

    return ""


def _get_default_provider_id(context: Context) -> str:
    """Get the default chat provider ID if available."""
    try:
        prov = context.get_using_provider()
        if prov:
            return prov.meta().id
    except Exception:
        pass
    return ""
