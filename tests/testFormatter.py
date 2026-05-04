"""formatter 模块单元测试。

测试重点：
1. build_forward_nodes 正确分离 File 组件
2. build_forward_nodes 的 forwardMsg 节点不含 File 组件
3. build_paper_chains 正确包含 PDF 失败时的错误消息
"""

from pathlib import Path

import pytest

from astrbot.api.message_components import File, Image, Plain
from astrbot.core.message.message_event_result import MessageChain

from ..arxiv_client import ArxivPaper
from ..formatter import build_forward_nodes, build_paper_chains, format_paper_text


def _makePaper(**kwargs) -> ArxivPaper:
    """创建测试用 ArxivPaper。"""
    defaults = {
        "arxiv_id": "2501.00001",
        "title": "测试论文标题",
        "authors": ["作者A", "作者B"],
        "abstract": "这是一个英文摘要。",
        "categories": ["cs.AI"],
        "published": "2025-01-01T00:00:00Z",
        "pdf_url": "https://arxiv.org/pdf/2501.00001.pdf",
        "abs_url": "https://arxiv.org/abs/2501.00001",
    }
    defaults.update(kwargs)
    return ArxivPaper(**defaults)


def _chainFromPlain(text: str) -> MessageChain:
    """创建纯文本消息链。"""
    chain = MessageChain()
    chain.chain.append(Plain(text))
    return chain


def _chainFromFile(name: str, path: str) -> MessageChain:
    """创建文件消息链。"""
    chain = MessageChain()
    chain.chain.append(File(name=name, file=path))
    return chain


# ===========================================================================
# build_forward_nodes — File 分离
# ===========================================================================


class TestBuildForwardNodes:
    """测试 build_forward_nodes 的 File 组件分离逻辑。"""

    def test_noFileChains_returnsEmptyFileChains(self):
        """无 File 组件的链 → fileChains 为空列表。"""
        chains = [_chainFromPlain("消息1"), _chainFromPlain("消息2")]
        forwardMsg, fileChains = build_forward_nodes(chains)

        assert isinstance(forwardMsg, MessageChain)
        assert fileChains == []

    def test_fileChainIsExtracted(self):
        """包含 File 组件的链应被提取到 fileChains。"""
        fileChain = _chainFromFile("paper.pdf", "/tmp/paper.pdf")
        chains = [_chainFromPlain("消息1"), fileChain]
        forwardMsg, fileChains = build_forward_nodes(chains)

        assert len(fileChains) == 1
        assert fileChains[0] is fileChain

    def test_allFileChainsExtracted(self):
        """所有含 File 的链都应被提取。"""
        chains = [
            _chainFromPlain("头部"),
            _chainFromFile("a.pdf", "/tmp/a.pdf"),
            _chainFromPlain("中间"),
            _chainFromFile("b.pdf", "/tmp/b.pdf"),
        ]
        forwardMsg, fileChains = build_forward_nodes(chains)

        assert len(fileChains) == 2
        assert fileChains[0].chain[0].name == "a.pdf"
        assert fileChains[1].chain[0].name == "b.pdf"

    def test_forwardMsgNoFileComponents(self):
        """合并转发消息的所有节点内不应包含 File 组件。"""
        fileChain = _chainFromFile("paper.pdf", "/tmp/paper.pdf")
        chains = [_chainFromPlain("文本"), fileChain]
        forwardMsg, _ = build_forward_nodes(chains)

        for comp in forwardMsg.chain:
            for node in comp.nodes:
                for nodeComp in node.content:
                    assert not isinstance(nodeComp, File), (
                        f"forward node 不应包含 File 组件: {nodeComp}"
                    )

    def test_mixedChain_filePartExtracted_textPartInForward(self):
        """一个链中同时有文本和 File → 文本留在转发中，File 链被提取。"""
        mixedChain = MessageChain()
        mixedChain.chain.append(Plain("摘要文本"))
        mixedChain.chain.append(File(name="paper.pdf", file="/tmp/paper.pdf"))

        chains = [mixedChain]
        forwardMsg, fileChains = build_forward_nodes(chains)

        assert len(fileChains) == 1
        assert fileChains[0] is mixedChain

        for comp in forwardMsg.chain:
            for node in comp.nodes:
                for nodeComp in node.content:
                    assert not isinstance(nodeComp, File)

    def test_emptyChains(self):
        """空链列表应正常工作。"""
        forwardMsg, fileChains = build_forward_nodes([])

        assert isinstance(forwardMsg, MessageChain)
        assert fileChains == []
        assert len(forwardMsg.chain) == 1


# ===========================================================================
# build_paper_chains — 错误消息传播
# ===========================================================================


