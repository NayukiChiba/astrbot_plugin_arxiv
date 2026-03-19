"""PDF download, size validation, text extraction, and first-page screenshot.

Uses aiohttp for async downloading and pymupdf (fitz) for PDF processing.
PyMuPDF is treated as a soft dependency — features degrade gracefully if
unavailable.
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiohttp

logger = logging.getLogger("astrbot")

# Try to import pymupdf; mark as unavailable if missing.
try:
    import fitz  # pymupdf

    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    logger.info(
        "pymupdf is not installed. PDF screenshot and text extraction "
        "features will be disabled."
    )


async def download_pdf(
    url: str,
    save_dir: Path,
    *,
    timeout: int = 30,
    max_size_mb: int = 20,
) -> Path | None:
    """Download a PDF file with size validation.

    Args:
        url: PDF download URL.
        save_dir: Directory to save the file.
        timeout: HTTP timeout in seconds.
        max_size_mb: Maximum allowed file size in megabytes.

    Returns:
        Path to the downloaded file, or None if download failed or
        exceeded size limit.
    """
    max_bytes = max_size_mb * 1024 * 1024
    save_dir.mkdir(parents=True, exist_ok=True)

    # Derive filename from URL
    filename = url.rstrip("/").split("/")[-1]
    if not filename.endswith(".pdf"):
        filename += ".pdf"
    save_path = save_dir / filename

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                resp.raise_for_status()

                # Check Content-Length header first
                content_length = resp.headers.get("Content-Length")
                if content_length and int(content_length) > max_bytes:
                    logger.warning(
                        "PDF %s exceeds size limit: %s bytes > %s bytes",
                        url,
                        content_length,
                        max_bytes,
                    )
                    return None

                # Stream download with size check
                downloaded = 0
                with open(save_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            logger.warning(
                                "PDF %s exceeded size limit during download.",
                                url,
                            )
                            f.close()
                            save_path.unlink(missing_ok=True)
                            return None
                        f.write(chunk)

        return save_path

    except Exception:
        logger.exception("Failed to download PDF from %s", url)
        save_path.unlink(missing_ok=True)
        return None


def screenshot_first_page(
    pdf_path: Path,
    output_dir: Path,
    *,
    dpi: int = 150,
) -> Path | None:
    """Render the first page of a PDF as a PNG image.

    Args:
        pdf_path: Path to the PDF file.
        output_dir: Directory to save the screenshot.
        dpi: Rendering resolution in dots per inch.

    Returns:
        Path to the screenshot PNG, or None if pymupdf is unavailable
        or rendering failed.
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
        logger.exception("Failed to screenshot PDF first page: %s", pdf_path)
        return None


def extract_text(pdf_path: Path, max_pages: int = 10) -> str:
    """Extract text content from a PDF file.

    Args:
        pdf_path: Path to the PDF file.
        max_pages: Maximum number of pages to extract text from.

    Returns:
        Extracted text string, or empty string if pymupdf is
        unavailable or extraction failed.
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
        logger.exception("Failed to extract text from PDF: %s", pdf_path)
        return ""
