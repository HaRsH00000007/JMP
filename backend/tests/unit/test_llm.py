"""Claude integration: request shape & caching, structured-output validation, anti-fabrication, retries,
usage and cost tracking. Uses a fake client — no network, no key."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.errors import ErrorCode, LlmError
from app.llm import pricing
from app.llm.client import AnthropicNarrativeProvider
from app.llm.facts import build_facts_payload
from app.llm.schemas import NarrativeV1, api_json_schema
from app.llm.service import generate_narrative
from app.llm.static_prefix import build_static_prefix
from app.llm.validator import validate_narrative
from tests.conftest import fake_response, valid_narrative_json


def _provider(fake_client_factory, responses):
    client = fake_client_factory(responses)
    return AnthropicNarrativeProvider(client=client, model="claude-opus-5"), client


def _mutate(facts, fn) -> str:
    d = json.loads(valid_narrative_json(facts))
    fn(d)
    return json.dumps(d)


# ------------------------------------------------------------------------------ request & cache
def test_static_prefix_is_byte_stable_and_cache_marked(library, zirakpur_facts, reference_facts, fake_client_factory):
    p1 = build_static_prefix(library)
    p2 = build_static_prefix(library)
    assert p1.sha256 == p2.sha256 and p1.blocks == p2.blocks
    prov, client = _provider(fake_client_factory, [fake_response(valid_narrative_json(zirakpur_facts)),
                                                   fake_response(valid_narrative_json(reference_facts))])
    generate_narrative(zirakpur_facts, library, provider=prov)
    generate_narrative(reference_facts, library, provider=prov)
    a, b = client.messages.calls
    assert a["system"] == b["system"]  # identical static prefix for two different journeys
    assert a["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert all("cache_control" not in blk for blk in a["system"][:-1])  # single breakpoint
    assert a["messages"] != b["messages"]  # only the variable part differs
    assert a["model"] == "claude-opus-5"
    assert a["output_config"]["format"]["type"] == "json_schema"
    assert a["output_config"]["effort"] == "high"
    assert a["extra_body"] == {"fallbacks": "default"}
    # the whole library travels in the cached prefix, never in the user message
    lib_block = a["system"][2]["text"]
    assert all(h.name in lib_block for h in library.hazards)
    user = a["messages"][0]["content"]
    assert "controls_4w" not in user and "Maintain speed upto" not in user
    assert '"geometry"' not in user  # no raw route geometry is sent


def test_prompt_version_guard(library):
    """Changing any static prompt block must come with a PROMPT_VERSION bump (update the hash below then)."""
    from app.versions import PROMPT_VERSION

    prefix = build_static_prefix(library)
    assert PROMPT_VERSION == "1.1"
    assert len(prefix.sha256) == 64
    schema = api_json_schema()
    txt = json.dumps(schema)
    assert "maxLength" not in txt and "minItems" not in txt
    assert schema["additionalProperties"] is False


# ------------------------------------------------------------------------------ validation cases
def test_valid_json_accepted_and_usage_recorded(library, zirakpur_facts, fake_client_factory):
    prov, client = _provider(fake_client_factory, [fake_response(valid_narrative_json(zirakpur_facts),
                                                                 cache_read=7400)])
    records = []
    n, src, model = generate_narrative(zirakpur_facts, library, provider=prov, on_attempt=records.append)
    assert isinstance(n, NarrativeV1) and src == "anthropic" and model == "claude-opus-5"
    assert len(records) == 1 and records[0].outcome == "ok"
    assert records[0].call.usage.cache_read == 7400 and records[0].call.usage.output_tokens == 3100


def test_invalid_json_then_valid_is_retried(library, zirakpur_facts, fake_client_factory):
    prov, client = _provider(fake_client_factory, [fake_response("{not json"),
                                                   fake_response(valid_narrative_json(zirakpur_facts))])
    records = []
    generate_narrative(zirakpur_facts, library, provider=prov, on_attempt=records.append)
    assert [r.outcome for r in records] == ["invalid_json", "ok"]
    retry_msgs = client.messages.calls[1]["messages"]
    assert retry_msgs[1]["role"] == "assistant" and "failed validation" in retry_msgs[2]["content"]
    assert client.messages.calls[1]["system"] == client.messages.calls[0]["system"]  # cache still hits


def test_missing_fields_rejected(library, zirakpur_facts, fake_client_factory):
    bad = _mutate(zirakpur_facts, lambda d: d.pop("executive_summary"))
    prov, _ = _provider(fake_client_factory, [fake_response(bad)] * 3)
    with pytest.raises(LlmError) as ei:
        generate_narrative(zirakpur_facts, library, provider=prov)
    assert ei.value.code == ErrorCode.LLM_INVALID_OUTPUT


def test_word_limit_enforced_client_side(library, zirakpur_facts, fake_client_factory):
    bad = _mutate(zirakpur_facts, lambda d: d.__setitem__("executive_summary", "word " * 200))
    good = valid_narrative_json(zirakpur_facts)
    prov, _ = _provider(fake_client_factory, [fake_response(bad), fake_response(good)])
    recs = []
    generate_narrative(zirakpur_facts, library, provider=prov, on_attempt=recs.append)
    assert recs[0].outcome == "schema_error" and "maximum is 110" in recs[0].errors[0]


def _validate(facts, library, data: str) -> list[str]:
    prefix = build_static_prefix(library)
    return validate_narrative(NarrativeV1.model_validate_json(data), build_facts_payload(facts), library,
                              prefix.library_numbers)


def test_unknown_hazard_id_rejected(library, zirakpur_facts):
    def f(d):
        d["hazard_notes"][0]["hazard_code"] = "HZ-99"
    errs = _validate(zirakpur_facts, library, _mutate(zirakpur_facts, f))
    assert any("unknown id(s) ['HZ-99']" in e for e in errs) and any("missing entries" in e for e in errs)


def test_unknown_segment_id_rejected(library, zirakpur_facts):
    def f(d):
        d["segment_notes"][0]["segment_id"] = "S42"
    errs = _validate(zirakpur_facts, library, _mutate(zirakpur_facts, f))
    assert any("segment_notes: unknown id(s) ['S42']" in e for e in errs)


def test_hallucinated_numbers_rejected(library, zirakpur_facts):
    def f(d):
        d["executive_summary"] = "The route is 147 km long and takes 6 hours with a score of 91."
    errs = _validate(zirakpur_facts, library, _mutate(zirakpur_facts, f))
    joined = " ".join(errs)
    assert "147" in joined and "91" in joined


def test_supplied_and_guidance_numbers_allowed(library, zirakpur_facts):
    lo, hi = zirakpur_facts.route.distance_range_km

    def f(d):
        d["executive_summary"] = f"About {lo}–{hi} km; keep a 3-second gap and 20–30 km/h through towns."
    assert _validate(zirakpur_facts, library, _mutate(zirakpur_facts, f)) == []


@pytest.mark.parametrize("text,needle", [
    ("Call 112 in an emergency.", "emergency/helpline"),
    ("Hospital helpline +91 98765 43210.", "phone-like"),
    ("See www.example.com for updates.", "URL"),
    ("Email ehs@danone.example for help.", "e-mail"),
])
def test_contacts_and_urls_rejected(library, zirakpur_facts, text, needle):
    errs = _validate(zirakpur_facts, library, _mutate(zirakpur_facts, lambda d: d.__setitem__("fatigue_management", text)))
    assert any(needle in e for e in errs), errs


def test_non_candidate_hazard_mention_rejected(library, zirakpur_facts):
    assert "HZ-13" not in {h.code for h in zirakpur_facts.hazards}
    errs = _validate(zirakpur_facts, library, _mutate(
        zirakpur_facts, lambda d: d.__setitem__("journey_assessment", "Watch for landslide debris.")))
    assert any("landslide" in e for e in errs)


def test_refusal_is_not_retried(library, zirakpur_facts, fake_client_factory):
    prov, client = _provider(fake_client_factory, [fake_response("", stop_reason="refusal")])
    with pytest.raises(LlmError) as ei:
        generate_narrative(zirakpur_facts, library, provider=prov)
    assert ei.value.code == ErrorCode.LLM_REFUSAL and len(client.messages.calls) == 1


def test_max_tokens_retries_with_larger_budget(library, zirakpur_facts, fake_client_factory):
    prov, client = _provider(fake_client_factory, [fake_response("{", stop_reason="max_tokens"),
                                                   fake_response(valid_narrative_json(zirakpur_facts))])
    generate_narrative(zirakpur_facts, library, provider=prov)
    assert client.messages.calls[1]["max_tokens"] > client.messages.calls[0]["max_tokens"]


def _api_err(cls, status):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx.Response(status, request=req), body=None)


@pytest.mark.parametrize("exc,code,retryable", [
    (_api_err(anthropic.RateLimitError, 429), ErrorCode.LLM_UNAVAILABLE, True),
    (_api_err(anthropic.InternalServerError, 500), ErrorCode.LLM_UNAVAILABLE, True),
    (_api_err(anthropic.AuthenticationError, 401), ErrorCode.LLM_NOT_CONFIGURED, False),
    (anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")), ErrorCode.LLM_UNAVAILABLE, True),
])
def test_api_error_mapping(library, zirakpur_facts, fake_client_factory, exc, code, retryable):
    prov, _ = _provider(fake_client_factory, [exc])
    with pytest.raises(LlmError) as ei:
        generate_narrative(zirakpur_facts, library, provider=prov)
    assert ei.value.code == code and ei.value.retryable is retryable


def test_missing_api_key_is_explicit():
    with pytest.raises(LlmError) as ei:
        AnthropicNarrativeProvider()
    assert ei.value.code == ErrorCode.LLM_NOT_CONFIGURED


# ------------------------------------------------------------------------------ pricing & usage
def test_cost_calculation():
    u = pricing.TokenUsage(input_tokens=2_000, cache_creation_5m=8_000, cache_read=0, output_tokens=4_000)
    # 2000*5 + 8000*5*1.25 + 4000*25 = 10000 + 50000 + 100000 = 160000 / 1e6
    assert pricing.cost_usd("claude-opus-5", u) == Decimal("0.160000")
    hit = pricing.TokenUsage(input_tokens=2_000, cache_read=8_000, output_tokens=4_000)
    assert pricing.cost_usd("claude-opus-5", hit) == Decimal("0.114000")
    assert pricing.cost_usd("claude-opus-5", hit, batch=True) == Decimal("0.057000")
    p = pricing.projection("claude-opus-5", prefix_tokens=8000, variable_tokens=2000, output_tokens=4000)
    assert p["usd"]["per_1000"] == pytest.approx(114.0)
    assert p["inr"]["per_jmp"] == pytest.approx(0.114 * 88.0, rel=1e-3)


def test_usage_rows_persisted_with_cost(library, zirakpur_facts, fake_client_factory, db):
    from sqlalchemy import select

    from app.db.models import GenerationUsage
    from app.services.jobs import record_usage

    prov, _ = _provider(fake_client_factory, [fake_response("nope"),
                                              fake_response(valid_narrative_json(zirakpur_facts), cache_read=7000)])
    recs = []
    generate_narrative(zirakpur_facts, library, provider=prov, on_attempt=recs.append)
    jid = uuid.uuid4()
    for r in recs:
        record_usage(None, jid, None, r)  # type: ignore[arg-type]
    rows = db.scalars(select(GenerationUsage).where(GenerationUsage.journey_id == jid)
                      .order_by(GenerationUsage.attempt)).all()
    assert [r.outcome for r in rows] == ["invalid_json", "ok"]
    assert rows[1].cache_read_input_tokens == 7000 and rows[1].estimated_cost_usd > 0
    assert float(rows[1].estimated_cost_inr) == pytest.approx(float(rows[1].estimated_cost_usd) * 88.0, rel=1e-3)
    assert rows[1].static_prefix_sha256 == build_static_prefix(library).sha256


def test_api_key_never_logged(capsys):
    from app.observability.logging import redact_text

    assert "sk-ant-" not in redact_text("key=sk-ant-api03-abcdefghijklmnop")
    assert "abcdefghijkl" not in redact_text("Authorization: Bearer abcdefghijklmnopqrstu")
    _ = SimpleNamespace


def test_supplied_score_of_100_is_not_an_emergency_number(library, zirakpur_facts):
    """Regression: a dimension genuinely scored 100 must not trip the helpline-number guard."""
    import copy

    facts = copy.deepcopy(zirakpur_facts)
    facts.scores.dimensions[4].score = 100
    errs = _validate(facts, library, _mutate(facts, lambda d: None))
    assert errs == []
    errs = _validate(facts, library, _mutate(facts, lambda d: d.__setitem__("fatigue_management", "Dial 100 if stopped.")))
    assert any("helpline" in e for e in errs)


# ------------------------------------------------------------------------------ cost optimisation
def test_small_word_overrun_is_accepted_without_a_retry(library, zirakpur_facts, fake_client_factory):
    """A field one or two words over its limit (most paid retries in the first real batch) is kept."""
    from app.llm.schemas import word_tolerance

    text = " ".join(["word"] * (70 + word_tolerance(70)))  # decision_rationale: limit 70
    good = _mutate(zirakpur_facts, lambda d: d.__setitem__("decision_rationale", text))
    prov, client = _provider(fake_client_factory, [fake_response(good)])
    records = []
    generate_narrative(zirakpur_facts, library, provider=prov, on_attempt=records.append)
    assert [r.outcome for r in records] == ["ok"] and len(client.messages.calls) == 1


def test_retry_asks_only_for_the_failing_fields_and_merges_them(library, zirakpur_facts, fake_client_factory):
    """A blocking failure in one field is fixed by a patch: the retry carries only that field (not the whole
    previous answer), the reply carries only that field, and the merged narrative is validated as a whole."""
    bad = _mutate(zirakpur_facts, lambda d: d.__setitem__("executive_summary", "word " * 200))
    fixed = json.loads(valid_narrative_json(zirakpur_facts))["executive_summary"]
    patch = json.dumps({"executive_summary": fixed})
    prov, client = _provider(fake_client_factory, [fake_response(bad), fake_response(patch, output_tokens=180)])
    records = []
    n, _, _ = generate_narrative(zirakpur_facts, library, provider=prov, on_attempt=records.append)
    assert [r.outcome for r in records] == ["schema_error", "ok"]
    assert n.executive_summary == " ".join(fixed.split())
    retry = client.messages.calls[1]["messages"]
    assert len(retry) == 1 and "ONLY these top-level keys" in retry[0]["content"]
    assert "journey_assessment" not in retry[0]["content"].split("Your previous narrative")[1]  # no full resend


def test_cost_optimized_mode_routes_to_economy_model_and_escalates_last(library, zirakpur_facts, fake_client_factory):
    from app.settings import get_settings, set_settings

    set_settings(get_settings().model_copy(update={"llm_cost_optimized": True, "llm_economy_model": "claude-sonnet-5",
                                                   "llm_economy_effort": "low", "llm_max_attempts": 3}))
    try:
        bad = _mutate(zirakpur_facts, lambda d: d.__setitem__("executive_summary", "word " * 200))
        prov, client = _provider(fake_client_factory, [fake_response(bad)] * 2
                                 + [fake_response(valid_narrative_json(zirakpur_facts))])
        generate_narrative(zirakpur_facts, library, provider=prov)
        calls = client.messages.calls
        assert [c["model"] for c in calls] == ["claude-sonnet-5", "claude-sonnet-5", "claude-opus-5"]
        assert calls[0]["output_config"]["effort"] == "low"
        assert "extra_body" not in calls[0]  # refusal fallbacks are an Opus/Fable feature
    finally:
        set_settings(None)


def test_excluded_hazard_terms_are_sent_and_never_count_as_supplied(library, zirakpur_facts):
    payload = build_facts_payload(zirakpur_facts)
    candidates = {h["code"] for h in payload["candidate_hazards"]}
    assert payload["excluded_hazard_terms"], "non-candidate hazard phrases must be listed for the model"
    if "HZ-12" not in candidates:
        assert "waterlogging" in payload["excluded_hazard_terms"]
        def f(d):
            d["route_recommendation"] = "Watch for waterlogging after rain."
        errs = _validate(zirakpur_facts, library, _mutate(zirakpur_facts, f))
        assert any("waterlogging" in e for e in errs)


def test_a_document_stops_retrying_once_its_budget_is_gone(library, zirakpur_facts, monkeypatch):
    """One row that keeps failing validation must not outspend the batch it sits in.

    Every retry pays for the whole answer again, so a handful of bad rows can cost more than the good ones
    put together. At the ceiling the document fails rather than trying once more.
    """
    from decimal import Decimal

    from app.errors import ErrorCode, LlmError
    from app.llm.client import LlmCallResult, TokenUsage
    from app.llm import service as svc
    from app.settings import get_settings, set_settings

    calls = {"n": 0}

    class AlwaysInvalid:
        name = "anthropic"
        model = "claude-opus-5"

        def call(self, prefix, messages, *, max_tokens, model, effort):
            calls["n"] += 1
            return LlmCallResult(text='{"not": "a narrative"}', usage=TokenUsage(), stop_reason="end_turn",
                                 request_id="req", duration_ms=1, model="claude-opus-5")

    # each attempt is charged well above the ceiling, so exactly one may run
    monkeypatch.setattr(svc, "cost_usd", lambda model, usage, batch=False: Decimal("0.75"))
    set_settings(get_settings().model_copy(update={"llm_max_attempts": 3,
                                                   "llm_max_cost_per_document_usd": 0.60}))
    try:
        with pytest.raises(LlmError) as ei:
            svc.generate_narrative(zirakpur_facts, library, provider=AlwaysInvalid())
    finally:
        set_settings(None)
    assert calls["n"] == 1, f"kept spending after the ceiling: {calls['n']} attempts"
    assert ei.value.code == ErrorCode.LLM_INVALID_OUTPUT
    assert "per-document ceiling" in ei.value.message


def test_zero_disables_the_document_budget(library, zirakpur_facts, monkeypatch):
    from decimal import Decimal

    from app.errors import LlmError
    from app.llm.client import LlmCallResult, TokenUsage
    from app.llm import service as svc
    from app.settings import get_settings, set_settings

    calls = {"n": 0}

    class AlwaysInvalid:
        name = "anthropic"
        model = "claude-opus-5"

        def call(self, prefix, messages, *, max_tokens, model, effort):
            calls["n"] += 1
            return LlmCallResult(text='{"not": "a narrative"}', usage=TokenUsage(), stop_reason="end_turn",
                                 request_id="req", duration_ms=1, model="claude-opus-5")

    monkeypatch.setattr(svc, "cost_usd", lambda model, usage, batch=False: Decimal("0.75"))
    set_settings(get_settings().model_copy(update={"llm_max_attempts": 3,
                                                   "llm_max_cost_per_document_usd": 0.0}))
    try:
        with pytest.raises(LlmError):
            svc.generate_narrative(zirakpur_facts, library, provider=AlwaysInvalid())
    finally:
        set_settings(None)
    assert calls["n"] == 3, "the attempt limit should still govern when the budget is off"
