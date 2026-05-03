"""LLM 驱动的摘要翻译和论文总结模块。

通过 AstrBot 的 Context.llm_generate() 调用已配置的 LLM 提供商，
实现论文摘要的中文翻译和视觉模型论文总结功能。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from astrbot.api import logger

if TYPE_CHECKING:
    from astrbot.core.star.context import Context


# 论文总结 prompt（文本模式）
DEFAULT_SUMMARY_PROMPT = (
    "你是一个学术论文总结助手。"
    "请用中文总结以下论文内容。"
    "重点关注: 1) 主要贡献 2) 核心方法 "
    "3) 关键结果和结论。"
    "总结应简洁专业，不超过 300 字。\n\n"
    "论文内容:\n{content}"
)

# 视觉模型论文总结 prompt
VISION_SUMMARY_PROMPT = (
    "你是一个学术论文总结助手。请仔细阅读这张论文首页截图，"
    "用中文总结论文内容。重点关注：\n"
    "1) 论文的主要贡献和创新点\n"
    "2) 核心方法和技术路线\n"
    "3) 关键结果和结论\n"
    "总结应简洁专业，不超过 300 字。"
)

# 摘要翻译 prompt
ABSTRACT_TRANSLATE_PROMPT = (
    "请将以下学术论文摘要翻译为中文。"
    "使用准确、专业的学术语言。"
    "不要添加任何额外评论 —— 仅输出翻译后的摘要。\n\n"
    "摘要:\n{abstract}"
)


def _get_default_provider_id(context: Context) -> str:
    """获取默认的聊天 LLM 提供商 ID。"""
    try:
        prov = context.get_using_provider()
        if prov is not None:
            meta = prov.meta()
            if meta and meta.id:
                logger.info("自动获取到默认 LLM 提供商: %s", meta.id)
                return meta.id
        logger.warning("未找到默认 LLM 提供商，请检查 LLM 提供商配置。")
    except Exception:
        logger.exception("获取默认 LLM 提供商失败。")
    return ""


async def translate_abstract(
    context: Context,
    abstract: str,
    *,
    provider_id: str = "",
) -> str:
    """使用 LLM 将论文摘要翻译为中文。

    Args:
        context: AstrBot 上下文。
        abstract: 原始英文摘要。
        provider_id: LLM 提供商 ID，留空使用默认提供商。

    Returns:
        翻译后的摘要，失败时返回原文。
    """
    if not abstract:
        return abstract

    pid = provider_id or _get_default_provider_id(context)
    if not pid:
        logger.warning("没有可用的 LLM 提供商，摘要翻译回退为原文。")
        return abstract

    logger.info("使用 LLM 提供商 '%s' 翻译摘要...", pid)
    prompt = ABSTRACT_TRANSLATE_PROMPT.format(abstract=abstract)

    try:
        resp = await context.llm_generate(
            chat_provider_id=pid,
            prompt=prompt,
        )
        if resp and resp.completion_text:
            logger.info("摘要翻译成功，%d 字符。", len(resp.completion_text))
            return resp.completion_text.strip()
        logger.warning("LLM 返回空内容，摘要翻译回退为原文。")
    except Exception:
        logger.exception("LLM 摘要翻译失败，回退为原文。")

    return abstract


async def summarize_paper(
    context: Context,
    content: str,
    *,
    provider_id: str = "",
    custom_prompt: str = "",
) -> str:
    """使用 LLM 总结论文内容（文本模式）。

    Args:
        context: AstrBot 上下文。
        content: 论文文本内容（从 PDF 提取）。
        provider_id: LLM 提供商 ID，留空使用默认提供商。
        custom_prompt: 自定义 prompt 模板，需包含 ``{content}`` 占位符。

    Returns:
        总结文本，失败时返回空字符串。
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


async def summarize_paper_vision(
    context: Context,
    screenshot_path: str,
    *,
    provider_id: str = "",
    custom_prompt: str = "",
) -> str:
    """使用视觉模型总结论文（传入 PDF 首页截图）。

    Args:
        context: AstrBot 上下文。
        screenshot_path: PDF 首页截图的文件路径。
        provider_id: LLM 提供商 ID，留空使用默认提供商。
        custom_prompt: 自定义 prompt，留空使用默认视觉提示词。

    Returns:
        总结文本，失败时返回空字符串。
    """
    if not screenshot_path:
        return ""

    pid = provider_id or _get_default_provider_id(context)
    if not pid:
        logger.warning("没有可用的 LLM 提供商，无法总结论文。")
        return ""

    prompt = custom_prompt or VISION_SUMMARY_PROMPT
    logger.info("使用视觉模型 '%s' 总结论文...", pid)

    try:
        resp = await context.llm_generate(
            chat_provider_id=pid,
            prompt=prompt,
            image_urls=[screenshot_path],
        )
        if resp and resp.completion_text:
            logger.info("视觉模型总结成功，%d 字符。", len(resp.completion_text))
            return resp.completion_text.strip()
        logger.warning("视觉模型返回空内容。")
    except Exception:
        logger.exception("视觉模型论文总结失败。")

    return ""
