"""Ed25519 signing for allowance certificates and device-voucher verification."""

import base64
import binascii
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from app.config import BACKEND_DIR, get_settings

KEY_PATH = BACKEND_DIR / "keys" / "server_ed25519.key"


def _plain(obj: Any) -> Any:
    """Integral floats become ints so Python and JS serialise numbers identically (5.0 -> 5)."""
    if isinstance(obj, float) and obj.is_integer():
        return int(obj)
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def canonical_json(obj: Any) -> bytes:
    return json.dumps(_plain(obj), sort_keys=True, separators=(",", ":")).encode()


def load_signing_key(seed_b64: str, key_path: Path) -> SigningKey:
    """Seed from env if given; else from key_path; else generate and save to key_path."""
    if seed_b64:
        return SigningKey(base64.b64decode(seed_b64))
    if key_path.exists():
        return SigningKey(base64.b64decode(key_path.read_text().strip()))
    key = SigningKey.generate()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(base64.b64encode(bytes(key)).decode())
    return key


@lru_cache
def get_signing_key() -> SigningKey:
    return load_signing_key(get_settings().server_signing_key, KEY_PATH)


def server_pubkey_b64() -> str:
    return base64.b64encode(bytes(get_signing_key().verify_key)).decode()


def sign(obj: Any) -> str:
    """Sign canonical JSON of obj with the server key. Returns base64 signature."""
    return base64.b64encode(get_signing_key().sign(canonical_json(obj)).signature).decode()


def _verify(pubkey_b64: str, obj: Any, sig_b64: str) -> bool:
    try:
        VerifyKey(base64.b64decode(pubkey_b64)).verify(
            canonical_json(obj), base64.b64decode(sig_b64)
        )
        return True
    except (BadSignatureError, binascii.Error, ValueError, TypeError):
        return False


def verify_server_sig(obj: Any, sig_b64: str) -> bool:
    return _verify(server_pubkey_b64(), obj, sig_b64)


def verify_device_sig(device_pubkey_b64: str, obj: Any, sig_b64: str) -> bool:
    return _verify(device_pubkey_b64, obj, sig_b64)
