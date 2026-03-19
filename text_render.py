"""Abstract text to image renderer.

Uses Pillow to render the abstract text into a clean, readable long image.
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("astrbot")

# Rendering settings
_WIDTH = 800
_PADDING = 40
_LINE_SPACING = 8
_BG_COLOR = "#FFFFFF"
_TEXT_COLOR = "#333333"
_TITLE_COLOR = "#1A1A2E"
_ACCENT_COLOR = "#4A90D9"
_FONT_SIZE = 22
_TITLE_FONT_SIZE = 26
_CHARS_PER_LINE = 34  # CJK chars per line at this width


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Try to load a system CJK font, fallback to default."""
    font_candidates = [
        # Windows
        "C:/Windows/Fonts/msyh.ttc",  # Microsoft YaHei
        "C:/Windows/Fonts/simhei.ttf",  # SimHei
        "C:/Windows/Fonts/simsun.ttc",  # SimSun
        # Linux
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
    ]
    if bold:
        bold_candidates = [
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        ]
        font_candidates = bold_candidates + font_candidates

    for path in font_candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue

    return ImageFont.load_default()


def _wrap_text(text: str, chars_per_line: int = _CHARS_PER_LINE) -> list[str]:
    """Wrap text for rendering, handling both CJK and Latin characters."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            lines.append("")
            continue
        # Use textwrap for basic wrapping, then split long CJK sequences
        wrapped = textwrap.fill(paragraph, width=chars_per_line * 2)
        for line in wrapped.split("\n"):
            # Further split if line is too wide visually
            current = ""
            width = 0
            for ch in line:
                # CJK chars count as ~2 width units
                ch_width = 2 if ord(ch) > 0x2E7F else 1
                if width + ch_width > chars_per_line * 2:
                    lines.append(current)
                    current = ch
                    width = ch_width
                else:
                    current += ch
                    width += ch_width
            if current:
                lines.append(current)
    return lines


def render_abstract_image(
    abstract: str,
    output_path: Path,
    *,
    title: str = "摘要 / Abstract",
) -> Path | None:
    """Render abstract text as a clean long image.

    Args:
        abstract: The abstract text to render.
        output_path: Where to save the PNG image.
        title: Title displayed at top of image.

    Returns:
        Path to the rendered image, or None on failure.
    """
    if not abstract:
        return None

    try:
        font = _get_font(_FONT_SIZE)
        title_font = _get_font(_TITLE_FONT_SIZE, bold=True)

        # Wrap text
        text_lines = _wrap_text(abstract)

        # Calculate image height
        line_height = _FONT_SIZE + _LINE_SPACING
        title_height = _TITLE_FONT_SIZE + _LINE_SPACING * 2
        # Title + divider + text + bottom padding
        content_height = (
            _PADDING  # top
            + title_height  # title
            + 20  # divider spacing
            + len(text_lines) * line_height  # text
            + _PADDING  # bottom
        )
        img_width = _WIDTH
        img_height = max(content_height, 150)

        # Create image
        img = Image.new("RGB", (img_width, img_height), _BG_COLOR)
        draw = ImageDraw.Draw(img)

        y = _PADDING

        # Draw title
        draw.text((_PADDING, y), title, fill=_TITLE_COLOR, font=title_font)
        y += title_height

        # Draw accent divider line
        draw.line(
            [(_PADDING, y), (img_width - _PADDING, y)],
            fill=_ACCENT_COLOR,
            width=2,
        )
        y += 20

        # Draw text lines
        for line in text_lines:
            if line == "":
                y += line_height // 2
                continue
            draw.text((_PADDING, y), line, fill=_TEXT_COLOR, font=font)
            y += line_height

        # Save
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), "PNG")
        return output_path

    except Exception:
        logger.exception("Failed to render abstract image.")
        return None
