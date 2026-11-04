import base64
import binascii
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import floor
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app import audit, crypto
from app.config import get_settings
from app.db import get_session
from app.models import Allowance, AllowanceStatus, Policy as PolicyRow, Voucher, VoucherStatus
from app.paypal import get_paypal_client
from app.policy.schema import Cat, Policy, SpendRequest
from app.services.spend import SpendResult, compute_spent_today, make_request_id, spend

router = APIRouter(prefix="/allowance", tags=["allowance"])

ALLOWANCE_TTL = timedelta(hours=72)


class CreateIn(BaseModel):
    policy_id: int
    amount: Annotated[float, Field(gt=0)]


class AuthorizeIn(BaseModel):
    device_pubkey_b64: str


class TestSpendIn(BaseModel):
    amount: Annotated[float, Field(gt=0)]
    category: Cat
    merchant_name: str
    channel: str = "online"


def _get(session: Session, allowance_id: int) -> Allowance:
    a = session.get(Allowance, allowance_id)
    if not a:
        raise HTTPException(404, f"Allowance {allowance_id} not found")
    return a


def _max_vouchers(amount: float, per_tap_max: float) -> int:
    if per_tap_max <= 0:
        return 0
    return floor(Decimal(str(amount)) / Decimal(str(per_tap_max)))


@router.post("")
async def create_allowance(
    body: CreateIn, session: Session = Depends(get_session)
) -> dict[str, Any]:
    if not session.get(PolicyRow, body.policy_id):
        raise HTTPException(404, f"Policy {body.policy_id} not found")
    allowance = Allowance(policy_id=body.policy_id, amount=body.amount)
    session.add(allowance)
    session.commit()
    session.refresh(allowance)
    front = get_settings().frontend_url.rstrip("/")
    try:
        order_id, approve_url = await get_paypal_client().create_allowance_order(
            body.amount,
            return_url=f"{front}/setup/return?allowance_id={allowance.id}",
            cancel_url=f"{front}/setup",
        )
    except Exception:
        session.delete(allowance)  # don't leave an allowance with no PayPal order
        session.commit()
        raise
    allowance.paypal_order_id = order_id
    session.add(allowance)
    session.commit()
    audit.log(session, "user", "create_allowance", paypal_ref=order_id,
              payload={"allowance_id": allowance.id, "amount": body.amount})
    return {"allowance_id": allowance.id, "approve_url": approve_url}


@router.post("/{allowance_id}/authorize")
async def authorize_allowance(
    allowance_id: int, body: AuthorizeIn, session: Session = Depends(get_session)
) -> dict[str, Any]:
    allowance = _get(session, allowance_id)
    if allowance.status != AllowanceStatus.CREATED or not allowance.paypal_order_id:
        raise HTTPException(409, f"Allowance is {allowance.status.value}, cannot authorize")
    try:
        if len(base64.b64decode(body.device_pubkey_b64, validate=True)) != 32:
            raise ValueError
    except (binascii.Error, ValueError):
        raise HTTPException(422, "device_pubkey_b64 must be a base64 32-byte Ed25519 public key")

    policy = Policy.model_validate(session.get(PolicyRow, allowance.policy_id).policy_json)  # type: ignore[union-attr]
    authorization_id = await get_paypal_client().authorize_order(allowance.paypal_order_id)

    expires_at = datetime.now(timezone.utc) + ALLOWANCE_TTL
    per_tap = policy.offline.per_tap_max
    cert = {
        "allowance_id": allowance.id,
        "device_pubkey": body.device_pubkey_b64,
        "per_tap_max": per_tap,
        "max_vouchers": _max_vouchers(allowance.amount, per_tap) if policy.offline.enabled else 0,
        "allowed_categories": policy.allowed_categories,
        "blocked_categories": policy.blocked_categories,
        "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    signature = crypto.sign(cert)

    allowance.authorization_id = authorization_id
    allowance.device_pubkey = body.device_pubkey_b64
    allowance.certificate = {"certificate": cert, "signature": signature}
    allowance.expires_at = expires_at
    allowance.status = AllowanceStatus.ACTIVE
    session.add(allowance)
    session.commit()
    audit.log(session, "user", "authorize_allowance", paypal_ref=authorization_id,
              payload={"allowance_id": allowance_id, "certificate": cert})
    return {"certificate": cert, "signature": signature, "server_pubkey": crypto.server_pubkey_b64()}


@router.get("/{allowance_id}")
def get_allowance(allowance_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    a = _get(session, allowance_id)
    policy_row = session.get(PolicyRow, a.policy_id)
    envelope: Optional[dict[str, Any]] = a.certificate
    return {
        "allowance_id": a.id,
        "status": a.status.value,
        "amount": a.amount,
        "captured_total": a.captured_total,
        "remaining": round(a.amount - a.captured_total, 2),
        "spent_today": compute_spent_today(session, allowance_id),
        "expires_at": a.expires_at,
        "authorization_id": a.authorization_id,
        "policy": policy_row.policy_json if policy_row else None,
        "certificate": envelope["certificate"] if envelope else None,
        "signature": envelope["signature"] if envelope else None,
    }


@router.post("/{allowance_id}/test-spend")
async def test_spend(
    allowance_id: int, body: TestSpendIn, session: Session = Depends(get_session)
) -> SpendResult:
    """Dev/demo endpoint: runs the real spend engine without a shopping agent or voucher."""
    allowance = _get(session, allowance_id)
    if body.channel not in ("online", "offline"):
        raise HTTPException(422, "channel must be 'online' or 'offline'")
    sid = uuid.uuid4().hex
    return await spend(
        session, allowance_id, body.amount, body.category, body.merchant_name, body.channel,
        source="manual", source_id=sid, request_id=make_request_id(allowance, "manual", sid),
    )


def offline_status(session: Session, a: Allowance) -> dict[str, Any]:
    """What the server knows about offline vouchers. Unsynced vouchers are invisible to it,
    so unknown_serials is an upper bound on the vouchers merchants may still hold."""
    max_vouchers = int(((a.certificate or {}).get("certificate") or {}).get("max_vouchers", 0))
    rows = session.exec(select(Voucher).where(Voucher.allowance_id == a.id)).all()
    paid = (VoucherStatus.SETTLED, VoucherStatus.SETTLED_PAYOUT_PENDING)
    known = {v.serial for v in rows}
    return {
        "max_vouchers": max_vouchers,
        "settled": sum(v.status in paid for v in rows),
        "rejected": sum(v.status == VoucherStatus.REJECTED for v in rows),
        "unknown_serials": [i for i in range(max_vouchers) if i not in known],
    }


@router.get("/{allowance_id}/offline-status")
def get_offline_status(allowance_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    return offline_status(session, _get(session, allowance_id))


@router.post("/{allowance_id}/close")
async def close_allowance(
    allowance_id: int, force: bool = False, session: Session = Depends(get_session)
) -> dict[str, Any]:
    a = _get(session, allowance_id)
    if a.status != AllowanceStatus.ACTIVE or not a.authorization_id:
        raise HTTPException(409, f"Allowance is {a.status.value}, cannot close")
    status = offline_status(session, a)
    unknown = status["unknown_serials"]
    if unknown and not force:
        raise HTTPException(409, {
            "message": (
                f"Up to {len(unknown)} offline vouchers not yet synced by merchants - "
                "closing now means those merchants can't be paid. Pass force=true to close anyway."
            ),
            **status,
        })
    # A PayPalError here propagates as a 502 carrying PayPal's exact error; status stays ACTIVE.
    await get_paypal_client().void(a.authorization_id)
    a.status = AllowanceStatus.CLOSED
    session.add(a)
    session.commit()
    audit.log(session, "user", "close_allowance", paypal_ref=a.authorization_id,
              payload={"allowance_id": allowance_id, "captured_total": a.captured_total,
                       "force": force, "unknown_serials": unknown})
    return {"allowance_id": allowance_id, "status": a.status.value, "captured_total": a.captured_total}
