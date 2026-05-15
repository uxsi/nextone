"""Tests for protocol module."""

from next_edit_server.protocol import (
    Position,
    Range,
    TextChange,
    LineDiff,
    SuggestParams,
    StatusParams,
    ServerState,
    CancelSuggestionParams,
    CancelReason,
    Methods,
    to_wire,
    make_notification,
)


def test_position_to_wire():
    p = Position(line=10, character=5)
    assert to_wire(p) == {"line": 10, "character": 5}


def test_suggest_params_to_wire():
    s = SuggestParams(
        id="s-001",
        uri="file:///test.ts",
        base_uri="file:///test.ts",
        base_version=3,
        location=Position(line=10, character=0),
        diff="10-| old\n10+| new",
        description="test",
        deleted_lines=[LineDiff(num=10, text="old")],
        added_lines=[LineDiff(num=10, text="new")],
    )
    wire = to_wire(s)
    assert wire["id"] == "s-001"
    assert wire["baseUri"] == "file:///test.ts"
    assert wire["baseVersion"] == 3
    assert wire["location"] == {"line": 10, "character": 0}
    assert wire["deletedLines"] == [{"num": 10, "text": "old"}]
    assert wire["addedLines"] == [{"num": 10, "text": "new"}]


def test_status_params_to_wire():
    s = StatusParams(state=ServerState.READY, message="ok")
    wire = to_wire(s)
    assert wire["state"] == "ready"
    assert wire["message"] == "ok"


def test_cancel_suggestion_to_wire():
    c = CancelSuggestionParams(id="s-001", reason=CancelReason.DOCUMENT_CHANGED)
    wire = to_wire(c)
    assert wire["reason"] == "document_changed"


def test_make_notification():
    notif = make_notification(
        Methods.STATUS,
        StatusParams(state=ServerState.LOADING_MODEL, message="Loading..."),
    )
    assert notif["jsonrpc"] == "2.0"
    assert notif["method"] == "nextEdit/status"
    assert notif["params"]["state"] == "loading_model"
    assert "id" not in notif
