import base64

from nacl.signing import SigningKey

from app import crypto

CERT = {"allowance_id": 1, "per_tap_max": 5.0, "max_vouchers": 4, "blocked_categories": ["alcohol"]}


def test_canonical_json_is_sorted_compact_and_int_normalised():
    assert crypto.canonical_json({"b": 1, "a": 5.0, "c": 2.5}) == b'{"a":5,"b":1,"c":2.5}'


def test_server_sign_and_verify_ignores_key_order():
    sig = crypto.sign(CERT)
    assert crypto.verify_server_sig(CERT, sig)
    assert crypto.verify_server_sig(dict(reversed(list(CERT.items()))), sig)


def test_tampered_cert_is_rejected():
    sig = crypto.sign(CERT)
    assert not crypto.verify_server_sig({**CERT, "max_vouchers": 400}, sig)
    assert not crypto.verify_server_sig({**CERT, "blocked_categories": []}, sig)


def test_garbage_signature_is_rejected_not_raised():
    assert not crypto.verify_server_sig(CERT, "not-base64!!")
    assert not crypto.verify_server_sig(CERT, base64.b64encode(b"short").decode())


def test_device_voucher_signature():
    device = SigningKey.generate()
    pub = base64.b64encode(bytes(device.verify_key)).decode()
    voucher = {"allowance_id": 1, "serial": 3, "issued_at": "2026-11-04T10:00:00Z"}
    sig = base64.b64encode(device.sign(crypto.canonical_json(voucher)).signature).decode()
    assert crypto.verify_device_sig(pub, voucher, sig)
    assert not crypto.verify_device_sig(pub, {**voucher, "serial": 4}, sig)
    other = base64.b64encode(bytes(SigningKey.generate().verify_key)).decode()
    assert not crypto.verify_device_sig(other, voucher, sig)
    assert not crypto.verify_server_sig(voucher, sig)  # device sig is not a server sig


def test_server_key_generated_once_then_reused(tmp_path):
    path = tmp_path / "keys" / "k.key"
    k1 = crypto.load_signing_key("", path)
    assert path.exists()
    assert bytes(crypto.load_signing_key("", path)) == bytes(k1)
    seed = base64.b64encode(bytes(range(32))).decode()
    assert bytes(crypto.load_signing_key(seed, tmp_path / "other")) == bytes(range(32))


def test_canonical_json_matches_the_shared_fixture_used_by_the_frontend():
    """frontend/lib/canonical.test.ts checks the SAME file; if either side drifts, one test fails."""
    import json
    from pathlib import Path
    fx = json.loads((Path(__file__).resolve().parents[2] / "shared" / "canonical_fixture.json").read_text(encoding="utf-8"))
    assert crypto.canonical_json(fx["input"]).decode("ascii") == fx["expected"]


def test_signed_fixture_verifies_with_the_backend_and_frontend_uses_the_same_bytes():
    """Cert signed by the server key, voucher by the device key (fixed seeds). The frontend test
    verifies the same signatures with @noble/ed25519 and re-signs the voucher to get the same bytes."""
    import json
    from pathlib import Path
    fx = json.loads((Path(__file__).resolve().parents[2] / "shared" / "signed_fixture.json").read_text(encoding="utf-8"))
    assert crypto._verify(fx["server_pubkey_b64"], fx["certificate"], fx["cert_signature"])
    assert crypto.verify_device_sig(fx["certificate"]["device_pubkey"], fx["voucher"], fx["device_signature"])
    assert not crypto.verify_device_sig(fx["certificate"]["device_pubkey"], {**fx["voucher"], "serial": 3},
                                        fx["device_signature"])
