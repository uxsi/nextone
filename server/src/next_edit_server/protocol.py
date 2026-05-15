"""Protocol message types for the NextOne JSON-RPC protocol.

All messages follow JSON-RPC 2.0 format over stdio. This module defines
the typed data structures for serialization and deserialization.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Position:
    line: int
    character: int


@dataclass(frozen=True, slots=True)
class Range:
    start: Position
    end: Position


@dataclass(frozen=True, slots=True)
class TextChange:
    range: Range
    text: str


@dataclass(frozen=True, slots=True)
class LineDiff:
    """A single line in a suggestion diff (deleted or added)."""
    num: int
    text: str


# ---------------------------------------------------------------------------
# Editor → Server messages
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DidOpenParams:
    uri: str
    language_id: str
    version: int
    text: str


@dataclass(frozen=True, slots=True)
class DidChangeParams:
    uri: str
    version: int
    changes: list[TextChange]
    timestamp: int


@dataclass(frozen=True, slots=True)
class DidSaveParams:
    uri: str
    version: int


@dataclass(frozen=True, slots=True)
class DidCloseParams:
    uri: str


@dataclass(frozen=True, slots=True)
class FullSyncParams:
    uri: str
    version: int
    text: str


@dataclass(frozen=True, slots=True)
class ResolveParams:
    id: str
    accepted: bool


# ---------------------------------------------------------------------------
# Server → Editor messages
# ---------------------------------------------------------------------------

class ServerState(str, Enum):
    READY = "ready"
    LOADING_MODEL = "loading_model"
    INFERRING = "inferring"
    ERROR = "error"


class CancelReason(str, Enum):
    DOCUMENT_CHANGED = "document_changed"
    SUPERSEDED = "superseded"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class SuggestParams:
    id: str
    uri: str
    base_uri: str
    base_version: int
    location: Position
    diff: str
    description: str
    deleted_lines: list[LineDiff]
    added_lines: list[LineDiff]


@dataclass(frozen=True, slots=True)
class CancelSuggestionParams:
    id: str
    reason: CancelReason


@dataclass(frozen=True, slots=True)
class StatusParams:
    state: ServerState
    message: str = ""


# ---------------------------------------------------------------------------
# JSON-RPC envelope
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class JsonRpcMessage:
    """A parsed JSON-RPC 2.0 message (request, notification, or response)."""

    method: str | None = None
    params: dict[str, Any] | None = None
    id: int | str | None = None
    result: Any = None
    error: dict[str, Any] | None = None

    @property
    def is_request(self) -> bool:
        return self.method is not None and self.id is not None

    @property
    def is_notification(self) -> bool:
        return self.method is not None and self.id is None

    @property
    def is_response(self) -> bool:
        return self.method is None and (self.result is not None or self.error is not None)


# ---------------------------------------------------------------------------
# Method name constants
# ---------------------------------------------------------------------------

class Methods:
    DID_OPEN = "nextEdit/didOpen"
    DID_CHANGE = "nextEdit/didChange"
    DID_SAVE = "nextEdit/didSave"
    DID_CLOSE = "nextEdit/didClose"
    FULL_SYNC = "nextEdit/fullSync"
    SUGGEST = "nextEdit/suggest"
    CANCEL_SUGGESTION = "nextEdit/cancelSuggestion"
    RESOLVE = "nextEdit/resolve"
    STATUS = "nextEdit/status"


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _to_camel(name: str) -> str:
    """Convert snake_case to camelCase for JSON wire format."""
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def to_wire(obj: Any) -> Any:
    """Convert a dataclass instance to a JSON-serializable dict with camelCase keys."""
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [to_wire(item) for item in obj]
    if hasattr(obj, "__dataclass_fields__"):
        return {
            _to_camel(k): to_wire(v)
            for k, v in asdict(obj).items()
        }
    return obj


def make_notification(method: str, params: Any) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 notification dict ready for serialization."""
    return {
        "jsonrpc": "2.0",
        "method": method,
        "params": to_wire(params) if hasattr(params, "__dataclass_fields__") else params,
    }
