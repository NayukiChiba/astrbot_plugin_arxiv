"""论文消息链构建模块。

将 ArxivPaper 数据构建为 AstrBot MessageChain 对象，
支持单条消息和合并转发 (Node/Nodes) 两种模式。
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
    """将论文元数据格式化为可读文本块。

    Args:
        paper: ArxivPaper 对象。
        index: 论文编号，0 表示不显示序号。
        show_abstract: 是否包含摘要。
        abstract_text: 覆盖摘要文本（如翻译版本）。
        summary_text: LLM 生成的总结。

    Returns:
        格式化后的论文文本。
    """
    lines: list[str] = []

    # 标题
    title = paper.title
    if index > 0:
        title = f"[{index}] {title}"
    lines.append(f"📄 {title}")
    lines.append("")

    # 作者
    authors_str = ", ".join(paper.authors[:5])
    if len(paper.authors) > 5:
        authors_str += f" et al. (+{len(paper.authors) - 5})"
    lines.append(f"👤 {authors_str}")

    # 分类
    if paper.categories:
        lines.append(f"🏷️ {', '.join(paper.categories[:5])}")

    # 日期
    lines.append(f"📅 {paper.published_date}")

    # 链接
    lines.append(f"🔗 {paper.abs_url}")

    # 摘要
    if show_abstract:
        abs_text = abstract_text or paper.abstract
        if abs_text:
            # 截断过长的摘要以提高可读性
            if len(abs_text) > 800:
                abs_text = abs_text[:800] + "..."
            lines.append("")
            lines.append(f"📝 摘要:\n{abs_text}")

    # LLM 总结
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
    """为单篇论文构建消息链。

    Args:
        paper: ArxivPaper 对象。
        index: 显示序号。
        show_abstract: 是否包含摘要。
        abstract_text: 覆盖摘要文本。
        summary_text: LLM 总结文本。
        screenshot_path: PDF 首页截图的绝对路径。
        pdf_path: 要附带的 PDF 文件绝对路径。

    Returns:
        包含论文信息的 MessageChain。
    """
    chain = MessageChain()

    # 文本内容
    text = format_paper_text(
        paper,
        index=index,
        show_abstract=show_abstract,
        abstract_text=abstract_text,
        summary_text=summary_text,
    )
    chain.chain.append(Plain(text))

    # 截图图片
    if screenshot_path:
        chain.chain.append(Image.fromFileSystem(screenshot_path))

    # PDF 文件附件
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
    """将多篇论文的消息链包装为合并转发 Nodes。

    Args:
        papers_chains: 每篇论文对应的 MessageChain 列表。
        bot_name: 合并转发中显示的机器人昵称。
        bot_uin: 合并转发中显示的 QQ 号。

    Returns:
        包含一个 Nodes 组件的 MessageChain。
    """
    nodes: list[Node] = []

    # 头部节点
    header_chain = MessageChain()
    header_chain.chain.append(Plain(f"📚 ArXiv 论文推送 ({len(papers_chains)} 篇)"))
    nodes.append(Node(content=header_chain.chain, name=bot_name, uin=bot_uin))

    # 每篇论文一个节点
    for chain in papers_chains:
        nodes.append(Node(content=chain.chain, name=bot_name, uin=bot_uin))

    result = MessageChain()
    result.chain.append(Nodes(nodes=nodes))
    return result


def build_no_results_chain() -> MessageChain:
    """构建无新论文时的提示消息。"""
    chain = MessageChain()
    chain.chain.append(Plain("📭 当前没有找到新的论文。"))
    return chain


def build_categories_chain() -> MessageChain:
    """构建可用 arXiv 学科分类列表消息。"""
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
