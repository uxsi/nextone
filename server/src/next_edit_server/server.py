"""JSON-RPC over stdio server for NextOne.

Reads JSON-RPC 2.0 messages from stdin using LSP base protocol framing
(Content-Length header), dispatches them to handlers, and writes responses
and notifications to stdout.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from io import BufferedReader, BufferedWriter
from typing import Any, Callable

from .protocol import (
    JsonRpcMessage,
    Methods,
    DidOpenParams,
    DidChangeParams,
    DidSaveParams,
    DidCloseParams,
    FullSyncParams,
    ResolveParams,
    TextChange,
    Range,
    Position,
    StatusParams,
    ServerState,
    make_notification,
)
from .document_store import DocumentStore
from .pipeline import Pipeline

logger = logging.getLogger("next-edit-server")


class NextEditServer:
    """The core server that processes JSON-RPC messages and coordinates modules."""

    def __init__(self, model_path: str | None = None) -> None:
        self.document_store = DocumentStore()
        self._writer_lock = threading.Lock()
        self._stdin: BufferedReader | None = None
        self._stdout: BufferedWriter | None = None
        self._running = False
        self._model_path = model_path

        # Pipeline (initialized after stdio is ready)
        self._pipeline: Pipeline | None = None

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def run(self, stdin: BufferedReader | None = None, stdout: BufferedWriter | None = None) -> None:
        """Start the server main loop. Blocks until stdin is closed."""
        self._stdin = stdin or sys.stdin.buffer
        self._stdout = stdout or sys.stdout.buffer
        self._running = True

        # Create the pipeline but do NOT initialize yet.
        # pipeline.initialize() sends status notifications, which are
        # forbidden before the LSP initialize handshake completes.
        # It will be called from _handle_initialize() after the response.
        self._pipeline = Pipeline(
            document_store=self.document_store,
            send_notification=self.send_notification,
            model_path=self._model_path,
        )

        while self._running:
            msg = self._read_message()
            if msg is None:
                break
            self._dispatch(msg)

    def stop(self) -> None:
        """Signal the server to stop."""
        self._running = False

    def send_notification(self, method: str, params: Any) -> None:
        """Send a JSON-RPC notification to the editor."""
        payload = make_notification(method, params)
        self._write_message(payload)

    # -----------------------------------------------------------------------
    # Message dispatch
    # -----------------------------------------------------------------------

    def _dispatch(self, msg: JsonRpcMessage) -> None:
        """Route a parsed message to the appropriate handler."""

        logger.debug("RECV method=%s id=%s", msg.method, msg.id)

        # Handle standard LSP lifecycle messages that LanguageClient requires.
        # LanguageClient sends "initialize" (request) first, then "initialized"
        # (notification). The server must respond to "initialize" before the
        # client will send any further messages.
        if msg.method == "initialize" and msg.is_request:
            self._handle_initialize(msg)
            return
        if msg.method == "initialized":
            logger.info("Client initialized")
            return
        if msg.method == "shutdown" and msg.is_request:
            self._handle_shutdown(msg)
            return
        if msg.method == "exit":
            self._running = False
            return

        handlers: dict[str, Callable[[dict[str, Any]], None]] = {
            Methods.DID_OPEN: self._handle_did_open,
            Methods.DID_CHANGE: self._handle_did_change,
            Methods.DID_SAVE: self._handle_did_save,
            Methods.DID_CLOSE: self._handle_did_close,
            Methods.FULL_SYNC: self._handle_full_sync,
            Methods.RESOLVE: self._handle_resolve,
        }

        # Also handle standard LSP textDocument notifications that
        # LanguageClient sends automatically based on documentSelector.
        lsp_to_custom: dict[str, str] = {
            "textDocument/didOpen": Methods.DID_OPEN,
            "textDocument/didChange": Methods.DID_CHANGE,
            "textDocument/didSave": Methods.DID_SAVE,
            "textDocument/didClose": Methods.DID_CLOSE,
        }

        method = msg.method
        if method and method in lsp_to_custom:
            # Translate standard LSP params to our custom format
            params = self._translate_lsp_params(method, msg.params or {})
            try:
                handlers[lsp_to_custom[method]](params)
            except Exception:
                logger.exception("Error handling %s", method)
            return

        if method and method in handlers:
            try:
                handlers[method](msg.params or {})
            except Exception:
                logger.exception("Error handling %s", method)
        elif method:
            logger.debug("Unhandled method: %s", method)

    # -----------------------------------------------------------------------
    # LSP lifecycle handlers
    # -----------------------------------------------------------------------

    def _handle_initialize(self, msg: JsonRpcMessage) -> None:
        """Respond to LSP initialize request.

        LanguageClient blocks until it receives this response. We advertise
        textDocumentSync so the client sends didOpen/didChange/didSave/didClose.
        """
        result = {
            "capabilities": {
                "textDocumentSync": {
                    "openClose": True,
                    "change": 2,  # Incremental
                    "save": {"includeText": False},
                },
            },
        }
        self._write_message({
            "jsonrpc": "2.0",
            "id": msg.id,
            "result": result,
        })
        logger.info("LSP initialize complete (id=%s)", msg.id)

        # Now that the handshake is done, initialize the pipeline.
        # This sends loading_model → ready status notifications to the client.
        if self._pipeline:
            self._pipeline.initialize()

    def _handle_shutdown(self, msg: JsonRpcMessage) -> None:
        """Respond to LSP shutdown request."""
        self._write_message({
            "jsonrpc": "2.0",
            "id": msg.id,
            "result": None,
        })
        logger.info("Shutdown requested")

    # -----------------------------------------------------------------------
    # LSP param translation
    # -----------------------------------------------------------------------

    @staticmethod
    def _translate_lsp_params(method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Translate standard LSP notification params to our custom format.

        LSP wraps everything under textDocument.uri, textDocument.version, etc.
        Our handlers expect flat params: uri, version, changes, etc.
        """
        text_doc = params.get("textDocument", {})

        if method == "textDocument/didOpen":
            return {
                "uri": text_doc.get("uri", ""),
                "languageId": text_doc.get("languageId", ""),
                "version": text_doc.get("version", 1),
                "text": text_doc.get("text", ""),
            }

        if method == "textDocument/didChange":
            return {
                "uri": text_doc.get("uri", ""),
                "version": text_doc.get("version", 0),
                "changes": params.get("contentChanges", []),
                "timestamp": int(time.time() * 1000),
            }

        if method == "textDocument/didSave":
            return {
                "uri": text_doc.get("uri", ""),
                "version": text_doc.get("version", 0),
            }

        if method == "textDocument/didClose":
            return {
                "uri": text_doc.get("uri", ""),
            }

        return params

    # -----------------------------------------------------------------------
    # Handler implementations
    # -----------------------------------------------------------------------

    def _handle_did_open(self, params: dict[str, Any]) -> None:
        p = _parse_did_open(params)
        self.document_store.open(p.uri, p.language_id, p.version, p.text)
        logger.info("Opened %s (v%d, %s)", p.uri, p.version, p.language_id)

    def _handle_did_change(self, params: dict[str, Any]) -> None:
        p = _parse_did_change(params)
        logger.info(
            "didChange: uri=%s version=%d changes=%d",
            p.uri, p.version, len(p.changes),
        )

        # Capture old lines before applying changes (for edit history)
        doc = self.document_store.get(p.uri)
        if doc is None:
            logger.warning("didChange for unknown document: %s (not in document_store)", p.uri)
            return

        old_lines_snapshot: list[str] = []
        change_start = 0
        change_end = 0
        if p.changes:
            if len(p.changes) > 1:
                logger.debug(
                    "didChange has %d changes, only processing the first one",
                    len(p.changes),
                )
            first_change = p.changes[0]
            change_start = first_change.range.start.line
            change_end = first_change.range.end.line + 1
            old_lines_snapshot = doc.get_range(change_start, change_end)
            logger.info(
                "  change: range=(%d,%d)-(%d,%d) text=%r",
                first_change.range.start.line, first_change.range.start.character,
                first_change.range.end.line, first_change.range.end.character,
                first_change.text[:80],
            )

        doc = self.document_store.apply_changes(p.uri, p.version, p.changes)
        if doc is None:
            logger.warning("apply_changes returned None for %s", p.uri)
            return
        logger.debug("Changed %s → v%d", p.uri, p.version)

        # Capture new lines from the updated document.
        new_lines_snapshot: list[str] = []
        if p.changes:
            first_change = p.changes[0]
            new_line_count = first_change.text.count("\n") + 1
            new_end = change_start + new_line_count
            new_lines_snapshot = doc.get_range(change_start, new_end)

        logger.info(
            "  old_lines=%r new_lines=%r start=%d end=%d",
            old_lines_snapshot, new_lines_snapshot, change_start, change_end,
        )

        # Trigger the pipeline
        if self._pipeline:
            self._pipeline.on_did_change(
                uri=p.uri,
                version=p.version,
                old_lines=old_lines_snapshot,
                new_lines=new_lines_snapshot,
                start_line=change_start,
                end_line=change_end,
                timestamp=p.timestamp,
            )
        else:
            logger.warning("Pipeline not initialized, ignoring didChange")

    def _handle_did_save(self, params: dict[str, Any]) -> None:
        uri = params.get("uri", "")
        version = params.get("version", 0)
        logger.debug("Saved %s (v%d)", uri, version)

    def _handle_did_close(self, params: dict[str, Any]) -> None:
        uri = params.get("uri", "")
        self.document_store.close(uri)
        if self._pipeline:
            self._pipeline.on_close(uri)
        logger.info("Closed %s", uri)

    def _handle_full_sync(self, params: dict[str, Any]) -> None:
        uri = params.get("uri", "")
        version = params.get("version", 0)
        text = params.get("text", "")
        self.document_store.full_sync(uri, version, text)
        logger.info("Full sync %s (v%d)", uri, version)

    def _handle_resolve(self, params: dict[str, Any]) -> None:
        suggestion_id = params.get("id", "")
        accepted = params.get("accepted", False)
        if self._pipeline:
            self._pipeline.on_resolve(suggestion_id, accepted)
        logger.info(
            "Suggestion %s %s",
            suggestion_id,
            "accepted" if accepted else "rejected",
        )

    # -----------------------------------------------------------------------
    # Status helpers
    # -----------------------------------------------------------------------

    def _send_status(self, state: ServerState, message: str = "") -> None:
        self.send_notification(
            Methods.STATUS,
            StatusParams(state=state, message=message),
        )

    # -----------------------------------------------------------------------
    # LSP base protocol: framed JSON-RPC over stdio
    # -----------------------------------------------------------------------

    def _read_message(self) -> JsonRpcMessage | None:
        """Read a single JSON-RPC message using LSP base protocol framing.

        Format:
            Content-Length: <length>\r\n
            \r\n
            <JSON payload of exactly <length> bytes>
        """
        assert self._stdin is not None

        # Read headers
        content_length = -1
        while True:
            line = self._stdin.readline()
            if not line:
                return None  # EOF
            line_str = line.decode("ascii", errors="replace").strip()
            if not line_str:
                break  # Empty line = end of headers
            if line_str.lower().startswith("content-length:"):
                content_length = int(line_str.split(":", 1)[1].strip())

        if content_length < 0:
            return None

        # Read body
        body = self._stdin.read(content_length)
        if not body:
            return None

        data = json.loads(body)
        return JsonRpcMessage(
            method=data.get("method"),
            params=data.get("params"),
            id=data.get("id"),
            result=data.get("result"),
            error=data.get("error"),
        )

    def _write_message(self, payload: dict[str, Any]) -> None:
        """Write a JSON-RPC message using LSP base protocol framing."""
        assert self._stdout is not None

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")

        with self._writer_lock:
            self._stdout.write(header + body)
            self._stdout.flush()


# ---------------------------------------------------------------------------
# Param parsers (camelCase wire format → typed dataclasses)
# ---------------------------------------------------------------------------

def _parse_position(data: dict[str, Any]) -> Position:
    return Position(line=data["line"], character=data["character"])


def _parse_range(data: dict[str, Any]) -> Range:
    return Range(start=_parse_position(data["start"]), end=_parse_position(data["end"]))


def _parse_text_change(data: dict[str, Any]) -> TextChange:
    return TextChange(range=_parse_range(data["range"]), text=data["text"])


def _parse_did_open(params: dict[str, Any]) -> DidOpenParams:
    return DidOpenParams(
        uri=params["uri"],
        language_id=params.get("languageId", ""),
        version=params.get("version", 1),
        text=params.get("text", ""),
    )


def _parse_did_change(params: dict[str, Any]) -> DidChangeParams:
    return DidChangeParams(
        uri=params["uri"],
        version=params.get("version", 0),
        changes=[_parse_text_change(c) for c in params.get("changes", [])],
        timestamp=params.get("timestamp", 0),
    )
