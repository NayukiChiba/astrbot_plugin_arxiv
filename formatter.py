"""论文消息链构建模块。

将 ArxivPaper 数据构建为 AstrBot MessageChain 对象，
支持单条消息和合并转发 (Node/Nodes) 两种模式。
"""

from __future__ import annotations

from astrbot.api.message_components import File, Image, Node, Nodes, Plain
from astrbot.core.message.message_event_result import MessageChain

from .arxiv_client import ARXIV_CATEGORIES, ArxivPaper


def _get_category_display(categories: list[str]) -> str:
    """将分类代码转换为 '代码 / 中文名' 格式。"""
    if not categories:
        return "未知"
    primary = categories[0]
    cn_name = ARXIV_CATEGORIES.get(primary, "")
    if cn_name:
        return f"{primary} / {cn_name}"
    return primary


def format_paper_text(
    paper: ArxivPaper,
    *,
    index: int = 0,
    show_abstract: bool = True,
    abstract_text: str = "",
    summary_text: str = "",
) -> str:
    """将论文元数据格式化为结构化文本。

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

    # 头部
    header = "📚 ArXiv 论文推送"
    if index > 0:
        header += f" [{index}]"
    lines.append(header)

    # 分区
    lines.append(f"分区: {_get_category_display(paper.categories)}")

    # 标题
    lines.append(f"标题: {paper.title}")

    # 作者
    authors_str = ", ".join(paper.authors[:5])
    if len(paper.authors) > 5:
        authors_str += f" et al. (+{len(paper.authors) - 5})"
    lines.append(f"作者: {authors_str}")

    # 提交时间
    lines.append(f"提交时间: {paper.published_date}")

    # 全部分类标签
    if len(paper.categories) > 1:
        lines.append(f"标签: {', '.join(paper.categories)}")

    # 链接
    lines.append(f"详情: {paper.abs_url}")

    # 摘要（仅在不使用图片模式时以文本形式显示）
    if show_abstract:
        abs_text = abstract_text or paper.abstract
        if abs_text:
            if len(abs_text) > 800:
                abs_text = abs_text[:800] + "..."
            lines.append(f"摘要: {abs_text}")

    # LLM 总结
    if summary_text:
        lines.append(f"AI 总结: {summary_text}")

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
    abstract_image_path: str = "",
) -> MessageChain:
    """为单篇论文构建消息链。

    构建顺序: 文本信息 -> 摘要图片 -> PDF 首页截图 -> PDF 附件

    Args:
        paper: ArxivPaper 对象。
        index: 显示序号。
        show_abstract: 是否包含摘要。
        abstract_text: 覆盖摘要文本。
        summary_text: LLM 总结文本。
        screenshot_path: PDF 首页截图的绝对路径。
        pdf_path: 要附带的 PDF 文件绝对路径。
        abstract_image_path: 摘要渲染图片的绝对路径。

    Returns:
        包含论文信息的 MessageChain。
    """
    chain = MessageChain()

    # 文本内容（不含摘要）
    text = format_paper_text(
        paper,
        index=index,
        show_abstract=False,
        summary_text=summary_text,
    )
    chain.chain.append(Plain(text))

    # 摘要图片
    if abstract_image_path:
        chain.chain.append(Image.fromFileSystem(abstract_image_path))

    # PDF 首页截图
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
