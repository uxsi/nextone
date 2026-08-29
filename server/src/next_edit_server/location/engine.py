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

# Conditional import: ProjectIndex is optional (not available in Phase 1)
try:
    from ..project_index import ProjectIndex
except ImportError:
    ProjectIndex = None  # type: ignore[misc,assignment]


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
    target_uri: str | None = None  # None = same file, set = cross-file target


class LocationEngine:
    """Runs location rules and returns the best prediction."""

    def __init__(self, confidence_threshold: float = 0.5) -> None:
        self._threshold = confidence_threshold
        self._project_index: ProjectIndex | None = None

    def set_project_index(self, index: Any) -> None:
        """Set the project index for cross-file prediction."""
        self._project_index = index

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

        All edits in the history window must target the same uri and line, with
        single-line changes only. This prevents unrelated edits on different
        files or lines from being incorrectly merged into a composite rename.
        """
        first = edit_history[0]
        last = edit_history[-1]

        # All edits must target the same uri and line, with single-line changes.
        # Note: the history window has a fixed small size (e.g. 3). When the user
        # types more characters than the window size, intermediate edits get evicted,
        # so we cannot require strict consecutive evolution (edit[i].new_lines ==
        # edit[i+1].old_lines). Instead we verify structural consistency: same file,
        # same line, all single-line.
        for edit in edit_history:
            if edit.uri != first.uri:
                return None
            if edit.start_line != first.start_line:
                return None
            if len(edit.old_lines) != 1 or len(edit.new_lines) != 1:
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

    # -----------------------------------------------------------------------
    # Cross-file prediction
    # -----------------------------------------------------------------------

    def predict_cross_file(
        self,
        edit: EditRecord,
        source_code: str,
        language: str,
        edit_history: list[EditRecord] | None = None,
    ) -> list[LocationPrediction]:
        """Cross-file predictions. Returns ALL predicted locations across other files.

        Called AFTER same-file predict() returns None. Returns predictions
        sorted by confidence. The pipeline picks the top-1 to suggest.

        Parameters:
            edit: The most recent edit event.
            source_code: Current full file content (after the edit).
            language: Language identifier.
            edit_history: Full edit history window (oldest first).

        Returns:
            List of predictions with target_uri set, sorted by confidence.
            Empty list if project index is not available or not ready.
        """
        if self._project_index is None or not self._project_index.is_ready():
            return []

        predictions: list[LocationPrediction] = []

        # Try cross-file rename (single-edit detection)
        rename_preds = self._try_cross_file_rename(edit)
        predictions.extend(rename_preds)

        # Try cross-file rename (multi-edit history detection)
        if not rename_preds and edit_history and len(edit_history) >= 2:
            composite_preds = self._try_cross_file_rename_from_history(edit_history)
            predictions.extend(composite_preds)

        # Try cross-file signature change
        sig_preds = self._try_cross_file_signature(edit)
        predictions.extend(sig_preds)

        # Filter by threshold and sort by confidence
        predictions = [p for p in predictions if p.confidence >= self._threshold]
        predictions.sort(key=lambda p: p.confidence, reverse=True)

        if predictions:
            logger.info(
                "Cross-file predictions: %d candidates (best: %s line %d confidence=%.2f)",
                len(predictions),
                predictions[0].target_uri,
                predictions[0].line,
                predictions[0].confidence,
            )

        return predictions

    def _try_cross_file_rename(
        self, edit: EditRecord,
    ) -> list[LocationPrediction]:
        """Find cross-file references to a renamed symbol."""
        detection = detect_rename(edit)
        if detection is None:
            return []

        return self._cross_file_rename_from_detection(detection, edit.uri)

    def _try_cross_file_rename_from_history(
        self, edit_history: list[EditRecord],
    ) -> list[LocationPrediction]:
        """Cross-file rename from composite history (multi-keystroke rename)."""
        first = edit_history[0]
        last = edit_history[-1]

        for e in edit_history:
            if e.uri != first.uri or e.start_line != first.start_line:
                return []
            if len(e.old_lines) != 1 or len(e.new_lines) != 1:
                return []

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
            return []

        return self._cross_file_rename_from_detection(detection, first.uri)

    def _cross_file_rename_from_detection(
        self, detection: RenameDetection, source_uri: str,
    ) -> list[LocationPrediction]:
        """Query project index for cross-file references of a rename detection.

        Filters results to only files that import the source module.
        """
        assert self._project_index is not None

        refs = self._project_index.query_references(
            name=detection.old_name,
            exclude_uri=source_uri,
        )

        if not refs:
            return []

        # Extract source module name from URI: file:///path/to/api.py → "api"
        source_module = self._module_name_from_uri(source_uri)

        # Filter: only keep refs in files that import the source module
        if source_module:
            refs = [
                ref for ref in refs
                if self._file_imports_module(ref.uri, source_module)
            ]

        if not refs:
            return []

        # Group by file, take first reference per file
        file_refs: dict[str, Any] = {}
        file_ref_counts: dict[str, int] = {}
        for ref in refs:
            file_ref_counts[ref.uri] = file_ref_counts.get(ref.uri, 0) + 1
            if ref.uri not in file_refs:
                file_refs[ref.uri] = ref

        # Create predictions for each file (max 5 files)
        predictions: list[LocationPrediction] = []
        for uri, ref in list(file_refs.items())[:5]:
            predictions.append(LocationPrediction(
                line=ref.line,
                column=ref.column,
                rule=RuleType.RENAME,
                confidence=0.75,  # Lower than same-file (0.9)
                context={
                    "old_name": detection.old_name,
                    "new_name": detection.new_name,
                    "remaining_refs": file_ref_counts[uri],
                    "cross_file": True,
                },
                text=ref.context,
                target_uri=uri,
            ))

        return predictions

    def _try_cross_file_signature(
        self, edit: EditRecord,
    ) -> list[LocationPrediction]:
        """Find cross-file call sites for a function whose signature changed.

        Filters results to only files that import the source module.
        """
        detection = detect_signature_change(edit)
        if detection is None:
            return []

        assert self._project_index is not None

        refs = self._project_index.query_references(
            name=detection.function_name,
            exclude_uri=edit.uri,
        )

        if not refs:
            return []

        # Filter by import relationship
        source_module = self._module_name_from_uri(edit.uri)
        if source_module:
            refs = [
                ref for ref in refs
                if self._file_imports_module(ref.uri, source_module)
            ]

        if not refs:
            return []

        # Group by file
        file_refs: dict[str, Any] = {}
        file_ref_counts: dict[str, int] = {}
        for ref in refs:
            file_ref_counts[ref.uri] = file_ref_counts.get(ref.uri, 0) + 1
            if ref.uri not in file_refs:
                file_refs[ref.uri] = ref

        predictions: list[LocationPrediction] = []
        for uri, ref in list(file_refs.items())[:5]:
            predictions.append(LocationPrediction(
                line=ref.line,
                column=ref.column,
                rule=RuleType.SIGNATURE,
                confidence=0.65,  # Lower than same-file (0.8)
                context={
                    "function_name": detection.function_name,
                    "remaining_sites": file_ref_counts[uri],
                    "cross_file": True,
                },
                text=ref.context,
                target_uri=uri,
            ))

        return predictions

    # -----------------------------------------------------------------------
    # Import relation helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _module_name_from_uri(uri: str) -> str | None:
        """Extract module base name from a file URI.

        file:///path/to/api.py → "api"
        file:///path/to/utils.js → "utils"
        """
        import os
        path = uri[7:] if uri.startswith("file://") else uri
        basename = os.path.basename(path)
        name, _ = os.path.splitext(basename)
        return name if name else None

    def _file_imports_module(self, file_uri: str, module_name: str) -> bool:
        """Check if a file imports a given module (by base name match)."""
        assert self._project_index is not None
        imports = self._project_index.get_file_imports(file_uri)
        return module_name in imports
