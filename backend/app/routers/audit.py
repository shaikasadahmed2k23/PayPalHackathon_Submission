from typing import Any, Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.db import get_session
from app.models import Actor, AuditLog, LedgerEntry, Purchase

router = APIRouter(tags=["audit"])


@router.get("/audit")
def list_audit(
    allowance_id: Optional[int] = None,
    actor: Optional[Actor] = None,
    verdict: Optional[str] = None,
    limit: int = 200,
    session: Session = Depends(get_session),
) -> list[AuditLog]:
    """Newest first. allowance_id matches the entry's payload (or its purchase's allowance)."""
    q = select(AuditLog).order_by(AuditLog.id.desc())  # type: ignore[union-attr]
    if actor:
        q = q.where(AuditLog.actor == actor)
    if verdict:
        q = q.where(AuditLog.verdict == verdict)
    if allowance_id is None:
        return list(session.exec(q.limit(limit)).all())
    own_purchases = {p.id for p in session.exec(select(Purchase).where(Purchase.allowance_id == allowance_id)).all()}
    out: list[AuditLog] = []
    for row in session.exec(q).all():
        pl: dict[str, Any] = row.payload_json
        if pl.get("allowance_id") == allowance_id or (
            "allowance_id" not in pl and pl.get("purchase_id") in own_purchases
        ):
            out.append(row)
            if len(out) >= limit:
                break
    return out


@router.get("/ledger")
def list_ledger(allowance_id: Optional[int] = None, session: Session = Depends(get_session)) -> list[LedgerEntry]:
    q = select(LedgerEntry).order_by(LedgerEntry.id.desc())  # type: ignore[union-attr]
    if allowance_id is not None:
        q = q.where(LedgerEntry.allowance_id == allowance_id)
    return list(session.exec(q).all())
