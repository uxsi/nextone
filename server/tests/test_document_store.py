"""Tests for DocumentStore."""

from next_edit_server.document_store import DocumentStore, DocumentState
from next_edit_server.protocol import TextChange, Range, Position


def test_open_and_get():
    store = DocumentStore()
    doc = store.open("file:///a.ts", "typescript", 1, "line0\nline1\nline2")
    assert doc.uri == "file:///a.ts"
    assert doc.version == 1
    assert doc.line_count == 3
    assert doc.get_line(0) == "line0"
    assert doc.get_line(2) == "line2"


def test_close():
    store = DocumentStore()
    store.open("file:///a.ts", "typescript", 1, "hello")
    assert "file:///a.ts" in store
    store.close("file:///a.ts")
    assert "file:///a.ts" not in store


def test_apply_changes_simple_replace():
    store = DocumentStore()
    store.open("file:///a.ts", "typescript", 1, "function hello() {\n  return 1\n}")

    change = TextChange(
        range=Range(
            start=Position(line=0, character=9),
            end=Position(line=0, character=14),
        ),
        text="goodbye",
    )
    doc = store.apply_changes("file:///a.ts", 2, [change])
    assert doc is not None
    assert doc.version == 2
    assert doc.get_line(0) == "function goodbye() {"


def test_apply_changes_multiline_insert():
    store = DocumentStore()
    store.open("file:///a.ts", "typescript", 1, "a\nb\nc")

    # Insert a new line after line 1
    change = TextChange(
        range=Range(
            start=Position(line=1, character=1),
            end=Position(line=1, character=1),
        ),
        text="\nnew_line",
    )
    doc = store.apply_changes("file:///a.ts", 2, [change])
    assert doc is not None
    assert doc.lines == ["a", "b", "new_line", "c"]


def test_full_sync():
    store = DocumentStore()
    store.open("file:///a.ts", "typescript", 1, "old content")

    doc = store.full_sync("file:///a.ts", 10, "completely new content")
    assert doc.version == 10
    assert doc.text == "completely new content"


def test_full_sync_creates_if_missing():
    store = DocumentStore()
    doc = store.full_sync("file:///b.ts", 5, "created by sync")
    assert doc.version == 5
    assert "file:///b.ts" in store


def test_version_checks():
    store = DocumentStore()
    store.open("file:///a.ts", "typescript", 3, "content")

    assert store.is_version_current("file:///a.ts", 3)
    assert not store.is_version_current("file:///a.ts", 2)
    assert store.is_version_stale("file:///a.ts", 1)
    assert not store.is_version_stale("file:///a.ts", 3)
    assert not store.is_version_stale("file:///a.ts", 5)
