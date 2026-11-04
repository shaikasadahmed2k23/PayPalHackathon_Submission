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
