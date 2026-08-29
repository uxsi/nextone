"""Per-language tree-sitter queries for symbol extraction.

Extracts identifier nodes from parsed source files, classifying them as
DEFINITION (function/class/variable declaration) or REFERENCE (usage).

Design: extract ALL identifiers as references, then additionally mark
definition-position identifiers. This is intentionally over-inclusive —
false positives are acceptable because the location engine validates
cross-file suggestions against actual target file content.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

import tree_sitter_languages as tsl

logger = logging.getLogger("next-edit-server.project_index.queries")


class SymbolKind(str, Enum):
    DEFINITION = "definition"
    REFERENCE = "reference"


@dataclass(frozen=True, slots=True)
class ExtractedSymbol:
    """A single symbol occurrence extracted from a file."""
    name: str
    line: int       # 0-based
    column: int     # 0-based
    kind: SymbolKind


# Tree-sitter query patterns for definition nodes per language.
# These queries capture the *name* node within definitions.
_DEFINITION_QUERIES: dict[str, str] = {
    "python": """
        (function_definition name: (identifier) @def)
        (class_definition name: (identifier) @def)
    """,
    "javascript": """
        (function_declaration name: (identifier) @def)
        (class_declaration name: (identifier) @def)
        (variable_declarator name: (identifier) @def)
        (method_definition name: (property_identifier) @def)
    """,
    "typescript": """
        (function_declaration name: (identifier) @def)
        (class_declaration name: (identifier) @def)
        (variable_declarator name: (identifier) @def)
        (method_definition name: (property_identifier) @def)
        (interface_declaration name: (type_identifier) @def)
        (type_alias_declaration name: (type_identifier) @def)
    """,
    "go": """
        (function_declaration name: (identifier) @def)
        (method_declaration name: (field_identifier) @def)
        (type_declaration (type_spec name: (type_identifier) @def))
    """,
    "rust": """
        (function_item name: (identifier) @def)
        (struct_item name: (type_identifier) @def)
        (enum_item name: (type_identifier) @def)
        (trait_item name: (type_identifier) @def)
        (impl_item type: (type_identifier) @def)
    """,
    "java": """
        (class_declaration name: (identifier) @def)
        (method_declaration name: (identifier) @def)
        (interface_declaration name: (identifier) @def)
    """,
    "c": """
        (function_definition declarator: (function_declarator declarator: (identifier) @def))
        (declaration declarator: (function_declarator declarator: (identifier) @def))
    """,
    "cpp": """
        (function_definition declarator: (function_declarator declarator: (identifier) @def))
        (class_specifier name: (type_identifier) @def)
    """,
    "ruby": """
        (method name: (identifier) @def)
        (class name: (constant) @def)
        (module name: (constant) @def)
    """,
}

# Minimum identifier length to index (filter noise like single-char vars)
_MIN_IDENTIFIER_LENGTH = 2

# Common keywords/builtins to skip (language-agnostic)
_SKIP_IDENTIFIERS: frozenset[str] = frozenset({
    # Python builtins
    "self", "cls", "None", "True", "False",
    "print", "len", "range", "str", "int", "float", "bool", "list", "dict",
    "set", "tuple", "type", "super", "object", "property",
    "if", "else", "elif", "for", "while", "return", "import", "from",
    "def", "class", "try", "except", "finally", "with", "as", "pass",
    "break", "continue", "yield", "raise", "and", "or", "not", "in", "is",
    # JS/TS common
    "this", "undefined", "null", "console", "require", "module", "exports",
    "var", "let", "const", "function", "async", "await",
    "if", "else", "for", "while", "return", "import", "export",
    # Go common
    "nil", "err", "fmt", "func", "var", "const", "type", "struct",
    "interface", "package", "import", "return", "if", "else", "for",
})


def extract_symbols(source_bytes: bytes, language: str) -> list[ExtractedSymbol]:
    """Extract all symbols from a source file.

    Parameters:
        source_bytes: UTF-8 encoded file content.
        language: tree-sitter language name (e.g., "python", "typescript").

    Returns:
        List of extracted symbols (definitions + references).
    """
    try:
        lang = tsl.get_language(language)
        parser = tsl.get_parser(language)
    except Exception:
        # tree-sitter/tree-sitter-languages version incompatibility or
        # unsupported language — fall back to regex extraction
        logger.debug("tree-sitter unavailable for %s, using regex fallback", language)
        return _extract_symbols_regex(source_bytes)

    tree = parser.parse(source_bytes)
    symbols: list[ExtractedSymbol] = []
    definition_positions: set[tuple[int, int]] = set()  # (line, column)

    # Step 1: Extract definitions using language-specific queries
    def_query_str = _DEFINITION_QUERIES.get(language)
    if def_query_str:
        try:
            def_query = lang.query(def_query_str)
            captures = def_query.captures(tree.root_node)
            for node, _capture_name in captures:
                name = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
                if _should_index(name):
                    line = node.start_point[0]
                    col = node.start_point[1]
                    definition_positions.add((line, col))
                    symbols.append(ExtractedSymbol(
                        name=name,
                        line=line,
                        column=col,
                        kind=SymbolKind.DEFINITION,
                    ))
        except Exception:
            logger.debug("Definition query failed for %s", language)

    # Step 2: Extract all identifiers as references
    try:
        ref_query = lang.query("(identifier) @ref")
        captures = ref_query.captures(tree.root_node)
        for node, _capture_name in captures:
            name = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            if not _should_index(name):
                continue
            line = node.start_point[0]
            col = node.start_point[1]
            # Skip if already recorded as a definition at this exact position
            if (line, col) in definition_positions:
                continue
            symbols.append(ExtractedSymbol(
                name=name,
                line=line,
                column=col,
                kind=SymbolKind.REFERENCE,
            ))
    except Exception:
        logger.debug("Reference query failed for %s", language)

    # If tree-sitter produced nothing (possibly due to API issues), try regex
    if not symbols:
        return _extract_symbols_regex(source_bytes)

    return symbols


def extract_imports(source_bytes: bytes, language: str) -> set[str]:
    """Extract imported module base names from a source file.

    Returns a set of module base names (the last segment of the module path).
    Used by the cross-file prediction engine to filter results to files that
    actually import the source module.

    Examples (Python):
        `import api`            → {"api"}
        `from api import hello` → {"api"}
        `from os.path import join` → {"path"}
        `import api, utils`     → {"api", "utils"}

    Examples (JS/TS):
        `import { hello } from './api'` → {"api"}
        `import api from 'api'`         → {"api"}
        `const api = require('./api')`  → {"api"}
    """
    import re

    try:
        source_text = source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return set()

    modules: set[str] = set()

    if language in ("python",):
        # `import foo` / `import foo, bar` / `import foo.bar`
        for m in re.finditer(r"^import\s+(.+)$", source_text, re.MULTILINE):
            for part in m.group(1).split(","):
                mod = part.strip().split()[0]  # handle `import foo as f`
                base = mod.rsplit(".", 1)[-1]   # last segment
                if base:
                    modules.add(base)

        # `from foo import ...` / `from foo.bar import ...`
        for m in re.finditer(r"^from\s+([\w.]+)\s+import\b", source_text, re.MULTILINE):
            mod = m.group(1)
            base = mod.rsplit(".", 1)[-1]
            if base:
                modules.add(base)

    elif language in ("javascript", "typescript"):
        # `import ... from 'module'` / `import ... from "./module"`
        for m in re.finditer(r"""(?:import|from)\s+.*?['"]([^'"]+)['"]""", source_text):
            path = m.group(1)
            # Extract base name: ./api → api, ../utils/helper → helper
            base = path.rsplit("/", 1)[-1]
            # Strip extension if present
            for ext in (".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"):
                if base.endswith(ext):
                    base = base[: -len(ext)]
                    break
            if base:
                modules.add(base)

        # `require('./module')` / `require('module')`
        for m in re.finditer(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""", source_text):
            path = m.group(1)
            base = path.rsplit("/", 1)[-1]
            for ext in (".js", ".ts", ".jsx", ".tsx"):
                if base.endswith(ext):
                    base = base[: -len(ext)]
                    break
            if base:
                modules.add(base)

    elif language in ("go",):
        # `import "path/to/pkg"` / `import alias "path/to/pkg"`
        for m in re.finditer(r"""import\s+(?:\w+\s+)?["']([^"']+)["']""", source_text):
            path = m.group(1)
            base = path.rsplit("/", 1)[-1]
            if base:
                modules.add(base)

    elif language in ("java", "kotlin"):
        # `import com.example.Foo;`
        for m in re.finditer(r"^import\s+([\w.]+)\s*;", source_text, re.MULTILINE):
            mod = m.group(1)
            base = mod.rsplit(".", 1)[-1]
            if base:
                modules.add(base)

    elif language in ("rust",):
        # `use std::collections::HashMap;`
        for m in re.finditer(r"^use\s+([\w:]+)", source_text, re.MULTILINE):
            mod = m.group(1)
            base = mod.rsplit("::", 1)[-1]
            if base:
                modules.add(base)

    return modules


