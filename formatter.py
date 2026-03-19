"""Message chain construction for arXiv paper messages.

Builds AstrBot MessageChain objects from ArxivPaper data, supporting both
individual and forwarded (Node/Nodes) message modes.
"""

from __future__ import annotations

from astrbot.api.message_components import File, Image, Node, Nodes, Plain
from astrbot.core.message.message_event_result import MessageChain

from .arxiv_client import ArxivPaper


def format_paper_text(
    paper: ArxivPaper,
    *,
    index: int = 0,
    show_abstract: bool = True,
    abstract_text: str = "",
    summary_text: str = "",
) -> str:
    """Format a paper's metadata into a readable text block.

    Args:
        paper: The ArxivPaper object.
        index: Paper index for numbered display (0 = no number).
        show_abstract: Whether to include the abstract.
        abstract_text: Override text for the abstract (e.g. translated version).
        summary_text: LLM-generated summary to append.

    Returns:
        Formatted paper text.
    """
    lines: list[str] = []

    # Title
    title = paper.title
    if index > 0:
        title = f"[{index}] {title}"
    lines.append(f"📄 {title}")
    lines.append("")

    # Authors
    authors_str = ", ".join(paper.authors[:5])
    if len(paper.authors) > 5:
        authors_str += f" et al. (+{len(paper.authors) - 5})"
    lines.append(f"👤 {authors_str}")

    # Categories
    if paper.categories:
        lines.append(f"🏷️ {', '.join(paper.categories[:5])}")

    # Date
    lines.append(f"📅 {paper.published_date}")

    # Link
    lines.append(f"🔗 {paper.abs_url}")

    # Abstract
    if show_abstract:
        abs_text = abstract_text or paper.abstract
        if abs_text:
            # Truncate very long abstracts for readability
            if len(abs_text) > 800:
                abs_text = abs_text[:800] + "..."
            lines.append("")
            lines.append(f"📝 摘要:\n{abs_text}")

    # LLM Summary
    if summary_text:
        lines.append("")
        lines.append(f"🤖 AI 总结:\n{summary_text}")

    return "\n".join(lines)


def build_paper_chain(
    paper: ArxivPaper,
    *,
    index: int = 0,
    show_abstract: bool = True,
    abstract_text: str = "",
    summary_text: str = "",
    screenshot_path: str = "",
    pdf_path: str = "",
) -> MessageChain:
    """Build a MessageChain for a single paper.

    Args:
        paper: The ArxivPaper object.
        index: Display index number.
        show_abstract: Whether to include the abstract.
        abstract_text: Override abstract text.
        summary_text: LLM summary text.
        screenshot_path: Absolute path to PDF first-page screenshot.
        pdf_path: Absolute path to PDF file to attach.

    Returns:
        A MessageChain containing the paper information.
    """
    chain = MessageChain()

    # Text content
    text = format_paper_text(
        paper,
        index=index,
        show_abstract=show_abstract,
        abstract_text=abstract_text,
        summary_text=summary_text,
    )
    chain.chain.append(Plain(text))

    # Screenshot image
    if screenshot_path:
        chain.chain.append(Image.fromFileSystem(screenshot_path))

    # PDF file attachment
    if pdf_path:
        pdf_name = f"{paper.arxiv_id.replace('/', '_')}.pdf"
        chain.chain.append(File(name=pdf_name, file=pdf_path))

    return chain


def build_forward_nodes(
    papers_chains: list[MessageChain],
    *,
    bot_name: str = "ArXiv Bot",
    bot_uin: str = "0",
) -> MessageChain:
    """Wrap multiple paper message chains into forwarded Nodes.

    Args:
        papers_chains: List of MessageChain objects, one per paper.
        bot_name: Display name for the bot in forwarded messages.
        bot_uin: QQ number for the bot in forwarded messages.

    Returns:
        A MessageChain containing a single Nodes component.
    """
    nodes: list[Node] = []

    # Header node
    header_chain = MessageChain()
    header_chain.chain.append(
        Plain(f"📚 ArXiv 论文推送 ({len(papers_chains)} 篇)")
    )
    nodes.append(Node(content=header_chain.chain, name=bot_name, uin=bot_uin))

    # One node per paper
    for chain in papers_chains:
        nodes.append(Node(content=chain.chain, name=bot_name, uin=bot_uin))

    result = MessageChain()
    result.chain.append(Nodes(nodes=nodes))
    return result


def build_no_results_chain() -> MessageChain:
    """Build a message chain for when no new papers are found."""
    chain = MessageChain()
    chain.chain.append(Plain("📭 当前没有找到新的论文。"))
    return chain


def build_categories_chain() -> MessageChain:
    """Build a message chain listing all available arXiv categories."""
    from .arxiv_client import ARXIV_CATEGORIES

    lines = ["📚 可用的 arXiv 学科分类:\n"]
    current_prefix = ""
    for code, name in sorted(ARXIV_CATEGORIES.items()):
        prefix = code.split(".")[0] if "." in code else code.split("-")[0]
        if prefix != current_prefix:
            current_prefix = prefix
            lines.append(f"\n【{prefix.upper()}】")
        lines.append(f"  {code}: {name}")

    chain = MessageChain()
    chain.chain.append(Plain("\n".join(lines)))
    return chain
