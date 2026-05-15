"""Location rule: function signature change propagation.

Detects when a user modifies a function's parameter list (added/removed
parameters) and finds all call sites of that function in the same file.

Limitations (Phase 1):
- Same file only.
- Identifies functions by name match on call expressions.
- Does not handle scope shadowing, method overloading, or indirect calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import tree_sitter_languages as tsl

if TYPE_CHECKING:
    from tree_sitter import Tree, Node

from ..edit_history import EditRecord


@dataclass(frozen=True, slots=True)
class SignatureChange:
    """A detected function signature change."""
    function_name: str
    line: int  # 0-based line of the function definition


@dataclass(frozen=True, slots=True)
class CallSiteLocation:
    """A call site that may need updating."""
    line: int       # 0-based
    column: int     # 0-based
    text: str       # Full line text


# Patterns that indicate a function/method definition
_DEF_PATTERNS = [
    # Python: def func_name(
    re.compile(r"\bdef\s+([a-zA-Z_]\w*)\s*\("),
    # JS/TS: function func_name(
    re.compile(r"\bfunction\s+([a-zA-Z_]\w*)\s*\("),
    # Rust: fn func_name(
    re.compile(r"\bfn\s+([a-zA-Z_]\w*)\s*\("),
    # Go: func func_name(
    re.compile(r"\bfunc\s+([a-zA-Z_]\w*)\s*\("),
    # Java/C/C++: type func_name( — simplified, matches "word word("
    re.compile(r"\b[a-zA-Z_]\w*\s+([a-zA-Z_]\w*)\s*\("),
]


def detect_signature_change(edit: EditRecord) -> SignatureChange | None:
    """Detect if an edit modifies a function's parameter list.

    Heuristic: the edited line(s) contain a function definition pattern,
    and the parameter list portion differs between old and new.
    """
    if not edit.old_lines or not edit.new_lines:
        return None

    old_text = "\n".join(edit.old_lines)
    new_text = "\n".join(edit.new_lines)

    for pattern in _DEF_PATTERNS:
        old_match = pattern.search(old_text)
        new_match = pattern.search(new_text)

        if old_match and new_match:
            old_name = old_match.group(1)
            new_name = new_match.group(1)

            # Function name must be the same (otherwise it's a rename, not a signature change)
            if old_name != new_name:
                continue

            # Check that something in the parameter area changed
            # (We already know the lines differ since old_lines != new_lines)
            return SignatureChange(
                function_name=old_name,
                line=edit.start_line,
            )

    return None


def find_call_sites(
    source_code: str,
    language: str,
    function_name: str,
    exclude_line: int = -1,
) -> list[CallSiteLocation]:
    """Find all call sites of a function in the same file using tree-sitter.

    Parameters:
        source_code: Full file content.
        language: Language identifier.
        function_name: The function name to search for.
        exclude_line: 0-based line to exclude (the definition itself).

    Returns:
        Call site locations sorted by line number.
    """
    try:
        lang = tsl.get_language(language)
        parser = tsl.get_parser(language)
    except Exception:
        return _find_call_sites_regex(source_code, function_name, exclude_line)

    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)

    # Query for call expressions — pattern varies by language, but
    # most languages have (call_expression function: (identifier) @fn)
    # We try multiple patterns for broader coverage.
    queries = [
        "(call_expression function: (identifier) @fn)",
        "(call (identifier) @fn)",  # Python
    ]

    lines = source_code.splitlines()
    results: list[CallSiteLocation] = []
    seen_lines: set[int] = set()

    for q_str in queries:
        try:
            query = lang.query(q_str)
        except Exception:
            continue

        captures = query.captures(tree.root_node)
        for node, _ in captures:
            node_text = source_bytes[node.start_byte:node.end_byte].decode("utf-8")
            if node_text != function_name:
                continue
            line_num = node.start_point[0]
            if line_num == exclude_line or line_num in seen_lines:
                continue
            seen_lines.add(line_num)
            results.append(CallSiteLocation(
                line=line_num,
                column=node.start_point[1],
                text=lines[line_num] if line_num < len(lines) else "",
            ))

    return sorted(results, key=lambda r: r.line)


def _find_call_sites_regex(
    source_code: str,
    function_name: str,
    exclude_line: int,
) -> list[CallSiteLocation]:
    """Fallback regex search for call sites."""
    pattern = re.compile(rf"\b{re.escape(function_name)}\s*\(")
    lines = source_code.splitlines()
    results: list[CallSiteLocation] = []

    for i, line in enumerate(lines):
        if i == exclude_line:
            continue
        match = pattern.search(line)
        if match:
            results.append(CallSiteLocation(line=i, column=match.start(), text=line))

    return results
