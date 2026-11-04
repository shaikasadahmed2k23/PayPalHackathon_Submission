import pytest
from nacl.signing import SigningKey

from app import crypto

FIXED_KEY = SigningKey(bytes(range(32)))


@pytest.fixture(autouse=True)
def fixed_server_key(monkeypatch):
    """Never touch backend/keys/ from tests."""
    monkeypatch.setattr(crypto, "get_signing_key", lambda: FIXED_KEY)
