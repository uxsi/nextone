"""Location rule engine: dispatches edits to individual rules and aggregates results.

Runs all enabled rules against each edit event, filters by confidence,
and returns the highest-confidence prediction of the next edit location.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..edit_history import EditRecord
from .rename import detect_rename, find_references, RenameDetection
from .signature import detect_signature_change, find_call_sites, SignatureChange
from .pattern import detect_pattern, find_methods_missing_reference, PatternDetection

logger = logging.getLogger("next-edit-server.location")


class RuleType(str, Enum):
    RENAME = "rename"
    SIGNATURE = "signature"
    PATTERN = "pattern"


@dataclass(frozen=True, slots=True)
class LocationPrediction:
    """A predicted next-edit location with confidence and provenance."""
    line: int             # 0-based target line
    column: int           # 0-based target column
    rule: RuleType        # Which rule produced this prediction
    confidence: float     # 0.0 to 1.0
    context: dict[str, Any]  # Rule-specific context (e.g., old_name, new_name for rename)
    text: str             # The line content at the predicted location


class LocationEngine:
    """Runs location rules and returns the best prediction."""

    def __init__(self, confidence_threshold: float = 0.5) -> None:
        self._threshold = confidence_threshold

    def predict(
        self,
        edit: EditRecord,
        source_code: str,
        language: str,
        edit_history: list[EditRecord] | None = None,
    ) -> LocationPrediction | None:
        """Run all rules and return the highest-confidence prediction above threshold.

        Parameters:
            edit: The most recent edit event.
            source_code: Current full file content (after the edit).
            language: Language identifier.
            edit_history: Full edit history window (oldest first). Used to detect
                          renames that span multiple keystrokes (delete old name +
                          type new name character by character).

        Returns:
            The best prediction, or None if no rule fires above threshold.
        """
        predictions: list[LocationPrediction] = []

        # Rule 1: Rename propagation (single-edit detection)
        rename_pred = self._try_rename(edit, source_code, language)
        if rename_pred:
            predictions.append(rename_pred)

        # Rule 1b: Rename propagation (multi-edit history detection)
        # Real users often delete the old name then type the new name character
        # by character, producing multiple didChange events. We compare the
        # first edit's old_lines against the current document state to detect
        # renames that span the entire history window.
        if not rename_pred and edit_history and len(edit_history) >= 2:
            composite_pred = self._try_rename_from_history(
                edit_history, source_code, language
            )
            if composite_pred:
                predictions.append(composite_pred)

        # Rule 2: Signature change propagation
        sig_pred = self._try_signature(edit, source_code, language)
        if sig_pred:
            predictions.append(sig_pred)

        # Rule 3: Repetitive pattern detection
        pattern_pred = self._try_pattern(edit, source_code, language)
        if pattern_pred:
            predictions.append(pattern_pred)

        if not predictions:
            return None

        # Return the highest-confidence prediction above threshold
        best = max(predictions, key=lambda p: p.confidence)
        if best.confidence < self._threshold:
            logger.debug(
                "Best prediction (%.2f) below threshold (%.2f), suppressing",
                best.confidence,
                self._threshold,
            )
            return None

        logger.info(
            "Prediction: line %d, rule=%s, confidence=%.2f",
            best.line,
            best.rule.value,
            best.confidence,
        )
        return best

    # -----------------------------------------------------------------------
    # Individual rule runners
    # -----------------------------------------------------------------------

    def _try_rename(
        self, edit: EditRecord, source_code: str, language: str
    ) -> LocationPrediction | None:
        detection = detect_rename(edit)
        if detection is None:
            return None

        refs = find_references(
            source_code,
            language,
            detection.old_name,
            exclude_line=detection.line,
        )
        if not refs:
            return None

        # Return the first (nearest) reference
        ref = refs[0]
        return LocationPrediction(
            line=ref.line,
            column=ref.column,
            rule=RuleType.RENAME,
            confidence=0.9,  # High confidence: exact AST match
            context={
                "old_name": detection.old_name,
                "new_name": detection.new_name,
                "remaining_refs": len(refs),
            },
            text=ref.text,
        )

    def _try_signature(
        self, edit: EditRecord, source_code: str, language: str
    ) -> LocationPrediction | None:
        detection = detect_signature_change(edit)
        if detection is None:
            return None

        call_sites = find_call_sites(
            source_code,
            language,
            detection.function_name,
            exclude_line=detection.line,
        )
        if not call_sites:
            return None

        site = call_sites[0]
        return LocationPrediction(
            line=site.line,
            column=site.column,
            rule=RuleType.SIGNATURE,
            confidence=0.8,  # High but slightly lower than rename (param changes are more nuanced)
            context={
                "function_name": detection.function_name,
                "remaining_sites": len(call_sites),
            },
            text=site.text,
        )

    def _try_pattern(
        self, edit: EditRecord, source_code: str, language: str
    ) -> LocationPrediction | None:
        detection = detect_pattern(edit, source_code)
        if detection is None:
            return None

        methods = find_methods_missing_reference(
            source_code,
            language,
            detection.new_identifier,
            edited_line=edit.start_line,
        )
        if not methods:
            return None

        method = methods[0]
        return LocationPrediction(
            line=method.line,
            column=0,
            rule=RuleType.PATTERN,
            confidence=0.6,  # Lower confidence: heuristic-based
            context={
                "new_identifier": detection.new_identifier,
                "class_name": detection.class_name,
                "target_method": method.method_name,
                "remaining_methods": len(methods),
            },
            text=method.text,
        )

    def _try_rename_from_history(
        self,
        edit_history: list[EditRecord],
        source_code: str,
        language: str,
    ) -> LocationPrediction | None:
        """Detect renames that span multiple edits in the history window.

        Real editing pattern: user selects "hello", deletes it (text=''),
        then types "good" one character at a time. This produces N+1 edits:
          edit 1: old=["def hello(name):"] new=["def (name):"]    (delete)
          edit 2: old=["def (name):"]      new=["def g(name):"]   (type 'g')
          edit 3: old=["def g(name):"]     new=["def go(name):"]  (type 'o')
          ...

        We compare the first edit's old_lines against the last edit's new_lines
        to synthesize a single "composite" rename detection.
        """
        # All edits must be on the same line
        first = edit_history[0]
        last = edit_history[-1]
        if first.start_line != last.start_line:
            return None
        if len(first.old_lines) != 1 or len(last.new_lines) != 1:
            return None

        # Synthesize a composite edit
        composite = EditRecord(
            uri=last.uri,
            version=last.version,
            timestamp=last.timestamp,
            old_lines=first.old_lines,
            new_lines=last.new_lines,
            start_line=first.start_line,
            end_line=first.end_line,
        )

        detection = detect_rename(composite)
        if detection is None:
            return None

        refs = find_references(
            source_code, language, detection.old_name, exclude_line=detection.line,
        )
        if not refs:
            return None

        ref = refs[0]
        logger.info(
            "Composite rename detected across %d edits: %s → %s",
            len(edit_history), detection.old_name, detection.new_name,
        )
        return LocationPrediction(
            line=ref.line,
            column=ref.column,
            rule=RuleType.RENAME,
            confidence=0.85,  # Slightly lower than single-edit rename
            context={
                "old_name": detection.old_name,
                "new_name": detection.new_name,
                "remaining_refs": len(refs),
            },
            text=ref.text,
        )
