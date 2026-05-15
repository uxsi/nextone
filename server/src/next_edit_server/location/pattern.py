"""Location rule: repetitive pattern detection.

Detects when a user adds a field to a struct/class and then initializes it
in one method, predicting that other methods need the same treatment.

Example: User adds field `session_id` to a Python class, then adds
`self.session_id = session_id` in `__init__`. This rule predicts that
`serialize()`, `validate()`, etc. also need to handle `session_id`.

Limitations (Phase 1):
- Same file only.
- Heuristic-based: looks for newly introduced identifiers in struct/class
  bodies and then scans methods for missing references.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import tree_sitter_languages as tsl

from ..edit_history import EditRecord


@dataclass(frozen=True, slots=True)
class PatternDetection:
    """A detected repetitive pattern."""
    new_identifier: str  # The newly introduced identifier (e.g., field name)
    class_name: str      # The containing class/struct name
    line: int            # 0-based line of the pattern edit


@dataclass(frozen=True, slots=True)
class PatternLocation:
    """A method that likely needs updating."""
    line: int            # 0-based line of the method definition
    method_name: str
    text: str            # The method definition line


def detect_pattern(edit: EditRecord, source_code: str) -> PatternDetection | None:
    """Detect if an edit introduces a new identifier in a class/struct context.

    Heuristic: the edit adds a line containing `self.<identifier>` (Python)
    or `this.<identifier>` (JS/TS) that wasn't in the old lines.
    """
    if not edit.new_lines:
        return None

    old_text = "\n".join(edit.old_lines) if edit.old_lines else ""
    new_text = "\n".join(edit.new_lines)

    # Look for self.xxx or this.xxx patterns
    old_attrs = set(re.findall(r"(?:self|this)\.([a-zA-Z_]\w*)", old_text))
    new_attrs = set(re.findall(r"(?:self|this)\.([a-zA-Z_]\w*)", new_text))

    added_attrs = new_attrs - old_attrs
    if not added_attrs:
        return None

    # Take the first newly added attribute
    new_id = sorted(added_attrs)[0]

    # Try to find the containing class name
    class_name = _find_containing_class(source_code, edit.start_line)

    return PatternDetection(
        new_identifier=new_id,
        class_name=class_name or "<unknown>",
        line=edit.start_line,
    )


def find_methods_missing_reference(
    source_code: str,
    language: str,
    identifier: str,
    edited_line: int,
) -> list[PatternLocation]:
    """Find methods in the same class that don't yet reference the identifier.

    Returns method definition locations that likely need updating.
    """
    try:
        lang = tsl.get_language(language)
        parser = tsl.get_parser(language)
    except Exception:
        return _find_methods_regex(source_code, identifier, edited_line)

    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)
    lines = source_code.splitlines()

    # Find the class node containing the edited line
    class_node = _find_class_node(tree.root_node, edited_line)
    if class_node is None:
        return []

    # Find all method/function definitions within the class
    methods = _find_method_nodes(class_node, source_bytes)

    results: list[PatternLocation] = []
    attr_pattern = re.compile(rf"(?:self|this)\.{re.escape(identifier)}\b")

    for method_name, method_node in methods:
        method_start = method_node.start_point[0]
        method_end = method_node.end_point[0]

        # Skip if this method is where the edit happened
        if method_start <= edited_line <= method_end:
            continue

        # Check if the method already references the identifier
        method_text = "\n".join(lines[method_start:method_end + 1])
        if attr_pattern.search(method_text):
            continue

        results.append(PatternLocation(
            line=method_start,
            method_name=method_name,
            text=lines[method_start] if method_start < len(lines) else "",
        ))

    return sorted(results, key=lambda r: r.line)


def _find_containing_class(source_code: str, line: int) -> str | None:
    """Find the class name containing a given line using regex heuristic."""
    class_pattern = re.compile(r"^\s*class\s+([a-zA-Z_]\w*)")
    lines = source_code.splitlines()

    for i in range(line, -1, -1):
        if i < len(lines):
            m = class_pattern.match(lines[i])
            if m:
                return m.group(1)
    return None


def _find_class_node(root: "Node", line: int) -> "Node | None":
    """Find the class_definition node containing the given line."""
    for child in root.children:
        if child.type in ("class_definition", "class_declaration"):
            if child.start_point[0] <= line <= child.end_point[0]:
                return child
        # Recurse into nested structures
        result = _find_class_node(child, line)
        if result is not None:
            return result
    return None


def _find_method_nodes(class_node: "Node", source_bytes: bytes) -> list[tuple[str, "Node"]]:
    """Find all method/function nodes within a class node."""
    methods: list[tuple[str, "Node"]] = []

    for child in class_node.children:
        # Handle body/block wrapper nodes
        if child.type in ("block", "class_body", "declaration_list"):
            methods.extend(_find_method_nodes(child, source_bytes))
            continue

        if child.type in ("function_definition", "method_definition", "function_declaration"):
            # Extract method name
            for sub in child.children:
                if sub.type in ("identifier", "property_identifier"):
                    name = source_bytes[sub.start_byte:sub.end_byte].decode("utf-8")
                    methods.append((name, child))
                    break

    return methods


def _find_methods_regex(
    source_code: str,
    identifier: str,
    edited_line: int,
) -> list[PatternLocation]:
    """Fallback: regex-based method finding."""
    method_pattern = re.compile(r"^\s+def\s+([a-zA-Z_]\w*)\s*\(")
    attr_pattern = re.compile(rf"(?:self|this)\.{re.escape(identifier)}\b")
    lines = source_code.splitlines()

    # Find all method definitions
    methods: list[tuple[str, int, int]] = []
    for i, line in enumerate(lines):
        m = method_pattern.match(line)
        if m:
            methods.append((m.group(1), i, i))

    # Estimate method boundaries (until next method or dedent)
    for idx in range(len(methods)):
        name, start, _ = methods[idx]
        end = methods[idx + 1][1] - 1 if idx + 1 < len(methods) else len(lines) - 1
        methods[idx] = (name, start, end)

    results: list[PatternLocation] = []
    for name, start, end in methods:
        if start <= edited_line <= end:
            continue
        method_text = "\n".join(lines[start:end + 1])
        if attr_pattern.search(method_text):
            continue
        results.append(PatternLocation(
            line=start,
            method_name=name,
            text=lines[start] if start < len(lines) else "",
        ))

    return results