class TestBuildPaperChains:
    """测试 build_paper_chains 的错误处理和组件包含。"""

    def test_errorMessage_appended_when_pdfPathEmpty(self):
        """PDF 下载失败时，extra_message 应作为独立消息链追加。"""
        paper = _makePaper()
        chains = build_paper_chains(
            paper,
            pdf_path="",
            extra_message="⚠️ PDF 下载失败（网络超时或服务器错误），已跳过。",
        )

        assert len(chains) >= 2

        lastChainText = ""
        for comp in chains[-1].chain:
            if isinstance(comp, Plain):
                lastChainText += comp.text
        assert "PDF" in lastChainText and "下载" in lastChainText

    def test_errorMessage_notAppended_when_empty(self):
        """extra_message 为空时不追加额外链。"""
        paper = _makePaper()
        chains = build_paper_chains(
            paper,
            pdf_path="",
            extra_message="",
        )

        allText = ""
        for chain in chains:
            for comp in chain.chain:
                if isinstance(comp, Plain):
                    allText += comp.text
        assert "PDF 下载失败" not in allText

    def test_fileChain_appended_when_pdfPathPresent(self):
        """PDF 路径存在时生成 File 链。"""
        paper = _makePaper()
        pdfPath = "/tmp/2501.00001.pdf"
        chains = build_paper_chains(paper, pdf_path=pdfPath)

        fileFound = False
        for chain in chains:
            for comp in chain.chain:
                if isinstance(comp, File):
                    fileFound = True
                    assert comp.file_ == pdfPath
                    assert comp.name == "2501.00001.pdf"
        assert fileFound, "应包含 File 组件的链"

    def test_allComponents_when_everythingPresent(self):
        """全部组件齐全时所有链正确生成。"""
        paper = _makePaper()
        chains = build_paper_chains(
            paper,
            index=1,
            show_abstract=True,
            abstract_text="这是中文翻译的摘要。",
            summary_text="这是AI总结。",
            screenshot_path="/tmp/screenshot.png",
            pdf_path="/tmp/paper.pdf",
            abstract_image_path="/tmp/abstract.png",
            extra_message="",
        )

        assert len(chains) >= 5

        allComps: list = []
        for chain in chains:
            allComps.extend(chain.chain)

        compTypes = [type(c).__name__ for c in allComps]
        assert "Plain" in compTypes
        assert "Image" in compTypes
        assert "File" in compTypes

    def test_showAbstract_false_hidesAbstract(self):
        """show_abstract=False 时不生成摘要链。"""
        paper = _makePaper()
        chains = build_paper_chains(paper, show_abstract=False)

        for chain in chains:
            for comp in chain.chain:
                if isinstance(comp, Plain):
                    assert "摘要:" not in comp.text


# ===========================================================================
# format_paper_text — 文本格式化
# ===========================================================================


class TestFormatPaperText:
    """测试 format_paper_text 文本格式化。"""

    def test_basicFormatting(self):
        """基本字段应出现在格式化输出中。"""
        paper = _makePaper()
        text = format_paper_text(paper)

        assert "测试论文标题" in text
        assert "cs.AI" in text
        assert "作者A" in text
        assert "arxiv.org/abs/2501.00001" in text

    def test_abstractIncluded_by_default(self):
        """默认包含摘要。"""
        paper = _makePaper()
        text = format_paper_text(paper)

        assert "这是一个英文摘要。" in text

    def test_abstractExcluded_when_showAbstractFalse(self):
        """show_abstract=False 时不包含摘要。"""
        paper = _makePaper()
        text = format_paper_text(paper, show_abstract=False)

        assert "这是一个英文摘要。" not in text

    def test_abstractText_overrides_original(self):
        """abstract_text 覆盖原始摘要。"""
        paper = _makePaper()
        translated = "这是翻译后的中文摘要。"
        text = format_paper_text(paper, abstract_text=translated)

        assert translated in text
        assert "这是一个英文摘要。" not in text

    def test_summaryIncluded_when_present(self):
        """存在总结文本时显示。"""
        paper = _makePaper()
        text = format_paper_text(paper, summary_text="AI生成的总结")

        assert "AI生成的总结" in text
        assert "AI 总结" in text

    def test_indexDisplayed_when_positive(self):
        """index > 0 时显示编号。"""
        paper = _makePaper()
        text = format_paper_text(paper, index=3)

        assert "[3]" in text

    def test_indexOmitted_when_zero(self):
        """index=0 时不显示编号。"""
        paper = _makePaper()
        text = format_paper_text(paper, index=0)

        assert "[0]" not in text

    def test_authorsTruncated_when_moreThanFive(self):
        """超过 5 位作者时截断。"""
        paper = _makePaper(authors=[f"作者{i}" for i in range(10)])
        text = format_paper_text(paper)

        assert "et al." in text
        assert "(+5)" in text
        assert "作者5" not in text
