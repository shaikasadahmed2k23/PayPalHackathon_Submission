import base64
from datetime import datetime, timedelta, timezone

from nacl.signing import SigningKey

from app import crypto
from tests.voucher_helpers import make_cert, make_payment, merchant, z


def reasons(p, category="food"):
    from app.services.voucher_verify import verify_offline_payment
    return verify_offline_payment(p, merchant(category))


def test_valid_payment():
    assert reasons(make_payment()) == []


def test_boundary_values_are_valid():
    assert reasons(make_payment(amount=5.0)) == []  # exactly per_tap_max
    assert reasons(make_payment(serial=3)) == []  # last serial (max_vouchers - 1)


def test_forged_certificate_signature():
    cert, _ = make_cert()
    other = base64.b64encode(SigningKey.generate().sign(crypto.canonical_json(cert)).signature).decode()
    assert "Certificate signature is not valid" in reasons(make_payment(cert=cert, cert_sig=other))[0]


def test_tampered_certificate_fails_signature():
    cert, sig = make_cert()
    cert["max_vouchers"] = 400
    assert any("Certificate signature" in r for r in reasons(make_payment(cert=cert, cert_sig=sig)))


def test_voucher_signed_by_wrong_device():
    r = reasons(make_payment(device=SigningKey.generate()))
    assert r == ["Voucher signature is not valid for this buyer's device"]


def test_voucher_from_other_allowance():
    assert any("does not belong" in r for r in reasons(make_payment(allowance_id=99)))


def test_serial_at_or_beyond_max_vouchers():
    assert any("beyond the 4 vouchers" in r for r in reasons(make_payment(serial=4)))
    assert any("invalid" in r for r in reasons(make_payment(serial=-1)))


def test_amount_checks():
    assert reasons(make_payment(amount=0)) == ["Amount must be greater than zero"]
    assert reasons(make_payment(amount=5.01)) == ["$5.01 is over the $5.00 per-tap limit"]
    assert reasons(make_payment(amount=1.005)) == ["Amount must be in whole cents"]


def test_issued_after_expiry():
    late = z(datetime.now(timezone.utc) + timedelta(days=10))
    assert any("after the allowance expired" in r for r in reasons(make_payment(issued_at=late)))


def test_blocked_category():
    p = make_payment(allowed=(), blocked=("alcohol",))
    assert reasons(p, "alcohol") == ["Alcohol purchases are blocked by the buyer's rules"]


def test_category_not_in_allowed_list():
    assert reasons(make_payment(allowed=("food",)), "electronics") == [
        "Electronics is not in the buyer's allowed categories"]


def test_empty_allowed_list_allows_any_unblocked_category():
    assert reasons(make_payment(allowed=()), "electronics") == []


def test_malformed_payment_does_not_raise():
    p = make_payment()
    p.voucher.pop("serial")
    assert any("malformed" in r for r in reasons(p))
