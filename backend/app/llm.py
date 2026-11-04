"""All LLM calls live here and nowhere else.

generate_json() tries the primary provider (LLM_PROVIDER), then the fallback (LLM_FALLBACK):
  - rate limited (HTTP 429 / RESOURCE_EXHAUSTED) or key missing -> no retry, next provider at once,
    and the limited provider is skipped until its cool-down ends;
  - any other error or invalid output -> one retry on the same provider, then the next provider.
"""

import json
import logging
import re
import time
from typing import Optional, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import get_settings

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger("leashpay.llm")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_COOLDOWN_S = 60.0

# provider -> time.monotonic() until which it is skipped after a rate limit
_cooldown: dict[str, float] = {}


class LLMError(Exception):
    """The LLM call failed or never produced valid output."""


class ProviderUnavailable(LLMError):
    """Retrying the same provider is pointless (rate limited, or no API key)."""

    def __init__(self, message: str, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class RateLimitError(ProviderUnavailable):
    pass


def _has_key(provider: str) -> bool:
    s = get_settings()
    return bool({"gemini": s.gemini_api_key, "groq": s.groq_api_key}.get(provider))


def _call_gemini(system: str, user: str, schema: type[BaseModel]) -> str:
    from google import genai
    from google.genai import types

    s = get_settings()
    if not s.gemini_api_key:
        raise ProviderUnavailable("GEMINI_API_KEY is not set")
    client = genai.Client(api_key=s.gemini_api_key)
    resp = client.models.generate_content(
        model=s.gemini_model,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0,
            # We pass no tools; stop the SDK announcing/using automatic function calling.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )
    if not resp.text:
        raise LLMError("Gemini returned an empty response")
    return resp.text


def _call_groq(system: str, user: str, schema: type[BaseModel]) -> str:
    s = get_settings()
    if not s.groq_api_key:
        raise ProviderUnavailable("GROQ_API_KEY is not set")
    # JSON mode has no response schema, so the schema goes in the prompt.
    system = (
        f"{system}\n\nRespond with a single JSON object matching this JSON Schema:\n"
        f"{json.dumps(schema.model_json_schema())}"
    )
    resp = httpx.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {s.groq_api_key}"},
        json={
            "model": s.groq_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=60.0,
    )
    if resp.status_code == 429:
        retry = resp.headers.get("retry-after", "")
        raise RateLimitError(f"Groq rate limited: {resp.text[:200]}",
                             float(retry) if retry.replace(".", "", 1).isdigit() else 0.0)
    if resp.is_error:
        raise LLMError(f"Groq error {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"]


def _is_rate_limited(e: Exception) -> bool:
    text = f"{type(e).__name__} {e}"
    return getattr(e, "code", None) == 429 or "RESOURCE_EXHAUSTED" in text or "429" in text[:80]


def _retry_delay(e: Exception) -> float:
    m = re.search(r"retry\w*\W+(?:delay\W+)?(\d+(?:\.\d+)?)\s*s", str(e), re.I)
    return float(m.group(1)) if m else 0.0


def _call_provider(provider: str, system: str, user: str, schema: type[BaseModel]) -> str:
    try:
        if provider == "gemini":
            return _call_gemini(system, user, schema)
        if provider == "groq":
            return _call_groq(system, user, schema)
    except LLMError:
        raise
    except Exception as e:  # SDK / network errors
        if _is_rate_limited(e):
            raise RateLimitError(f"{provider} rate limited: {type(e).__name__}: {e}", _retry_delay(e)) from e
        raise LLMError(f"{provider} call failed: {type(e).__name__}: {e}") from e
    raise ProviderUnavailable(f"Unknown LLM provider: {provider!r}")


def _attempt(provider: str, system: str, user: str, schema: type[T]) -> T:
    """Up to two tries on one provider. Rate limits / missing keys raise immediately."""
    prompt, last = user, ""
    for _ in range(2):
        try:
            raw = _call_provider(provider, system, prompt, schema)
        except ProviderUnavailable:
            raise
        except LLMError as e:
            last = str(e)
            logger.warning("%s", e)
            continue
        try:
            return schema.model_validate_json(raw)
        except ValidationError as e:
            last = str(e)
            logger.warning("%s returned invalid %s output: %s | raw=%r", provider, schema.__name__, last, raw[:500])
            prompt = (
                f"{user}\n\nYour previous answer was invalid:\n{raw}\n"
                f"Validation errors:\n{last}\nReturn corrected JSON only."
            )
    raise LLMError(f"{provider} failed twice: {last}")


def provider_order() -> list[str]:
    s = get_settings()
    primary = s.llm_provider.lower()
    fallback = s.llm_fallback.lower() or ("groq" if primary == "gemini" else "gemini")
    order = [primary]
    if fallback not in ("none", primary) and _has_key(fallback):
        order.append(fallback)
    return order


def generate_json(system: str, user: str, schema: type[T]) -> T:
    """Ask the LLM for JSON matching `schema`, falling back to the secondary provider."""
    errors: list[str] = []
    for provider in provider_order():
        wait = _cooldown.get(provider, 0.0) - time.monotonic()
        if wait > 0:
            errors.append(f"{provider} skipped (rate limited, {wait:.0f}s left)")
            continue
        try:
            result = _attempt(provider, system, user, schema)
        except RateLimitError as e:
            _cooldown[provider] = time.monotonic() + (e.retry_after or DEFAULT_COOLDOWN_S)
            logger.warning("%s rate limited, switching provider: %s", provider, e)
            errors.append(str(e))
            continue
        except LLMError as e:
            logger.warning("%s failed, switching provider: %s", provider, e)
            errors.append(str(e))
            continue
        logger.info("LLM answered via %s (%s)", provider, schema.__name__)
        return result
    raise LLMError("All LLM providers failed: " + " | ".join(errors))
