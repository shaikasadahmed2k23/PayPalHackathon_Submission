from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app import audit
from app.db import get_session
from app.models import Allowance, Approval, ApprovalStatus, Purchase, PurchaseStatus
from app.models import AuditLog
from app.services.owner import OwnerHeader, check_owner
from app.services.spend import SpendResult, make_request_id, spend

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _get_pending(session: Session, approval_id: int) -> Approval:
    ap = session.get(Approval, approval_id)
    if not ap:
        raise HTTPException(404, f"Approval {approval_id} not found")
    if ap.status != ApprovalStatus.PENDING:
        raise HTTPException(409, f"Approval is already {ap.status.value}")
    return ap


def _sync_purchase(
    session: Session, ap: Approval, status: PurchaseStatus, capture_id: Optional[str],
    reasons: Optional[list[str]],
) -> None:
    """An approval created by the shopping agent points at its Purchase via source_id."""
    if ap.source != "purchase" or not (ap.source_id or "").isdigit():
        return
    purchase = session.get(Purchase, int(ap.source_id))  # type: ignore[arg-type]
    if purchase is None:
        return
    purchase.status = status
    if capture_id:
        purchase.capture_id = capture_id
    if reasons:
        purchase.judge_reasons = reasons
    session.add(purchase)


def _agent_extra(session: Session, ap: Approval) -> dict[str, str]:
    """If an external agent asked for this purchase, its judge_spend audit row names it; carry that onto
    the approved capture's audit rows so the dashboard still shows who shopped."""
    rows = session.exec(select(AuditLog).where(AuditLog.action == "judge_spend").order_by(AuditLog.id.desc())).all()  # type: ignore[attr-defined]
    for r in rows:
        if r.payload_json.get("approval_id") == ap.id and r.payload_json.get("agent"):
            return {"agent": r.payload_json["agent"], "key_prefix": r.payload_json.get("key_prefix", "")}
    return {}


@router.get("")
def list_approvals(
    status: Optional[ApprovalStatus] = None, session: Session = Depends(get_session)
) -> list[Approval]:
    q = select(Approval).order_by(Approval.id.desc())  # type: ignore[union-attr]
    if status:
        q = q.where(Approval.status == status)
    return list(session.exec(q).all())


@router.post("/{approval_id}/approve")
async def approve(approval_id: int, session: Session = Depends(get_session),
                  x_owner_token: OwnerHeader = None) -> SpendResult:
    ap = _get_pending(session, approval_id)
    check_owner(session, ap.allowance_id, x_owner_token)
    allowance = session.get(Allowance, ap.allowance_id)
    assert allowance is not None
    # Same spend path as everything else; only the approval threshold is waived.
    result = await spend(
        session, ap.allowance_id, ap.amount, ap.category, ap.merchant_name, ap.channel,
        source=ap.source, source_id=ap.source_id, request_id=make_request_id(allowance, "approval", ap.id),
        skip_approval=True, audit_extra=_agent_extra(session, ap),
    )
    if result.verdict.decision != "PASS":
        raise HTTPException(
            409, {"message": "Approval can no longer be spent", "reasons": result.verdict.reasons}
        )
    ap.status = ApprovalStatus.APPROVED
    _sync_purchase(session, ap, PurchaseStatus.CAPTURED, result.capture_id, None)
    ap.decided_at = datetime.now(timezone.utc)
    session.add(ap)
    session.commit()
    audit.log(session, "user", "approve", verdict="APPROVED", paypal_ref=result.capture_id,
              payload={"approval_id": ap.id, "allowance_id": ap.allowance_id, "amount": ap.amount})
    return result


@router.post("/{approval_id}/deny")
def deny(approval_id: int, session: Session = Depends(get_session), x_owner_token: OwnerHeader = None) -> Approval:
    ap = _get_pending(session, approval_id)
    check_owner(session, ap.allowance_id, x_owner_token)
    ap.status = ApprovalStatus.DENIED
    _sync_purchase(session, ap, PurchaseStatus.BLOCKED, None, ["You denied this purchase"])
    ap.decided_at = datetime.now(timezone.utc)
    session.add(ap)
    session.commit()
    audit.log(session, "user", "deny", verdict="DENIED",
              payload={"approval_id": ap.id, "allowance_id": ap.allowance_id, "amount": ap.amount})
    session.refresh(ap)  # audit.log committed, which expired ap
    return ap
