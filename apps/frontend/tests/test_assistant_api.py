from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
from algotrading.frontend.app import create_app
from algotrading.frontend.assistant_prompt import (
    build_messages,
    honest_gap_answer,
    is_grounded,
    ungrounded_numbers,
)
from algotrading.frontend.context import AppContext
from algotrading.frontend.grounding import (
    MODE_INDICATIVE,
    build_grounding_context,
)
from algotrading.frontend.openrouter import (
    ChatMessage,
    MissingApiKeyError,
    OpenRouterClient,
    OpenRouterConfig,
    OpenRouterError,
)
from algotrading.frontend.sci_format import sci_unit
from algotrading.infra.contracts import tables
from algotrading.infra.contracts.instrument_key import InstrumentKey
from algotrading.infra.storage import ParquetStore
from fastapi.testclient import TestClient

UNDERLYING = "SX5E"
TRADE_DATE = date(2026, 6, 16)
AS_OF = datetime(2026, 6, 16, 15, 30, tzinfo=UTC)
FORWARD = 4300.0

ATM_IV = 0.1840
PUT_25D_IV = 0.2100
CALL_25D_IV = 0.1700
EXPECTED_SKEW = PUT_25D_IV - CALL_25D_IV
EXPECTED_CONVEXITY = PUT_25D_IV + CALL_25D_IV - 2.0 * ATM_IV

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIGS_DIR = REPO_ROOT / "configs"


def _prov(source: str) -> ProvenanceStamp:
    return stamp(
        calc_ts=AS_OF,
        code_version="assistant-test",
        config_hashes={"cfg": "cfg-assistant"},
        source_records=(source_ref("raw_market_events", "sess-assistant", source),),
        source_timestamps=(AS_OF,),
    )


def _cell(
    delta_band: str,
    target_delta: float,
    log_moneyness: float,
    implied_vol: float,
) -> tables.ProjectedOptionAnalytics:
    return tables.ProjectedOptionAnalytics(
        snapshot_ts=AS_OF,
        provider="IBKR",
        underlying=UNDERLYING,
        tenor_label="3m",
        maturity_years=0.25,
        delta_band=delta_band,
        target_delta=target_delta,
        log_moneyness=log_moneyness,
        strike=FORWARD * (1.0 + log_moneyness),
        forward_price=FORWARD,
        implied_vol=implied_vol,
        total_variance=implied_vol * implied_vol * 0.25,
        price=120.0,
        delta=target_delta,
        gamma=0.0001,
        vega=10.0,
        theta=-2.0,
        rho=1.0,
        dollar_delta=10.0,
        dollar_gamma=1.0,
        dollar_vega=1.0,
        dollar_delta_unit="$ per $1 of underlying",
        dollar_gamma_unit="$ per 1% move",
        dollar_vega_unit="$ per 1 vol point",
        model_version="svi-assistant",
        pricer_version="px-assistant",
        source_snapshot_ts=AS_OF,
        provenance=_prov(f"cell:{delta_band}"),
    )


def _snapshot(
    strike: float, right: str, *, two_sided: bool
) -> tables.MarketStateSnapshot:
    key = InstrumentKey(
        underlying_symbol=UNDERLYING,
        security_type="OPT",
        exchange="EUREX",
        currency="EUR",
        multiplier=10.0,
        broker_contract_id=f"o-{right}-{int(strike)}",
        expiry=date(2026, 9, 18),
        strike=strike,
        option_right=right,
    )
    return tables.MarketStateSnapshot(
        snapshot_ts=AS_OF,
        instrument_key=key.canonical(),
        reference_spot=FORWARD,
        bid=119.0 if two_sided else 0.0,
        ask=121.0 if two_sided else 0.0,
        last=120.0,
        spread_pct=0.01,
        reference_type="mid",
        flags=(),
        completeness=1.0,
        trade_date=TRADE_DATE,
        underlying=UNDERLYING,
        provenance=_prov(f"snap:{right}:{int(strike)}"),
    )


# Six option quotes: four two-sided, two one-sided (excluded). Independent oracle:
# option_rows = 6, two_sided = 4, excluded = 2, fraction = 4/6.
EXPECTED_OPTION_ROWS = 6
EXPECTED_TWO_SIDED = 4
EXPECTED_EXCLUDED = 2


