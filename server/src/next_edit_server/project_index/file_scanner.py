"""File scanner: discovers source files in a git repository.

Uses `git ls-files` for tracked files (respects .gitignore, handles submodules).
Falls back to os.walk when not inside a git repo.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Iterator

logger = logging.getLogger("next-edit-server.project_index.scanner")

# File extensions recognized as source code
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".pyi",
    ".js", ".jsx", ".mjs", ".cjs",
    ".ts", ".tsx", ".mts", ".cts",
    ".go",
    ".rs",
    ".java",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx",
    ".rb",
    ".php",
    ".swift",
    ".kt", ".kts",
    ".lua",
    ".sh", ".bash", ".zsh",
})

# Skip files larger than this (likely generated/minified)
MAX_FILE_SIZE: int = 1_000_000  # 1 MB

# Safety limit on total file count
MAX_FILES: int = 50_000

# Directories to skip in the os.walk fallback
SKIP_DIRS: frozenset[str] = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
    "target",  # Rust/Java build output
    "vendor",  # Go vendored deps
})

# Extension → tree-sitter language name mapping
_EXT_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".lua": "lua",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
}


def find_git_root(start_path: str) -> str | None:
    """Walk up from start_path to find the closest .git directory.

    Returns the absolute path of the git root, or None if not found.
    """
    current = Path(start_path).resolve()
    while True:
        if (current / ".git").exists():
            return str(current)
        parent = current.parent
        if parent == current:
            return None
        current = parent


def get_language_for_file(file_path: str) -> str | None:
    """Map a file path to its tree-sitter language name.

    Returns None for unsupported files.
    """
    ext = Path(file_path).suffix.lower()
    return _EXT_TO_LANGUAGE.get(ext)


class FileScanner:
    """Discovers source files in a workspace, respecting .gitignore."""

    def __init__(self, workspace_root: str) -> None:
        self._root = Path(workspace_root).resolve()
        self._git_root = find_git_root(str(self._root))

    @property
    def workspace_root(self) -> str:
        return str(self._root)

    @property
    def git_root(self) -> str | None:
        return self._git_root

    def scan(self) -> Iterator[str]:
        """Yield absolute file paths that should be indexed.

        Priority:
        1. `git ls-files` — fast, respects .gitignore
        2. os.walk fallback — when not in a git repo
        """
        if self._git_root:
            yield from self._scan_git()
        else:
            yield from self._scan_walk()

    def _scan_git(self) -> Iterator[str]:
        """Use git ls-files to enumerate tracked source files.

        Runs `git ls-files` from the git root but only yields files that
        fall within workspace_root. This handles the case where workspace_root
        is a subdirectory of the git repo.
        """
        assert self._git_root is not None
        try:
            result = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=self._git_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            logger.warning("git ls-files failed, falling back to os.walk")
            yield from self._scan_walk()
            return

        if result.returncode != 0:
            logger.warning(
                "git ls-files returned %d, falling back to os.walk", result.returncode
            )
            yield from self._scan_walk()
            return

        count = 0
        root_prefix = str(self._root) + os.sep
        for relative_path in result.stdout.splitlines():
            if count >= MAX_FILES:
                logger.info("Reached MAX_FILES limit (%d), stopping scan", MAX_FILES)
                return

            abs_path = os.path.join(self._git_root, relative_path)

            # Only include files within workspace_root (which may be a subdir of git root)
            if not abs_path.startswith(root_prefix) and abs_path != str(self._root):
                continue

            if self._should_index(abs_path):
                count += 1
                yield abs_path

        logger.info("git ls-files scan complete: %d files", count)

    def _scan_walk(self) -> Iterator[str]:
        """Fallback: walk the filesystem, skipping known non-source directories."""
        count = 0
        for dirpath, dirnames, filenames in os.walk(str(self._root)):
            # Prune skipped directories (in-place modification of dirnames)
            dirnames[:] = [
                d for d in dirnames
                if d not in SKIP_DIRS and not d.startswith(".")
            ]

            for filename in filenames:
                if count >= MAX_FILES:
                    logger.info("Reached MAX_FILES limit (%d), stopping scan", MAX_FILES)
                    return

                abs_path = os.path.join(dirpath, filename)
                if self._should_index(abs_path):
                    count += 1
                    yield abs_path

        logger.info("os.walk scan complete: %d files", count)

    @staticmethod
    def _should_index(abs_path: str) -> bool:
        """Check if a file should be indexed (extension + size)."""
        ext = Path(abs_path).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return False

        try:
            size = os.path.getsize(abs_path)
        except OSError:
            return False

        if size > MAX_FILE_SIZE:
            return False

        return True
