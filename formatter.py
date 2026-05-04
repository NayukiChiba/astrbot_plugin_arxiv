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

    # 摘要
    if show_abstract:
        abs_text = abstract_text or paper.abstract
        if abs_text:
            lines.append(f"\n📝 摘要:\n{abs_text}")

    # AI 总结
    if summary_text:
        lines.append(f"\n🤖 AI 总结:\n{summary_text}")

    return "\n".join(lines)


def build_paper_chains(
    paper: ArxivPaper,
    *,
    index: int = 0,
    show_abstract: bool = True,
    abstract_text: str = "",
    summary_text: str = "",
    screenshot_path: str = "",
    pdf_path: str = "",
    abstract_image_path: str = "",
    extra_message: str = "",
) -> list[MessageChain]:
    """为单篇论文构建消息链列表。

    每篇论文可能产生多条消息：
    1. 论文基本信息（标题、作者等）+ PDF 截图 + PDF 附件
    2. 摘要（图片或文本，单独一条消息）

    Args:
        paper: ArxivPaper 对象。
        index: 显示序号。
        show_abstract: 是否包含摘要。
        abstract_text: 覆盖摘要文本。
        summary_text: LLM 总结文本。
        screenshot_path: PDF 首页截图的绝对路径。
        pdf_path: 要附带的 PDF 文件绝对路径。
        abstract_image_path: 摘要渲染图片的绝对路径。
        extra_message: 额外消息（如跳过原因等），附加在链末尾。

    Returns:
        MessageChain 列表。
    """
    chains: list[MessageChain] = []

    # 第 1 条消息：论文基本信息
    info_chain = MessageChain()
    text = format_paper_text(
        paper,
        index=index,
        show_abstract=False,
        summary_text=summary_text,
    )
    info_chain.chain.append(Plain(text))
    chains.append(info_chain)

    # 第 2 条消息：摘要（图片或文本）
    if show_abstract:
        abstract_chain = MessageChain()
        if abstract_image_path:
            abstract_chain.chain.append(Image.fromFileSystem(abstract_image_path))
            chains.append(abstract_chain)
        else:
            abs_text = abstract_text or paper.abstract
            if abs_text:
                abstract_chain.chain.append(Plain(f"📝 摘要:\n{abs_text}"))
                chains.append(abstract_chain)

    # 第 3 条消息：PDF 首页截图
    if screenshot_path:
        screenshot_chain = MessageChain()
        screenshot_chain.chain.append(Image.fromFileSystem(screenshot_path))
        chains.append(screenshot_chain)

    # 第 4 条消息：PDF 文件附件
    if pdf_path:
        pdf_chain = MessageChain()
        pdf_name = f"{paper.arxiv_id.replace('/', '_')}.pdf"
        pdf_chain.chain.append(File(name=pdf_name, file=pdf_path))
        chains.append(pdf_chain)

    # 第 5 条消息：AI 总结
    if summary_text:
        summary_chain = MessageChain()
        summary_chain.chain.append(Plain(f"🤖 AI 总结:\n{summary_text}"))
        chains.append(summary_chain)

    # 额外消息（如 PDF 跳过原因等）
    if extra_message:
        extra_chain = MessageChain()
        extra_chain.chain.append(Plain(extra_message))
        chains.append(extra_chain)

    return chains


def build_forward_nodes(
    papers_chains: list[MessageChain],
    *,
    bot_name: str = "ArXiv Bot",
    bot_uin: str = "0",
) -> tuple[MessageChain, list[MessageChain]]:
    """将多篇论文的消息链包装为合并转发 Nodes。

    File 组件（PDF 附件）在合并转发消息中不被 NapCat 等平台支持
    （仅含本地路径，缺少 url/file_id），因此会被提取并返回，
    由调用方在合并转发消息之后单独发送。

    Args:
        papers_chains: 每篇论文对应的 MessageChain 列表。
        bot_name: 合并转发中显示的机器人昵称。
        bot_uin: 合并转发中显示的 QQ 号。

    Returns:
        (合并转发消息, 需要单独发送的 File 链列表)
    """
    nodes: list[Node] = []
    fileChains: list[MessageChain] = []

    # 头部节点
    header_lines = [f"📚 ArXiv 论文推送 ({len(papers_chains)} 篇)"]
    header_chain = MessageChain()
    header_chain.chain.append(Plain("\n".join(header_lines)))
    nodes.append(Node(content=header_chain.chain, name=bot_name, uin=bot_uin))

    # 每篇论文一个节点，提取 File 组件
    for chain in papers_chains:
        filtered: list = []
        hasFile = False
        for comp in chain.chain:
            if isinstance(comp, File):
                hasFile = True
            else:
                filtered.append(comp)
        if hasFile:
            fileChains.append(chain)
        if not filtered:
            continue
        nodes.append(Node(content=filtered, name=bot_name, uin=bot_uin))

    result = MessageChain()
    result.chain.append(Nodes(nodes=nodes))
    return result, fileChains


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
