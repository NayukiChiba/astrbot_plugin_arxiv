"""已发送论文历史记录追踪器。

通过 JSON 文件持久化存储每个目标会话已发送的论文 ID，
防止重复推送同一篇论文。
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class SentHistory:
    """按会话追踪已发送的论文。"""

    def __init__(self, data_dir: Path, retention_days: int = 30) -> None:
        self._file = data_dir / "sent_history.json"
        self._retention_days = retention_days
        # 数据结构: {session_id: {paper_id: timestamp}}
        self._data: dict[str, dict[str, float]] = {}
        self._load()

    def _load(self) -> None:
        """从磁盘加载历史记录。"""
        if self._file.exists():
            try:
                with open(self._file, encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        """将历史记录持久化到磁盘。"""
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def is_sent(self, session: str, paper_id: str) -> bool:
        """检查论文是否已发送到指定会话。"""
        return paper_id in self._data.get(session, {})

    def mark_sent(self, session: str, paper_id: str) -> None:
        """标记论文已发送到指定会话。"""
        if session not in self._data:
            self._data[session] = {}
        self._data[session][paper_id] = time.time()
        self._save()

    def mark_sent_batch(self, session: str, paper_ids: list[str]) -> None:
        """批量标记多篇论文已发送。"""
        if session not in self._data:
            self._data[session] = {}
        now = time.time()
        for pid in paper_ids:
            self._data[session][pid] = now
        self._save()

    def filter_unsent(self, session: str, paper_ids: list[str]) -> list[str]:
        """返回尚未发送到该会话的论文 ID 列表。"""
        sent = self._data.get(session, {})
        return [pid for pid in paper_ids if pid not in sent]

    def cleanup_old(self) -> int:
        """清理超过保留天数的旧记录，返回清理数量。"""
        cutoff = time.time() - self._retention_days * 86400
        removed = 0
        for session in list(self._data.keys()):
            old_ids = [pid for pid, ts in self._data[session].items() if ts < cutoff]
            for pid in old_ids:
                del self._data[session][pid]
                removed += 1
            if not self._data[session]:
                del self._data[session]
        if removed > 0:
            self._save()
        return removed
