"""PDF 下载、体积校验、文本提取和首页截图模块。

使用 aiohttp 进行异步下载，pymupdf (fitz) 进行 PDF 处理。
PyMuPDF 为软依赖 —— 若未安装则相关功能优雅降级。
"""

from __future__ import annotations

from pathlib import Path

import aiohttp

from astrbot.api import logger

# 尝试导入 pymupdf，未安装则标记为不可用
try:
    import fitz  # pymupdf

    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    logger.info("pymupdf 未安装，PDF 截图和文本提取功能将被禁用。")


class PdfSizeExceededError(Exception):
    """PDF 文件超出大小限制。"""

    def __init__(self, url: str, actual_bytes: int, max_bytes: int):
        self.url = url
        self.actual_bytes = actual_bytes
        self.max_bytes = max_bytes
        size_mb = actual_bytes / (1024 * 1024)
        limit_mb = max_bytes / (1024 * 1024)
        super().__init__(
            f"PDF 大小 ({size_mb:.1f} MB) 超出限制 ({limit_mb:.0f} MB)"
        )


async def download_pdf(
    url: str,
    save_dir: Path,
    *,
    timeout: int = 30,
    max_size_mb: int = 20,
) -> Path | None:
    """下载 PDF 文件，支持体积限制。

    Args:
        url: PDF 下载链接。
        save_dir: 保存目录。
        timeout: HTTP 超时秒数。
        max_size_mb: 最大允许文件大小（MB）。

    Returns:
        下载成功返回文件路径，失败返回 None。

    Raises:
        PdfSizeExceededError: PDF 文件超出大小限制。
    """
    max_bytes = max_size_mb * 1024 * 1024
    save_dir.mkdir(parents=True, exist_ok=True)

    # 从 URL 推导文件名
    filename = url.rstrip("/").split("/")[-1]
    if not filename.endswith(".pdf"):
        filename += ".pdf"
    save_path = save_dir / filename

    headers = {
        "User-Agent": "astrbot-arxiv-plugin/1.0",
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=True,
            ) as resp:
                resp.raise_for_status()

                # 检查响应类型，拒绝 HTML 响应
                content_type = resp.headers.get("Content-Type", "")
                if "text/html" in content_type:
                    logger.warning(
                        "PDF %s 返回了 HTML 而非 PDF (Content-Type: %s)",
                        url,
                        content_type,
                    )
                    return None

                # 优先检查 Content-Length 头
                content_length = resp.headers.get("Content-Length")
                if content_length and int(content_length) > max_bytes:
                    raise PdfSizeExceededError(url, int(content_length), max_bytes)

                # 流式下载，实时校验大小
                downloaded = 0
                with open(save_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            f.close()
                            save_path.unlink(missing_ok=True)
                            raise PdfSizeExceededError(url, downloaded, max_bytes)
                        f.write(chunk)

        # 验证下载的文件是否为有效 PDF（检查文件头魔数）
        with open(save_path, "rb") as f:
            header = f.read(5)
        if header != b"%PDF-":
            logger.warning(
                "PDF %s 下载的文件不是有效的 PDF (文件头: %r)",
                url,
                header,
            )
            save_path.unlink(missing_ok=True)
            return None

        logger.info("PDF 下载成功: %s (%d 字节)", save_path.name, downloaded)
        return save_path

    except Exception:
        logger.exception("从 %s 下载 PDF 失败", url)
        save_path.unlink(missing_ok=True)
        return None


def screenshot_first_page(
    pdf_path: Path,
    output_dir: Path,
    *,
    dpi: int = 150,
) -> Path | None:
    """将 PDF 第一页渲染为 PNG 图片。

    Args:
        pdf_path: PDF 文件路径。
        output_dir: 截图保存目录。
        dpi: 渲染分辨率（每英寸点数），建议 72~300。

    Returns:
        截图文件路径，pymupdf 不可用或渲染失败返回 None。
    """
    if not PYMUPDF_AVAILABLE:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{pdf_path.stem}_page1.png"

    try:
        doc = fitz.open(str(pdf_path))
        if len(doc) == 0:
            doc.close()
            return None

        page = doc[0]
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        pix.save(str(output_path))
        doc.close()

        return output_path

    except Exception:
        logger.exception("PDF 首页截图失败: %s", pdf_path)
        return None


def extract_text(pdf_path: Path, max_pages: int = 10) -> str:
    """从 PDF 文件中提取文本内容。

    Args:
        pdf_path: PDF 文件路径。
        max_pages: 最大提取页数。

    Returns:
        提取的文本字符串，pymupdf 不可用或提取失败返回空字符串。
    """
    if not PYMUPDF_AVAILABLE:
        return ""

    try:
        doc = fitz.open(str(pdf_path))
        texts: list[str] = []
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            texts.append(page.get_text())
        doc.close()
        return "\n".join(texts)

    except Exception:
        logger.exception("从 PDF 提取文本失败: %s", pdf_path)
        return ""
