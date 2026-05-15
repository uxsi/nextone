"""Document state management with versioning.

Maintains the full text content and version number for each open document.
Supports incremental updates (apply text changes) and full sync (replace all).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from .protocol import TextChange, Range, Position


@dataclass
class DocumentState:
    """The current state of a single open document."""

    uri: str
    language_id: str
    version: int
    lines: list[str]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def line_count(self) -> int:
        return len(self.lines)

    def get_line(self, line_num: int) -> str:
        """Get a line by 0-based index. Returns empty string if out of range."""
        if 0 <= line_num < len(self.lines):
            return self.lines[line_num]
        return ""

    def get_range(self, start: int, end: int) -> list[str]:
        """Get lines from start (inclusive) to end (exclusive), 0-based."""
        return self.lines[max(0, start) : min(len(self.lines), end)]

    @classmethod
    def from_text(cls, uri: str, language_id: str, version: int, text: str) -> DocumentState:
        return cls(
            uri=uri,
            language_id=language_id,
            version=version,
            lines=text.split("\n"),
        )


class DocumentStore:
    """Manages open documents, applies changes, and tracks versions."""

    def __init__(self) -> None:
        self._documents: dict[str, DocumentState] = {}

    def open(self, uri: str, language_id: str, version: int, text: str) -> DocumentState:
        """Register a newly opened document."""
        doc = DocumentState.from_text(uri, language_id, version, text)
        self._documents[uri] = doc
        return doc

    def close(self, uri: str) -> DocumentState | None:
        """Remove a document from the store. Returns the removed state or None."""
        return self._documents.pop(uri, None)

    def get(self, uri: str) -> DocumentState | None:
        """Get the current state of a document, or None if not open."""
        return self._documents.get(uri)

    def apply_changes(
        self, uri: str, version: int, changes: list[TextChange]
    ) -> DocumentState | None:
        """Apply incremental changes to a document.

        Returns the updated document state, or None if the document is not open.
        Changes are applied in order. The version is set to the provided value
        after all changes are applied.
        """
        doc = self._documents.get(uri)
        if doc is None:
            return None

        for change in changes:
            self._apply_single_change(doc, change)

        doc.version = version
        return doc

    def full_sync(self, uri: str, version: int, text: str) -> DocumentState:
        """Replace document content entirely. Creates if not exists."""
        doc = self._documents.get(uri)
        if doc is None:
            return self.open(uri, "", version, text)

        doc.lines = text.split("\n")
        doc.version = version
        return doc

    def is_version_current(self, uri: str, version: int) -> bool:
        """Check if a version matches the document's current version."""
        doc = self._documents.get(uri)
        return doc is not None and doc.version == version

    def is_version_stale(self, uri: str, version: int) -> bool:
        """Check if a version is older than the document's current version."""
        doc = self._documents.get(uri)
        return doc is not None and doc.version > version

    @property
    def open_uris(self) -> list[str]:
        return list(self._documents.keys())

    def __len__(self) -> int:
        return len(self._documents)

    def __contains__(self, uri: str) -> bool:
        return uri in self._documents

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    @staticmethod
    def _apply_single_change(doc: DocumentState, change: TextChange) -> None:
        """Apply a single TextChange to the document's line buffer.

        The change range uses 0-based line/character positions (LSP convention).
        """
        start = change.range.start
        end = change.range.end

        # Collect text before the change on the start line
        before = doc.lines[start.line][:start.character] if start.line < len(doc.lines) else ""

        # Collect text after the change on the end line
        after = doc.lines[end.line][end.character:] if end.line < len(doc.lines) else ""

        # Build new lines from the replacement text
        new_text = before + change.text + after
        new_lines = new_text.split("\n")

        # Splice into the line buffer
        doc.lines[start.line : end.line + 1] = new_lines
