"""Sent-paper history tracker.

Prevents duplicate papers from being sent to the same target session.
Stores paper IDs keyed by session in a JSON file under the plugin data
directory.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class SentHistory:
    """Track which papers have been sent to each session."""

    def __init__(self, data_dir: Path, retention_days: int = 30) -> None:
        self._file = data_dir / "sent_history.json"
        self._retention_days = retention_days
        self._data: dict[str, dict[str, float]] = {}  # session -> {paper_id: timestamp}
        self._load()

    def _load(self) -> None:
        """Load history from disk."""
        if self._file.exists():
            try:
                with open(self._file, encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        """Persist history to disk."""
        self._file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def is_sent(self, session: str, paper_id: str) -> bool:
        """Check if a paper has already been sent to a session."""
        return paper_id in self._data.get(session, {})

    def mark_sent(self, session: str, paper_id: str) -> None:
        """Mark a paper as sent to a session."""
        if session not in self._data:
            self._data[session] = {}
        self._data[session][paper_id] = time.time()
        self._save()

    def mark_sent_batch(self, session: str, paper_ids: list[str]) -> None:
        """Mark multiple papers as sent to a session."""
        if session not in self._data:
            self._data[session] = {}
        now = time.time()
        for pid in paper_ids:
            self._data[session][pid] = now
        self._save()

    def filter_unsent(self, session: str, paper_ids: list[str]) -> list[str]:
        """Return only paper IDs that have NOT been sent to this session."""
        sent = self._data.get(session, {})
        return [pid for pid in paper_ids if pid not in sent]

    def cleanup_old(self) -> int:
        """Remove entries older than retention_days. Returns count removed."""
        cutoff = time.time() - self._retention_days * 86400
        removed = 0
        for session in list(self._data.keys()):
            old_ids = [
                pid
                for pid, ts in self._data[session].items()
                if ts < cutoff
            ]
            for pid in old_ids:
                del self._data[session][pid]
                removed += 1
            if not self._data[session]:
                del self._data[session]
        if removed > 0:
            self._save()
        return removed
