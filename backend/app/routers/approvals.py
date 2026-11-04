from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app import audit
from app.db import get_session
from app.models import Allowance, Approval, ApprovalStatus
from app.services.spend import SpendResult, make_request_id, spend

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _get_pending(session: Session, approval_id: int) -> Approval:
    ap = session.get(Approval, approval_id)
    if not ap:
        raise HTTPException(404, f"Approval {approval_id} not found")
    if ap.status != ApprovalStatus.PENDING:
        raise HTTPException(409, f"Approval is already {ap.status.value}")
    return ap


@router.get("")
def list_approvals(
    status: Optional[ApprovalStatus] = None, session: Session = Depends(get_session)
) -> list[Approval]:
    q = select(Approval).order_by(Approval.id.desc())  # type: ignore[union-attr]
    if status:
        q = q.where(Approval.status == status)
    return list(session.exec(q).all())


@router.post("/{approval_id}/approve")
async def approve(approval_id: int, session: Session = Depends(get_session)) -> SpendResult:
    ap = _get_pending(session, approval_id)
    allowance = session.get(Allowance, ap.allowance_id)
    assert allowance is not None
    # Same spend path as everything else; only the approval threshold is waived.
    result = await spend(
        session, ap.allowance_id, ap.amount, ap.category, ap.merchant_name, ap.channel,
        source=ap.source, source_id=ap.source_id, request_id=make_request_id(allowance, "approval", ap.id),
        skip_approval=True,
    )
    if result.verdict.decision != "PASS":
        raise HTTPException(
            409, {"message": "Approval can no longer be spent", "reasons": result.verdict.reasons}
        )
    ap.status = ApprovalStatus.APPROVED
    ap.decided_at = datetime.now(timezone.utc)
    session.add(ap)
    session.commit()
    audit.log(session, "user", "approve", verdict="APPROVED", paypal_ref=result.capture_id,
              payload={"approval_id": ap.id, "allowance_id": ap.allowance_id, "amount": ap.amount})
    return result


@router.post("/{approval_id}/deny")
def deny(approval_id: int, session: Session = Depends(get_session)) -> Approval:
    ap = _get_pending(session, approval_id)
    ap.status = ApprovalStatus.DENIED
    ap.decided_at = datetime.now(timezone.utc)
    session.add(ap)
    session.commit()
    audit.log(session, "user", "deny", verdict="DENIED",
              payload={"approval_id": ap.id, "allowance_id": ap.allowance_id, "amount": ap.amount})
    session.refresh(ap)  # audit.log committed, which expired ap
    return ap
