"""NES diff prompt template for the Generation Module.

Builds the prompt that asks the LLM to generate code edits in NES diff format,
given the current code context, edit history, and target location.
"""

from __future__ import annotations

from typing import Any


SYSTEM_PROMPT = """\
You are a code edit prediction model. Given the current code state \
and recent edit history, generate the next edit in NES diff format.

NES diff format rules:
- Every line has an absolute line number prefix
- Deleted lines: {line_num}-| {content}
- Added lines: {line_num}+| {content}
- Unchanged context lines: {line_num} | {content}
- Only output the changed region with 2 lines of context above and below
- Output ONLY the NES diff block, no explanation"""


def build_generation_prompt(
    current_code: str,
    edit_history: list[dict[str, Any]],
    target_location: int,
    context_window: int = 5,
) -> str:
    """Build the full prompt for the generation model.

    Parameters:
        current_code: The full file content after the most recent edit.
        edit_history: List of recent edits, each with keys 'file' and 'diff'
                      (NES diff format strings).
        target_location: 0-based line number predicted by the Location Module.
        context_window: Number of lines above and below the target to include.

    Returns:
        The formatted prompt string.
    """
    lines = current_code.splitlines()

    # Extract code context around the target location (1-based line numbers for display)
    start = max(0, target_location - context_window)
    end = min(len(lines), target_location + context_window + 1)

    code_context = "\n".join(
        f"{i + 1} | {lines[i]}" for i in range(start, end)
    )

    # Format edit history (most recent 3)
    history_entries = edit_history[-3:]
    if history_entries:
        history_text = "\n---\n".join(
            f"Edit {i + 1} ({entry.get('file', 'unknown')}):\n{entry['diff']}"
            for i, entry in enumerate(history_entries)
        )
    else:
        history_text = "(no prior edits)"

    return f"""{SYSTEM_PROMPT}

<edit_history>
{history_text}
</edit_history>

<current_code>
{code_context}
</current_code>

<next_edit>
"""


def build_rename_prompt(
    current_code: str,
    target_line: int,
    old_name: str,
    new_name: str,
    context_window: int = 3,
) -> str:
    """Build a specialized prompt for rename propagation.

    For rename cases, the edit is highly predictable and we can use
    a more constrained prompt that produces faster, more accurate output.
    """
    lines = current_code.splitlines()
    start = max(0, target_line - context_window)
    end = min(len(lines), target_line + context_window + 1)

    code_context = "\n".join(
        f"{i + 1} | {lines[i]}" for i in range(start, end)
    )

    return f"""\
Replace all occurrences of `{old_name}` with `{new_name}` in the following code region.
Output ONLY the NES diff for the changed lines.

NES diff format: {{line_num}}-| {{old_content}} and {{line_num}}+| {{new_content}}

<code>
{code_context}
</code>

<next_edit>
"""


def parse_nes_diff(diff_text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse NES diff output into deleted and added line lists.

    Returns:
        (deleted_lines, added_lines) where each item is
        {"num": int, "text": str}.
    """
    deleted: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []

    for line in diff_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue

        # Parse: {num}-| {text} or {num}+| {text} or {num} | {text}
        if "-|" in line:
            parts = line.split("-|", 1)
            try:
                num = int(parts[0].strip())
                text = parts[1].strip() if len(parts) > 1 else ""
                deleted.append({"num": num, "text": text})
            except ValueError:
                continue
        elif "+|" in line:
            parts = line.split("+|", 1)
            try:
                num = int(parts[0].strip())
                text = parts[1].strip() if len(parts) > 1 else ""
                added.append({"num": num, "text": text})
            except ValueError:
                continue

    return deleted, added
