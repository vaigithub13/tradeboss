"""Pine panel API. Scanning is local. The model is only asked after the key is present."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from app.pine.checks import planned_checks, realistic_options_config, summary_card
from app.pine.convert import convert_draft
from app.pine.gates import (
    GateError, accept_report, draft_hash, issue_draft, issue_report, load_draft,
    remember_written, report_hash, require_accepted, require_approved,
)
from app.pine.openai_client import OpenAIError, OpenAIPineClient, openai_key
from app.pine.report import MissingKeyError, ReportError, build_report
from app.pine.sandbox import SandboxError, check_source
from app.pine.save import module_name, save_user_strategy
from app.pine.smoke import smoke_strategy
from app.pine.scanner import scan

router = APIRouter(prefix="/api/pine", tags=["pine"])


@router.post("/scan")
def scan_script(body: dict[str, Any]) -> dict[str, Any]:
    source = _source(body)
    found = scan(source)
    return {"scan": found, "checks": planned_checks(False), "realistic": realistic_options_config("user:draft")}


@router.post("/report")
def report_script(body: dict[str, Any]) -> dict[str, Any]:
    source = _source(body)
    key = openai_key()
    client = OpenAIPineClient(key) if key else None
    try:
        report = build_report(source, client=client, api_key=key)
    except MissingKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ReportError, OpenAIError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if client is not None:
        report["openai"] = {"model": client.model, "usage": client.usage, "finish_reason": client.finish_reason}
    stop_na = report["scan"]["traps"]["stop_na"]["status"] == "hit"
    report["card"] = summary_card(
        cost_warnings=["cost rates are UNVERIFIED"],
        option_fill="delta_adjusted",
        overnight_net=0.0,
        same_day_net=0.0,
        stop_na=stop_na,
        include_walk_forward=bool(body.get("walk_forward")),
    )
    report["card"]["warnings"] = list(dict.fromkeys([*report["warnings"], *report["card"]["warnings"]]))
    report_id, digest = issue_report(report)
    report["id"] = report_id
    report["hash"] = digest
    return report


@router.post("/accept")
def accept_script(body: dict[str, Any]) -> dict[str, bool]:
    """The Accept button. A convert call cannot record this."""
    try:
        accept_report(str(body.get("report_id") or ""), str(body.get("report_hash") or ""))
    except GateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"accepted": True}


@router.post("/convert")
def convert_script(body: dict[str, Any]) -> dict[str, Any]:
    source = _source(body)
    supplied = body.get("report") if isinstance(body.get("report"), dict) else {}
    report = {**supplied, "scan": scan(source)}
    try:
        require_accepted(str(body.get("report_id") or ""), report_hash(report))
    except GateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    key = openai_key()
    if not key:
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY is not set")
    client = OpenAIPineClient(key, purpose="conversion")
    try:
        result = convert_draft(source, client=client, report=report)
    except OpenAIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=type(exc).__name__) from exc
    result["openai"] = {
        "model": client.model,
        "usage": client.usage,
        "finish_reason": client.finish_reason,
    }
    draft_id, digest = issue_draft(str(result.get("python") or ""))
    result["id"] = draft_id
    result["hash"] = digest
    return result


@router.post("/approve")
def approve_script(body: dict[str, Any]) -> dict[str, Any]:
    """The Approve button. Re-check the AST, then write the strategy file."""
    draft_id = str(body.get("draft_id") or "")
    digest = str(body.get("draft_hash") or "")
    try:
        issued = load_draft(draft_id)
        python = issued.get("python") if issued is not None else None
        if issued is None or issued.get("hash") != digest or not isinstance(python, str) or draft_hash(python) != digest:
            raise GateError("this diff was not issued")
        check_source(python)
        chosen = str(body.get("name") or "").strip() or module_name(python)
        from app.pine.save import USER_DIR
        if (USER_DIR / f"{chosen}.py").exists() and not bool(body.get("replace")):
            raise FileExistsError(f"{chosen} already exists. Approve again to replace it.")
        problem = _smoke_draft(chosen, python)
        if problem:
            raise SandboxError(problem)
        path = save_user_strategy(chosen, python, replace=bool(body.get("replace")))
        shown = _repo_path(path)
        remember_written(draft_id, digest, path=shown, name=chosen)
    except GateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SandboxError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"approved": True, "path": _repo_path(path), "strategy": f"user:{chosen}"}


@router.post("/save")
def save_script(body: dict[str, Any]) -> dict[str, Any]:
    name = str(body.get("name") or "")
    source = str(body.get("source") or "")
    try:
        require_approved(str(body.get("draft_id") or ""), draft_hash(source))
    except GateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        path = save_user_strategy(name, source, replace=bool(body.get("replace")))
    except SandboxError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    found = scan(str(body.get("pine") or ""))
    card = summary_card(
        cost_warnings=["cost rates are UNVERIFIED"],
        option_fill=str(body.get("option_fill") or "delta_adjusted"),
        overnight_net=float(body.get("overnight_net") or 0),
        same_day_net=float(body.get("same_day_net") or 0),
        stop_na=found["traps"]["stop_na"]["status"] == "hit",
        include_walk_forward=bool(body.get("walk_forward")),
    )
    return {"path": str(path), "strategy": f"user:{name}", "card": card}


def _smoke_draft(name: str, python: str) -> str | None:
    """Run the draft in the worker before it is saved. A failure is not written."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{name}.py"
        path.write_text(python)
        return smoke_strategy(path)


def _repo_path(path: Path) -> str:
    from app.config import ROOT_DIR
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def _source(body: dict[str, Any]) -> str:
    source = body.get("source")
    if not isinstance(source, str) or not source.strip():
        raise HTTPException(status_code=400, detail="source is required")
    return source
