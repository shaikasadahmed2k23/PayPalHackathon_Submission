"""Offline payment verification. Pure (no DB, no network): the merchant phone runs the same checks."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app import crypto
from app.models import Merchant


class OfflinePayment(BaseModel):
    # certificate/voucher stay raw dicts: signatures cover their exact content.
    certificate: dict[str, Any]
    cert_signature: str
    voucher: dict[str, Any]
    device_signature: str
    amount: float
    merchant_id: int
    scanned_at: str


def parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def cert_signature_ok(p: OfflinePayment) -> bool:
    return crypto.verify_server_sig(p.certificate, p.cert_signature)


def device_signature_ok(p: OfflinePayment) -> bool:
    pub = p.certificate.get("device_pubkey")
    return isinstance(pub, str) and crypto.verify_device_sig(pub, p.voucher, p.device_signature)


def is_authentic(p: OfflinePayment) -> bool:
    """Both signatures valid: the server issued this certificate and the device issued this voucher."""
    return cert_signature_ok(p) and device_signature_ok(p)


def verify_offline_payment(p: OfflinePayment, merchant: Merchant) -> list[str]:
    """Return plain-English failure reasons; an empty list means the payment is valid."""
    reasons: list[str] = []
    if not cert_signature_ok(p):
        reasons.append("Certificate signature is not valid (it was not issued by LeashPay)")
    if not device_signature_ok(p):
        reasons.append("Voucher signature is not valid for this buyer's device")

    try:
        cert, voucher = p.certificate, p.voucher
        serial = voucher["serial"]
        max_vouchers = cert["max_vouchers"]
        per_tap_max = Decimal(str(cert["per_tap_max"]))
        allowed = cert["allowed_categories"]
        blocked = cert["blocked_categories"]
        expires_at = parse_ts(cert["expires_at"])
        issued_at = parse_ts(voucher["issued_at"])
        if voucher["allowance_id"] != cert["allowance_id"]:
            reasons.append("Voucher does not belong to the allowance on the certificate")
        if isinstance(serial, bool) or not isinstance(serial, int) or serial < 0:
            reasons.append("Voucher serial number is invalid")
        elif serial >= max_vouchers:
            reasons.append(f"Voucher #{serial} is beyond the {max_vouchers} vouchers this allowance allows")
        if issued_at > expires_at:
            reasons.append("Voucher was issued after the allowance expired")
        if not (isinstance(allowed, list) and isinstance(blocked, list)):
            raise ValueError("category lists")
    except (KeyError, TypeError, ValueError, ArithmeticError) as e:
        reasons.append(f"Payment is malformed ({type(e).__name__}: {e})")
        return reasons

    amount = Decimal(str(p.amount))
    if amount <= 0:
        reasons.append("Amount must be greater than zero")
    elif amount != amount.quantize(Decimal("0.01")):
        reasons.append("Amount must be in whole cents")
    elif amount > per_tap_max:
        reasons.append(f"${amount:,.2f} is over the ${per_tap_max:,.2f} per-tap limit")

    label = merchant.category.capitalize()
    if merchant.category in blocked:
        reasons.append(f"{label} purchases are blocked by the buyer's rules")
    elif allowed and merchant.category not in allowed:
        reasons.append(f"{label} is not in the buyer's allowed categories")
    return reasons
