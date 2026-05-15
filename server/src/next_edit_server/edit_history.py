"""Edit history sliding window.

Maintains the most recent N edits as NES diff sequences, used as input
context for both the Location Module and Generation Module.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class EditRecord:
    """A single recorded edit event."""

    uri: str
    version: int
    timestamp: int
    old_lines: list[str]  # Lines before the edit (in the changed region)
    new_lines: list[str]  # Lines after the edit (in the changed region)
    start_line: int       # 0-based line number where the change starts
    end_line: int         # 0-based line number where the change ends (exclusive, in old text)

    @property
    def nes_diff(self) -> str:
        """Format this edit as NES diff."""
        parts: list[str] = []

        # Context: 2 lines above (not stored here — caller provides if needed)
        # Deleted lines
        for i, line in enumerate(self.old_lines):
            line_num = self.start_line + i + 1  # 1-based
            parts.append(f"{line_num}-| {line}")

        # Added lines
        for i, line in enumerate(self.new_lines):
            line_num = self.start_line + i + 1  # 1-based
            parts.append(f"{line_num}+| {line}")

        return "\n".join(parts)

    @property
    def is_rename(self) -> bool:
        """Heuristic: single-line edit that only changes an identifier."""
        return (
            len(self.old_lines) == 1
            and len(self.new_lines) == 1
            and self.old_lines[0].strip() != self.new_lines[0].strip()
        )


class EditHistory:
    """Sliding window of recent edits, per document.

    NES 论文实验表明历史窗口长度为 3 时效果最佳。
    """

    def __init__(self, window_size: int = 3) -> None:
        self._window_size = window_size
        # uri → deque of EditRecord
        self._history: dict[str, deque[EditRecord]] = {}

    def record(self, edit: EditRecord) -> None:
        """Add an edit to the history for its document."""
        uri = edit.uri
        if uri not in self._history:
            self._history[uri] = deque(maxlen=self._window_size)
        self._history[uri].append(edit)

    def get(self, uri: str) -> list[EditRecord]:
        """Get the edit history for a document (oldest first)."""
        if uri not in self._history:
            return []
        return list(self._history[uri])

    def latest(self, uri: str) -> EditRecord | None:
        """Get the most recent edit for a document."""
        q = self._history.get(uri)
        if q and len(q) > 0:
            return q[-1]
        return None

    def clear(self, uri: str) -> None:
        """Clear history for a document (e.g., on close)."""
        self._history.pop(uri, None)

    def clear_all(self) -> None:
        """Clear all history."""
        self._history.clear()

    @property
    def window_size(self) -> int:
        return self._window_size


def build_edit_record(
    uri: str,
    version: int,
    timestamp: int,
    old_text_lines: list[str],
    new_text_lines: list[str],
    start_line: int,
    end_line: int,
) -> EditRecord:
    """Build an EditRecord from before/after line slices.

    Parameters:
        old_text_lines: The lines in the changed region before the edit.
        new_text_lines: The lines in the changed region after the edit.
        start_line: 0-based start line of the change in the document.
        end_line: 0-based end line (exclusive) of the change in the old document.
    """
    return EditRecord(
        uri=uri,
        version=version,
        timestamp=timestamp,
        old_lines=old_text_lines,
        new_lines=new_text_lines,
        start_line=start_line,
        end_line=end_line,
    )
