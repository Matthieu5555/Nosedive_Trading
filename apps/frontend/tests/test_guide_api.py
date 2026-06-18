from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from algotrading.frontend.app import create_app
from algotrading.frontend.context import AppContext
from algotrading.frontend.guide_prompt import (
    CatalogEntry,
    build_guide_messages,
    parse_guide_step,
)
from algotrading.frontend.openrouter import ChatMessage, OpenRouterError
from algotrading.infra.storage import ParquetStore
from fastapi import FastAPI

# A small, fixed catalog mirroring the registry shape. The ids here are the closed set the
# validator trusts; anything outside it must be nulled.
CATALOG = [
    CatalogEntry(
        id="nav.basket",
        label="Basket",
        description="Open the basket page to build a basket.",
        route="/",
    ),
    CatalogEntry(
        id="basket.underlying",
        label="Underlying",
        description="Pick the underlying for the basket.",
        route="/basket",
    ),
    CatalogEntry(
        id="market.smile",
        label="Smile",
        description="The smile panel shows vol across strikes.",
        route="/",
    ),
]
CATALOG_IDS = {entry.id for entry in CATALOG}


# --- parse_guide_step: the trust guard -----------------------------------------------------


def test_valid_json_passes_through() -> None:
    raw = (
        '{"say": "Click Basket up top.", "highlight": "nav.basket", '
        '"expect": "navigate", "done": false}'
    )
    step = parse_guide_step(raw, CATALOG_IDS)
    # Independently derived: a well-formed step with an in-catalog id is preserved verbatim.
    assert step == {
        "say": "Click Basket up top.",
        "highlight": "nav.basket",
        "expect": "navigate",
        "done": False,
    }


def test_valid_catalog_id_is_preserved() -> None:
    raw = '{"say": "Pick an underlying.", "highlight": "basket.underlying", "expect": "click", "done": false}'
    step = parse_guide_step(raw, CATALOG_IDS)
    assert step["highlight"] == "basket.underlying"
    assert step["expect"] == "click"


def test_invented_highlight_id_gets_nulled() -> None:
    # "basket.nonexistent" is NOT in the catalog: the navigation analogue of an ungrounded number.
    raw = (
        '{"say": "Click the magic button.", "highlight": "basket.nonexistent", '
        '"expect": "click", "done": false}'
    )
    step = parse_guide_step(raw, CATALOG_IDS)
    assert step["highlight"] is None
    # The rest of the step still comes through.
    assert step["say"] == "Click the magic button."
    assert step["expect"] == "click"


def test_code_fenced_json_is_parsed() -> None:
    raw = (
        "Sure, here is the step:\n"
        "```json\n"
        '{"say": "Open the smile.", "highlight": "market.smile", "expect": "click", "done": false}\n'
        "```\n"
        "Let me know if you need more."
    )
    step = parse_guide_step(raw, CATALOG_IDS)
    assert step["highlight"] == "market.smile"
    assert step["say"] == "Open the smile."


def test_surrounding_prose_without_fence_is_parsed() -> None:
    raw = 'Next: {"say": "You are done.", "highlight": null, "expect": "none", "done": true} done.'
    step = parse_guide_step(raw, CATALOG_IDS)
    assert step["done"] is True
    assert step["highlight"] is None
    assert step["say"] == "You are done."


def test_garbage_falls_back_safely() -> None:
    step = parse_guide_step("this is not json at all", CATALOG_IDS)
    # No parseable object: a safe, honest fallback step with no highlight.
    assert isinstance(step["say"], str) and step["say"].strip()
    assert step["highlight"] is None
    assert step["expect"] == "none"
    assert step["done"] is False


def test_missing_fields_fall_back() -> None:
    # Only "say" present; everything else must default safely.
    step = parse_guide_step('{"say": "Look at the screen."}', CATALOG_IDS)
    assert step == {
        "say": "Look at the screen.",
        "highlight": None,
        "expect": "none",
        "done": False,
    }


def test_missing_say_falls_back_to_honest_message() -> None:
    step = parse_guide_step('{"highlight": "nav.basket", "expect": "navigate"}', CATALOG_IDS)
    assert isinstance(step["say"], str) and step["say"].strip()
    # The valid highlight still survives even when say is absent.
    assert step["highlight"] == "nav.basket"


def test_empty_say_string_falls_back() -> None:
    step = parse_guide_step('{"say": "   ", "highlight": null, "expect": "none", "done": false}', CATALOG_IDS)
    assert step["say"].strip()  # not the empty/whitespace string


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("navigate", "navigate"),
        ("click", "click"),
        ("none", "none"),
        ("NAVIGATE", "none"),  # case must match exactly, else default
        ("hover", "none"),  # unknown verb defaults
        ("", "none"),
        (None, "none"),
        (42, "none"),  # non-string defaults
    ],
)
def test_expect_coercion(given: object, expected: str) -> None:
    raw = {"say": "Step.", "highlight": None, "expect": given, "done": False}
    import json

    step = parse_guide_step(json.dumps(raw), CATALOG_IDS)
    assert step["expect"] == expected


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (True, True),
        (False, False),
        ("yes", True),  # truthy non-bool coerces to True
        (1, True),
        (0, False),
        (None, False),
    ],
)
def test_done_coercion(given: object, expected: bool) -> None:
    import json

    raw = {"say": "Step.", "highlight": None, "expect": "none", "done": given}
    step = parse_guide_step(json.dumps(raw), CATALOG_IDS)
    assert step["done"] is expected


