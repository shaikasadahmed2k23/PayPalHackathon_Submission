import pytest
from pydantic import ValidationError

from app import llm
from app.llm import LLMError, generate_json
from app.policy import classifier, parser
from app.policy.schema import Policy, SpendRequest

GOOD = """{"policy": {"per_tx_max": 15, "require_approval_above": 8,
  "allowed_categories": ["Food", " TRANSPORT"], "blocked_categories": ["alcohol"]},
  "ambiguities": ["No daily limit given; assumed $60."]}"""


def test_parse_rules_builds_summary_and_applies_defaults(monkeypatch):
    monkeypatch.setattr(llm, "_call_provider", lambda s, u, schema: GOOD)
    result = parser.parse_rules("Food and transport only, max $15, never alcohol, ask above $8")
    p = result.policy
    assert p.per_tx_max == 15 and p.require_approval_above == 8
    assert p.daily_max == 60 and p.offline.per_tap_max == 5  # defaults
    assert p.allowed_categories == ["food", "transport"]  # normalised to lowercase
    assert "Up to $15.00 per purchase" in result.summary
    assert "Blocked categories: alcohol" in result.summary
    assert result.ambiguities == ["No daily limit given; assumed $60."]


def test_parse_rules_receives_user_text(monkeypatch):
    seen = {}

    def fake(system, user, schema):
        seen["system"], seen["user"] = system, user
        return GOOD

    monkeypatch.setattr(llm, "_call_provider", fake)
    parser.parse_rules("hello rules")
    assert "hello rules" in seen["user"]
    assert "gambling" in seen["system"]  # category vocabulary is in the prompt


def test_retry_once_on_invalid_then_succeed(monkeypatch):
    answers = iter(['{"policy": {"per_tx_max": -5}}', GOOD])
    monkeypatch.setattr(llm, "_call_provider", lambda s, u, schema: next(answers))
    assert parser.parse_rules("x").policy.per_tx_max == 15


def test_raises_llm_error_after_two_invalid(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "_call_provider", lambda s, u, schema: calls.append(1) or "not json")
    with pytest.raises(LLMError):
        generate_json("s", "u", parser._LLMParse)
    assert len(calls) == 2


def test_schema_rejects_bad_values():
    with pytest.raises(ValidationError):
        Policy(per_tx_max=-1)
    with pytest.raises(ValidationError):
        Policy(blocked_categories=["spaceships"])
    with pytest.raises(ValidationError):
        SpendRequest(amount=5, category="food", merchant_name="x", channel="telepathy")


def test_classifier_uses_cache_and_falls_back(monkeypatch):
    classifier._cache.clear()
    calls = []

    def fake(system, user, schema):
        calls.append(user)
        return '{"category": "Groceries"}'

    monkeypatch.setattr(llm, "_call_provider", fake)
    assert classifier.classify_category("Bananas") == "groceries"
    assert classifier.classify_category(" bananas ") == "groceries"
    assert len(calls) == 1

    def boom(system, user, schema):
        raise LLMError("down")

    monkeypatch.setattr(llm, "_call_provider", boom)
    assert classifier.classify_category("Mystery thing") == "other"
