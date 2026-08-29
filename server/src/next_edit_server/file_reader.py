"""File reader with LRU caching for cross-file prediction.

Reads file content from disk when the target file is not open in the editor
(not in DocumentStore). Uses mtime-based cache invalidation to avoid
redundant disk I/O.
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict
from pathlib import Path

logger = logging.getLogger("next-edit-server.file_reader")


class FileReader:
    """Read file content from disk with LRU caching.

    Used by the pipeline when generating cross-file suggestions for files
    that are not open in the editor. Files in DocumentStore (open in editor)
    are read from there instead — this class is the fallback for closed files.
    """

    def __init__(self, cache_size: int = 32) -> None:
        # uri → (content, mtime)
        self._cache: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._max_size = cache_size

    def read(self, uri: str) -> str | None:
        """Read file content by URI.

        Returns the file content as a string, or None if:
        - The file does not exist
        - The file cannot be decoded as UTF-8
        - The path is not a regular file

        Cache behavior: checks mtime on every call. If the file has been
        modified since last cache, re-reads from disk.
        """
        path = self._uri_to_path(uri)

        try:
            stat = os.stat(path)
        except OSError:
            # File doesn't exist or not accessible
            self._cache.pop(uri, None)
            return None

        if not os.path.isfile(path):
            return None

        mtime = stat.st_mtime

        # Check cache
        if uri in self._cache:
            cached_content, cached_mtime = self._cache[uri]
            if cached_mtime == mtime:
                self._cache.move_to_end(uri)
                return cached_content

        # Read from disk
        try:
            content = Path(path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.debug("Cannot decode file as UTF-8: %s", path)
            return None
        except OSError:
            logger.debug("Cannot read file: %s", path)
            return None

        # Update cache
        self._cache[uri] = (content, mtime)
        self._cache.move_to_end(uri)

        # Evict oldest if over capacity
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

        return content

    def get_mtime(self, uri: str) -> float | None:
        """Get the modification time of a file.

        Returns None if the file does not exist.
        """
        path = self._uri_to_path(uri)
        try:
            return os.path.getmtime(path)
        except OSError:
            return None

    def invalidate(self, uri: str) -> None:
        """Remove a file from the cache."""
        self._cache.pop(uri, None)

    def clear(self) -> None:
        """Clear the entire cache."""
        self._cache.clear()

    @staticmethod
    def _uri_to_path(uri: str) -> str:
        """Convert file:// URI to filesystem path."""
        if uri.startswith("file://"):
            return uri[7:]
        return uri
