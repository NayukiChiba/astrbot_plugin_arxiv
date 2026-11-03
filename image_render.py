"""论文卡片 HTML 渲染模块。

模板文件存放于两处：
- 内置目录 ``templates/``（随插件发布，提供 default.html 等内置模板）；
- 用户目录 ``<插件数据目录>/templates/``，插件初始化时会把内置模板
  复制过去（不覆盖已有文件），用户可修改副本或添加自己的 ``.html``
  模板，并通过配置项 ``image_template`` 按文件名（不含扩展名）选用。

模板通过 AstrBot 的 t2i 服务（``Star.html_render``）渲染为图片，
动态文本在插件侧预转义后以 ``| safe`` 输出，避免服务端是否
自动转义带来的双重转义或注入问题。

模板可用的 Jinja2 变量（由 build_paper_template_data 提供）：
- ``index``: 论文序号，0 表示无序号
- ``category``: 主分类，如 "cs.AI / 人工智能"
- ``title_html``: 论文标题（已转义，配合 | safe 使用）
- ``authors_html``: 作者列表（已转义）
- ``published``: 提交日期，如 "2026-10-30"
- ``tags``: 全部分类标签，逗号分隔；只有一个分类时为空字符串
- ``arxiv_id``: arXiv ID
- ``abs_url``: 论文详情页链接
- ``abstract_html``: 摘要（已转义，含 <br> 换行；无摘要时为空字符串）
- ``summary_html``: AI 总结（已转义；无总结或识图失败时为空字符串）
- ``summary_failure``: 是否识图失败（布尔值）
- ``screenshot_b64``: PDF 首页截图 Base64（无截图时为空字符串）
- ``extra_html``: 额外提示，如 PDF 跳过原因（已转义）
"""

from __future__ import annotations

import html
import re
import shutil
from pathlib import Path

from astrbot.api import logger

from .arxiv_client import ArxivPaper
from .formatter import format_authors, get_category_display
from .llm_service import VISION_FAILURE_TEXT

# 内置模板目录（随插件发布）
BUILTIN_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
# 默认模板名（不含扩展名）
DEFAULT_TEMPLATE_NAME = "default"

# 模板名仅允许字母数字、下划线、连字符与中文等 Unicode 字符，防止路径穿越
_VALID_TEMPLATE_NAME = re.compile(r"^[\w\-]+$")


def install_builtin_templates(user_template_dir: Path) -> None:
    """将内置模板复制到用户模板目录，已存在的文件不覆盖。

    用户可直接修改副本或添加新的 .html 模板文件；
    插件升级重新安装时不会覆盖用户的修改。

    Args:
        user_template_dir: 用户模板目录（插件数据目录下的 templates/）。
    """
    try:
        user_template_dir.mkdir(parents=True, exist_ok=True)
        for src in BUILTIN_TEMPLATE_DIR.glob("*.html"):
            dst = user_template_dir / src.name
            if not dst.exists():
                shutil.copy2(src, dst)
                logger.info("已安装内置卡片模板: %s", dst)
    except OSError:
        logger.exception("安装内置卡片模板失败。")


def load_template(user_template_dir: Path, name: str) -> str:
    """按名称加载卡片模板内容。

    查找顺序：用户模板目录 -> 内置目录同名模板 -> 内置默认模板。

    Args:
        user_template_dir: 用户模板目录。
        name: 模板名（不含 .html 扩展名）。

    Returns:
        模板文件内容。

    Raises:
        FileNotFoundError: 内置默认模板也缺失时抛出。
    """
    if not _VALID_TEMPLATE_NAME.match(name):
        logger.warning("模板名 '%s' 含非法字符，回退为默认模板。", name)
        name = DEFAULT_TEMPLATE_NAME

    candidates = [
        user_template_dir / f"{name}.html",
        BUILTIN_TEMPLATE_DIR / f"{name}.html",
        BUILTIN_TEMPLATE_DIR / f"{DEFAULT_TEMPLATE_NAME}.html",
    ]
    for path in candidates:
        if path.is_file():
            if path != candidates[0] and name != DEFAULT_TEMPLATE_NAME:
                logger.warning("未找到模板 '%s'，使用 %s。", name, path.name)
            return path.read_text(encoding="utf-8")

    raise FileNotFoundError("内置默认卡片模板 default.html 缺失。")


def _escape_text(text: str) -> str:
    """转义 HTML 特殊字符并将换行转换为 <br>，用于模板安全输出。"""
    return html.escape(text).replace("\n", "<br>\n")


def build_paper_template_data(
    paper: ArxivPaper,
    *,
    index: int = 0,
    abstract_text: str = "",
    summary_text: str = "",
    screenshot_b64: str = "",
    extra_message: str = "",
) -> dict:
    """装配论文卡片模板的渲染数据。

    Args:
        paper: ArxivPaper 对象。
        index: 论文编号，0 表示不显示序号。
        abstract_text: 摘要文本（可为翻译版本），空字符串表示不展示摘要。
        summary_text: LLM 总结文本。
        screenshot_b64: PDF 首页截图的 Base64 编码（不含 data URI 前缀）。
        extra_message: 额外提示（如 PDF 跳过原因）。

    Returns:
        传给卡片模板的 Jinja2 数据字典。
    """
    return {
        "index": index,
        "category": get_category_display(paper.categories),
        "title_html": _escape_text(paper.title),
        "authors_html": _escape_text(format_authors(paper.authors)),
        "published": paper.published_date,
        "tags": ", ".join(paper.categories) if len(paper.categories) > 1 else "",
        "arxiv_id": paper.arxiv_id,
        "abs_url": paper.abs_url,
        "abstract_html": _escape_text(abstract_text) if abstract_text else "",
        "summary_html": (
            _escape_text(summary_text)
            if summary_text and summary_text != VISION_FAILURE_TEXT
            else ""
        ),
        "summary_failure": summary_text == VISION_FAILURE_TEXT,
        "screenshot_b64": screenshot_b64,
        "extra_html": _escape_text(extra_message) if extra_message else "",
    }
