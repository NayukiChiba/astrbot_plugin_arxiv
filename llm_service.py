"""LLM 驱动的摘要翻译和论文总结模块。

通过 AstrBot 的 Context.llm_generate() 调用已配置的 LLM 提供商，
实现论文摘要的中文翻译和 PDF 内容总结功能。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrbot.core.star.context import Context

logger = logging.getLogger("astrbot")

# 默认论文总结 prompt
DEFAULT_SUMMARY_PROMPT = (
    "你是一个学术论文总结助手。"
    "请用中文总结以下论文内容。"
    "重点关注: 1) 主要贡献 2) 核心方法 "
    "3) 关键结果和结论。"
    "总结应简洁专业，不超过 300 字。\n\n"
    "论文内容:\n{content}"
)

# 摘要翻译 prompt
ABSTRACT_TRANSLATE_PROMPT = (
    "请将以下学术论文摘要翻译为中文。"
    "使用准确、专业的学术语言。"
    "不要添加任何额外评论 —— 仅输出翻译后的摘要。\n\n"
    "摘要:\n{abstract}"
)


async def translate_abstract(
    context: Context,
    abstract: str,
    *,
    provider_id: str = "",
) -> str:
    """使用 LLM 将论文摘要翻译为中文。

    Args:
        context: AstrBot 上下文，用于访问 LLM。
        abstract: 原始英文摘要。
        provider_id: LLM 提供商 ID，留空使用默认提供商。

    Returns:
        翻译后的摘要，LLM 调用失败时返回原文。
    """
    if not abstract:
        return abstract

    pid = provider_id or _get_default_provider_id(context)
    if not pid:
        logger.warning("没有可用的 LLM 提供商，无法翻译摘要。")
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
        logger.exception("LLM 摘要翻译失败。")

    return abstract


async def summarize_paper(
    context: Context,
    content: str,
    *,
    provider_id: str = "",
    custom_prompt: str = "",
) -> str:
    """使用 LLM 总结论文内容。

    Args:
        context: AstrBot 上下文，用于访问 LLM。
        content: 论文文本内容（从 PDF 提取）。
        provider_id: LLM 提供商 ID，留空使用默认提供商。
        custom_prompt: 自定义 prompt 模板，需包含 ``{content}`` 占位符。
            留空则使用默认 prompt。

    Returns:
        总结文本，LLM 调用失败时返回空字符串。
    """
    if not content:
        return ""

    pid = provider_id or _get_default_provider_id(context)
    if not pid:
        logger.warning("没有可用的 LLM 提供商，无法总结论文。")
        return ""

    template = (
        custom_prompt
        if custom_prompt and "{content}" in custom_prompt
        else DEFAULT_SUMMARY_PROMPT
    )
    # 截断内容以避免超出上下文窗口
    max_chars = 15000
    truncated = content[:max_chars]
    if len(content) > max_chars:
        truncated += "\n\n[... 内容已截断 ...]"

    prompt = template.format(content=truncated)

    try:
        resp = await context.llm_generate(
            chat_provider_id=pid,
            prompt=prompt,
        )
        if resp and resp.completion_text:
            return resp.completion_text.strip()
    except Exception:
        logger.exception("LLM 论文总结失败。")

    return ""


def _get_default_provider_id(context: Context) -> str:
    """获取默认的聊天 LLM 提供商 ID。"""
    try:
        prov = context.get_using_provider()
        if prov:
            return prov.meta().id
    except Exception:
        pass
    return ""
