from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.agents.settlement_agent import SettlementReport, settle
from app.db import get_session
from app.models import Merchant, Voucher
from app.services.voucher_verify import OfflinePayment, verify_offline_payment

router = APIRouter(prefix="/vouchers", tags=["vouchers"])


class VerifyIn(BaseModel):
    payment: OfflinePayment
    merchant_id: int


class SettleIn(BaseModel):
    merchant_id: int
    payments: list[OfflinePayment]


@router.post("/verify")
def verify(body: VerifyIn, session: Session = Depends(get_session)) -> dict[str, object]:
    """Dev helper: the same offline check the merchant phone runs."""
    merchant = session.get(Merchant, body.merchant_id)
    if not merchant:
        raise HTTPException(404, f"Merchant {body.merchant_id} not found")
    reasons = verify_offline_payment(body.payment, merchant)
    return {"valid": not reasons, "reasons": reasons}


@router.post("/settle")
async def settle_vouchers(body: SettleIn, session: Session = Depends(get_session)) -> SettlementReport:
    try:
        return await settle(session, body.merchant_id, body.payments)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


@router.get("")
def list_vouchers(
    allowance_id: Optional[int] = None,
    merchant_id: Optional[int] = None,
    session: Session = Depends(get_session),
) -> list[Voucher]:
    q = select(Voucher).order_by(Voucher.id)  # type: ignore[arg-type]
    if allowance_id is not None:
        q = q.where(Voucher.allowance_id == allowance_id)
    if merchant_id is not None:
        q = q.where(Voucher.merchant_id == merchant_id)
    return list(session.exec(q).all())
