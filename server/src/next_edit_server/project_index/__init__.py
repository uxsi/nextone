"""Project Index: background-threaded symbol index for cross-file prediction.

Public facade that coordinates FileScanner, Indexer, and SymbolTable.
Thread-safe queries return immediately (empty results if index not ready).

Usage:
    index = ProjectIndex(workspace_root="/path/to/project")
    index.start()
    # ... later ...
    refs = index.query_references("hello", exclude_uri="file:///path/to/api.py")
"""

from __future__ import annotations

import logging
import os
from typing import Callable

from .file_scanner import FileScanner, find_git_root, get_language_for_file
from .indexer import Indexer, path_to_uri, uri_to_path
from .symbol_table import SymbolTable, SymbolLocation
from .queries import SymbolKind, ExtractedSymbol, extract_symbols, extract_imports

logger = logging.getLogger("next-edit-server.project_index")

__all__ = [
    "ProjectIndex",
    "SymbolLocation",
    "SymbolKind",
    "path_to_uri",
    "uri_to_path",
    "find_git_root",
    "get_language_for_file",
]


class ProjectIndex:
    """Background-threaded project symbol index.

    Lifecycle:
        1. __init__(workspace_root) — sets up data structures
        2. start() — begins background scan on a daemon thread
        3. query_references() — thread-safe lookup (returns [] if not ready)
        4. on_file_saved(uri) — triggers re-index of one file
        5. stop() — graceful shutdown

    The index degrades gracefully: before is_ready() returns True, all queries
    return empty results. Same-file prediction continues to work unaffected.
    """

    def __init__(
        self,
        workspace_root: str,
        on_ready: Callable[[], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        self._root = workspace_root
        self._symbol_table = SymbolTable()
        self._indexer = Indexer(
            symbol_table=self._symbol_table,
            on_ready=on_ready,
            on_progress=on_progress,
        )

    @property
    def workspace_root(self) -> str:
        return self._root

    def start(self) -> None:
        """Start background indexing. Non-blocking."""
        logger.info("Starting project index for: %s", self._root)
        self._indexer.start(self._root)

    def is_ready(self) -> bool:
        """True once the initial full scan is complete."""
        return self._indexer.is_ready

    def query_references(
        self,
        name: str,
        exclude_uri: str | None = None,
    ) -> list[SymbolLocation]:
        """Find all locations of a symbol across the project.

        Thread-safe. Returns empty list if index is not ready yet.

        Parameters:
            name: Symbol name to search for.
            exclude_uri: File URI to exclude (typically the file being edited).

        Returns:
            List of SymbolLocation sorted by (uri, line).
        """
        if not self._indexer.is_ready:
            return []
        return self._symbol_table.query_references(name, exclude_uri)

    def query_definitions(self, name: str) -> list[SymbolLocation]:
        """Find all definition locations of a symbol.

        Thread-safe. Returns empty list if index is not ready yet.
        """
        if not self._indexer.is_ready:
            return []
        return self._symbol_table.query_definitions(name)

    def get_file_imports(self, uri: str) -> set[str]:
        """Get the imported module base names for a file.

        Thread-safe. Returns empty set if index is not ready or file not indexed.
        """
        if not self._indexer.is_ready:
            return set()
        return self._symbol_table.get_file_imports(uri)

    def on_file_saved(self, uri: str) -> None:
        """Schedule re-indexing of a single file.

        Called when the editor reports a didSave event. Converts the URI to
        a file path and queues it for background re-indexing.
        """
        path = uri_to_path(uri)
        if os.path.isfile(path):
            self._indexer.schedule_reindex(path)
        else:
            self._indexer.schedule_remove(path)

    def stop(self) -> None:
        """Stop the indexer thread."""
        self._indexer.stop()

    @property
    def file_count(self) -> int:
        """Number of files currently indexed."""
        return self._symbol_table.file_count

    @property
    def symbol_count(self) -> int:
        """Number of unique symbol names in the index."""
        return self._symbol_table.symbol_count
