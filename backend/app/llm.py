"""All LLM calls live here and nowhere else."""

import json
import logging
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import get_settings

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger("leashpay.llm")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class LLMError(Exception):
    """The LLM call failed or never produced valid output."""


def _call_gemini(system: str, user: str, schema: type[BaseModel]) -> str:
    from google import genai
    from google.genai import types

    s = get_settings()
    if not s.gemini_api_key:
        raise LLMError("GEMINI_API_KEY is not set")
    client = genai.Client(api_key=s.gemini_api_key)
    resp = client.models.generate_content(
        model=s.gemini_model,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0,
        ),
    )
    if not resp.text:
        raise LLMError("Gemini returned an empty response")
    return resp.text


def _call_groq(system: str, user: str, schema: type[BaseModel]) -> str:
    s = get_settings()
    if not s.groq_api_key:
        raise LLMError("GROQ_API_KEY is not set")
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
    if resp.is_error:
        raise LLMError(f"Groq error {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"]


def _call_provider(system: str, user: str, schema: type[BaseModel]) -> str:
    provider = get_settings().llm_provider.lower()
    try:
        if provider == "gemini":
            return _call_gemini(system, user, schema)
        if provider == "groq":
            return _call_groq(system, user, schema)
    except LLMError as e:
        logger.warning("%s call failed: %s", provider, e)
        raise
    except Exception as e:  # SDK / network errors
        logger.warning("%s call failed: %s: %s", provider, type(e).__name__, e)
        raise LLMError(f"{provider} call failed: {type(e).__name__}: {e}") from e
    raise LLMError(f"Unknown LLM_PROVIDER: {provider!r}")


def generate_json(system: str, user: str, schema: type[T]) -> T:
    """Ask the LLM for JSON matching `schema`. One retry on invalid output."""
    prompt = user
    last_error = ""
    for _ in range(2):
        raw = _call_provider(system, prompt, schema)
        try:
            return schema.model_validate_json(raw)
        except ValidationError as e:
            last_error = str(e)
            logger.warning("LLM returned invalid %s output: %s | raw=%r", schema.__name__, last_error, raw[:500])
            prompt = (
                f"{user}\n\nYour previous answer was invalid:\n{raw}\n"
                f"Validation errors:\n{last_error}\nReturn corrected JSON only."
            )
    raise LLMError(f"LLM returned invalid output twice: {last_error}")
