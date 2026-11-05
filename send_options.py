"""发送选项与论文处理产物的数据结构。

抽出独立模块，供 main（处理与投递）与 web_api（手动发送接口）共同使用，
避免两者相互导入形成循环依赖。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.api.event import MessageChain

from .arxiv_client import ArxivPaper


@dataclass
class SendOptions:
    """一次发送的覆盖选项。

    除 ``text_format`` 外，值为 ``None`` 的字段表示跟随插件配置；
    WebUI 手动发送 / 重发时按字段覆盖，定时推送全部用默认值（跟随配置）。
    """

    text_format: str = "plain"
    send_as_image: bool | None = None
    attach_pdf: bool | None = None
    screenshot_pdf: bool | None = None
    send_abstract: bool | None = None
    image_template: str | None = None


@dataclass
class PaperArtifacts:
    """单篇论文处理产物（与平台无关，可持久化到论文库）。

    ``screenshot_path`` / ``card_path`` 为处理期生成的本地文件路径，
    由发送方复制进论文库并记录文件名；``abstract_zh`` / ``llm_summary``
    为 LLM 结果文本。
    """

    screenshot_path: str = ""
    card_path: str = ""
    abstract_zh: str = ""
    llm_summary: str = ""


@dataclass
class ProcessedPaper:
    """一篇成功处理的论文：消息链 + 处理产物。"""

    paper: ArxivPaper
    chains: list[MessageChain] = field(default_factory=list)
    artifacts: PaperArtifacts = field(default_factory=PaperArtifacts)
