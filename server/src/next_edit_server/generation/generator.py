"""Generation Module: produces NES diff edits using the LLM backend.

Receives a target location and context from the Location Module,
builds the prompt, calls the inference backend, and parses the output
into structured diff data ready for the editor.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from ..inference.backend import InferenceBackend, DummyBackend
from ..location.engine import LocationPrediction, RuleType
from .prompt import (
    build_generation_prompt,
    build_rename_prompt,
    parse_nes_diff,
)

logger = logging.getLogger("next-edit-server.generation")


@dataclass
class GenerationResult:
    """The output of the generation module."""
    suggestion_id: str
    uri: str
    base_version: int
    location_line: int  # 0-based
    diff_text: str      # Raw NES diff string
    description: str
    deleted_lines: list[dict[str, Any]]  # [{"num": int, "text": str}]
    added_lines: list[dict[str, Any]]    # [{"num": int, "text": str}]


class Generator:
    """Generates code edits using the inference backend."""

    def __init__(self, backend: InferenceBackend) -> None:
        self._backend = backend

    def generate(
        self,
        prediction: LocationPrediction,
        source_code: str,
        uri: str,
        version: int,
        edit_history: list[dict[str, Any]],
    ) -> GenerationResult | None:
        """Generate a code edit based on the location prediction.

        Parameters:
            prediction: The location prediction from the Location Module.
            source_code: Current full file content.
            uri: Document URI.
            version: Current document version (used to anchor the suggestion).
            edit_history: Recent edit history as list of {"file": str, "diff": str}.

        Returns:
            A GenerationResult, or None if generation fails.
        """
        # For rename predictions with DummyBackend, set context
        if isinstance(self._backend, DummyBackend) and prediction.rule == RuleType.RENAME:
            self._backend.set_context({
                "old_name": prediction.context.get("old_name", ""),
                "new_name": prediction.context.get("new_name", ""),
                "target_line": prediction.line,
                "line_text": prediction.text,
            })

        # Build prompt based on rule type
        if prediction.rule == RuleType.RENAME:
            prompt = build_rename_prompt(
                current_code=source_code,
                target_line=prediction.line,
                old_name=prediction.context.get("old_name", ""),
                new_name=prediction.context.get("new_name", ""),
            )
        else:
            prompt = build_generation_prompt(
                current_code=source_code,
                edit_history=edit_history,
                target_location=prediction.line,
            )

        # Call the inference backend
        try:
            raw_output = self._backend.generate(prompt, max_tokens=256)
        except Exception:
            logger.exception("Inference failed")
            return None

        if not raw_output.strip():
            logger.warning("Empty generation output")
            return None

        # Parse the NES diff output
        deleted, added = parse_nes_diff(raw_output)
        if not deleted and not added:
            logger.warning("Could not parse NES diff from output: %s", raw_output[:200])
            return None

        # Build description
        description = self._build_description(prediction)

        return GenerationResult(
            suggestion_id=f"suggest-{uuid.uuid4().hex[:8]}",
            uri=uri,
            base_version=version,
            location_line=prediction.line,
            diff_text=raw_output,
            description=description,
            deleted_lines=deleted,
            added_lines=added,
        )

    @staticmethod
    def _build_description(prediction: LocationPrediction) -> str:
        """Build a human-readable description of the suggested edit."""
        if prediction.rule == RuleType.RENAME:
            old = prediction.context.get("old_name", "?")
            new = prediction.context.get("new_name", "?")
            remaining = prediction.context.get("remaining_refs", 0)
            suffix = f" ({remaining} more)" if remaining > 1 else ""
            return f"Rename `{old}` → `{new}`{suffix}"

        if prediction.rule == RuleType.SIGNATURE:
            fn = prediction.context.get("function_name", "?")
            remaining = prediction.context.get("remaining_sites", 0)
            suffix = f" ({remaining} more)" if remaining > 1 else ""
            return f"Update call to `{fn}` to match new signature{suffix}"

        if prediction.rule == RuleType.PATTERN:
            ident = prediction.context.get("new_identifier", "?")
            method = prediction.context.get("target_method", "?")
            return f"Add `{ident}` handling in `{method}`"

        return "Suggested edit"
