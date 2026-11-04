import base64
from datetime import datetime, timedelta, timezone

from nacl.signing import SigningKey

from app import crypto
from app.models import Merchant
from app.services.voucher_verify import OfflinePayment

DEVICE = SigningKey(bytes([7] * 32))
DEVICE_PUB = base64.b64encode(bytes(DEVICE.verify_key)).decode()


def z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_cert(allowance_id=1, per_tap_max=5.0, max_vouchers=4, allowed=("food",), blocked=(), expires_in_h=72):
    cert = {
        "allowance_id": allowance_id, "device_pubkey": DEVICE_PUB, "per_tap_max": per_tap_max,
        "max_vouchers": max_vouchers, "allowed_categories": list(allowed),
        "blocked_categories": list(blocked),
        "expires_at": z(datetime.now(timezone.utc) + timedelta(hours=expires_in_h)),
    }
    return cert, crypto.sign(cert)


def make_payment(merchant_id=1, serial=0, amount=3.0, cert=None, cert_sig=None, device=DEVICE,
                 allowance_id=None, issued_at=None, **cert_kw) -> OfflinePayment:
    if cert is None:
        cert, cert_sig = make_cert(**cert_kw)
    voucher = {
        "allowance_id": cert["allowance_id"] if allowance_id is None else allowance_id,
        "serial": serial,
        "issued_at": issued_at or z(datetime.now(timezone.utc)),
    }
    sig = base64.b64encode(device.sign(crypto.canonical_json(voucher)).signature).decode()
    return OfflinePayment(
        certificate=cert, cert_signature=cert_sig, voucher=voucher, device_signature=sig,
        amount=amount, merchant_id=merchant_id, scanned_at=z(datetime.now(timezone.utc)),
    )


def merchant(category="food", id=1) -> Merchant:
    return Merchant(id=id, name="Chai Corner", category=category, paypal_email="m@test.com")
