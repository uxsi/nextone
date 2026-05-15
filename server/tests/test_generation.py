"""Tests for the Generation Module."""

from next_edit_server.generation.prompt import (
    build_generation_prompt,
    build_rename_prompt,
    parse_nes_diff,
)
from next_edit_server.generation.generator import Generator, GenerationResult
from next_edit_server.inference.backend import DummyBackend
from next_edit_server.location.engine import LocationPrediction, RuleType


# ---------------------------------------------------------------------------
# Prompt tests
# ---------------------------------------------------------------------------

def test_build_generation_prompt_includes_history():
    code = "line0\nline1\nline2\nline3\nline4\nline5\nline6"
    history = [
        {"file": "a.py", "diff": "1-| old\n1+| new"},
    ]
    prompt = build_generation_prompt(code, history, target_location=3)
    assert "<edit_history>" in prompt
    assert "Edit 1 (a.py)" in prompt
    assert "<current_code>" in prompt
    assert "4 | line3" in prompt  # target line (1-based)


def test_build_generation_prompt_empty_history():
    code = "a\nb\nc"
    prompt = build_generation_prompt(code, [], target_location=1)
    assert "(no prior edits)" in prompt


def test_build_rename_prompt():
    code = "def goodbye(name):\n    return name\n\nhello('world')\n"
    prompt = build_rename_prompt(code, target_line=3, old_name="hello", new_name="goodbye")
    assert "`hello`" in prompt
    assert "`goodbye`" in prompt
    assert "4 | hello('world')" in prompt


def test_parse_nes_diff():
    diff_text = """3-| print("Hello")
3+| print("Goodbye")
4 | return True"""

    deleted, added = parse_nes_diff(diff_text)
    assert len(deleted) == 1
    assert deleted[0]["num"] == 3
    assert deleted[0]["text"] == 'print("Hello")'
    assert len(added) == 1
    assert added[0]["num"] == 3
    assert added[0]["text"] == 'print("Goodbye")'


def test_parse_nes_diff_preserves_indentation():
    diff_text = """4-|     hello("world")
4+|     goodbye("world")"""

    deleted, added = parse_nes_diff(diff_text)
    assert deleted[0]["text"] == '    hello("world")'
    assert added[0]["text"] == '    goodbye("world")'


def test_parse_nes_diff_empty():
    deleted, added = parse_nes_diff("")
    assert deleted == []
    assert added == []


# ---------------------------------------------------------------------------
# Generator + DummyBackend integration tests
# ---------------------------------------------------------------------------

def test_generator_rename_with_dummy():
    backend = DummyBackend()
    backend.load()
    gen = Generator(backend)

    prediction = LocationPrediction(
        line=3,
        column=0,
        rule=RuleType.RENAME,
        confidence=0.9,
        context={"old_name": "hello", "new_name": "goodbye", "remaining_refs": 1},
        text="hello('world')",
    )

    source = "def goodbye(name):\n    return name\n\nhello('world')\n"
    result = gen.generate(prediction, source, "file:///a.py", version=2, edit_history=[])

    assert result is not None
    assert result.uri == "file:///a.py"
    assert result.base_version == 2
    assert len(result.deleted_lines) == 1
    assert len(result.added_lines) == 1
    assert result.deleted_lines[0]["text"] == "hello('world')"
    assert result.added_lines[0]["text"] == "goodbye('world')"
    assert "Rename" in result.description
