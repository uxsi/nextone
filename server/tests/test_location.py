"""Tests for the Location module (rename, signature, pattern rules)."""

from next_edit_server.edit_history import EditRecord, EditHistory
from next_edit_server.location.rename import detect_rename, find_references
from next_edit_server.location.signature import detect_signature_change, find_call_sites
from next_edit_server.location.pattern import detect_pattern, find_methods_missing_reference
from next_edit_server.location.engine import LocationEngine, RuleType


# ---------------------------------------------------------------------------
# Rename rule tests
# ---------------------------------------------------------------------------

def test_detect_rename_simple():
    edit = EditRecord(
        uri="file:///a.py",
        version=2,
        timestamp=0,
        old_lines=["def hello(name):"],
        new_lines=["def goodbye(name):"],
        start_line=0,
        end_line=1,
    )
    result = detect_rename(edit)
    assert result is not None
    assert result.old_name == "hello"
    assert result.new_name == "goodbye"


def test_detect_rename_no_change():
    edit = EditRecord(
        uri="file:///a.py",
        version=2,
        timestamp=0,
        old_lines=["x = 1"],
        new_lines=["x = 2"],
        start_line=0,
        end_line=1,
    )
    # Only value changed, no identifier rename
    result = detect_rename(edit)
    assert result is None


def test_detect_rename_multiline_returns_none():
    edit = EditRecord(
        uri="file:///a.py",
        version=2,
        timestamp=0,
        old_lines=["def hello(", "    name):"],
        new_lines=["def goodbye(", "    name):"],
        start_line=0,
        end_line=2,
    )
    # Multi-line edits are not handled as simple renames
    result = detect_rename(edit)
    assert result is None


def test_find_references_python():
    source = """def goodbye(name):
    return name

hello(\"world\")
result = hello(\"test\")
print(result)
"""
    refs = find_references(source, "python", "hello", exclude_line=-1)
    assert len(refs) == 2
    assert refs[0].line == 3
    assert refs[1].line == 4


def test_find_references_excludes_line():
    source = """def hello(name):
    return name

hello(\"world\")
"""
    refs = find_references(source, "python", "hello", exclude_line=0)
    assert len(refs) == 1
    assert refs[0].line == 3


# ---------------------------------------------------------------------------
# Signature rule tests
# ---------------------------------------------------------------------------

def test_detect_signature_change_python():
    edit = EditRecord(
        uri="file:///a.py",
        version=2,
        timestamp=0,
        old_lines=["def process(name):"],
        new_lines=["def process(name, age):"],
        start_line=5,
        end_line=6,
    )
    result = detect_signature_change(edit)
    assert result is not None
    assert result.function_name == "process"


def test_detect_signature_change_rename_not_matched():
    edit = EditRecord(
        uri="file:///a.py",
        version=2,
        timestamp=0,
        old_lines=["def hello(name):"],
        new_lines=["def goodbye(name):"],
        start_line=0,
        end_line=1,
    )
    # Function name changed, so this is a rename not a signature change
    result = detect_signature_change(edit)
    assert result is None


def test_find_call_sites_python():
    source = """def process(name, age):
    return name

process("alice")
x = process("bob")
print("done")
"""
    sites = find_call_sites(source, "python", "process", exclude_line=0)
    assert len(sites) == 2
    assert sites[0].line == 3
    assert sites[1].line == 4


# ---------------------------------------------------------------------------
# Pattern rule tests
# ---------------------------------------------------------------------------

def test_detect_pattern_python():
    source = """class User:
    def __init__(self, name):
        self.name = name
        self.session_id = None

    def serialize(self):
        return {"name": self.name}
"""
    edit = EditRecord(
        uri="file:///a.py",
        version=2,
        timestamp=0,
        old_lines=["        self.name = name"],
        new_lines=["        self.name = name", "        self.session_id = None"],
        start_line=2,
        end_line=3,
    )
    result = detect_pattern(edit, source)
    assert result is not None
    assert result.new_identifier == "session_id"


def test_find_methods_missing_reference():
    source = """class User:
    def __init__(self, name):
        self.name = name
        self.session_id = None

    def serialize(self):
        return {"name": self.name}

    def validate(self):
        assert self.name is not None
"""
    methods = find_methods_missing_reference(source, "python", "session_id", edited_line=3)
    # serialize and validate both miss session_id
    assert len(methods) >= 1
    method_names = [m.method_name for m in methods]
    assert "serialize" in method_names


# ---------------------------------------------------------------------------
# Engine integration tests
# ---------------------------------------------------------------------------

def test_engine_rename_prediction():
    source = """def goodbye(name):
    return name

hello("world")
result = hello("test")
"""
    edit = EditRecord(
        uri="file:///a.py",
        version=2,
        timestamp=0,
        old_lines=["def hello(name):"],
        new_lines=["def goodbye(name):"],
        start_line=0,
        end_line=1,
    )
    engine = LocationEngine(confidence_threshold=0.5)
    pred = engine.predict(edit, source, "python")
    assert pred is not None
    assert pred.rule == RuleType.RENAME
    assert pred.line == 3  # First reference to "hello"
    assert pred.confidence >= 0.5


def test_engine_no_prediction_when_no_match():
    source = """x = 1
y = 2
z = 3
"""
    edit = EditRecord(
        uri="file:///a.py",
        version=2,
        timestamp=0,
        old_lines=["x = 1"],
        new_lines=["x = 99"],
        start_line=0,
        end_line=1,
    )
    engine = LocationEngine(confidence_threshold=0.5)
    pred = engine.predict(edit, source, "python")
    assert pred is None


def test_engine_composite_rename_from_history():
    """Simulate real user behavior: select 'hello', delete, type 'good' char by char."""
    source = """def good(name):
    return name

hello("world")
result = hello("test")
"""
    # Edit history: delete "hello" then type g, o, o, d
    history = [
        EditRecord(
            uri="file:///a.py", version=2, timestamp=0,
            old_lines=["def hello(name):"],
            new_lines=["def (name):"],
            start_line=0, end_line=1,
        ),
        EditRecord(
            uri="file:///a.py", version=3, timestamp=0,
            old_lines=["def (name):"],
            new_lines=["def g(name):"],
            start_line=0, end_line=1,
        ),
        EditRecord(
            uri="file:///a.py", version=4, timestamp=0,
            old_lines=["def goo(name):"],
            new_lines=["def good(name):"],
            start_line=0, end_line=1,
        ),
    ]

    engine = LocationEngine(confidence_threshold=0.5)
    pred = engine.predict(history[-1], source, "python", edit_history=history)
    assert pred is not None
    assert pred.rule == RuleType.RENAME
    assert pred.context["old_name"] == "hello"
    assert pred.context["new_name"] == "good"
    assert pred.line == 3  # First reference to "hello"
