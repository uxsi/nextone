"""Tests for the cross-file pipeline flow (end-to-end)."""

import os
import time
from typing import Any
from unittest.mock import MagicMock

from next_edit_server.document_store import DocumentStore
from next_edit_server.pipeline import Pipeline
from next_edit_server.project_index import path_to_uri


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "cross_file", "simple_rename")


def _wait_for_notifications(notifications: list, timeout: float = 5.0) -> list:
    """Wait until at least one nextEdit/suggest notification appears."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        suggests = [n for n in notifications if n[0] == "nextEdit/suggest"]
        if suggests:
            return suggests
        time.sleep(0.1)
    return []


def test_cross_file_rename_pipeline():
    """End-to-end: rename in api.py → cross-file suggest for test_api.py."""
    doc_store = DocumentStore()
    notifications: list[tuple[str, Any]] = []

    def mock_send(method: str, params: Any) -> None:
        from next_edit_server.protocol import to_wire
        notifications.append((method, to_wire(params)))

    pipeline = Pipeline(
        document_store=doc_store,
        send_notification=mock_send,
        model_path=None,  # DummyBackend
        debounce_ms=50,   # Short debounce for testing
    )
    pipeline.initialize()

    # Start the project index
    pipeline.start_project_index(FIXTURE_DIR)

    # Wait for index to be ready
    deadline = time.monotonic() + 10.0
    while not pipeline._project_index.is_ready() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert pipeline._project_index.is_ready(), "Index not ready"

    # Open api.py with original content
    api_path = os.path.join(FIXTURE_DIR, "api.py")
    api_uri = path_to_uri(api_path)
    with open(api_path) as f:
        api_content = f.read()
    doc_store.open(api_uri, "python", 1, api_content)

    # Simulate rename: hello → goodbye on line 0
    # First, apply the edit to the document
    from next_edit_server.protocol import TextChange, Range, Position
    change = TextChange(
        range=Range(start=Position(line=0, character=4), end=Position(line=0, character=9)),
        text="goodbye",
    )
    doc_store.apply_changes(api_uri, 2, [change])

    # Trigger pipeline with the edit info
    notifications.clear()  # Clear status notifications from initialize
    pipeline.on_did_change(
        uri=api_uri,
        version=2,
        old_lines=["def hello(name):"],
        new_lines=["def goodbye(name):"],
        start_line=0,
        end_line=1,
        timestamp=int(time.time() * 1000),
    )

    # Wait for suggest notification
    suggests = _wait_for_notifications(notifications, timeout=5.0)
    assert len(suggests) >= 1, f"Expected at least 1 suggest, got notifications: {[n[0] for n in notifications]}"

    suggest_params = suggests[0][1]

    # The suggestion should target a different file (cross-file)
    assert suggest_params["uri"] != api_uri, \
        f"Expected cross-file suggest (uri != {api_uri}), got uri={suggest_params['uri']}"
    assert suggest_params["baseUri"] == api_uri
    assert suggest_params["baseVersion"] == 2

    # Verify the suggestion contains hello → goodbye replacement
    deleted = suggest_params.get("deletedLines", [])
    added = suggest_params.get("addedLines", [])
    assert len(deleted) >= 1
    assert len(added) >= 1
    assert "hello" in deleted[0]["text"]
    assert "goodbye" in added[0]["text"]


def test_same_file_takes_priority_over_cross_file():
    """When same-file references exist, they should be suggested first."""
    doc_store = DocumentStore()
    notifications: list[tuple[str, Any]] = []

    def mock_send(method: str, params: Any) -> None:
        from next_edit_server.protocol import to_wire
        notifications.append((method, to_wire(params)))

    pipeline = Pipeline(
        document_store=doc_store,
        send_notification=mock_send,
        model_path=None,
        debounce_ms=50,
    )
    pipeline.initialize()
    pipeline.start_project_index(FIXTURE_DIR)

    deadline = time.monotonic() + 10.0
    while not pipeline._project_index.is_ready() and time.monotonic() < deadline:
        time.sleep(0.1)

    # Open a file that has both definition AND call of hello (same-file refs)
    source = "def hello(name):\n    return name\n\nhello('world')\n"
    uri = "file:///tmp/test_priority.py"
    doc_store.open(uri, "python", 1, source)

    # Apply rename: hello → goodbye
    new_source = "def goodbye(name):\n    return name\n\nhello('world')\n"
    doc_store.full_sync(uri, 2, new_source)

    notifications.clear()
    pipeline.on_did_change(
        uri=uri,
        version=2,
        old_lines=["def hello(name):"],
        new_lines=["def goodbye(name):"],
        start_line=0,
        end_line=1,
        timestamp=int(time.time() * 1000),
    )

    suggests = _wait_for_notifications(notifications, timeout=5.0)
    assert len(suggests) >= 1

    suggest_params = suggests[0][1]
    # Same-file suggestion: uri == baseUri
    assert suggest_params["uri"] == uri
    assert suggest_params["baseUri"] == uri
