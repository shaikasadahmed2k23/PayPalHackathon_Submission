"""Print a fresh base64 Ed25519 seed for SERVER_SIGNING_KEY: python scripts/gen_signing_key.py

Keep it secret and stable: changing it invalidates every allowance certificate already issued."""
import base64
import os

print(base64.b64encode(os.urandom(32)).decode())
