"""Regenerates shared/canonical_fixture.json from the BACKEND canonical_json. Run from backend/."""
import json, sys
sys.path.insert(0, ".")
from app.crypto import canonical_json

inp = json.loads(r'''{
  "serial": 2, "issued_at": "2026-11-05T12:00:00Z", "per_tap_max": 5.0, "price": 4.5, "tiny": 0.25,
  "zero": 0.0, "neg": -3.0, "flag": true, "nothing": null,
  "name": "Café ☃ 😀 \"q\" \\ \n \u007f",
  "allowed_categories": ["food", "transport"], "blocked_categories": [],
  "nested": {"z": 1.0, "a": [3.0, {"y": 2, "b": 1.5}], "m": {}}
}''')
exp = canonical_json(inp).decode("ascii")
json.dump({"input": inp, "expected": exp}, open("../shared/canonical_fixture.json", "w", encoding="utf-8"), indent=1)
print(exp)

# ---- signed vector: server-signed cert + device-signed voucher with FIXED seeds (Ed25519 is deterministic)
import base64
from nacl.signing import SigningKey

server = SigningKey(bytes(range(32)))
device = SigningKey(bytes(range(32, 64)))
b64 = lambda b: base64.b64encode(bytes(b)).decode()  # noqa: E731
cert = {"allowance_id": 7, "device_pubkey": b64(device.verify_key), "per_tap_max": 5.0, "max_vouchers": 6,
        "allowed_categories": ["food", "transport"], "blocked_categories": ["alcohol"],
        "expires_at": "2026-11-08T12:00:00Z"}
voucher = {"allowance_id": 7, "serial": 2, "issued_at": "2026-11-05T12:00:00Z"}
sign = lambda k, o: b64(k.sign(canonical_json(o)).signature)  # noqa: E731
json.dump({
    "server_seed_b64": b64(bytes(range(32))), "device_seed_b64": b64(bytes(range(32, 64))),
    "server_pubkey_b64": b64(server.verify_key), "certificate": cert, "cert_signature": sign(server, cert),
    "voucher": voucher, "device_signature": sign(device, voucher),
}, open("../shared/signed_fixture.json", "w", encoding="utf-8"), indent=1)
print("signed fixture written")
