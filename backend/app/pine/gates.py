"""Server-side Accept and Approve. A boolean from the browser is not an acceptance.

Drafts and acceptances are files under data/pine/. A process restart keeps them.
The strategy file written by Approve lives in app/strategies/user/ and can be committed.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from threading import Lock
from typing import Any

_LOCK = Lock()
_STORE = Path(__file__).resolve().parents[3] / "data" / "pine"
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


def _report_path(report_id: str) -> Path:
    return _STORE / "reports" / f"{report_id}.json"


def _draft_path(draft_id: str) -> Path:
    return _STORE / "drafts" / f"{draft_id}.json"


def _read(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text())
    if not isinstance(loaded, dict):
        return None
    return loaded


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    temporary.replace(path)


def _report_record(report: dict[str, Any], digest: str, *, accepted: bool) -> dict[str, Any]:
    return {
        "hash": digest,
        "accepted": accepted,
        "report": {
            "scan": report.get("scan"),
            "model": report.get("model"),
            "warnings": report.get("warnings"),
            "card": report.get("card"),
        },
    }


def issue_report(report: dict[str, Any]) -> tuple[str, str]:
    digest = report_hash(report)
    report_id = uuid.uuid4().hex
    with _LOCK:
        _write(_report_path(report_id), _report_record(report, digest, accepted=False))
        _issued_reports[report_id] = digest
    return report_id, digest


def _issued_hash(report_id: str) -> str | None:
    cached = _issued_reports.get(report_id)
    if cached is not None:
        return cached
    data = _read(_report_path(report_id))
    if data is None or not isinstance(data.get("hash"), str):
        return None
    _issued_reports[report_id] = data["hash"]
    if data.get("accepted") is True:
        _accepted_reports[report_id] = data["hash"]
    return data["hash"]


def accept_report(report_id: str, digest: str) -> None:
    """Record that the user accepted this exact issued report."""
    with _LOCK:
        issued = _issued_hash(report_id)
        if issued is None or issued != digest:
            raise GateError("this report was not issued")
        current = _read(_report_path(report_id)) or _report_record({}, digest, accepted=True)
        current["hash"] = digest
        current["accepted"] = True
        _write(_report_path(report_id), current)
        _accepted_reports[report_id] = digest


def require_accepted(report_id: str, digest: str) -> None:
    with _LOCK:
        accepted = _accepted_reports.get(report_id)
        if accepted is None:
            data = _read(_report_path(report_id))
            if data is not None and data.get("accepted") is True and isinstance(data.get("hash"), str):
                accepted = data["hash"]
                _accepted_reports[report_id] = accepted
                _issued_reports[report_id] = accepted
    if accepted is None or accepted != digest:
        raise GateError("accept the semantics report before conversion")


def issue_draft(python: str) -> tuple[str, str]:
    digest = draft_hash(python)
    draft_id = uuid.uuid4().hex
    with _LOCK:
        _write(_draft_path(draft_id), {
            "hash": digest,
            "python": python,
            "approved": False,
            "path": None,
            "name": None,
        })
        _issued_drafts[draft_id] = digest
    return draft_id, digest


def load_draft(draft_id: str) -> dict[str, Any] | None:
    """The persisted draft. Memory is not required; a restart reads the file."""
    with _LOCK:
        data = _read(_draft_path(draft_id))
        if data is not None and isinstance(data.get("hash"), str):
            _issued_drafts[draft_id] = data["hash"]
            if data.get("approved") is True:
                _approved_drafts[draft_id] = data["hash"]
        return data


def approve_draft(draft_id: str, digest: str) -> None:
    with _LOCK:
        data = _read(_draft_path(draft_id))
        issued = data.get("hash") if data is not None else _issued_drafts.get(draft_id)
        if not isinstance(issued, str) or issued != digest:
            raise GateError("this diff was not issued")
        if data is None:
            data = {"hash": digest, "python": None, "approved": True, "path": None, "name": None}
        data["approved"] = True
        data["hash"] = digest
        _write(_draft_path(draft_id), data)
        _issued_drafts[draft_id] = digest
        _approved_drafts[draft_id] = digest


def remember_written(draft_id: str, digest: str, *, path: str, name: str) -> None:
    """Mark the persisted draft approved and record where the strategy file went."""
    with _LOCK:
        data = _read(_draft_path(draft_id))
        issued = data.get("hash") if data is not None else None
        if data is None or not isinstance(issued, str) or issued != digest:
            raise GateError("this diff was not issued")
        data["approved"] = True
        data["path"] = path
        data["name"] = name
        _write(_draft_path(draft_id), data)
        _issued_drafts[draft_id] = digest
        _approved_drafts[draft_id] = digest


def require_approved(draft_id: str, digest: str) -> None:
    with _LOCK:
        approved = _approved_drafts.get(draft_id)
        if approved is None:
            data = _read(_draft_path(draft_id))
            if data is not None and data.get("approved") is True and isinstance(data.get("hash"), str):
                approved = data["hash"]
                _approved_drafts[draft_id] = approved
                _issued_drafts[draft_id] = approved
    if approved is None or approved != digest:
        raise GateError("approve the diff before saving")


def clear_gates() -> None:
    """Drop the in-memory copies. Files under data/pine/ stay, which is what a restart does."""
    with _LOCK:
        _issued_reports.clear()
        _accepted_reports.clear()
        _issued_drafts.clear()
        _approved_drafts.clear()
