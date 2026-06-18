from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..assistant_prompt import (
    build_messages,
    honest_gap_answer,
    is_grounded,
    ungrounded_numbers,
)
from ..deps import CtxDep
from ..grounding import MODE_INDICATIVE, MODE_STRICT, build_grounding_context
from ..guide_prompt import CatalogEntry, build_guide_messages, parse_guide_step
from ..openrouter import OpenRouterClient, OpenRouterError

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class AssistantRequest(BaseModel):
    question: str
    underlying: str | None = None
    trade_date: str | None = None
    run_id: str | None = None
    mode: str | None = None
    element_id: str | None = None
    gloss: bool = False


class GuideCatalogEntry(BaseModel):
    id: str
    label: str
    description: str
    route: str


class GuideRequest(BaseModel):
    goal: str
    route: str
    completed: list[str] = []
    catalog: list[GuideCatalogEntry] = []


def _openrouter_client(request: Request) -> OpenRouterClient:
    client: OpenRouterClient = request.app.state.openrouter
    return client


ClientDep = Annotated[OpenRouterClient, Depends(_openrouter_client)]


def _resolve_mode(mode: str | None) -> str:
    return MODE_INDICATIVE if mode == MODE_INDICATIVE else MODE_STRICT


def _resolve_trade_date(trade_date: str | None) -> date | None:
    if trade_date is None:
        return None
    try:
        return date.fromisoformat(trade_date)
    except ValueError:
        return None


@router.post("")
def post_assistant(ctx: CtxDep, client: ClientDep, body: AssistantRequest) -> JSONResponse:
    resolved_date = _resolve_trade_date(body.trade_date)
    grounding = build_grounding_context(
        ctx,
        body.underlying,
        resolved_date,
        mode=_resolve_mode(body.mode),
        run_id=body.run_id,
    )
    frame = grounding.frame.to_dict()
    citations = grounding.citations()
    messages = build_messages(grounding, body.question)

    try:
        raw_answer = client.complete(messages, gloss=body.gloss)
    except OpenRouterError as exc:
        return JSONResponse(
            {
                "error": "assistant_unavailable",
                "detail": exc.detail,
                "frame": frame,
            },
            status_code=502,
        )

    grounded = is_grounded(raw_answer, grounding)
    answer = raw_answer if grounded else honest_gap_answer()

    return JSONResponse(
        {
            "answer": answer,
            "grounded": grounded,
            "rejected_numbers": ungrounded_numbers(raw_answer, grounding),
            "citations": citations if grounded else [],
            "frame": frame,
        }
    )


@router.post("/stream")
def post_assistant_stream(
    ctx: CtxDep, client: ClientDep, body: AssistantRequest
) -> StreamingResponse:
    resolved_date = _resolve_trade_date(body.trade_date)
    grounding = build_grounding_context(
        ctx, body.underlying, resolved_date, mode=_resolve_mode(body.mode)
    )
    messages = build_messages(grounding, body.question)

    def _events() -> Iterator[str]:
        buffer: list[str] = []
        try:
            for token in client.stream(messages, gloss=body.gloss):
                buffer.append(token)
        except OpenRouterError as exc:
            yield f"\n[assistant_unavailable] {exc.detail}"
            return
        raw_answer = "".join(buffer)
        if is_grounded(raw_answer, grounding):
            yield raw_answer
        else:
            yield honest_gap_answer()

    return StreamingResponse(_events(), media_type="text/plain")


@router.post("/guide")
def post_assistant_guide(client: ClientDep, body: GuideRequest) -> JSONResponse:
    catalog = [
        CatalogEntry(
            id=entry.id,
            label=entry.label,
            description=entry.description,
            route=entry.route,
        )
        for entry in body.catalog
    ]
    catalog_ids = {entry.id for entry in catalog}
    messages = build_guide_messages(body.goal, body.route, body.completed, catalog)

    try:
        raw_step = client.complete(messages)
    except OpenRouterError as exc:
        return JSONResponse(
            {
                "error": "assistant_unavailable",
                "detail": exc.detail,
            },
            status_code=502,
        )

    return JSONResponse(parse_guide_step(raw_step, catalog_ids))
