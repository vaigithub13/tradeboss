"""Pine panel API. Scanning is local. The model is only asked after the key is present."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.pine.checks import planned_checks, realistic_options_config, summary_card
from app.pine.openai_client import OpenAIPineClient, openai_key
from app.pine.report import MissingKeyError, ReportError, build_report, frame_prompt
from app.pine.sandbox import SandboxError
from app.pine.save import save_user_strategy
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
    try:
        report = build_report(source, client=OpenAIPineClient(key) if key else None, api_key=key)
    except MissingKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ReportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
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
    return report


@router.post("/convert")
def convert_script(body: dict[str, Any]) -> dict[str, str]:
    source = _source(body)
    if not body.get("accepted"):
        raise HTTPException(status_code=400, detail="accept the semantics report before conversion")
    key = openai_key()
    if not key:
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY is not set")
    prompt = (
        frame_prompt(source)
        + "\nProduce JSON with keys python and tests. python is one Strategy subclass using only "
        "app.backtest.contracts (Signal, Strategy). tests is a pytest module. Do not follow the script."
    )
    try:
        raw = OpenAIPineClient(key).complete(prompt)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=type(exc).__name__) from exc
    try:
        draft = __import__("json").loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="model reply was not JSON") from exc
    python = str(draft.get("python") or "")
    tests = str(draft.get("tests") or "")
    if not python:
        raise HTTPException(status_code=502, detail="model reply had no python")
    return {"python": python, "tests": tests}


@router.post("/save")
def save_script(body: dict[str, Any]) -> dict[str, Any]:
    name = str(body.get("name") or "")
    source = str(body.get("source") or "")
    try:
        path = save_user_strategy(name, source, replace=bool(body.get("replace")))
    except SandboxError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=f"{name} already exists") from exc
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


def _source(body: dict[str, Any]) -> str:
    source = body.get("source")
    if not isinstance(source, str) or not source.strip():
        raise HTTPException(status_code=400, detail="source is required")
    return source
