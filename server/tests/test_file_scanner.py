"""Tests for project_index.file_scanner."""

import os
import tempfile
import subprocess
from pathlib import Path

from next_edit_server.project_index.file_scanner import (
    FileScanner,
    find_git_root,
    get_language_for_file,
    SUPPORTED_EXTENSIONS,
)


def test_get_language_for_file():
    assert get_language_for_file("foo.py") == "python"
    assert get_language_for_file("bar.ts") == "typescript"
    assert get_language_for_file("baz.tsx") == "typescript"
    assert get_language_for_file("qux.js") == "javascript"
    assert get_language_for_file("main.go") == "go"
    assert get_language_for_file("lib.rs") == "rust"
    assert get_language_for_file("README.md") is None
    assert get_language_for_file("data.json") is None


def test_find_git_root_in_repo():
    """find_git_root should return the repo root when called from inside it."""
    # We're inside the nextone repo, so this should work
    this_file = os.path.abspath(__file__)
    root = find_git_root(os.path.dirname(this_file))
    assert root is not None
    assert os.path.isdir(os.path.join(root, ".git"))


def test_find_git_root_not_in_repo():
    with tempfile.TemporaryDirectory() as tmpdir:
        result = find_git_root(tmpdir)
        # tmpdir is not inside a git repo (unless /tmp itself is, which is unusual)
        # This test may pass or fail depending on the system; we just verify it doesn't crash
        assert result is None or os.path.isdir(os.path.join(result, ".git"))


def test_scanner_scan_finds_python_files():
    """FileScanner should find .py files in a directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create some files
        Path(os.path.join(tmpdir, "main.py")).write_text("print('hello')")
        Path(os.path.join(tmpdir, "utils.py")).write_text("def util(): pass")
        Path(os.path.join(tmpdir, "README.md")).write_text("# readme")
        Path(os.path.join(tmpdir, "data.json")).write_text("{}")

        scanner = FileScanner(tmpdir)
        files = list(scanner.scan())

        # Should find the two .py files but not .md or .json
        basenames = {os.path.basename(f) for f in files}
        assert "main.py" in basenames
        assert "utils.py" in basenames
        assert "README.md" not in basenames
        assert "data.json" not in basenames


def test_scanner_skips_large_files():
    """FileScanner should skip files larger than MAX_FILE_SIZE."""
    with tempfile.TemporaryDirectory() as tmpdir:
        small = os.path.join(tmpdir, "small.py")
        large = os.path.join(tmpdir, "large.py")

        Path(small).write_text("x = 1")
        # Create a file larger than 1MB
        Path(large).write_text("x = 1\n" * 200_000)

        scanner = FileScanner(tmpdir)
        files = list(scanner.scan())

        basenames = {os.path.basename(f) for f in files}
        assert "small.py" in basenames
        assert "large.py" not in basenames


def test_scanner_skips_hidden_dirs():
    """FileScanner should skip .hidden directories in walk fallback."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Normal file
        Path(os.path.join(tmpdir, "visible.py")).write_text("x = 1")

        # File inside a hidden directory
        hidden_dir = os.path.join(tmpdir, ".hidden")
        os.makedirs(hidden_dir)
        Path(os.path.join(hidden_dir, "secret.py")).write_text("x = 2")

        scanner = FileScanner(tmpdir)
        files = list(scanner.scan())

        basenames = {os.path.basename(f) for f in files}
        assert "visible.py" in basenames
        assert "secret.py" not in basenames


def test_scanner_with_git_repo():
    """FileScanner using git ls-files in a real git repo."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Initialize a git repo
        subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmpdir, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmpdir, capture_output=True,
        )

        # Create and add files
        Path(os.path.join(tmpdir, "tracked.py")).write_text("x = 1")
        Path(os.path.join(tmpdir, "untracked.py")).write_text("y = 2")
        subprocess.run(["git", "add", "tracked.py"], cwd=tmpdir, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=tmpdir, capture_output=True,
        )

        scanner = FileScanner(tmpdir)
        assert scanner.git_root is not None
        files = list(scanner.scan())

        basenames = {os.path.basename(f) for f in files}
        assert "tracked.py" in basenames
        # git ls-files with --others --exclude-standard also picks up untracked
        assert "untracked.py" in basenames
