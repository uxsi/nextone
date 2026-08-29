"""Background indexer thread: full scan + incremental update queue.

The Indexer runs on a daemon thread. On start it performs a full scan of the
workspace, then enters a loop processing incremental update requests (triggered
by didSave events or explicit re-index calls).
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .file_scanner import FileScanner, get_language_for_file
from .queries import extract_symbols, extract_imports, SymbolKind, ExtractedSymbol
from .symbol_table import SymbolTable, SymbolLocation

logger = logging.getLogger("next-edit-server.project_index.indexer")


@dataclass(frozen=True, slots=True)
class IndexTask:
    """A unit of work for the indexer queue."""
    type: str       # "reindex" | "remove"
    path: str       # Absolute file path


def path_to_uri(path: str) -> str:
    """Convert an absolute file path to a file:// URI."""
    abs_path = os.path.abspath(path)
    return f"file://{abs_path}"


def uri_to_path(uri: str) -> str:
    """Convert a file:// URI to an absolute path."""
    if uri.startswith("file://"):
        return uri[7:]
    return uri


class Indexer:
    """Background thread that builds and maintains the symbol table."""

    def __init__(
        self,
        symbol_table: SymbolTable,
        on_ready: Callable[[], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        self._table = symbol_table
        self._on_ready = on_ready
        self._on_progress = on_progress
        self._thread: threading.Thread | None = None
        self._queue: queue.Queue[IndexTask] = queue.Queue()
        self._stop_event = threading.Event()
        self._ready = threading.Event()
        self._workspace_root: str = ""

    @property
    def is_ready(self) -> bool:
        return self._ready.is_set()

    def start(self, workspace_root: str) -> None:
        """Start background indexing. Non-blocking."""
        self._workspace_root = workspace_root
        self._thread = threading.Thread(
            target=self._run,
            name="project-indexer",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the indexer to stop and wait for thread exit."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def schedule_reindex(self, file_path: str) -> None:
        """Queue a single file for re-indexing (called on didSave)."""
        self._queue.put(IndexTask(type="reindex", path=file_path))

    def schedule_remove(self, file_path: str) -> None:
        """Queue file removal from index."""
        self._queue.put(IndexTask(type="remove", path=file_path))

    # -----------------------------------------------------------------------
    # Worker thread
    # -----------------------------------------------------------------------

    def _run(self) -> None:
        """Worker thread: full scan then process incremental queue."""
        try:
            self._full_scan()
        except Exception:
            logger.exception("Full scan failed")

        self._ready.set()
        if self._on_ready:
            self._on_ready()
        logger.info(
            "Index ready: %d files, %d symbols, %d locations",
            self._table.file_count,
            self._table.symbol_count,
            self._table.location_count,
        )

        # Process incremental updates
        while not self._stop_event.is_set():
            try:
                task = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                if task.type == "reindex":
                    self._index_file(task.path)
                elif task.type == "remove":
                    uri = path_to_uri(task.path)
                    self._table.remove_file(uri)
                    logger.debug("Removed from index: %s", task.path)
            except Exception:
                logger.exception("Error processing index task: %s", task)

    def _full_scan(self) -> None:
        """Scan all source files in the workspace and build the symbol table."""
        scanner = FileScanner(self._workspace_root)
        start_time = time.monotonic()

        # First pass: count files for progress reporting
        files = list(scanner.scan())
        total = len(files)
        logger.info("Starting full scan: %d files in %s", total, self._workspace_root)

        for i, file_path in enumerate(files):
            if self._stop_event.is_set():
                logger.info("Full scan interrupted at %d/%d", i, total)
                return

            self._index_file(file_path)

            # Progress callback every 100 files
            if self._on_progress and (i + 1) % 100 == 0:
                self._on_progress(i + 1, total)

        elapsed = time.monotonic() - start_time
        logger.info("Full scan completed in %.2fs", elapsed)

    def _index_file(self, file_path: str) -> None:
        """Parse one file and update the symbol table."""
        language = get_language_for_file(file_path)
        if language is None:
            return

        uri = path_to_uri(file_path)

        # Check mtime for cache validity
        try:
            mtime = os.path.getmtime(file_path)
        except OSError:
            self._table.remove_file(uri)
            return

        # Skip if already indexed at this mtime
        existing_mtime = self._table.get_file_mtime(uri)
        if existing_mtime is not None and existing_mtime == mtime:
            return

        # Read and parse
        try:
            source_bytes = Path(file_path).read_bytes()
        except (OSError, IOError):
            logger.debug("Cannot read file: %s", file_path)
            self._table.remove_file(uri)
            return

        # Decode for line context extraction
        try:
            source_text = source_bytes.decode("utf-8")
        except UnicodeDecodeError:
            # Binary file — skip
            return

        lines = source_text.splitlines()

        # Extract symbols
        extracted = extract_symbols(source_bytes, language)

        # Extract import relationships
        imports = extract_imports(source_bytes, language)

        if not extracted:
            # Still register the file (with no symbols) so we know it was processed
            self._table.update_file(uri, [], mtime=mtime, imports=imports)
            return

        # Convert to SymbolLocation entries
        locations: list[SymbolLocation] = []
        for sym in extracted:
            context = lines[sym.line][:80] if sym.line < len(lines) else ""
            locations.append(SymbolLocation(
                name=sym.name,
                uri=uri,
                line=sym.line,
                column=sym.column,
                kind=sym.kind,
                context=context,
            ))

        self._table.update_file(uri, locations, mtime=mtime, imports=imports)
