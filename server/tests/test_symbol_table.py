"""Tests for project_index.symbol_table."""

import threading
from next_edit_server.project_index.symbol_table import SymbolTable, SymbolLocation
from next_edit_server.project_index.queries import SymbolKind


def _loc(name: str, uri: str, line: int, col: int = 0, kind: SymbolKind = SymbolKind.REFERENCE, ctx: str = "") -> SymbolLocation:
    return SymbolLocation(name=name, uri=uri, line=line, column=col, kind=kind, context=ctx)


def test_empty_query():
    table = SymbolTable()
    assert table.query_references("hello") == []
    assert table.file_count == 0
    assert table.symbol_count == 0


def test_update_and_query():
    table = SymbolTable()
    symbols = [
        _loc("hello", "file:///a.py", 0, kind=SymbolKind.DEFINITION, ctx="def hello():"),
        _loc("hello", "file:///a.py", 5, ctx="hello()"),
    ]
    table.update_file("file:///a.py", symbols)

    refs = table.query_references("hello")
    assert len(refs) == 2
    assert refs[0].uri == "file:///a.py"
    assert refs[0].line == 0
    assert refs[1].line == 5


def test_query_with_exclude():
    table = SymbolTable()
    table.update_file("file:///a.py", [
        _loc("hello", "file:///a.py", 0, kind=SymbolKind.DEFINITION, ctx="def hello():"),
    ])
    table.update_file("file:///b.py", [
        _loc("hello", "file:///b.py", 3, ctx="hello()"),
    ])

    refs = table.query_references("hello", exclude_uri="file:///a.py")
    assert len(refs) == 1
    assert refs[0].uri == "file:///b.py"


def test_query_definitions():
    table = SymbolTable()
    table.update_file("file:///a.py", [
        _loc("hello", "file:///a.py", 0, kind=SymbolKind.DEFINITION, ctx="def hello():"),
        _loc("hello", "file:///a.py", 5, kind=SymbolKind.REFERENCE, ctx="hello()"),
    ])

    defs = table.query_definitions("hello")
    assert len(defs) == 1
    assert defs[0].kind == SymbolKind.DEFINITION


def test_update_replaces_old_entries():
    table = SymbolTable()
    table.update_file("file:///a.py", [
        _loc("hello", "file:///a.py", 0, ctx="hello()"),
    ])
    assert len(table.query_references("hello")) == 1

    # Re-index with different content
    table.update_file("file:///a.py", [
        _loc("goodbye", "file:///a.py", 10, ctx="goodbye()"),
    ])
    assert table.query_references("hello") == []
    assert len(table.query_references("goodbye")) == 1


def test_remove_file():
    table = SymbolTable()
    table.update_file("file:///a.py", [
        _loc("hello", "file:///a.py", 0, ctx="hello()"),
    ])
    assert table.file_count == 1

    table.remove_file("file:///a.py")
    assert table.file_count == 0
    assert table.query_references("hello") == []


def test_remove_nonexistent_file():
    table = SymbolTable()
    table.remove_file("file:///nope.py")  # Should not raise
    assert table.file_count == 0


def test_multiple_files():
    table = SymbolTable()
    table.update_file("file:///a.py", [
        _loc("hello", "file:///a.py", 0, kind=SymbolKind.DEFINITION, ctx="def hello():"),
    ])
    table.update_file("file:///b.py", [
        _loc("hello", "file:///b.py", 5, ctx="hello()"),
    ])
    table.update_file("file:///c.py", [
        _loc("hello", "file:///c.py", 10, ctx="hello()"),
        _loc("hello", "file:///c.py", 12, ctx="hello()"),
    ])

    refs = table.query_references("hello")
    assert len(refs) == 4
    assert table.file_count == 3


def test_mtime_tracking():
    table = SymbolTable()
    table.update_file("file:///a.py", [], mtime=1000.0)
    assert table.get_file_mtime("file:///a.py") == 1000.0
    assert table.get_file_mtime("file:///nope.py") is None


def test_thread_safety():
    """Multiple threads updating and querying concurrently should not crash."""
    table = SymbolTable()
    errors = []

    def writer(file_idx: int):
        try:
            uri = f"file:///file{file_idx}.py"
            for j in range(50):
                table.update_file(uri, [
                    _loc(f"sym{j}", uri, j, ctx=f"sym{j}()"),
                ])
        except Exception as e:
            errors.append(e)

    def reader():
        try:
            for _ in range(100):
                table.query_references("sym25")
        except Exception as e:
            errors.append(e)

    threads = []
    for i in range(4):
        threads.append(threading.Thread(target=writer, args=(i,)))
    for _ in range(2):
        threads.append(threading.Thread(target=reader))

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Thread safety violation: {errors}"
