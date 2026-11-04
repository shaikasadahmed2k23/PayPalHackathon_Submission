from typing import Any, Optional

from sqlmodel import Session

from app.models import Actor, AuditLog


def log(
    session: Session,
    actor: Actor | str,
    action: str,
    verdict: Optional[str] = None,
    reasons: Optional[list[str]] = None,
    paypal_ref: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
) -> AuditLog:
    entry = AuditLog(
        actor=Actor(actor),
        action=action,
        verdict=verdict,
        reasons=reasons or [],
        paypal_ref=paypal_ref,
        payload_json=payload or {},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry
