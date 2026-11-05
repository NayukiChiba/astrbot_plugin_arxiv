"""已发送论文的元数据与 PDF 持久化存储。

插件下载的 PDF 保存在 ``data_dir/papers/`` 目录（不随临时目录清理），
论文元数据持久化到 ``sent_papers.json``，供 WebUI 页面浏览历史论文、
在线查看 PDF。超过保留天数的记录与孤立 PDF 会被自动清理。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .arxiv_client import ArxivPaper


def pdf_filename_from_url(pdf_url: str) -> str:
    """按下载器的命名规则从 PDF URL 推导文件名（URL 最后一段 + .pdf）。"""
    filename = pdf_url.rstrip("/").split("/")[-1]
    if not filename.endswith(".pdf"):
        filename += ".pdf"
    return filename


def _asset_filename(arxiv_id: str, suffix: str) -> str:
    """生成论文附属资源（截图 / 卡片图片）的文件名，ID 中的路径分隔符替换为下划线。"""
    safe_id = arxiv_id.replace("/", "_")
    return f"{safe_id}{suffix}"


class PaperStore:
    """已发送论文的元数据存储与 PDF 文件管理。"""

    def __init__(self, data_dir: Path, retention_days: int = 30) -> None:
        self._dir = data_dir / "papers"
        self._file = data_dir / "sent_papers.json"
        self._retention_days = retention_days
        # 数据结构: {arxiv_id: {元数据..., sent_at, sessions, pdf_file}}
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    @property
    def papers_dir(self) -> Path:
        """PDF 持久化保存目录。"""
        return self._dir

    @property
    def screenshots_dir(self) -> Path:
        """PDF 首页截图持久化保存目录。"""
        return self._dir / "screenshots"

    @property
    def cards_dir(self) -> Path:
        """按图发送卡片图片持久化保存目录。"""
        return self._dir / "cards"

    def _load(self) -> None:
        """从磁盘加载元数据。"""
        if self._file.exists():
            try:
                with open(self._file, encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        """将元数据持久化到磁盘。"""
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def record(
        self,
        paper: ArxivPaper,
        session: str,
        *,
        screenshot_file: str = "",
        card_file: str = "",
        abstract_zh: str = "",
        llm_summary: str = "",
    ) -> None:
        """记录一篇已发送的论文（重复记录时更新发送时间与会话列表）。

        Args:
            paper: 论文元数据。
            session: 目标会话 UMO。
            screenshot_file: PDF 首页截图文件名（需已存入 screenshots_dir）。
            card_file: 按图发送卡片图片文件名（需已存入 cards_dir）。
            abstract_zh: LLM 翻译的中文摘要，空字符串时保留已有记录。
            llm_summary: LLM 总结内容，空字符串时保留已有记录。
        """
        pdf_file = pdf_filename_from_url(paper.pdf_url) if paper.pdf_url else ""
        existing = self._data.get(paper.arxiv_id, {})
        sessions = list(dict.fromkeys([*existing.get("sessions", []), session]))
        self._data[paper.arxiv_id] = {
            "arxiv_id": paper.arxiv_id,
            "title": paper.title,
            "authors": paper.authors,
            "categories": paper.categories,
            "published": paper.published,
            "abs_url": paper.abs_url,
            "pdf_url": paper.pdf_url,
            "abstract": paper.abstract,
            "abstract_zh": abstract_zh or existing.get("abstract_zh", ""),
            "llm_summary": llm_summary or existing.get("llm_summary", ""),
            "sent_at": time.time(),
            "sessions": sessions,
            # 仅记录确实存在于对应目录中的文件名
            "pdf_file": (
                pdf_file if pdf_file and (self._dir / pdf_file).is_file() else None
            ),
            "screenshot_file": (
                screenshot_file
                if screenshot_file
                and (self.screenshots_dir / screenshot_file).is_file()
                else None
            ),
            "card_file": (
                card_file
                if card_file and (self.cards_dir / card_file).is_file()
                else None
            ),
        }
        self._save()

    def save_screenshot(self, arxiv_id: str, source_path: Path) -> str:
        """把 PDF 首页截图复制到 screenshots_dir，返回文件名（失败返回空串）。"""
        return self._copy_asset(
            source_path, self.screenshots_dir, _asset_filename(arxiv_id, ".png")
        )

    def save_card(self, arxiv_id: str, source_path: Path) -> str:
        """把按图发送卡片图片复制到 cards_dir，返回文件名（失败/无源返回空串）。"""
        if not source_path:
            return ""
        suffix = source_path.suffix or ".jpeg"
        return self._copy_asset(
            source_path, self.cards_dir, _asset_filename(arxiv_id, suffix)
        )

    def update_card(self, arxiv_id: str, source_path: Path) -> str:
        """保存重新渲染的卡片图片并更新论文记录，返回文件名（失败返回空串）。

        用于 WebUI 按当前配置重新渲染卡片后持久化最新样式。
        """
        record = self._data.get(arxiv_id)
        if not record:
            return ""
        card_file = self.save_card(arxiv_id, source_path)
        if card_file:
            old = record.get("card_file")
            if old and old != card_file:
                (self.cards_dir / old).unlink(missing_ok=True)
            record["card_file"] = card_file
            self._save()
        return card_file

    @staticmethod
    def _copy_asset(source_path: Path, target_dir: Path, filename: str) -> str:
        """把资源文件复制进持久化目录，返回文件名；源不存在或失败返回空串。"""
        try:
            if not source_path or not Path(source_path).is_file():
                return ""
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / filename
            target.write_bytes(Path(source_path).read_bytes())
            return filename
        except OSError:
            return ""

    def list_papers(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        session: str = "",
    ) -> tuple[int, list[dict[str, Any]]]:
        """按发送时间倒序列出已发送论文。

        Args:
            limit: 最大返回条数。
            offset: 起始偏移。
            session: 按目标会话过滤，空字符串表示不过滤。

        Returns:
            (过滤后的总条数, 当前页的元数据列表)。
        """
        records = sorted(
            self._data.values(),
            key=lambda r: r.get("sent_at", 0),
            reverse=True,
        )
        if session:
            records = [r for r in records if session in r.get("sessions", [])]
        return len(records), records[offset : offset + limit]

    def get(self, arxiv_id: str) -> dict[str, Any] | None:
        """获取单篇论文的元数据。"""
        return self._data.get(arxiv_id)

    def pdf_path(self, arxiv_id: str) -> Path | None:
        """获取论文对应的 PDF 文件路径，不存在时返回 None。"""
        record = self._data.get(arxiv_id)
        if not record:
            return None
        pdf_file = record.get("pdf_file")
        if not pdf_file:
            return None
        path = self._dir / pdf_file
        return path if path.is_file() else None

    def _asset_path(self, arxiv_id: str, key: str, directory: Path) -> Path | None:
        """获取论文附属资源（截图 / 卡片）的文件路径，不存在时返回 None。"""
        record = self._data.get(arxiv_id)
        if not record:
            return None
        filename = record.get(key)
        if not filename:
            return None
        path = directory / filename
        return path if path.is_file() else None

    def screenshot_path(self, arxiv_id: str) -> Path | None:
        """获取论文对应的 PDF 首页截图路径，不存在时返回 None。"""
        return self._asset_path(arxiv_id, "screenshot_file", self.screenshots_dir)

    def card_path(self, arxiv_id: str) -> Path | None:
        """获取论文对应的按图发送卡片路径，不存在时返回 None。"""
        return self._asset_path(arxiv_id, "card_file", self.cards_dir)

    def delete_papers(self, arxiv_ids: list[str]) -> int:
        """批量删除论文记录及其关联的 PDF / 截图 / 卡片，返回删除的记录数。"""
        removed = 0
        for arxiv_id in arxiv_ids:
            record = self._data.pop(arxiv_id, None)
            if not record:
                continue
            removed += 1
            if record.get("pdf_file"):
                (self._dir / record["pdf_file"]).unlink(missing_ok=True)
            if record.get("screenshot_file"):
                (self.screenshots_dir / record["screenshot_file"]).unlink(
                    missing_ok=True
                )
            if record.get("card_file"):
                (self.cards_dir / record["card_file"]).unlink(missing_ok=True)
        if removed > 0:
            self._save()
        return removed

    def clear_all(self) -> int:
        """清空全部记录与 PDF / 截图 / 卡片（关闭存储时调用），返回清除的记录数。"""
        count = len(self._data)
        self._data = {}
        self._save()
        for directory in (self._dir, self.screenshots_dir, self.cards_dir):
            if directory.is_dir():
                for path in directory.iterdir():
                    if path.is_file():
                        path.unlink(missing_ok=True)
        return count

    def total_size_bytes(self) -> int:
        """返回论文库（PDF + 截图 + 卡片）的总字节数。"""
        total = 0
        for directory in (self._dir, self.screenshots_dir, self.cards_dir):
            if directory.is_dir():
                total += sum(
                    p.stat().st_size for p in directory.iterdir() if p.is_file()
                )
        return total

    def cleanup_old(self) -> int:
        """清理超过保留天数的记录与不再被引用的旧文件，返回清理数量。"""
        cutoff = time.time() - self._retention_days * 86400
        removed = 0
        for arxiv_id in list(self._data.keys()):
            if self._data[arxiv_id].get("sent_at", 0) < cutoff:
                for key, directory in (
                    ("pdf_file", self._dir),
                    ("screenshot_file", self.screenshots_dir),
                    ("card_file", self.cards_dir),
                ):
                    filename = self._data[arxiv_id].get(key)
                    if filename:
                        (directory / filename).unlink(missing_ok=True)
                del self._data[arxiv_id]
                removed += 1
        if removed > 0:
            self._save()

        # 清理没有元数据记录引用的孤立文件（如下载后未发送的）
        for key, directory, pattern in (
            ("pdf_file", self._dir, "*.pdf"),
            ("screenshot_file", self.screenshots_dir, "*.png"),
            ("card_file", self.cards_dir, "*.*"),
        ):
            referenced = {r.get(key) for r in self._data.values()}
            if directory.is_dir():
                for path in directory.glob(pattern):
                    if (
                        path.is_file()
                        and path.name not in referenced
                        and path.stat().st_mtime < cutoff
                    ):
                        path.unlink(missing_ok=True)
        return removed
