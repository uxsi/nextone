"""Location rule: symbol rename propagation.

Detects when a user renames a symbol (function, variable, class, etc.)
and finds all other references to the old name in the same file using
tree-sitter AST queries.

Limitations (Phase 1):
- Same file only.
- Simple identifier matching — does not handle scope shadowing,
  aliased imports, dynamic references, or method overloading.
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
class RenameDetection:
    """Result of detecting a rename in an edit."""
    old_name: str
    new_name: str
    line: int  # 0-based line where the rename happened


@dataclass(frozen=True, slots=True)
class RenameLocation:
    """A location where the old name is still referenced."""
    line: int       # 0-based
    column: int     # 0-based
    text: str       # The full line text


def detect_rename(edit: EditRecord) -> RenameDetection | None:
    """Try to detect a simple rename from an edit record.

    Heuristic: single-line edits where exactly one identifier changed,
    and the rest of the line structure is the same.
    """
    if len(edit.old_lines) != 1 or len(edit.new_lines) != 1:
        return None

    old_line = edit.old_lines[0]
    new_line = edit.new_lines[0]

    # Extract identifiers from both lines
    old_ids = set(re.findall(r"\b([a-zA-Z_]\w*)\b", old_line))
    new_ids = set(re.findall(r"\b([a-zA-Z_]\w*)\b", new_line))

    removed = old_ids - new_ids
    added = new_ids - old_ids

    # Expect exactly one identifier changed
    if len(removed) != 1 or len(added) != 1:
        return None

    old_name = removed.pop()
    new_name = added.pop()

    # Sanity: the old name should appear in old line, new name in new line
    if old_name not in old_line or new_name not in new_line:
        return None

    return RenameDetection(
        old_name=old_name,
        new_name=new_name,
        line=edit.start_line,
    )


def find_references(
    source_code: str,
    language: str,
    old_name: str,
    exclude_line: int = -1,
) -> list[RenameLocation]:
    """Find all references to `old_name` in the source code using tree-sitter.

    Parameters:
        source_code: Full file content.
        language: Language identifier (e.g., "python", "typescript", "javascript").
        old_name: The symbol name to search for.
        exclude_line: 0-based line to exclude (typically the rename site itself).

    Returns:
        List of locations where the old name is referenced, sorted by line number.
    """
    try:
        lang = tsl.get_language(language)
        parser = tsl.get_parser(language)
    except Exception:
        # Unsupported language — fall back to regex
        return _find_references_regex(source_code, old_name, exclude_line)

    source_bytes = source_code.encode("utf-8")
    tree = parser.parse(source_bytes)

    # Use tree-sitter query to find all identifier nodes matching old_name
    # This is a broad query — it matches any identifier, then we filter by name.
    query = lang.query("(identifier) @id")
    captures = query.captures(tree.root_node)

    lines = source_code.splitlines()
    results: list[RenameLocation] = []

    for node, _ in captures:
        node_text = source_bytes[node.start_byte:node.end_byte].decode("utf-8")
        if node_text != old_name:
            continue
        line_num = node.start_point[0]
        if line_num == exclude_line:
            continue

        results.append(RenameLocation(
            line=line_num,
            column=node.start_point[1],
            text=lines[line_num] if line_num < len(lines) else "",
        ))

    # Deduplicate by line (multiple references on the same line → report once)
    seen_lines: set[int] = set()
    deduped: list[RenameLocation] = []
    for loc in sorted(results, key=lambda r: (r.line, r.column)):
        if loc.line not in seen_lines:
            seen_lines.add(loc.line)
            deduped.append(loc)

    return deduped


def _find_references_regex(
    source_code: str,
    old_name: str,
    exclude_line: int,
) -> list[RenameLocation]:
    """Fallback: find references using regex when tree-sitter doesn't support the language."""
    pattern = re.compile(rf"\b{re.escape(old_name)}\b")
    lines = source_code.splitlines()
    results: list[RenameLocation] = []

    for i, line in enumerate(lines):
        if i == exclude_line:
            continue
        match = pattern.search(line)
        if match:
            results.append(RenameLocation(line=i, column=match.start(), text=line))

    return results