def _seed_store(root: Path) -> None:
    store = ParquetStore(root)
    store.write(
        "projected_option_analytics",
        [
            _cell("25dp", -0.25, -0.05, PUT_25D_IV),
            _cell("atm", 0.0, 0.0, ATM_IV),
            _cell("25dc", 0.25, 0.05, CALL_25D_IV),
        ],
    )
    store.write(
        "market_state_snapshots",
        [
            _snapshot(4100.0, "P", two_sided=True),
            _snapshot(4200.0, "P", two_sided=True),
            _snapshot(4300.0, "C", two_sided=True),
            _snapshot(4400.0, "C", two_sided=True),
            _snapshot(4500.0, "C", two_sided=False),
            _snapshot(4600.0, "C", two_sided=False),
        ],
    )


class FakeOpenRouterClient:
    def __init__(self, answer: str, *, stream_tokens: list[str] | None = None) -> None:
        self.answer = answer
        self.stream_tokens = stream_tokens or [answer]
        self.calls: list[tuple[list[ChatMessage], bool]] = []

    def complete(
        self, messages: list[ChatMessage], *, gloss: bool = False, max_tokens: int = 1024
    ) -> str:
        self.calls.append((messages, gloss))
        return self.answer

    def stream(
        self, messages: list[ChatMessage], *, gloss: bool = False, max_tokens: int = 1024
    ) -> Iterator[str]:
        self.calls.append((messages, gloss))
        yield from self.stream_tokens


class RaisingOpenRouterClient:
    def complete(self, messages: list[ChatMessage], **_: object) -> str:
        raise OpenRouterError("boom", status_code=503)

    def stream(self, messages: list[ChatMessage], **_: object) -> Iterator[str]:
        raise OpenRouterError("boom", status_code=503)
        yield ""  # pragma: no cover


def _ctx(root: Path, *, real_configs: bool = False) -> AppContext:
    _seed_store(root)
    configs = CONFIGS_DIR if real_configs else root.parent / "configs"
    return AppContext(
        store_root=root,
        configs_dir=configs,
        store=ParquetStore(root),
        default_underlying=UNDERLYING,
    )


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    return _ctx(tmp_path / "data", real_configs=True)


def _client(ctx: AppContext, fake: object) -> TestClient:
    return TestClient(create_app(ctx, openrouter=fake))  # type: ignore[arg-type]


# --- Grounding fidelity: the central test --------------------------------------------------

def test_facts_block_carries_the_screen_numbers(ctx: AppContext) -> None:
    grounding = build_grounding_context(ctx, UNDERLYING, TRADE_DATE)
    by_id = {fact.fact_id: fact for fact in grounding.facts}

    # Expected formatted strings derived independently via the house sci/sciUnit idiom.
    assert by_id["atm_level"].value_text == sci_unit(ATM_IV, "Vol")
    assert by_id["skew_25d"].value_text == sci_unit(EXPECTED_SKEW, "Vol")
    assert by_id["convexity_25d"].value_text == sci_unit(EXPECTED_CONVEXITY, "Vol")

    coverage = grounding.frame.coverage
    assert coverage.option_rows == EXPECTED_OPTION_ROWS
    assert coverage.two_sided == EXPECTED_TWO_SIDED
    assert coverage.excluded == EXPECTED_EXCLUDED
    assert coverage.two_sided_fraction == pytest.approx(
        EXPECTED_TWO_SIDED / EXPECTED_OPTION_ROWS
    )


def test_close_instant_is_1730_local_not_2200(ctx: AppContext) -> None:
    # The close instant is the PM-legible venue time-of-day + zone resolved from the registry
    # (OESX settlement 17:30 in the XEUR venue zone), NOT the 22:00 XEUR futures close. June is
    # summer in Berlin → CEST; the abbreviation is honest per-date, never a hard-coded "CET".
    grounding = build_grounding_context(ctx, UNDERLYING, TRADE_DATE)
    assert grounding.frame.close_instant == "17:30 CEST"
    assert grounding.frame.close_instant is not None
    assert "17:30" in grounding.frame.close_instant
    assert "22:00" not in grounding.frame.close_instant


# --- Never-invents: the guardrail test -----------------------------------------------------

