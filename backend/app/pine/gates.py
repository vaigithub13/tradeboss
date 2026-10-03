"""Server-side Accept and Approve. A boolean from the browser is not an acceptance."""

from __future__ import annotations

import hashlib
import json
import uuid
from threading import Lock
from typing import Any

_LOCK = Lock()
_issued_reports: dict[str, str] = {}
_accepted_reports: dict[str, str] = {}
_issued_drafts: dict[str, str] = {}
_approved_drafts: dict[str, str] = {}


class GateError(ValueError):
    pass


def _canon(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {str(key): _canon(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canon(item) for item in value]
    return value


def _digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(_canon(payload), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def report_hash(report: dict[str, Any]) -> str:
    """Hash of the semantics the user can accept. Token usage is not part of it."""
    return _digest({
        "scan": report.get("scan"),
        "model": report.get("model"),
        "warnings": report.get("warnings"),
        "card": report.get("card"),
    })


def draft_hash(python: str) -> str:
    return hashlib.sha256(python.encode()).hexdigest()


def issue_report(report: dict[str, Any]) -> tuple[str, str]:
    digest = report_hash(report)
    report_id = uuid.uuid4().hex
    with _LOCK:
        _issued_reports[report_id] = digest
    return report_id, digest


def accept_report(report_id: str, digest: str) -> None:
    """Record that the user accepted this exact issued report."""
    with _LOCK:
        issued = _issued_reports.get(report_id)
        if issued is None or issued != digest:
            raise GateError("this report was not issued")
        _accepted_reports[report_id] = digest


def require_accepted(report_id: str, digest: str) -> None:
    with _LOCK:
        accepted = _accepted_reports.get(report_id)
    if accepted is None or accepted != digest:
        raise GateError("accept the semantics report before conversion")


def issue_draft(python: str) -> tuple[str, str]:
    digest = draft_hash(python)
    draft_id = uuid.uuid4().hex
    with _LOCK:
        _issued_drafts[draft_id] = digest
    return draft_id, digest


def approve_draft(draft_id: str, digest: str) -> None:
    with _LOCK:
        issued = _issued_drafts.get(draft_id)
        if issued is None or issued != digest:
            raise GateError("this diff was not issued")
        _approved_drafts[draft_id] = digest


def require_approved(draft_id: str, digest: str) -> None:
    with _LOCK:
        approved = _approved_drafts.get(draft_id)
    if approved is None or approved != digest:
        raise GateError("approve the diff before saving")


def clear_gates() -> None:
    """Test isolation. The running app does not call this."""
    with _LOCK:
        _issued_reports.clear()
        _accepted_reports.clear()
        _issued_drafts.clear()
        _approved_drafts.clear()
