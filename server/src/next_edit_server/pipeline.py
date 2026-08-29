"""Pipeline: orchestrates the end-to-end flow from edit event to suggestion.

    didChange → edit_history → location → generation → suggest

Handles debouncing, stale detection, and suggestion lifecycle.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Callable

from .document_store import DocumentStore, DocumentState
from .edit_history import EditHistory, EditRecord, build_edit_record
from .location.engine import LocationEngine, LocationPrediction
from .generation.generator import Generator, GenerationResult
from .inference.backend import InferenceBackend, create_backend
from .file_reader import FileReader
from .project_index import ProjectIndex
from .protocol import (
    Methods,
    SuggestParams,
    CancelSuggestionParams,
    CancelReason,
    StatusParams,
    ServerState,
    Position,
    LineDiff,
)

logger = logging.getLogger("next-edit-server.pipeline")


class MetricsCollector:
    """Simple metrics collector that logs events as JSON Lines."""

    def __init__(self) -> None:
        self._logger = logging.getLogger("next-edit-server.metrics")
        self.trigger_count = 0
        self.accept_count = 0
        self.reject_count = 0
        self.stale_count = 0
        self.cancel_count = 0

    def record(self, event_type: str, suggestion_id: str = "", latency_ms: int = 0) -> None:
        if event_type == "trigger":
            self.trigger_count += 1
        elif event_type == "accept":
            self.accept_count += 1
        elif event_type == "reject":
            self.reject_count += 1
        elif event_type == "stale":
            self.stale_count += 1
        elif event_type == "cancel":
            self.cancel_count += 1

        self._logger.info(
            '{"event":"%s","id":"%s","latency_ms":%d,"ts":%d}',
            event_type,
            suggestion_id,
            latency_ms,
            int(time.time() * 1000),
        )

    @property
    def acceptance_rate(self) -> float:
        total = self.accept_count + self.reject_count
        return self.accept_count / total if total > 0 else 0.0


class Pipeline:
    """Connects all modules and manages the suggestion lifecycle."""

    def __init__(
        self,
        document_store: DocumentStore,
        send_notification: Callable[[str, Any], None],
        model_path: str | None = None,
        debounce_ms: int = 300,
    ) -> None:
        self._doc_store = document_store
        self._send = send_notification

        # Modules
        self._edit_history = EditHistory(window_size=3)
        self._location_engine = LocationEngine(confidence_threshold=0.5)
        self._backend = create_backend(model_path)
        self._generator = Generator(self._backend)
        self._metrics = MetricsCollector()

        # Cross-file support
        self._project_index: ProjectIndex | None = None
        self._file_reader = FileReader()

        # Debounce state
        self._debounce_ms = debounce_ms
        self._debounce_timer: threading.Timer | None = None
        self._debounce_lock = threading.Lock()

        # Current suggestion state
        self._current_suggestion_id: str | None = None
        self._current_suggestion_version: int = 0

    @property
    def metrics(self) -> MetricsCollector:
        return self._metrics

    def initialize(self) -> None:
        """Load the inference backend model."""
        self._send(
            Methods.STATUS,
            StatusParams(state=ServerState.LOADING_MODEL, message="Loading model..."),
        )
        self._backend.load()
        self._send(
            Methods.STATUS,
            StatusParams(state=ServerState.READY, message="Ready"),
        )

    def start_project_index(self, workspace_root: str) -> None:
        """Start the background project index for cross-file prediction.

        Called once the workspace root is known (from initialize params,
        CLI arg, or inferred from the first didOpen URI).
        """
        if self._project_index is not None:
            logger.info("Project index already started, skipping")
            return

        self._project_index = ProjectIndex(
            workspace_root=workspace_root,
            on_ready=self._on_index_ready,
        )
        self._project_index.start()
        # Pass the index to the location engine
        self._location_engine.set_project_index(self._project_index)
        logger.info("Project index started for: %s", workspace_root)

    def _on_index_ready(self) -> None:
        """Callback from indexer when initial scan completes."""
        if self._project_index:
            logger.info(
                "Project index ready: %d files, %d symbols",
                self._project_index.file_count,
                self._project_index.symbol_count,
            )

    def on_file_saved(self, uri: str) -> None:
        """Called when a document is saved. Triggers re-indexing."""
        if self._project_index:
            self._project_index.on_file_saved(uri)

    def on_did_change(self, uri: str, version: int, old_lines: list[str], new_lines: list[str], start_line: int, end_line: int, timestamp: int) -> None:
        """Called when a document changes. Triggers debounced pipeline."""
        # Record the edit
        edit = build_edit_record(
            uri=uri,
            version=version,
            timestamp=timestamp,
            old_text_lines=old_lines,
            new_text_lines=new_lines,
            start_line=start_line,
            end_line=end_line,
        )
        self._edit_history.record(edit)
        logger.info(
            "Edit recorded: uri=%s v=%d old=%r new=%r start=%d",
            uri, version, old_lines, new_lines, start_line,
        )

        # Cancel any existing suggestion for this document
        self._cancel_current_suggestion(CancelReason.DOCUMENT_CHANGED)

        # Debounce: wait for user to stop typing
        self._schedule_pipeline(uri, version)

    def on_resolve(self, suggestion_id: str, accepted: bool) -> None:
        """Called when the user accepts or rejects a suggestion."""
        event = "accept" if accepted else "reject"
        self._metrics.record(event, suggestion_id)
        self._current_suggestion_id = None

    def on_close(self, uri: str) -> None:
        """Called when a document is closed."""
        self._edit_history.clear(uri)
        self._cancel_current_suggestion(CancelReason.DOCUMENT_CHANGED)

    # -----------------------------------------------------------------------
    # Pipeline execution
    # -----------------------------------------------------------------------

    def _schedule_pipeline(self, uri: str, version: int) -> None:
        """Schedule pipeline execution after debounce interval."""
        with self._debounce_lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()

            self._debounce_timer = threading.Timer(
                self._debounce_ms / 1000.0,
                self._run_pipeline,
                args=(uri, version),
            )
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _run_pipeline(self, uri: str, version: int) -> None:
        """Execute the full pipeline: location → generation → suggest."""
        start_time = time.monotonic()
        logger.info("Pipeline started: uri=%s version=%d", uri, version)

        # Check if the document version is still current
        if self._doc_store.is_version_stale(uri, version):
            logger.info("Pipeline aborted: version %d is stale", version)
            self._metrics.record("stale")
            return

        doc = self._doc_store.get(uri)
        if doc is None:
            logger.warning("Pipeline aborted: document %s not found", uri)
            return

        # Get the latest edit
        latest_edit = self._edit_history.latest(uri)
        if latest_edit is None:
            logger.warning("Pipeline aborted: no edit history for %s", uri)
            return

        logger.info(
            "Pipeline input: language=%s edit.old=%r edit.new=%r edit.start=%d",
            doc.language_id, latest_edit.old_lines, latest_edit.new_lines, latest_edit.start_line,
        )

        # Run Location Module (same-file first)
        self._send(
            Methods.STATUS,
            StatusParams(state=ServerState.INFERRING, message="Analyzing..."),
        )

        prediction = self._location_engine.predict(
            edit=latest_edit,
            source_code=doc.text,
            language=doc.language_id,
            edit_history=self._edit_history.get(uri),
        )

        if prediction is not None:
            # Same-file suggestion
            self._emit_same_file_suggestion(prediction, uri, version, doc.text, start_time)
            return

        # No same-file prediction — try cross-file
        cross_predictions = self._location_engine.predict_cross_file(
            edit=latest_edit,
            source_code=doc.text,
            language=doc.language_id,
            edit_history=self._edit_history.get(uri),
        )

        if cross_predictions:
            best = cross_predictions[0]
            self._emit_cross_file_suggestion(best, uri, version, start_time)
            return

        # Nothing to suggest
        logger.info("Pipeline: no prediction (same-file or cross-file)")
        self._send(
            Methods.STATUS,
            StatusParams(state=ServerState.READY),
        )

    def _emit_same_file_suggestion(
        self,
        prediction: LocationPrediction,
        uri: str,
        version: int,
        source_code: str,
        start_time: float,
    ) -> None:
        """Generate and send a same-file suggestion (existing Phase 1 logic)."""
        # Check version again before generation
        if self._doc_store.is_version_stale(uri, version):
            self._metrics.record("stale")
            self._send(Methods.STATUS, StatusParams(state=ServerState.READY))
            return

        # Run Generation Module
        history_diffs = [
            {"file": e.uri, "diff": e.nes_diff}
            for e in self._edit_history.get(uri)
        ]

        result = self._generator.generate(
            prediction=prediction,
            source_code=source_code,
            uri=uri,
            version=version,
            edit_history=history_diffs,
        )

        if result is None:
            self._send(Methods.STATUS, StatusParams(state=ServerState.READY))
            return

        # Final staleness check
        if self._doc_store.is_version_stale(uri, version):
            self._metrics.record("stale", result.suggestion_id)
            self._send(Methods.STATUS, StatusParams(state=ServerState.READY))
            return

        # Send suggestion to editor
        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        self._send(
            Methods.SUGGEST,
            SuggestParams(
                id=result.suggestion_id,
                uri=result.uri,
                base_uri=result.uri,
                base_version=result.base_version,
                location=Position(line=result.location_line, character=0),
                diff=result.diff_text,
                description=result.description,
                deleted_lines=[
                    LineDiff(num=d["num"], text=d["text"])
                    for d in result.deleted_lines
                ],
                added_lines=[
                    LineDiff(num=a["num"], text=a["text"])
                    for a in result.added_lines
                ],
            ),
        )

        self._current_suggestion_id = result.suggestion_id
        self._current_suggestion_version = version
        self._metrics.record("trigger", result.suggestion_id, elapsed_ms)

        self._send(Methods.STATUS, StatusParams(state=ServerState.READY))

    def _emit_cross_file_suggestion(
        self,
        prediction: LocationPrediction,
        source_uri: str,
        source_version: int,
        start_time: float,
    ) -> None:
        """Generate and send a cross-file suggestion."""
        target_uri = prediction.target_uri
        assert target_uri is not None

        # Read target file content: prefer DocumentStore (open in editor), fallback to disk
        target_doc = self._doc_store.get(target_uri)
        if target_doc:
            target_source = target_doc.text
            target_version = target_doc.version
        else:
            target_source = self._file_reader.read(target_uri)
            if target_source is None:
                logger.info("Cross-file target not readable: %s", target_uri)
                self._send(Methods.STATUS, StatusParams(state=ServerState.READY))
                return
            target_version = -1  # Not tracked by editor

        # Check source version hasn't gone stale during file read
        if self._doc_store.is_version_stale(source_uri, source_version):
            self._metrics.record("stale")
            self._send(Methods.STATUS, StatusParams(state=ServerState.READY))
            return

        # Run Generation Module on the target file
        history_diffs = [
            {"file": e.uri, "diff": e.nes_diff}
            for e in self._edit_history.get(source_uri)
        ]

        result = self._generator.generate(
            prediction=prediction,
            source_code=target_source,
            uri=target_uri,
            version=source_version,  # baseVersion references the source file
            edit_history=history_diffs,
        )

        if result is None:
            self._send(Methods.STATUS, StatusParams(state=ServerState.READY))
            return

        # Send suggestion — note uri (target) != base_uri (source)
        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        self._send(
            Methods.SUGGEST,
            SuggestParams(
                id=result.suggestion_id,
                uri=target_uri,
                base_uri=source_uri,
                base_version=source_version,
                location=Position(line=result.location_line, character=0),
                diff=result.diff_text,
                description=result.description,
                deleted_lines=[
                    LineDiff(num=d["num"], text=d["text"])
                    for d in result.deleted_lines
                ],
                added_lines=[
                    LineDiff(num=a["num"], text=a["text"])
                    for a in result.added_lines
                ],
            ),
        )

        self._current_suggestion_id = result.suggestion_id
        self._current_suggestion_version = source_version
        self._metrics.record("trigger", result.suggestion_id, elapsed_ms)

        self._send(Methods.STATUS, StatusParams(state=ServerState.READY))

    # -----------------------------------------------------------------------
    # Suggestion lifecycle
    # -----------------------------------------------------------------------

    def _cancel_current_suggestion(self, reason: CancelReason) -> None:
        """Cancel the currently active suggestion, if any."""
        if self._current_suggestion_id is None:
            return

        self._send(
            Methods.CANCEL_SUGGESTION,
            CancelSuggestionParams(
                id=self._current_suggestion_id,
                reason=reason,
            ),
        )
        self._metrics.record("cancel", self._current_suggestion_id)
        self._current_suggestion_id = None
