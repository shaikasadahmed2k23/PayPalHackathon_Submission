import logging

import pytest
from pydantic import BaseModel

from app import llm
from app.config import get_settings
from app.llm import LLMError, ProviderUnavailable, RateLimitError, generate_json


class Out(BaseModel):
    value: int


@pytest.fixture
def both(monkeypatch):
    """Gemini primary, Groq fallback, both 'configured'."""
    s = get_settings()
    monkeypatch.setattr(s, "gemini_api_key", "g")
    monkeypatch.setattr(s, "groq_api_key", "q")
    return s


def script(monkeypatch, plan):
    """plan: provider -> list of answers (str = raw output, Exception = raised). Records the calls."""
    calls = []

    def fake(provider, system, user, schema):
        calls.append(provider)
        answer = plan[provider].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(llm, "_call_provider", fake)
    return calls


def test_primary_answers_without_touching_fallback(both, monkeypatch, caplog):
    calls = script(monkeypatch, {"gemini": ['{"value": 1}'], "groq": []})
    with caplog.at_level(logging.INFO, logger="leashpay.llm"):
        assert generate_json("s", "u", Out).value == 1
    assert calls == ["gemini"] and "LLM answered via gemini" in caplog.text


def test_rate_limit_falls_back_immediately_with_no_retry(both, monkeypatch, caplog):
    calls = script(monkeypatch, {"gemini": [RateLimitError("429 RESOURCE_EXHAUSTED", 49)],
                                 "groq": ['{"value": 2}']})
    with caplog.at_level(logging.INFO, logger="leashpay.llm"):
        assert generate_json("s", "u", Out).value == 2
    assert calls == ["gemini", "groq"]  # Gemini was NOT retried
    assert "LLM answered via groq" in caplog.text


def test_rate_limited_provider_is_skipped_during_cooldown(both, monkeypatch):
    calls = script(monkeypatch, {"gemini": [RateLimitError("429", 49)],
                                 "groq": ['{"value": 1}', '{"value": 2}']})
    generate_json("s", "u", Out)
    generate_json("s", "u", Out)
    assert calls == ["gemini", "groq", "groq"]  # second call never even tries Gemini


def test_other_errors_retry_once_on_same_provider_then_fall_back(both, monkeypatch):
    calls = script(monkeypatch, {"gemini": [LLMError("boom"), LLMError("boom again")],
                                 "groq": ['{"value": 3}']})
    assert generate_json("s", "u", Out).value == 3
    assert calls == ["gemini", "gemini", "groq"]


def test_invalid_output_retries_once_then_falls_back(both, monkeypatch):
    calls = script(monkeypatch, {"gemini": ["nope", "still nope"], "groq": ['{"value": 4}']})
    assert generate_json("s", "u", Out).value == 4
    assert calls == ["gemini", "gemini", "groq"]


def test_invalid_then_valid_stays_on_primary(both, monkeypatch):
    calls = script(monkeypatch, {"gemini": ["nope", '{"value": 5}'], "groq": []})
    assert generate_json("s", "u", Out).value == 5 and calls == ["gemini", "gemini"]


def test_all_providers_failing_raises_one_llm_error(both, monkeypatch):
    script(monkeypatch, {"gemini": [RateLimitError("429 gemini")], "groq": [LLMError("a"), LLMError("b")]})
    with pytest.raises(LLMError) as e:
        generate_json("s", "u", Out)
    assert "All LLM providers failed" in str(e.value) and "429 gemini" in str(e.value)


def test_fallback_needs_a_key_and_can_be_disabled_or_swapped(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "gemini_api_key", "g")
    assert llm.provider_order() == ["gemini"]  # no Groq key -> no fallback
    monkeypatch.setattr(s, "groq_api_key", "q")
    assert llm.provider_order() == ["gemini", "groq"]
    monkeypatch.setattr(s, "llm_fallback", "none")
    assert llm.provider_order() == ["gemini"]
    monkeypatch.setattr(s, "llm_provider", "groq")
    monkeypatch.setattr(s, "llm_fallback", "")
    assert llm.provider_order() == ["groq", "gemini"]
    monkeypatch.setattr(s, "llm_fallback", "groq")
    assert llm.provider_order() == ["groq"]


def test_missing_key_on_primary_goes_straight_to_fallback(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "groq_api_key", "q")  # no Gemini key
    monkeypatch.setattr(s, "llm_provider", "gemini")
    calls = script(monkeypatch, {"gemini": [ProviderUnavailable("GEMINI_API_KEY is not set")],
                                 "groq": ['{"value": 6}']})
    assert generate_json("s", "u", Out).value == 6 and calls == ["gemini", "groq"]


def test_real_gemini_429_is_recognised_as_a_rate_limit(monkeypatch):
    class ClientError(Exception):
        code = 429

    def raising(system, user, schema):
        raise ClientError("429 RESOURCE_EXHAUSTED. retryDelay: 49s")

    monkeypatch.setattr(llm, "_call_gemini", raising)
    with pytest.raises(RateLimitError) as e:
        llm._call_provider("gemini", "s", "u", Out)
    assert e.value.retry_after == 49.0


def test_gemini_call_disables_automatic_function_calling(monkeypatch):
    seen = {}

    class FakeModels:
        def generate_content(self, model, contents, config):
            seen["config"] = config

            class R:
                text = '{"value": 1}'
            return R()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    import google.genai as genai
    monkeypatch.setattr(genai, "Client", FakeClient)
    monkeypatch.setattr(get_settings(), "gemini_api_key", "g")
    assert llm._call_gemini("sys", "user", Out) == '{"value": 1}'
    assert seen["config"].automatic_function_calling.disable is True