def _should_index(name: str) -> bool:
    """Check if an identifier is worth indexing."""
    if len(name) < _MIN_IDENTIFIER_LENGTH:
        return False
    if name in _SKIP_IDENTIFIERS:
        return False
    # Skip all-uppercase short names (likely constants like OK, ID)
    if len(name) <= 3 and name.isupper():
        return False
    # Skip dunder methods
    if name.startswith("__") and name.endswith("__"):
        return False
    return True


def _extract_symbols_regex(source_bytes: bytes) -> list[ExtractedSymbol]:
    """Fallback: extract identifiers using regex when tree-sitter is unavailable.

    This produces only REFERENCE entries (no definition/reference distinction)
    but ensures the index has data even when tree-sitter fails.
    """
    import re

    try:
        source_text = source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return []

    pattern = re.compile(r"\b([a-zA-Z_]\w*)\b")
    symbols: list[ExtractedSymbol] = []
    seen: set[tuple[str, int]] = set()  # (name, line) dedup

    for line_num, line in enumerate(source_text.splitlines()):
        for match in pattern.finditer(line):
            name = match.group(1)
            if not _should_index(name):
                continue
            key = (name, line_num)
            if key in seen:
                continue
            seen.add(key)
            symbols.append(ExtractedSymbol(
                name=name,
                line=line_num,
                column=match.start(),
                kind=SymbolKind.REFERENCE,
            ))

    return symbols
