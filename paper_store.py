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

    def record(self, paper: ArxivPaper, session: str) -> None:
        """记录一篇已发送的论文（重复记录时更新发送时间与会话列表）。"""
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
            "sent_at": time.time(),
            "sessions": sessions,
            # 仅记录确实存在于 papers 目录中的 PDF 文件名
            "pdf_file": (
                pdf_file if pdf_file and (self._dir / pdf_file).is_file() else None
            ),
        }
        self._save()

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

    def clear_all(self) -> int:
        """清空全部记录与 PDF 文件（关闭存储时调用），返回清除的记录数。"""
        count = len(self._data)
        self._data = {}
        self._save()
        if self._dir.is_dir():
            for path in self._dir.glob("*.pdf"):
                path.unlink(missing_ok=True)
        return count

    def total_size_bytes(self) -> int:
        """返回 papers 目录中所有 PDF 的总字节数。"""
        if not self._dir.is_dir():
            return 0
        return sum(p.stat().st_size for p in self._dir.glob("*.pdf"))

    def cleanup_old(self) -> int:
        """清理超过保留天数的记录与不再被引用的旧 PDF，返回清理数量。"""
        cutoff = time.time() - self._retention_days * 86400
        removed = 0
        for arxiv_id in list(self._data.keys()):
            if self._data[arxiv_id].get("sent_at", 0) < cutoff:
                pdf_file = self._data[arxiv_id].get("pdf_file")
                if pdf_file:
                    (self._dir / pdf_file).unlink(missing_ok=True)
                del self._data[arxiv_id]
                removed += 1
        if removed > 0:
            self._save()

        # 清理没有元数据记录引用的孤立 PDF（如下载后未发送的）
        referenced = {r.get("pdf_file") for r in self._data.values()}
        if self._dir.is_dir():
            for path in self._dir.glob("*.pdf"):
                if path.name not in referenced and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
        return removed
