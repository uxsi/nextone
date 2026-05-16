"""Tests for the end-to-end pipeline."""

import time
from unittest.mock import MagicMock

from next_edit_server.document_store import DocumentStore
from next_edit_server.pipeline import Pipeline, MetricsCollector
from next_edit_server.protocol import Methods


def test_pipeline_rename_end_to_end():
    """Test the full pipeline: didChange with rename → location → generation → suggest."""
    doc_store = DocumentStore()
    doc_store.open(
        "file:///a.py",
        "python",
        1,
        "def hello(name):\n    return name\n\nhello('world')\nresult = hello('test')\n",
    )

    sent_messages: list[tuple[str, object]] = []

    def mock_send(method: str, params: object) -> None:
        sent_messages.append((method, params))

    pipeline = Pipeline(
        document_store=doc_store,
        send_notification=mock_send,
        model_path=None,  # DummyBackend
        debounce_ms=0,    # No debounce for testing
    )
    pipeline.initialize()

    # Simulate a rename: hello → goodbye on line 0
    # First update the document store
    from next_edit_server.protocol import TextChange, Range, Position
    doc_store.apply_changes(
        "file:///a.py",
        2,
        [TextChange(
            range=Range(
                start=Position(line=0, character=4),
                end=Position(line=0, character=9),
            ),
            text="goodbye",
        )],
    )

    # Trigger pipeline
    pipeline.on_did_change(
        uri="file:///a.py",
        version=2,
        old_lines=["def hello(name):"],
        new_lines=["def goodbye(name):"],
        start_line=0,
        end_line=1,
        timestamp=int(time.time() * 1000),
    )

    # Wait for debounce + processing
    time.sleep(0.2)

    # Check that a suggest notification was sent
    suggest_msgs = [(m, p) for m, p in sent_messages if m == Methods.SUGGEST]
    assert len(suggest_msgs) >= 1, f"Expected suggest message, got: {[m for m, _ in sent_messages]}"

    suggest_params = suggest_msgs[0][1]
    assert suggest_params.uri == "file:///a.py"
    assert suggest_params.base_version == 2
    assert len(suggest_params.deleted_lines) > 0
    assert len(suggest_params.added_lines) > 0

    # Check metrics
    assert pipeline.metrics.trigger_count >= 1


def test_pipeline_no_prediction_for_simple_edit():
    """An edit that doesn't match any rule should not produce a suggestion."""
    doc_store = DocumentStore()
    doc_store.open("file:///b.py", "python", 1, "x = 1\ny = 2\nz = 3\n")

    sent_messages: list[tuple[str, object]] = []
    pipeline = Pipeline(
        document_store=doc_store,
        send_notification=lambda m, p: sent_messages.append((m, p)),
        model_path=None,
        debounce_ms=0,
    )
    pipeline.initialize()

    doc_store.apply_changes(
        "file:///b.py",
        2,
        [TextChange(
            range=Range(
                start=Position(line=0, character=4),
                end=Position(line=0, character=5),
            ),
            text="99",
        )],
    )

    pipeline.on_did_change(
        uri="file:///b.py",
        version=2,
        old_lines=["x = 1"],
        new_lines=["x = 99"],
        start_line=0,
        end_line=1,
        timestamp=int(time.time() * 1000),
    )

    time.sleep(0.2)

    suggest_msgs = [(m, p) for m, p in sent_messages if m == Methods.SUGGEST]
    assert len(suggest_msgs) == 0


def test_pipeline_resolve_updates_metrics():
    """Resolving a suggestion should update acceptance metrics."""
    doc_store = DocumentStore()
    pipeline = Pipeline(
        document_store=doc_store,
        send_notification=lambda m, p: None,
        model_path=None,
    )

    pipeline.on_resolve("suggest-001", accepted=True)
    pipeline.on_resolve("suggest-002", accepted=False)
    pipeline.on_resolve("suggest-003", accepted=True)

    assert pipeline.metrics.accept_count == 2
    assert pipeline.metrics.reject_count == 1
    assert pipeline.metrics.acceptance_rate == 2 / 3


# Import here to avoid issues at module level
from next_edit_server.protocol import TextChange, Range, Position


def test_server_multi_change_skips_pipeline():
    """Multi-change didChange events must sync the document but not trigger the pipeline."""
    from next_edit_server.server import NextEditServer

    server = NextEditServer()

    # Open a document
    server.document_store.open(
        "file:///multi.py", "python", 1,
        "x = 1\ny = 2\nz = 3\n",
    )

    # Set up a mock pipeline to track on_did_change calls
    mock_pipeline = MagicMock()
    server._pipeline = mock_pipeline

    # Simulate a didChange with 2 changes (e.g. multi-cursor edit)
    server._handle_did_change({
        "uri": "file:///multi.py",
        "version": 2,
        "changes": [
            {
                "range": {
                    "start": {"line": 0, "character": 4},
                    "end": {"line": 0, "character": 5},
                },
                "text": "99",
            },
            {
                "range": {
                    "start": {"line": 1, "character": 4},
                    "end": {"line": 1, "character": 5},
                },
                "text": "99",
            },
        ],
        "timestamp": 0,
    })

    # Pipeline must NOT have been called
    mock_pipeline.on_did_change.assert_not_called()

    # But the document must be updated (changes applied)
    doc = server.document_store.get("file:///multi.py")
    assert doc is not None
    lines = doc.get_range(0, 3)
    assert lines[0].rstrip() == "x = 99"
    assert lines[1].rstrip() == "y = 99"


def test_server_single_change_triggers_pipeline():
    """A single-change didChange should trigger the pipeline normally."""
    from next_edit_server.server import NextEditServer

    server = NextEditServer()
    server.document_store.open(
        "file:///single.py", "python", 1,
        "x = 1\ny = 2\n",
    )

    mock_pipeline = MagicMock()
    server._pipeline = mock_pipeline

    server._handle_did_change({
        "uri": "file:///single.py",
        "version": 2,
        "changes": [
            {
                "range": {
                    "start": {"line": 0, "character": 4},
                    "end": {"line": 0, "character": 5},
                },
                "text": "99",
            },
        ],
        "timestamp": 0,
    })

    # Pipeline MUST have been called
    mock_pipeline.on_did_change.assert_called_once()