def test_router_flags_a_fabricated_number_and_returns_the_honest_gap(ctx: AppContext) -> None:
    # The stub returns an IV the facts block never contained (0.30 is not on the screen).
    fake = FakeOpenRouterClient("L'ATM est à 30.0% — soit 3 × 10⁻¹ Vol.")
    with _client(ctx, fake) as client:
        resp = client.post(
            "/api/assistant",
            json={"question": "C'est quoi l'ATM ?", "underlying": UNDERLYING,
                  "trade_date": TRADE_DATE.isoformat()},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["grounded"] is False
    assert body["answer"] == honest_gap_answer()
    assert body["answer"] != fake.answer
    assert body["rejected_numbers"]  # the fabricated 30.0 / 3 was caught


def test_router_passes_a_grounded_answer_through(ctx: AppContext) -> None:
    grounding = build_grounding_context(ctx, UNDERLYING, TRADE_DATE)
    atm_text = next(f.value_text for f in grounding.facts if f.fact_id == "atm_level")
    grounded_answer = f"L'ATM à la monnaie est {atm_text}, en clôture strict."
    fake = FakeOpenRouterClient(grounded_answer)
    with _client(ctx, fake) as client:
        resp = client.post(
            "/api/assistant",
            json={"question": "C'est quoi l'ATM ?", "underlying": UNDERLYING,
                  "trade_date": TRADE_DATE.isoformat()},
        )
    body = resp.json()
    assert body["grounded"] is True
    assert body["answer"] == grounded_answer
    assert body["frame"]["underlying"] == UNDERLYING


def test_validator_allows_coverage_counts_and_facts(ctx: AppContext) -> None:
    grounding = build_grounding_context(ctx, UNDERLYING, TRADE_DATE)
    answer = "La nappe repose sur 4 cotations deux-faces sur 6, soit 2 exclues."
    assert is_grounded(answer, grounding)
    assert ungrounded_numbers(answer, grounding) == []


# A number the facts block never carried, expressed three ways the model might reach for. 0.30
# (the ATM is 0.184) is absent in every form: an ASCII percent, the house sci-notation idiom with a
# Unicode-superscript exponent, and spelled out in French and English.
_FABRICATED_FORMS = {
    "ascii_percent": "L'ATM est à 30.0%.",
    "ascii_decimal": "La vol implicite vaut 0.30.",
    "sci_superscript": "La vol implicite vaut 3 × 10⁻¹ Vol.",
    "sci_wrong_exponent": "La vol à la monnaie est 1.84 × 10⁻⁹ Vol.",
    "spelled_fr": "La vol implicite est de trente pour cent.",
    "spelled_en": "Implied vol is thirty percent.",
}


@pytest.mark.parametrize("form", sorted(_FABRICATED_FORMS))
def test_validator_catches_a_fabricated_number_in_every_form(
    ctx: AppContext, form: str
) -> None:
    grounding = build_grounding_context(ctx, UNDERLYING, TRADE_DATE)
    answer = _FABRICATED_FORMS[form]
    assert not is_grounded(answer, grounding), form
    assert ungrounded_numbers(answer, grounding), form


@pytest.mark.parametrize("form", sorted(_FABRICATED_FORMS))
def test_post_refuses_a_fabricated_number_in_every_form(ctx: AppContext, form: str) -> None:
    fake = FakeOpenRouterClient(_FABRICATED_FORMS[form])
    with _client(ctx, fake) as client:
        resp = client.post(
            "/api/assistant",
            json={"question": "C'est quoi l'ATM ?", "underlying": UNDERLYING,
                  "trade_date": TRADE_DATE.isoformat()},
        )
    body = resp.json()
    assert body["grounded"] is False, form
    assert body["answer"] == honest_gap_answer(), form
    assert body["answer"] != fake.answer, form
    assert body["citations"] == [], form  # no fabricated number may ride along in a citation


@pytest.mark.parametrize("form", sorted(_FABRICATED_FORMS))
def test_stream_refuses_a_fabricated_number_in_every_form(ctx: AppContext, form: str) -> None:
    # Split the fabricated answer into tokens so the stub mimics a real token stream; the router
    # must buffer-and-validate before emitting, so the client never sees the fabricated number.
    fabricated = _FABRICATED_FORMS[form]
    tokens = [fabricated[i : i + 4] for i in range(0, len(fabricated), 4)]
    fake = FakeOpenRouterClient("ignored", stream_tokens=tokens)
    with _client(ctx, fake) as client:
        resp = client.post(
            "/api/assistant/stream",
            json={"question": "C'est quoi l'ATM ?", "underlying": UNDERLYING,
                  "trade_date": TRADE_DATE.isoformat()},
        )
    assert resp.status_code == 200
    assert resp.text == honest_gap_answer(), form
    assert resp.text != fabricated, form


def test_stream_passes_a_grounded_answer_through(ctx: AppContext) -> None:
    grounding = build_grounding_context(ctx, UNDERLYING, TRADE_DATE)
    atm_text = next(f.value_text for f in grounding.facts if f.fact_id == "atm_level")
    grounded = f"L'ATM à la monnaie est {atm_text}."
    tokens = [grounded[i : i + 4] for i in range(0, len(grounded), 4)]
    fake = FakeOpenRouterClient("ignored", stream_tokens=tokens)
    with _client(ctx, fake) as client:
        resp = client.post(
            "/api/assistant/stream",
            json={"question": "C'est quoi l'ATM ?", "underlying": UNDERLYING,
                  "trade_date": TRADE_DATE.isoformat()},
        )
    assert resp.text == grounded


def test_contract_frame_carries_run_id_close_instant_and_coverage_label(
    ctx: AppContext,
) -> None:
    fake = FakeOpenRouterClient("Vous regardez la nappe SX5E.")
    with _client(ctx, fake) as client:
        resp = client.post(
            "/api/assistant",
            json={"question": "Qu'est-ce que je regarde ?", "underlying": UNDERLYING,
                  "trade_date": TRADE_DATE.isoformat(), "run_id": "run-0616"},
        )
    frame = resp.json()["frame"]
    # The fields the front's AssistantFrame declares (assistantApi.ts) are all present and live.
    assert frame["run_id"] == "run-0616"
    assert frame["close_instant"] is not None and "17:30" in frame["close_instant"]
    assert frame["coverage_label"] == (
        f"{EXPECTED_TWO_SIDED}/{EXPECTED_OPTION_ROWS} cotations"
    )


def test_contract_citation_has_id_label_value_source(ctx: AppContext) -> None:
    grounding = build_grounding_context(ctx, UNDERLYING, TRADE_DATE)
    atm_text = next(f.value_text for f in grounding.facts if f.fact_id == "atm_level")
    fake = FakeOpenRouterClient(f"L'ATM est {atm_text}.")
    with _client(ctx, fake) as client:
        resp = client.post(
            "/api/assistant",
            json={"question": "C'est quoi l'ATM ?", "underlying": UNDERLYING,
                  "trade_date": TRADE_DATE.isoformat()},
        )
    citations = resp.json()["citations"]
    assert citations
    atm = next(c for c in citations if c["id"] == "atm_level")
    # The shape the front's AssistantCitation declares: {id, label, value, source}.
    assert set(atm) == {"id", "label", "value", "source"}
    assert atm["value"] == atm_text
    assert atm["source"].startswith("signal enregistré")


# --- Strict / indicative honesty -----------------------------------------------------------

def test_indicative_mode_tags_the_frame(ctx: AppContext) -> None:
    grounding = build_grounding_context(ctx, UNDERLYING, TRADE_DATE, mode=MODE_INDICATIVE)
    assert grounding.frame.mode == MODE_INDICATIVE
    assert grounding.frame.to_dict()["indicative"] is True
    messages = build_messages(grounding, "Qu'est-ce que je regarde ?")
    user_text = messages[1].content
    assert "INDICATIF" in user_text


def test_strict_mode_has_no_indicative_framing(ctx: AppContext) -> None:
    grounding = build_grounding_context(ctx, UNDERLYING, TRADE_DATE)
    messages = build_messages(grounding, "Qu'est-ce que je regarde ?")
    assert "INDICATIF" not in messages[1].content
    assert grounding.frame.to_dict()["indicative"] is False


# --- No look-ahead -------------------------------------------------------------------------

def test_no_lookahead_a_later_date_does_not_change_the_facts(tmp_path: Path) -> None:
    root = tmp_path / "data"
    ctx = _ctx(root, real_configs=True)
    before = build_grounding_context(ctx, UNDERLYING, TRADE_DATE).fact_values()

    later = date(2026, 6, 17)
    store = ParquetStore(root)
    store.write(
        "projected_option_analytics",
        [
            tables.ProjectedOptionAnalytics(
                snapshot_ts=datetime(2026, 6, 17, 15, 30, tzinfo=UTC),
                provider="IBKR", underlying=UNDERLYING, tenor_label="3m",
                maturity_years=0.25, delta_band="atm", target_delta=0.0,
                log_moneyness=0.0, strike=FORWARD, forward_price=FORWARD,
                implied_vol=0.5000, total_variance=0.0625, price=120.0, delta=0.0,
                gamma=0.0001, vega=10.0, theta=-2.0, rho=1.0, dollar_delta=10.0,
                dollar_gamma=1.0, dollar_vega=1.0,
                dollar_delta_unit="$ per $1 of underlying",
                dollar_gamma_unit="$ per 1% move", dollar_vega_unit="$ per 1 vol point",
                model_version="svi", pricer_version="px",
                source_snapshot_ts=datetime(2026, 6, 17, 15, 30, tzinfo=UTC),
                provenance=_prov("lookahead"),
            )
        ],
    )
    after = build_grounding_context(ctx, UNDERLYING, TRADE_DATE).fact_values()
    assert after == before
    assert later  # the injected later partition is real, just must be ignored


# --- No secret leak ------------------------------------------------------------------------

def test_missing_key_raises_before_any_network() -> None:
    client = OpenRouterClient(OpenRouterConfig.from_env({}))
    assert not client.config.has_key()
    with pytest.raises(MissingApiKeyError):
        client.complete([ChatMessage(role="user", content="hi")])


def test_config_reads_key_from_env_only() -> None:
    config = OpenRouterConfig.from_env(
        {"OPENROUTER_API_KEY": "sk-secret", "ASSISTANT_MODEL": "anthropic/claude-opus-4-8"}
    )
    assert config.api_key == "sk-secret"
    assert config.reasoning_model == "anthropic/claude-opus-4-8"
    # The default gloss route is a cheaper, smaller model than the reasoning route.
    assert config.gloss_model != config.reasoning_model


# --- Model error → labelled non-500 --------------------------------------------------------

def test_model_error_is_a_labelled_non_500(ctx: AppContext) -> None:
    with _client(ctx, RaisingOpenRouterClient()) as client:
        resp = client.post(
            "/api/assistant",
            json={"question": "x", "underlying": UNDERLYING,
                  "trade_date": TRADE_DATE.isoformat()},
        )
    assert resp.status_code == 502
    body = resp.json()
    assert body["error"] == "assistant_unavailable"
    assert "frame" in body


# --- Routing: gloss flag selects the cheap model -------------------------------------------

def test_gloss_flag_routes_to_the_cheap_model(ctx: AppContext) -> None:
    fake = FakeOpenRouterClient("une nappe de volatilité")
    with _client(ctx, fake) as client:
        client.post(
            "/api/assistant",
            json={"question": "c'est quoi la nappe ?", "underlying": UNDERLYING,
                  "trade_date": TRADE_DATE.isoformat(), "gloss": True,
                  "element_id": "nappe"},
        )
    assert fake.calls and fake.calls[-1][1] is True


def test_reasoning_route_is_the_default(ctx: AppContext) -> None:
    fake = FakeOpenRouterClient("explication")
    with _client(ctx, fake) as client:
        client.post(
            "/api/assistant",
            json={"question": "explique", "underlying": UNDERLYING,
                  "trade_date": TRADE_DATE.isoformat()},
        )
    assert fake.calls and fake.calls[-1][1] is False


def test_client_model_routing_picks_distinct_models() -> None:
    client = OpenRouterClient(
        OpenRouterConfig(
            api_key="k", reasoning_model="anthropic/claude-opus-4-8",
            gloss_model="anthropic/claude-haiku-4-5",
        )
    )
    assert client.model_for(gloss=False) == "anthropic/claude-opus-4-8"
    assert client.model_for(gloss=True) == "anthropic/claude-haiku-4-5"


# --- Streaming -----------------------------------------------------------------------------

def test_stream_endpoint_relays_tokens(ctx: AppContext) -> None:
    fake = FakeOpenRouterClient("ignored", stream_tokens=["La ", "nappe ", "SX5E."])
    with _client(ctx, fake) as client:
        resp = client.post(
            "/api/assistant/stream",
            json={"question": "explique", "underlying": UNDERLYING,
                  "trade_date": TRADE_DATE.isoformat()},
        )
    assert resp.status_code == 200
    assert resp.text == "La nappe SX5E."
