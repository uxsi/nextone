"""Thread-safe symbol table for the project index.

Stores symbol locations (definitions + references) indexed by name for O(1) lookup.
Supports incremental updates: replacing all symbols for a single file without
rebuilding the entire table.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Iterator

from .queries import SymbolKind, ExtractedSymbol


@dataclass(frozen=True, slots=True)
class SymbolLocation:
    """A single occurrence of a symbol in the project."""
    name: str           # The symbol name
    uri: str            # file:///absolute/path
    line: int           # 0-based
    column: int         # 0-based
    kind: SymbolKind    # DEFINITION or REFERENCE
    context: str        # The line text at this location (truncated to 80 chars)


class SymbolTable:
    """In-memory symbol index with O(1) lookup by name.

    Thread safety: all public methods acquire _lock. Lock is held for the
    duration of a single operation (typically <1ms for queries, <10ms for
    file updates on large files).
    """

    def __init__(self) -> None:
        self._symbols: dict[str, list[SymbolLocation]] = {}
        self._file_symbols: dict[str, set[str]] = {}  # uri → symbol names in that file
        self._file_mtime: dict[str, float] = {}       # uri → last indexed mtime
        self._file_imports: dict[str, set[str]] = {}   # uri → imported module base names
        self._lock = threading.Lock()

    def query_references(
        self, name: str, exclude_uri: str | None = None
    ) -> list[SymbolLocation]:
        """Find all locations of a symbol (both definitions and references).

        Parameters:
            name: The symbol name to look up.
            exclude_uri: Optional URI to exclude from results (typically the
                         file where the rename happened).

        Returns:
            List of SymbolLocation, sorted by (uri, line).
        """
        with self._lock:
            locations = self._symbols.get(name)
            if not locations:
                return []

            if exclude_uri:
                filtered = [loc for loc in locations if loc.uri != exclude_uri]
            else:
                filtered = list(locations)

        filtered.sort(key=lambda loc: (loc.uri, loc.line))
        return filtered

    def query_definitions(self, name: str) -> list[SymbolLocation]:
        """Find all definition locations of a symbol."""
        with self._lock:
            locations = self._symbols.get(name)
            if not locations:
                return []
            return [loc for loc in locations if loc.kind == SymbolKind.DEFINITION]

    def update_file(
        self,
        uri: str,
        symbols: list[SymbolLocation],
        mtime: float = 0.0,
        imports: set[str] | None = None,
    ) -> None:
        """Replace all symbol entries for a file.

        Removes old entries for this URI, then inserts new ones.
        This is the standard incremental update path.

        Parameters:
            uri: File URI.
            symbols: Symbol locations extracted from the file.
            mtime: File modification time at index time.
            imports: Set of imported module base names (e.g., {"api", "utils"}).
        """
        with self._lock:
            self._remove_file_locked(uri)
            # Index new symbols
            names_in_file: set[str] = set()
            for sym in symbols:
                names_in_file.add(sym.name)
                if sym.name not in self._symbols:
                    self._symbols[sym.name] = []
                self._symbols[sym.name].append(sym)
            self._file_symbols[uri] = names_in_file
            if mtime > 0:
                self._file_mtime[uri] = mtime
            if imports is not None:
                self._file_imports[uri] = imports

    def remove_file(self, uri: str) -> None:
        """Remove all entries for a file (e.g., file deleted)."""
        with self._lock:
            self._remove_file_locked(uri)

    def get_file_mtime(self, uri: str) -> float | None:
        """Get the mtime at which a file was last indexed."""
        with self._lock:
            return self._file_mtime.get(uri)

    def get_file_imports(self, uri: str) -> set[str]:
        """Get the imported module base names for a file.

        Returns empty set if file is not indexed.
        """
        with self._lock:
            return set(self._file_imports.get(uri, set()))

    def has_file(self, uri: str) -> bool:
        """Check if a file is in the index."""
        with self._lock:
            return uri in self._file_symbols

    @property
    def file_count(self) -> int:
        with self._lock:
            return len(self._file_symbols)

    @property
    def symbol_count(self) -> int:
        """Total number of unique symbol names."""
        with self._lock:
            return len(self._symbols)

    @property
    def location_count(self) -> int:
        """Total number of symbol locations across all files."""
        with self._lock:
            return sum(len(locs) for locs in self._symbols.values())

    def all_files(self) -> list[str]:
        """Return all indexed file URIs."""
        with self._lock:
            return list(self._file_symbols.keys())

    # -----------------------------------------------------------------------
    # Internal (must be called with _lock held)
    # -----------------------------------------------------------------------

    def _remove_file_locked(self, uri: str) -> None:
        """Remove all entries for a file. Caller must hold _lock."""
        names = self._file_symbols.pop(uri, None)
        self._file_mtime.pop(uri, None)
        self._file_imports.pop(uri, None)
        if not names:
            return

        for name in names:
            locs = self._symbols.get(name)
            if locs is None:
                continue
            locs[:] = [loc for loc in locs if loc.uri != uri]
            if not locs:
                del self._symbols[name]
