"""Tests for the project index (integration: scanner + indexer + symbol table)."""

import os
import time
import tempfile
from pathlib import Path

from next_edit_server.project_index import ProjectIndex, path_to_uri


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "cross_file", "simple_rename")


def test_project_index_indexes_fixture_files():
    """ProjectIndex should index the simple_rename fixture and find symbols."""
    index = ProjectIndex(workspace_root=FIXTURE_DIR)
    index.start()

    # Wait for indexing to complete (should be <1s for 3 files)
    deadline = time.monotonic() + 10.0
    while not index.is_ready() and time.monotonic() < deadline:
        time.sleep(0.1)

    assert index.is_ready(), "Index did not become ready within 10s"
    assert index.file_count >= 3  # api.py, test_api.py, cli.py

    # Query for "hello" — should find references in test_api.py and cli.py
    api_uri = path_to_uri(os.path.join(FIXTURE_DIR, "api.py"))
    refs = index.query_references("hello", exclude_uri=api_uri)
    assert len(refs) >= 2, f"Expected >=2 cross-file refs for 'hello', got {len(refs)}"

    # Check that results come from the expected files
    ref_uris = {r.uri for r in refs}
    test_api_uri = path_to_uri(os.path.join(FIXTURE_DIR, "test_api.py"))
    cli_uri = path_to_uri(os.path.join(FIXTURE_DIR, "cli.py"))
    assert test_api_uri in ref_uris, f"Expected {test_api_uri} in {ref_uris}"
    assert cli_uri in ref_uris, f"Expected {cli_uri} in {ref_uris}"

    index.stop()


def test_project_index_graceful_before_ready():
    """Queries return empty before index is ready."""
    index = ProjectIndex(workspace_root=FIXTURE_DIR)
    # Don't call start() — index is not ready
    refs = index.query_references("hello")
    assert refs == []
    assert not index.is_ready()


def test_project_index_incremental_reindex():
    """on_file_saved should update the index for a single file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Resolve symlinks (macOS /tmp → /private/var/...)
        tmpdir = os.path.realpath(tmpdir)

        # Create initial file
        py_file = os.path.join(tmpdir, "mod.py")
        Path(py_file).write_text("def hello(): pass\nhello()")

        index = ProjectIndex(workspace_root=tmpdir)
        index.start()

        deadline = time.monotonic() + 10.0
        while not index.is_ready() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert index.is_ready()

        # Verify initial indexing
        uri = path_to_uri(py_file)
        refs = index.query_references("hello")
        assert len(refs) >= 1

        # Modify the file: rename hello → goodbye
        Path(py_file).write_text("def goodbye(): pass\ngoodbye()")

        # Trigger re-index
        index.on_file_saved(uri)

        # Wait a bit for the background reindex
        time.sleep(1.0)

        # Old symbol should be gone, new should appear
        old_refs = index.query_references("hello")
        new_refs = index.query_references("goodbye")
        assert len(old_refs) == 0, f"Expected 0 refs for 'hello' after reindex, got {len(old_refs)}"
        assert len(new_refs) >= 1, f"Expected >=1 refs for 'goodbye' after reindex, got {len(new_refs)}"

        index.stop()
