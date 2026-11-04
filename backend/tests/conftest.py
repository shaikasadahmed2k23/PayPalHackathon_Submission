import pytest
from nacl.signing import SigningKey

from app import crypto

FIXED_KEY = SigningKey(bytes(range(32)))


@pytest.fixture(autouse=True)
def fixed_server_key(monkeypatch):
    """Never touch backend/keys/ from tests."""
    monkeypatch.setattr(crypto, "get_signing_key", lambda: FIXED_KEY)


@pytest.fixture(autouse=True)
def isolated_llm(monkeypatch):
    """Tests must never reach a real LLM, whatever keys are in backend/.env."""
    from app import llm
    from app.config import get_settings

    s = get_settings()
    for name, value in (("gemini_api_key", ""), ("groq_api_key", ""), ("llm_provider", "gemini"),
                        ("llm_fallback", "")):
        monkeypatch.setattr(s, name, value)
    llm._cooldown.clear()
