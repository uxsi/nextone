"""Tests for cross-file location prediction."""

import os
import time

from next_edit_server.edit_history import EditRecord
from next_edit_server.location.engine import LocationEngine, RuleType
from next_edit_server.project_index import ProjectIndex, path_to_uri


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "cross_file", "simple_rename")


def _make_index() -> ProjectIndex:
    """Create and wait for a ProjectIndex over the simple_rename fixtures."""
    index = ProjectIndex(workspace_root=FIXTURE_DIR)
    index.start()
    deadline = time.monotonic() + 10.0
    while not index.is_ready() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert index.is_ready(), "Index not ready"
    return index


def test_cross_file_rename_detection():
    """Rename in api.py should produce cross-file predictions for test_api.py and cli.py."""
    index = _make_index()
    engine = LocationEngine(confidence_threshold=0.5)
    engine.set_project_index(index)

    api_uri = path_to_uri(os.path.join(FIXTURE_DIR, "api.py"))

    # Simulate: user renamed "hello" → "goodbye" on line 0 of api.py
    edit = EditRecord(
        uri=api_uri,
        version=2,
        timestamp=1000,
        old_lines=["def hello(name):"],
        new_lines=["def goodbye(name):"],
        start_line=0,
        end_line=1,
    )

    # api.py after the edit
    source_code = "def goodbye(name):\n    return f\"Hello, {name}!\"\n"

    predictions = engine.predict_cross_file(
        edit=edit,
        source_code=source_code,
        language="python",
    )

    assert len(predictions) >= 2, f"Expected >=2 cross-file predictions, got {len(predictions)}"

    # All predictions should be cross-file (target_uri set, different from source)
    for pred in predictions:
        assert pred.target_uri is not None
        assert pred.target_uri != api_uri
        assert pred.rule == RuleType.RENAME
        assert pred.context["old_name"] == "hello"
        assert pred.context["new_name"] == "goodbye"
        assert pred.context.get("cross_file") is True

    # Check target files
    target_uris = {p.target_uri for p in predictions}
    test_api_uri = path_to_uri(os.path.join(FIXTURE_DIR, "test_api.py"))
    cli_uri = path_to_uri(os.path.join(FIXTURE_DIR, "cli.py"))
    assert test_api_uri in target_uris
    assert cli_uri in target_uris

    index.stop()


def test_cross_file_returns_empty_when_no_index():
    """Without a project index, cross-file prediction returns empty."""
    engine = LocationEngine(confidence_threshold=0.5)
    # No set_project_index call

    edit = EditRecord(
        uri="file:///test.py",
        version=1,
        timestamp=1000,
        old_lines=["def hello():"],
        new_lines=["def goodbye():"],
        start_line=0,
        end_line=1,
    )

    predictions = engine.predict_cross_file(
        edit=edit,
        source_code="def goodbye(): pass",
        language="python",
    )
    assert predictions == []


def test_cross_file_returns_empty_for_no_match():
    """If the renamed symbol has no cross-file references, return empty."""
    index = _make_index()
    engine = LocationEngine(confidence_threshold=0.5)
    engine.set_project_index(index)

    api_uri = path_to_uri(os.path.join(FIXTURE_DIR, "api.py"))

    # Rename a symbol that doesn't exist in other files
    edit = EditRecord(
        uri=api_uri,
        version=2,
        timestamp=1000,
        old_lines=["unique_symbol_xyz = 1"],
        new_lines=["unique_symbol_abc = 1"],
        start_line=5,
        end_line=6,
    )

    predictions = engine.predict_cross_file(
        edit=edit,
        source_code="unique_symbol_abc = 1\n",
        language="python",
    )
    assert predictions == []

    index.stop()


def test_same_file_prediction_still_works():
    """Adding cross-file support should not break same-file prediction."""
    index = _make_index()
    engine = LocationEngine(confidence_threshold=0.5)
    engine.set_project_index(index)

    # Source code where hello is both defined and called
    source = "def hello(name):\n    return name\n\nhello('world')\n"
    # After rename: hello → goodbye on line 0
    new_source = "def goodbye(name):\n    return name\n\nhello('world')\n"

    edit = EditRecord(
        uri="file:///inline.py",
        version=2,
        timestamp=1000,
        old_lines=["def hello(name):"],
        new_lines=["def goodbye(name):"],
        start_line=0,
        end_line=1,
    )

    # Same-file prediction should still find the reference on line 3
    prediction = engine.predict(
        edit=edit,
        source_code=new_source,
        language="python",
    )
    assert prediction is not None
    assert prediction.target_uri is None  # Same-file = no target_uri
    assert prediction.rule == RuleType.RENAME

    index.stop()