def test_highlight_non_string_is_nulled() -> None:
    import json

    raw = {"say": "Step.", "highlight": 123, "expect": "none", "done": False}
    step = parse_guide_step(json.dumps(raw), CATALOG_IDS)
    assert step["highlight"] is None


def test_empty_catalog_nulls_every_highlight() -> None:
    raw = '{"say": "Click it.", "highlight": "nav.basket", "expect": "click", "done": false}'
    step = parse_guide_step(raw, set())
    assert step["highlight"] is None


# --- build_guide_messages: prompt shape ----------------------------------------------------


def test_build_guide_messages_grounds_only_on_posted_catalog() -> None:
    messages = build_guide_messages(
        "how do I build a basket?", "/", [], CATALOG
    )
    assert len(messages) == 2
    assert messages[0].role == "system"
    system = messages[0].content
    # The closed-set rule and the JSON shape are stated.
    assert "highlight" in system
    assert "null" in system
    user = messages[1].content
    # Every posted id appears in the grounding block; nothing else.
    for entry in CATALOG:
        assert entry.id in user
        assert entry.label in user
    assert "how do I build a basket?" in user
    assert "Current route: /" in user


def test_build_guide_messages_reports_completed_steps() -> None:
    messages = build_guide_messages(
        "read the smile", "/", ["nav.market", "market.smile"], CATALOG
    )
    user = messages[1].content
    assert "nav.market" in user
    assert "market.smile" in user


def test_build_guide_messages_handles_no_completed() -> None:
    messages = build_guide_messages("goal", "/basket", [], CATALOG)
    user = messages[1].content
    assert "none" in user.lower()


# --- Route handler: stub the OpenRouter client like the existing tests ---------------------


class FakeGuideClient:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list[list[ChatMessage]] = []

    def complete(self, messages: list[ChatMessage], **_: object) -> str:
        self.calls.append(messages)
        return self.answer

    def stream(self, messages: list[ChatMessage], **_: object) -> Iterator[str]:
        yield self.answer


class RaisingGuideClient:
    def complete(self, messages: list[ChatMessage], **_: object) -> str:
        raise OpenRouterError("boom", status_code=503)

    def stream(self, messages: list[ChatMessage], **_: object) -> Iterator[str]:
        raise OpenRouterError("boom", status_code=503)
        yield ""  # pragma: no cover


_CATALOG_BODY = [
    {"id": e.id, "label": e.label, "description": e.description, "route": e.route}
    for e in CATALOG
]


def _build_app(tmp_path: Path, fake: object) -> FastAPI:
    # The guide route never touches the store; a minimal context pointed at tmp paths is enough.
    root = tmp_path / "data"
    ctx = AppContext(
        store_root=root,
        configs_dir=tmp_path / "configs",
        store=ParquetStore(root),
        default_underlying="SX5E",
    )
    return create_app(ctx, openrouter=fake)  # type: ignore[arg-type]


def test_guide_route_returns_clean_step(tmp_path: Path) -> None:
    app = _build_app(tmp_path, FakeGuideClient(
        '{"say": "Click Basket up top.", "highlight": "nav.basket", '
        '"expect": "navigate", "done": false}'
    ))
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        resp = client.post(
            "/api/assistant/guide",
            json={
                "goal": "how do I build a basket?",
                "route": "/",
                "completed": [],
                "catalog": _CATALOG_BODY,
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "say": "Click Basket up top.",
        "highlight": "nav.basket",
        "expect": "navigate",
        "done": False,
    }


def test_guide_route_nulls_invented_highlight(tmp_path: Path) -> None:
    app = _build_app(tmp_path, FakeGuideClient(
        '{"say": "Click the ghost.", "highlight": "no.such.id", "expect": "click", "done": false}'
    ))
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        resp = client.post(
            "/api/assistant/guide",
            json={
                "goal": "do something",
                "route": "/",
                "completed": [],
                "catalog": _CATALOG_BODY,
            },
        )
    body = resp.json()
    assert body["highlight"] is None
    assert body["say"] == "Click the ghost."


def test_guide_route_model_error_is_502(tmp_path: Path) -> None:
    app = _build_app(tmp_path, RaisingGuideClient())
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        resp = client.post(
            "/api/assistant/guide",
            json={"goal": "x", "route": "/", "completed": [], "catalog": _CATALOG_BODY},
        )
    assert resp.status_code == 502
    body = resp.json()
    assert body["error"] == "assistant_unavailable"
